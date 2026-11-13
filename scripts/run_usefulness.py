"""Run the usefulness experiment (template vs verified agent answers, LLM-judged, blind).

  python scripts/run_usefulness.py --dry-run          # offline fakes, no Azure calls
  python scripts/run_usefulness.py --repeats 3        # real run, DefaultAzureCredential, no keys
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pinned_scoring.experiment.clients import AzureOpenAIModelClient  # noqa: E402
from pinned_scoring.experiment.config import load_config  # noqa: E402
from pinned_scoring.experiment.runner import POLICY_PATH  # noqa: E402
from pinned_scoring.experiment.usefulness import render_summary, run_usefulness  # noqa: E402
from pinned_scoring.policy import Policy  # noqa: E402
from pinned_scoring.synth import read_dataset  # noqa: E402


class _OfflineAgent:
    def chat(self, messages, tools=None):
        return {"content": "This parcel's receipt is shown in the data.", "tool_calls": []}


class _OfflineJudge:
    def chat(self, messages, tools=None):
        s = {"answers_question": 3, "plain_language": 3, "actionable": 3}
        return {"content": json.dumps({"A": s, "B": s}), "tool_calls": []}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--seed", type=int, default=20260715)
    ap.add_argument("--agent-model", default=None, help="deployment for Arm D (default: strong)")
    ap.add_argument("--judge-model", default=None, help="deployment for the judge (default: small)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=str(ROOT / "docs" / "evidence" / "scenario-2-ontology"))
    args = ap.parse_args()

    config = load_config(args.config) if args.config else load_config()
    agent_model = args.agent_model or config.strong
    judge_model = args.judge_model or config.small
    if args.dry_run:
        agent, judge = _OfflineAgent(), _OfflineJudge()
    else:
        agent = AzureOpenAIModelClient(agent_model, config)
        judge = AzureOpenAIModelClient(judge_model, config)

    with tempfile.TemporaryDirectory() as tmp:
        result = run_usefulness(read_dataset(), Policy.load(POLICY_PATH), agent, judge, tmp,
                                args.repeats, args.seed, args.workers)
    meta = {"agent_model": agent_model, "judge_model": judge_model, "dry_run": args.dry_run,
            "endpoint": config.endpoint}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = "usefulness-dryrun" if args.dry_run else "usefulness"
    (out / f"{name}.json").write_text(json.dumps({"meta": meta, **result}, indent=1), encoding="utf-8")
    (out / f"{name}-summary.md").write_text(render_summary(result, meta), encoding="utf-8")
    print(render_summary(result, meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
