"""Merge per-batch extra-parcel results into results/extra/summary.md.

Pooled figures concatenate every DONE batch's runs.json (parcel is the resampling unit);
per-batch figures use each batch alone. Verifier blocks and model-endpoint 429s are
reported separately. Usage: python scripts/merge_extra_results.py [--dir results/extra]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pinned_scoring.experiment.metrics import METRICS
from pinned_scoring.experiment.runner import summarize


def fmt(m) -> str:
    if not m or m.get("mean") is None:
        return "-"
    return f"{m['mean']:.3f} [{m['lo']:.3f}, {m['hi']:.3f}]"


def load_batches(root: Path) -> dict:
    out = {}
    for d in sorted(root.glob("batch_*")):
        if (d / "DONE").exists() and (d / "runs.json").exists():
            out[d.name] = (json.loads((d / "runs.json").read_text(encoding="utf-8")),
                           json.loads((d / "stats.json").read_text(encoding="utf-8")))
    return out


def tally(rows) -> dict:
    t = {}
    for r in rows:
        s = t.setdefault((r["model"], r["arm"]), {"runs": 0, "blocks": 0, "errors": 0, "e429": 0})
        s["runs"] += 1
        s["blocks"] += bool(r["blocked"])
        err = r.get("error")
        s["errors"] += bool(err)
        s["e429"] += bool(err) and "429" in str(err)
    return t


def metrics_table(rows, lines) -> None:
    models = list(dict.fromkeys(r["model"] for r in rows))
    summ = summarize(rows, models)
    lines.append("| model | arm | " + " | ".join(METRICS) + " | reproducibility |")
    lines.append("|---|---|" + "---|" * (len(METRICS) + 1))
    for key, agg in summ.items():
        m, a = key.split("|", 1)
        lines.append(f"| {m} | {a} | " + " | ".join(fmt(agg[k]) for k in METRICS)
                     + f" | {fmt(agg['reproducibility'])} |")


def counts_table(rows, lines) -> None:
    lines += ["| model | arm | runs | verifier blocks | block rate | errors | of which HTTP 429 |",
              "|---|---|---|---|---|---|---|"]
    for (m, a), s in sorted(tally(rows).items()):
        lines.append(f"| {m} | {a} | {s['runs']} | {s['blocks']} | {s['blocks'] / s['runs']:.1%} "
                     f"| {s['errors']} | {s['e429']} |")


def build(root: Path) -> str:
    batches = load_batches(root)
    lines = ["# Extra-parcel run summary", "",
             f"Completed batches: {len(batches)} ({', '.join(batches) or 'none'}).", ""]
    cost = root / "cost.json"
    if cost.exists():
        c = json.loads(cost.read_text(encoding="utf-8"))
        lines += [f"Estimated spend (token counts x assumed list prices): ${c['est_total_usd']} "
                  f"of ${c['budget_usd']} cap. Prices USD/1M in/out: {c['prices_usd_per_1M_in_out']}", ""]
    allrows = [r for rows, _ in batches.values() for r in rows]
    if allrows:
        lines += ["## Pooled", "", "Mean [95% bootstrap CI]; resampling unit = parcel.", ""]
        metrics_table(allrows, lines)
        lines += ["", "### Verifier blocks vs errors (pooled)", ""]
        counts_table(allrows, lines)
    lines += ["", "## Model-endpoint 429 retries (separate from blocks)", "",
              "| batch | est. cost USD | 429 retries per model |", "|---|---|---|"]
    for name, (_, stats) in batches.items():
        lines.append(f"| {name} | {stats.get('est_cost_usd', '-')} | {stats.get('model_429_retries')} |")
    for name, (rows, _) in batches.items():
        lines += ["", f"## {name}", ""]
        metrics_table(rows, lines)
        lines.append("")
        counts_table(rows, lines)
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="results/extra")
    a = ap.parse_args()
    root = Path(a.dir)
    (root / "summary.md").write_text(build(root), encoding="utf-8")
    print(f"wrote {root / 'summary.md'}")


if __name__ == "__main__":
    main()
