"""Evaluate the Foundry agent that uses the Fabric ontology MCP tool (no data agent).

  python scripts/run_foundry_ontology_eval.py --step usefulness   # 20 questions, blind judge, faithfulness
  python scripts/run_foundry_ontology_eval.py --step chain        # C1-C5 once, C3 repeated, vs ground truth

The agent (default 'pinned-score-agent-fabric', latest version) is called through the Responses API with
DefaultAzureCredential. Every failure is recorded in the evidence; nothing is retried silently except
HTTP 429 back-off, which is counted and reported. Evidence goes to docs/evidence/scenario-2-ontology/.
"""

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from pinned_scoring import access_chain as ac  # noqa: E402
from pinned_scoring import foundry_agent as fa  # noqa: E402

OUT = ROOT / "docs" / "evidence" / "scenario-2-ontology"
AGENT = "pinned-score-agent-fabric"
FORMAT_HINT = ('Reply with one JSON object only: {"parcels": [<parcel ids, or crew type names for crew '
               'questions>], "value": <number or null>, "note": "<one sentence>"}.')


def is_throttle(exc) -> bool:
    return "429" in str(exc) or "Too Many" in str(exc) or "RateLimit" in type(exc).__name__


class OntologyAgent:
    """Calls the Foundry agent; counts 429 back-offs and keeps tool-call evidence."""

    def __init__(self, name=AGENT, max_attempts=5):
        self.oc = fa.make_project_client().get_openai_client(agent_name=name)
        self.max_attempts, self.throttled, self.calls = max_attempts, 0, []

    def ask(self, text: str) -> dict:
        for attempt in range(self.max_attempts):
            try:
                r = self.oc.responses.create(input=text)
                items = [json.loads(i.model_dump_json()) for i in r.output]
                tools = [i for i in items if i.get("type") == "mcp_call"]
                rec = {"answer": r.output_text, "tool_calls": len(tools),
                       "tool_errors": [t.get("error") for t in tools if t.get("error")],
                       "status": getattr(r, "status", None), "error": None}
                self.calls.append(rec)
                return rec
            except Exception as exc:  # noqa: BLE001 - recorded
                if is_throttle(exc) and attempt < self.max_attempts - 1:
                    self.throttled += 1
                    time.sleep(min(60, 10 * 2 ** attempt))
                    continue
                rec = {"answer": None, "tool_calls": 0, "tool_errors": [],
                       "error": f"{type(exc).__name__}: {exc}"[:400]}
                self.calls.append(rec)
                return rec
        raise AssertionError("unreachable")


class AgentAsChatClient:
    """Adapter for the usefulness harness: sends ONLY the question (no receipt data) to the agent."""

    def __init__(self, agent: OntologyAgent):
        self.agent = agent

    def chat(self, messages, tools=None):
        m = re.search(r"Question:\s*(.*?)\n", messages[-1]["content"])
        rec = self.agent.ask(m.group(1) if m else messages[-1]["content"])
        if rec["error"]:
            raise RuntimeError(rec["error"])
        return {"content": rec["answer"], "tool_calls": []}


def step_usefulness(args):
    from pinned_scoring.experiment.clients import AzureOpenAIModelClient
    from pinned_scoring.experiment.config import load_config
    from pinned_scoring.experiment.runner import POLICY_PATH
    from pinned_scoring.experiment.usefulness import render_summary, run_usefulness
    from pinned_scoring.policy import Policy
    from pinned_scoring.synth import read_dataset

    config = load_config()
    agent = OntologyAgent()
    judge = AzureOpenAIModelClient(config.small, config)
    with tempfile.TemporaryDirectory() as tmp:
        result = run_usefulness(read_dataset(), Policy.load(POLICY_PATH), AgentAsChatClient(agent), judge,
                                tmp, args.repeats, 20260715, workers=1)
    meta = {"agent_model": f"Foundry agent {AGENT} (ontology MCP tool)", "judge_model": config.small,
            "dry_run": False, "endpoint": fa.DEFAULT_PROJECT_ENDPOINT}
    result["agent_calls"] = {"total": len(agent.calls), "throttled_retries": agent.throttled,
                             "tool_calls": sum(c["tool_calls"] for c in agent.calls),
                             "errors": [c["error"] for c in agent.calls if c["error"]]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "foundry_ontology_usefulness.json").write_text(json.dumps({"meta": meta, **result}, indent=1),
                                                          encoding="utf-8")
    text = render_summary(result, meta)
    (OUT / "foundry_ontology_usefulness_summary.md").write_text(text, encoding="utf-8")
    print(text)
    print(json.dumps(result["agent_calls"]))
    return 0


def step_chain(args):
    import fabric_load_chain as fl
    chain = ac.Chain(ac.generate(fl.local_parcels()))
    qs = ac.questions(chain)
    agent = OntologyAgent()
    jobs = [(q, 0) for q in qs] + [(q, i) for i in range(1, args.c3_repeats + 1) for q in qs if q.qid == "C3"]
    rows = []
    for q, rep in jobs:
        truth = ac.truth(chain, q)
        rec = agent.ask(f"{q.text}\n{FORMAT_HINT}")
        ans = ac.parse(rec["answer"])
        g = ac.grade({**truth, "basis": (ans or {}).get("basis", truth["basis"])}, ans)
        rows.append({"qid": q.qid, "repeat": rep, "question": q.text, "truth": truth, "answer_text": rec["answer"],
                     "parsed": ans, "correct": g["correct"], "tool_calls": rec["tool_calls"],
                     "tool_errors": rec["tool_errors"], "error": rec["error"]})
        print(q.qid, rep, "correct" if g["correct"] else "WRONG", ascii(str((ans or {}).get("parcels")))
              if ans else ascii(str(rec["answer"] or rec["error"])[:200]))
    summary = {"questions_run": len(rows), "correct": sum(r["correct"] for r in rows),
               "errors": sum(bool(r["error"]) for r in rows), "throttled_retries": agent.throttled,
               "c3_runs": [{"repeat": r["repeat"], "parcels": (r["parsed"] or {}).get("parcels"),
                            "correct": r["correct"]} for r in rows if r["qid"] == "C3"]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "foundry_ontology_chain.json").write_text(
        json.dumps({"agent": AGENT, "format_hint": FORMAT_HINT, "summary": summary, "rows": rows}, indent=1,
                   default=str), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--step", choices=["usefulness", "chain"], required=True)
    ap.add_argument("--repeats", type=int, default=3, help="judge repeats per question (usefulness)")
    ap.add_argument("--c3-repeats", type=int, default=4, help="extra C3 asks (chain)")
    args = ap.parse_args(argv)
    return step_usefulness(args) if args.step == "usefulness" else step_chain(args)


if __name__ == "__main__":
    raise SystemExit(main())
