"""Re-score saved per-run rows with the CURRENT verifier (no model calls, no network).

Only Arm B delivers through the verifier, so only Arm B rows can change. A and A+ rows are copied
verbatim. A B row is re-scored from its stored raw (pre-verifier) answer: the same guarded_answer
path used at run time decides block / pass, and metrics are recomputed. Rows that ended in an API
error (no model answer) are kept as they are. Original rows are never modified in place; the
re-scored row keeps the original block decision under `original_blocked` / `original_violations`.

Usage: python scripts/rescore_rows.py [--evidence DIR] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import tempfile
from decimal import Decimal
from pathlib import Path

from pinned_scoring.agent import ReceiptStore, guarded_answer
from pinned_scoring.experiment.arms import _narration, receipt_answer
from pinned_scoring.experiment.metrics import score_answer, signature
from pinned_scoring.experiment.runner import POLICY_PATH, _jsonable, build_cases
from pinned_scoring.policy import Policy
from pinned_scoring.receipts_store import ReadOnlyReceiptStore
from pinned_scoring.synth.generator import read_dataset

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence" / "scenario-1-locking-the-score"
SOURCES = {  # label -> path of runs.json relative to the evidence dir
    "main": "runs.json",
    "gpt-6.1-sol": "model-comparison/gpt-6.1-sol/runs.json",
    "gpt-6.1-terra": "model-comparison/gpt-6.1-terra/runs.json",
    "grok-4.7": "model-comparison/grok-4.7/runs.json",
    "phi-4-rerun": "model-comparison/phi-4-rerun/runs.json",
}


def _parsed(raw: dict) -> dict:
    return {**raw, "score": None if raw["score"] is None else Decimal(str(raw["score"]))}


def rescore_row(row: dict, case) -> dict:
    """Return a re-scored copy of one row (A / A+ and errored rows are returned unchanged)."""
    out = json.loads(json.dumps(row))
    if row["arm"] != "B" or row.get("error") or row.get("raw_answer") is None:
        out["rescored"] = False
        return out
    parsed = _parsed(row["raw_answer"])
    rstore = ReceiptStore()
    rstore.load(case.receipt)
    verdict = guarded_answer(lambda q, ctx: _narration(parsed), "explain", rstore, case.parcel)
    blocked = bool(verdict.blocked)
    delivered = receipt_answer(case) if blocked else parsed
    out.update(
        rescored=True,
        original_blocked=row["blocked"], original_violations=row["violations"],
        blocked=blocked,
        violations=[{"kind": v.kind, "detail": v.detail} for v in verdict.violations],
        answer=_jsonable(delivered), signature=list(signature(delivered)),
        metrics=score_answer(case, delivered, blocked),
        raw_metrics=score_answer(case, parsed, False),
    )
    out["block_changed"] = blocked != row["blocked"]
    return out


def build_case_map(parcels) -> dict:
    policy = Policy.load(POLICY_PATH)
    dataset = read_dataset(ROOT / "data")
    with tempfile.TemporaryDirectory() as tmp:
        return build_cases(dataset, policy, sorted(parcels), tmp)


def rescore_file(rows: list, cases: dict) -> list:
    return [rescore_row(r, cases[r["parcel"]]) for r in rows]


def summarize_changes(rows: list) -> dict:
    out = {}
    for r in rows:
        if r["arm"] != "B":
            continue
        s = out.setdefault(r["model"], {"B_runs": 0, "rescored": 0, "errors_kept": 0,
                                        "blocked_before": 0, "blocked_after": 0,
                                        "block_changed": 0})
        s["B_runs"] += 1
        s["errors_kept"] += bool(r.get("error"))
        if r.get("rescored"):
            s["rescored"] += 1
            s["blocked_before"] += bool(r["original_blocked"])
            s["blocked_after"] += bool(r["blocked"])
            s["block_changed"] += bool(r["block_changed"])
        else:
            s["blocked_before"] += bool(r["blocked"])
            s["blocked_after"] += bool(r["blocked"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", default=str(EVIDENCE))
    ap.add_argument("--out", default=str(EVIDENCE / "combined-150" / "rescored-p001-p050"))
    a = ap.parse_args()
    ev, out = Path(a.evidence), Path(a.out)
    report = {}
    for label, rel in SOURCES.items():
        rows = json.loads((ev / rel).read_text(encoding="utf-8"))
        cases = build_case_map({r["parcel"] for r in rows})
        done = rescore_file(rows, cases)
        d = out / label
        d.mkdir(parents=True, exist_ok=True)
        (d / "runs.json").write_text(json.dumps(done, indent=1), encoding="utf-8")
        report[label] = {"source": rel, "rows": len(rows), "arm_B": summarize_changes(done)}
        print(label, report[label]["arm_B"])
    (out / "rescore_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
