"""Access-chain run: Fabric chain tables -> Foundry agent, graph vs flat vs bare model.

  python scripts/run_e2e_chain.py --repeats 3 --workers 2 --budget-usd 3
  python scripts/run_e2e_chain.py --source local     # same flow on locally generated tables (no Fabric)
Arms: foundry-graph (traversal tools), foundry-flat (single-table reads, no joins), bare-model (no tools).
Ground truth is computed in code from the tables. Writes docs/evidence/scenario-2-ontology/chain_arms.json.
"""

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from pinned_scoring import access_chain as ac  # noqa: E402
from pinned_scoring import foundry_agent as fa  # noqa: E402
from pinned_scoring.controls import BudgetExceeded, BudgetGuard, CostMeter  # noqa: E402

OUT = ROOT / "docs" / "evidence" / "scenario-2-ontology" / "chain_arms.json"
GRAPH_AGENT, FLAT_AGENT = "pinned-score-agent-chain", "pinned-score-agent-chain-flat"
ARMS = ("foundry-graph", "foundry-flat", "bare-model")


class Recorder:
    def __init__(self, box):
        self.box, self.results = box, []

    def call(self, name, arguments):
        out = self.box.call(name, arguments)
        self.results.append(out)
        return out


def load_tables(source):
    import fabric_load_chain as fl
    local = ac.generate(fl.local_parcels())
    if source == "local":
        return local, local
    return fl.fabric_tables(), local


def is_throttle(exc):
    return "429" in str(exc) or "RateLimit" in type(exc).__name__ or "Too Many" in str(exc)


def run_job(client, arm, chain, q, truth, repeat, model, guard, max_attempts=6):
    throttled, total = 0, CostMeter(model)
    for attempt in range(max_attempts):
        meter, trace, results = CostMeter(model), [], []
        prompt = f"{ac.SYSTEM}\n\n{q.text}"
        try:
            guard.check()
            if arm == "bare-model":
                resp = client.responses.create(model=model, instructions=ac.SYSTEM, input=q.text)
                meter.add(getattr(resp, "usage", None))
                text = resp.output_text
            else:
                box = Recorder(ac.ChainTools(chain, graph=(arm == "foundry-graph")))
                text = fa.run_agent_turn(client, box, prompt, GRAPH_AGENT if arm == "foundry-graph"
                                         else FLAT_AGENT, trace, meter=meter)
                results = box.results
            error = None
        except BudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001
            for k in ("input_tokens", "output_tokens", "calls"):
                setattr(total, k, getattr(total, k) + getattr(meter, k))
            if is_throttle(exc) and attempt < max_attempts - 1:
                throttled += 1
                time.sleep(min(60, 5 * 2 ** attempt))
                continue
            text, results, error = None, [], f"{type(exc).__name__}: {exc}"[:300]
        answer = ac.parse(text)
        m = ac.grade(truth, answer)
        m["numbers_pinned"] = ac.numbers_pinned(answer, results, q.text) if answer is not None else 0.0
        for k in ("input_tokens", "output_tokens", "calls"):
            setattr(total, k, getattr(total, k) + getattr(meter, k))
        throttled_error = bool(error) and is_throttle(Exception(error))
        return {"qid": q.qid, "arm": arm, "repeat": repeat, "metrics": m, "tool_calls": len(trace),
                "error": error, "api_error": bool(error) and "did not finish" not in error,
                "throttled_retries": throttled, "throttled_error": throttled_error, "answer": answer,
                "tokens": {"input": total.input_tokens, "output": total.output_tokens, "calls": total.calls,
                           "usd_estimate": round(total.usd, 5)}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--budget-usd", type=float, default=3.0)
    ap.add_argument("--model", default=fa.DEFAULT_MODEL)
    ap.add_argument("--arms", nargs="*", default=list(ARMS))
    ap.add_argument("--source", default="fabric", choices=["fabric", "local"])
    args = ap.parse_args()

    tables, local = load_tables(args.source)
    chain, local_chain = ac.Chain(tables), ac.Chain(local)
    qs = ac.questions(local_chain)
    truths = {q.qid: ac.truth(chain, q) for q in qs}
    parity = {"questions": len(qs), "truth_equal_local_vs_source": sum(
        truths[q.qid] == ac.truth(local_chain, q) for q in qs)}

    project = fa.make_project_client()
    client = project.get_openai_client()
    from azure.ai.projects.models import PromptAgentDefinition
    instr = ac.SYSTEM + " Call tools to read the data; never answer from memory."
    versions = {}
    for name, tools in ((GRAPH_AGENT, ac.GRAPH_TOOLS), (FLAT_AGENT, ac.FLAT_TOOLS)):
        v = project.agents.create_version(agent_name=name, description="Access-chain arm (read-only tools).",
                                          definition=PromptAgentDefinition(model=args.model, instructions=instr,
                                                                           tools=tools))
        versions[name] = getattr(v, "version", None)

    meter = CostMeter(args.model)
    guard = BudgetGuard(meter, args.budget_usd)
    jobs = [(a, q, r) for a in args.arms for q in qs for r in range(args.repeats)]
    runs, lock, aborted = [], threading.Lock(), None

    def work(job):
        a, q, r = job
        rec = run_job(client, a, chain, q, truths[q.qid], r, args.model, guard)
        with lock:
            runs.append(rec)
            t = rec["tokens"]
            meter.input_tokens += t["input"]
            meter.output_tokens += t["output"]
            meter.calls += t["calls"]
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(work, jobs))
    except BudgetExceeded as exc:
        aborted = str(exc)
    runs.sort(key=lambda r: (r["arm"], r["qid"], r["repeat"]))

    summary = {}
    for a in args.arms:
        rows = [r for r in runs if r["arm"] == a]
        if not rows:
            continue
        n = len(rows)
        mean = lambda k: sum(r["metrics"][k] or 0 for r in rows) / n  # noqa: E731
        summary[a] = {"n": n, "correct": round(mean("correct"), 3), "provenance_ok": round(mean("provenance_ok"), 3),
                      "numbers_pinned": round(mean("numbers_pinned"), 3),
                      "tool_calls_mean": round(sum(r["tool_calls"] for r in rows) / n, 2),
                      "api_errors": sum(1 for r in rows if r["api_error"]),
                      "agent_failures": sum(1 for r in rows if r["error"] and not r["api_error"]),
                      "throttled_retries": sum(r["throttled_retries"] for r in rows),
                      "usd_per_question": round(sum(r["tokens"]["usd_estimate"] for r in rows) / n, 5),
                      "by_question": {q.qid: round(sum(r["metrics"]["correct"] for r in rows if r["qid"] == q.qid)
                                                   / max(1, sum(1 for r in rows if r["qid"] == q.qid)), 2)
                                      for q in qs}}
    result = {"meta": {"source": args.source, "model": args.model, "repeats": args.repeats,
                       "agent_versions": versions, "aborted": aborted, "cost": meter.summary(),
                       "seed": ac.SEED, "tables": list(ac.TABLES), "relationships": list(ac.RELATIONSHIPS)},
              "parity": parity, "ground_truth": {k: v for k, v in truths.items()},
              "questions": {q.qid: q.text for q in qs}, "summary": summary, "runs": runs}
    OUT.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"meta": result["meta"], "parity": parity, "summary": summary}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
