"""Export experiment results as a JSONL dataset usable by Microsoft Foundry evaluations.

Each line is one run: query, response, context (the pinned receipt), ground truth and the
harness's own metrics. Foundry evaluations map dataset columns to evaluator inputs, so column
names here are plain (query, response, context, ground_truth). Nothing is uploaded or run.
"""

import json
from pathlib import Path

QUERY = "Explain the wildfire risk score, band, status and data provenance for parcel {parcel}."


def export_rows(runs: list[dict], receipts_dir: Path, arms: set[str] | None = None) -> list[dict]:
    rows = []
    for run in runs:
        if arms and run["arm"] not in arms:
            continue
        receipt_path = Path(receipts_dir) / f"{run['parcel']}.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else None
        rows.append({
            "query": QUERY.format(parcel=run["parcel"]),
            "response": json.dumps(run.get("answer"), sort_keys=True),
            "context": json.dumps(receipt, sort_keys=True) if receipt else "",
            "ground_truth": json.dumps(
                {k: receipt.get(k) for k in ("status", "score", "band", "reasons")}, sort_keys=True
            ) if receipt else "",
            "model": run["model"], "arm": run["arm"], "parcel": run["parcel"],
            "repeat": run["repeat"], "blocked": bool(run.get("blocked")),
            "harness_metrics": run.get("metrics", {}),
        })
    return rows


def write_jsonl(rows: list[dict], path: Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return len(rows)
