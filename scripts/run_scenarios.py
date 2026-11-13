"""Graph agent vs flat-table agent on the decision scenarios.

  python scripts/run_scenarios.py --dry-run     # offline oracle stub, no Azure calls
  python scripts/run_scenarios.py --repeats 3   # live, DefaultAzureCredential, no keys
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pinned_scoring.experiment import scenarios_eval as ev  # noqa: E402
from pinned_scoring.experiment.clients import AzureOpenAIModelClient  # noqa: E402
from pinned_scoring.experiment.config import load_config  # noqa: E402
from pinned_scoring.experiment.runner import POLICY_PATH  # noqa: E402
from pinned_scoring.graph import read_graph  # noqa: E402
from pinned_scoring.policy import Policy  # noqa: E402
from pinned_scoring.scenarios import Context  # noqa: E402
from pinned_scoring.synth import read_dataset  # noqa: E402


def build_context():
    ds = read_dataset()
    return Context(read_graph(), ds.observations, Policy.load(POLICY_PATH), ds.as_of)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--models", nargs="*", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=str(ROOT / "docs" / "evidence" / "scenario-3-four-tools"))
    args = ap.parse_args()
    ctx = build_context()
    config = None if args.dry_run else (load_config(args.config) if args.config else load_config())
    if args.dry_run:
        names = ["oracle-stub"]
        clients = {"oracle-stub": ev.OracleClient(ctx)}
    else:
        names = args.models or [config.strong, config.small]
        clients = {m: AzureOpenAIModelClient(m, config) for m in names}
    result = ev.run_scenarios(ctx, clients, args.repeats, args.workers)
    meta = {"models": names, "repeats": args.repeats, "dry_run": args.dry_run,
            "endpoint": None if config is None else config.endpoint}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = "scenarios-dryrun" if args.dry_run else "scenarios"
    (out / f"{name}.json").write_text(json.dumps({"meta": meta, **result}, indent=1), encoding="utf-8")
    (out / f"{name}-summary.md").write_text(ev.render_summary(result, meta), encoding="utf-8")
    print(ev.render_summary(result, meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
