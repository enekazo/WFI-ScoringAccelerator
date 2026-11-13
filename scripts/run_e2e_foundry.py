"""End-to-end run: Fabric data + Fabric graph tables -> Foundry agent, three arms.

  python scripts/run_e2e_foundry.py --budget-usd 8 --repeats 1 --workers 2

Arms (same 20 decision questions, same grader, same instructions text):
  foundry-graph : Foundry agent 'pinned-score-agent' (new version) + receipt tools + graph tools
  foundry-flat  : Foundry agent 'pinned-score-agent-flat' + receipt tools + raw tables + what_if
  bare-model    : plain model call, no agent, no tools
Receipts check: the Foundry agent explains parcels; the verifier checks the narration.
Everything is read from Fabric (SQL analytics endpoint, Entra auth, no keys). Set
PINNED_DATA_SOURCE=local to run the same flow against the local files instead.
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pinned_scoring import foundry_agent as fa  # noqa: E402
from pinned_scoring import graph_tools as gt  # noqa: E402
from pinned_scoring.controls import BudgetExceeded, BudgetGuard, CostMeter  # noqa: E402
from pinned_scoring.experiment import scenarios_eval as ev  # noqa: E402
from pinned_scoring.experiment.runner import POLICY_PATH  # noqa: E402
from pinned_scoring.graph import read_graph  # noqa: E402
from pinned_scoring.policy import Policy  # noqa: E402
from pinned_scoring.scenarios import Context  # noqa: E402
from pinned_scoring.synth import read_dataset  # noqa: E402

OUT = ROOT / "docs" / "evidence" / "scenario-3-four-tools"
FLAT_AGENT = "pinned-score-agent-flat"
ARMS = ("foundry-graph", "foundry-flat", "bare-model")


def build_contexts(source: str):
    ds = read_dataset()
    policy = Policy.load(POLICY_PATH)
    local = Context(read_graph(), ds.observations, policy, ds.as_of)
    if source != "fabric":
        return local, local
    from pinned_scoring.fabric.graph_store import graph_from_fabric, silver_observations_from_fabric
    from pinned_scoring.fabric.sql_endpoint import connect_auto
    server, db = fa.FABRIC_SERVER, fa.FABRIC_DATABASE
    graph = graph_from_fabric(connect_auto, server, db)
    obs = silver_observations_from_fabric(connect_auto, server, db)
    return Context(graph, obs, policy, ds.as_of), local


class FlatToolbox(fa.FoundryToolbox):
    def __init__(self, ctx, reader):
        super().__init__(reader=reader)
        self._flat = ev.FlatTools(ctx)

    def call(self, name, arguments):
        if name in ("get_table", "what_if"):
            return self._flat.call(name, arguments)
        return super().call(name, arguments)


class Recorder:
    """Wraps a toolbox so every tool result is kept for the numbers-pinned check."""

    def __init__(self, box):
        self.box, self.results = box, []

    def call(self, name, arguments):
        out = self.box.call(name, arguments)
        self.results.append(out)
        return out


def is_throttle(exc) -> bool:
    return "429" in str(exc) or "RateLimit" in type(exc).__name__ or "Too Many" in str(exc)


def run_job(client, arm, ctx, reader, q, truth, repeat, model, cap_guard, max_attempts=6):
    throttled, meter_total = 0, CostMeter(model)
    for attempt in range(max_attempts):
        meter = CostMeter(model)
        trace, rec = [], None
        prompt = f"{ev.SYSTEM}\n\n[scenario:{q.scenario}] {q.text}"
        try:
            cap_guard.check()
            if arm == "bare-model":
                resp = client.responses.create(model=model, instructions=ev.SYSTEM,
                                               input=f"[scenario:{q.scenario}] {q.text}")
                meter.add(getattr(resp, "usage", None))
                text = resp.output_text
                results = []
            else:
                box = Recorder(fa.GraphToolbox(ctx, reader=reader) if arm == "foundry-graph"
                               else FlatToolbox(ctx, reader))
                name = fa.AGENT_NAME if arm == "foundry-graph" else FLAT_AGENT
                text = fa.run_agent_turn(client, box, prompt, name, trace, meter=meter)
                results = box.results
            error = None
        except BudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001
            for k in ("input_tokens", "output_tokens", "calls"):
                setattr(meter_total, k, getattr(meter_total, k) + getattr(meter, k))
            if is_throttle(exc) and attempt < max_attempts - 1:
                throttled += 1
                time.sleep(min(60, 5 * 2 ** attempt))
                continue
            text, results, error = None, [], f"{type(exc).__name__}: {exc}"[:300]
        answer = ev.parse_answer(text)
        m = ev.grade(q, truth, answer)
        if answer is not None:
            pin = gt.verify_pinned_numbers(answer, results, q.text)
            m["numbers_pinned"] = float(pin["ok"])
        else:
            pin = {"unpinned": []}
            m["numbers_pinned"] = 0.0
        for k in ("input_tokens", "output_tokens", "calls"):
            setattr(meter_total, k, getattr(meter_total, k) + getattr(meter, k))
        return {"qid": q.qid, "scenario": q.scenario, "arm": arm, "repeat": repeat, "metrics": m,
                "tool_calls": len(trace), "unpinned": pin["unpinned"][:8], "error": error,
                "throttled_retries": throttled, "answer": answer,
                "tokens": {"input": meter_total.input_tokens, "output": meter_total.output_tokens,
                           "calls": meter_total.calls, "usd_estimate": round(meter_total.usd, 5)}}


def receipts_check(client, ctx, reader, parcels, model, guard):
    out, meter = [], CostMeter(model)
    for p in parcels:
        box = fa.GraphToolbox(ctx, reader=reader)
        trace = []
        try:
            guard.check()
            g = fa.chat(client, box, f"Explain the score for parcel {p} in plain language.", trace=trace)
            out.append({"parcel": p, "blocked": g.blocked, "violations": [str(v) for v in g.violations],
                        "tool_calls": len(trace), "error": None})
        except BudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001
            out.append({"parcel": p, "blocked": None, "error": f"{type(exc).__name__}: {exc}"[:300]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--budget-usd", type=float, default=8.0)
    ap.add_argument("--receipt-parcels", type=int, default=20)
    ap.add_argument("--model", default=fa.DEFAULT_MODEL)
    ap.add_argument("--arms", nargs="*", default=list(ARMS))
    ap.add_argument("--skip-agent-create", action="store_true")
    ap.add_argument("--source", default=os.environ.get(fa.DATA_SOURCE_ENV, "fabric"))
    args = ap.parse_args()
    os.environ[fa.DATA_SOURCE_ENV] = args.source

    ctx, local = build_contexts(args.source)
    reader = fa.make_reader(args.source)
    questions = ev.default_questions(ctx.policy)
    truths = {q.qid: ev.truth_for(ctx, q) for q in questions}
    local_truths = {q.qid: ev.truth_for(local, q) for q in questions}
    parity = {"questions": len(questions),
              "truth_equal_local_vs_source": sum(truths[k] == local_truths[k] for k in truths)}

    project = fa.make_project_client()
    client = project.get_openai_client()
    versions = {}
    if not args.skip_agent_create:
        v = fa.create_or_update_agent(project, args.model, fa.AGENT_NAME, graph_tools=True)
        versions[fa.AGENT_NAME] = getattr(v, "version", None)
        from azure.ai.projects.models import PromptAgentDefinition
        flat = project.agents.create_version(
            agent_name=FLAT_AGENT, description="Flat-table arm (no graph tools).",
            definition=PromptAgentDefinition(model=args.model, instructions=fa.INSTRUCTIONS,
                                             tools=fa.TOOL_DEFINITIONS + ev.FLAT_TOOL_DEFINITIONS))
        versions[FLAT_AGENT] = getattr(flat, "version", None)

    meter = CostMeter(args.model)
    guard = BudgetGuard(meter, args.budget_usd)
    jobs = [(a, q, r) for a in args.arms for q in questions for r in range(args.repeats)]
    runs, lock = [], threading.Lock()

    def work(job):
        a, q, r = job
        rec = run_job(client, a, ctx, reader, q, truths[q.qid], r, args.model, guard)
        with lock:
            runs.append(rec)
            t = rec["tokens"]
            meter.input_tokens += t["input"]
            meter.output_tokens += t["output"]
            meter.calls += t["calls"]
    aborted = None
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(work, jobs))
    except BudgetExceeded as exc:
        aborted = str(exc)
    runs.sort(key=lambda r: (r["arm"], r["qid"], r["repeat"]))

    receipts = []
    if aborted is None and "foundry-graph" in args.arms:
        parcels = [f"P{i:03d}" for i in range(1, args.receipt_parcels + 1)]
        try:
            receipts = receipts_check(client, ctx, reader, parcels, args.model, guard)
        except BudgetExceeded as exc:
            aborted = str(exc)

    summary = {}
    for a in args.arms:
        rows = [r for r in runs if r["arm"] == a]
        if not rows:
            continue
        n = len(rows)
        mean = lambda k: sum(r["metrics"][k] or 0 for r in rows) / n  # noqa: E731
        summary[a] = {"n": n, "correct": mean("correct"), "provenance_ok": mean("provenance_ok"),
                      "numbers_pinned": mean("numbers_pinned"),
                      "tool_calls_mean": sum(r["tool_calls"] for r in rows) / n,
                      "api_errors": sum(1 for r in rows if r["error"]),
                      "throttled_retries": sum(r["throttled_retries"] for r in rows),
                      "usd_estimate": round(sum(r["tokens"]["usd_estimate"] for r in rows), 4),
                      "usd_per_question": round(sum(r["tokens"]["usd_estimate"] for r in rows) / n, 5)}
    result = {"meta": {"source": args.source, "model": args.model, "repeats": args.repeats,
                       "workers": args.workers, "agent_versions": versions,
                       "aborted": aborted, "cost": meter.summary()},
              "parity": parity, "summary": summary, "runs": runs,
              "receipts_check": {"n": len(receipts),
                                 "blocked": sum(1 for r in receipts if r.get("blocked")),
                                 "errors": sum(1 for r in receipts if r.get("error")),
                                 "rows": receipts}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / ("foundry_arms.json" if args.model == fa.DEFAULT_MODEL else f"foundry_arms_{args.model}.json")).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({"meta": result["meta"], "parity": parity, "summary": summary,
                      "receipts": {k: v for k, v in result["receipts_check"].items() if k != "rows"}},
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
