"""Export docs/evidence/scenario-1-locking-the-score/runs.json as a JSONL dataset for Microsoft Foundry evaluations (local only)."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pinned_scoring import foundry_eval  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", default=str(ROOT / "docs" / "evidence" / "scenario-1-locking-the-score" / "runs.json"))
    ap.add_argument("--receipts", default=str(ROOT / "docs" / "evidence" / "receipts"))
    ap.add_argument("--out", default=str(ROOT / "results" / "foundry-eval.jsonl"))
    ap.add_argument("--arm", action="append", help="limit to arm(s), e.g. --arm B")
    a = ap.parse_args(argv)
    runs = json.loads(Path(a.runs).read_text(encoding="utf-8"))
    rows = foundry_eval.export_rows(runs, Path(a.receipts), set(a.arm) if a.arm else None)
    print(f"wrote {foundry_eval.write_jsonl(rows, Path(a.out))} rows to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
