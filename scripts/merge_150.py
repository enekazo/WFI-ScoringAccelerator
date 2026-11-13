"""Merge per-run rows for parcels P001-P150 into one cumulative result (no model calls).

Sources (all optional except the first two):
  * re-scored P001-P050 rows        combined-150/rescored-p001-p050/*/runs.json
  * P051-P150 batch rows            extra-100/per-run/batch_*/runs.json
  * gpt-5.5 rows for P051-P150      gpt55-p051-p150/batch_*/runs.json (falls back to the
                                    git-ignored results/extra_gpt55/batch_*/runs.json)
  * gpt-5.5 rows for P001-P050      gpt55-p001-p050/**/runs.json
  * replacement rows for errors     error-reruns/**/rerun_rows.json and
                                    error-reruns-p001-p050/**/rerun_rows.json
                                    (a dict keyed "model|arm|parcel|repeat")

Replacement rows are keyed by (model, arm, parcel, repeat). A replaced row is dropped from the
statistics, the replacement is flagged `replacement: true`, and both are kept in
replaced_rows.json. Model labels drop a trailing "-cmp" (comparison deployment suffix).

Usage: python scripts/merge_150.py [--evidence DIR] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import fmean

from pinned_scoring.experiment.metrics import METRICS, aggregate, bootstrap_ci

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence" / "scenario-1-locking-the-score"
ALL_PARCELS = [f"P{i:03d}" for i in range(1, 151)]
EXCLUDED_SOURCES = ("phi-4/",)  # first phi-4 run; the full re-run is used instead
ARM_ORDER = {"A": 0, "A+": 1, "B": 2, "B(raw)": 3}


def norm_model(name: str) -> str:
    return name[:-4] if name.endswith("-cmp") else name


def key(row: dict) -> tuple:
    return (norm_model(row["model"]), row["arm"], row["parcel"], row["repeat"])


def _load(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))


def _glob(root: Path, pattern: str) -> list:
    return sorted(root.glob(pattern)) if root.exists() else []


def gather(evidence: Path, results: Path | None = None) -> dict:
    results = results if results is not None else ROOT / "results" / "extra_gpt55"
    base = evidence / "combined-150" / "rescored-p001-p050"
    groups = {
        "rescored P001-P050": _glob(base, "*/runs.json"),
        "P051-P150 batches": _glob(evidence / "extra-100" / "per-run", "batch_*/runs.json"),
        "gpt-5.5 P051-P150": _glob(evidence / "gpt55-p051-p150", "batch_*/runs.json")
        or _glob(results, "batch_*/runs.json"),
        "gpt-5.5 P001-P050": _glob(evidence / "gpt55-p001-p050", "**/runs.json"),
    }
    repl = []
    for d in ("error-reruns", "error-reruns-p001-p050"):
        repl += _glob(evidence / d, "**/rerun_rows.json")
    return {"groups": groups, "replacements": repl}


def build_rows(groups: dict, replacement_files: list):
    rows, seen, sources = [], {}, {}
    for group, files in groups.items():
        for f in files:
            for r in _load(f):
                r = {**r, "model": norm_model(r["model"]), "source": group}
                k = key(r)
                if k in seen:
                    raise ValueError(f"duplicate row {k} in {f} and {seen[k]}")
                seen[k] = str(f)
                rows.append(r)
                sources.setdefault(group, set()).add(str(f))
    by_key = {key(r): i for i, r in enumerate(rows)}
    replaced = []
    for f in replacement_files:
        data = _load(f)
        for new in (data.values() if isinstance(data, dict) else data):
            new = {**new, "model": norm_model(new["model"]), "replacement": True,
                   "source": "error re-run"}
            k = key(new)
            if k not in by_key:
                raise ValueError(f"replacement for unknown row {k} in {f}")
            old = rows[by_key[k]]
            replaced.append({"key": list(k), "original": old, "replacement": new})
            rows[by_key[k]] = new
    return rows, replaced, {g: sorted(s) for g, s in sources.items()}


def block_kind(row: dict) -> str | None:
    """None when not blocked, else 'api_429' | 'api_error' | 'verifier'."""
    if not row.get("blocked"):
        return None
    err = row.get("error")
    if err:
        return "api_429" if "429" in str(err) else "api_error"
    return "verifier"


def _rate(rows, pred) -> dict:
    per_parcel: dict = {}
    for r in rows:
        per_parcel.setdefault(r["parcel"], []).append(float(pred(r)))
    return bootstrap_ci([fmean(v) for v in per_parcel.values()])


def cell_stats(recs: list, raw: bool = False) -> dict:
    view = [{**r, "metrics": r["raw_metrics"]} for r in recs] if raw else recs
    agg = aggregate(view)
    out = {"runs": len(recs), "parcels": len({r["parcel"] for r in recs}),
           "score_error_mae": agg["score_error"], "fault_detection": agg["fault_detection"],
           "reproducibility": agg["reproducibility"],
           "errors": sum(bool(r.get("error")) for r in recs),
           "errors_429": sum(bool(r.get("error")) and "429" in str(r["error"]) for r in recs),
           "replaced_runs": sum(bool(r.get("replacement")) for r in recs),
           "tool_calls_total": sum(len(r.get("tool_calls") or []) for r in recs),
           "tool_calls_per_run": fmean(len(r.get("tool_calls") or []) for r in recs)}
    if recs and recs[0]["arm"] == "B" and not raw:
        kinds = [block_kind(r) for r in recs]
        out["blocks"] = {
            "total": sum(k is not None for k in kinds),
            "verifier": kinds.count("verifier"),
            "api_429": kinds.count("api_429"),
            "api_error_other": kinds.count("api_error"),
            "rate_total": _rate(recs, lambda r: block_kind(r) is not None),
            "rate_verifier": _rate(recs, lambda r: block_kind(r) == "verifier"),
            "rate_api_429": _rate(recs, lambda r: block_kind(r) == "api_429"),
        }
    return out


def compute(rows: list) -> dict:
    cells = {}
    for model in sorted({r["model"] for r in rows}):
        for arm in sorted({r["arm"] for r in rows}, key=ARM_ORDER.get):
            recs = [r for r in rows if r["model"] == model and r["arm"] == arm]
            if not recs:
                continue
            cells[f"{model}|{arm}"] = cell_stats(recs)
            if arm == "B":
                cells[f"{model}|B(raw)"] = cell_stats(recs, raw=True)
    return cells


def coverage(rows: list) -> dict:
    out = {}
    for model in sorted({r["model"] for r in rows}):
        have = {r["parcel"] for r in rows if r["model"] == model}
        arms = sorted({r["arm"] for r in rows if r["model"] == model}, key=ARM_ORDER.get)
        out[model] = {"parcels": len(have), "arms": arms,
                      "missing_parcels": [p for p in ALL_PARCELS if p not in have],
                      "missing_arms": [a for a in ("A", "A+", "B") if a not in arms]}
    return out


def fmt(m) -> str:
    if not m or m.get("mean") is None:
        return "-"
    return f"{m['mean']:.3f} [{m['lo']:.3f}, {m['hi']:.3f}]"


def _ranges(parcels: list) -> str:
    if not parcels:
        return "none"
    nums, out, start = [int(p[1:]) for p in parcels], [], None
    for i, n in enumerate(nums):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append((start, prev))
            start = prev = n
        if i == len(nums) - 1:
            out.append((start, prev))
    return ", ".join(f"P{a:03d}" if a == b else f"P{a:03d}-P{b:03d}" for a, b in out)


def render_md(stats: dict) -> str:
    cells, cov = stats["cells"], stats["coverage"]
    L = ["# Scenario 1 - combined result, 150 parcels (P001-P150)", "",
         "Mean [95% bootstrap CI]; the resampling unit is the parcel (repeats are averaged first). "
         "Arm B rows were re-scored with the final verifier where the raw answer was stored.", "",
         "## Score error, fault detection and reproducibility", "",
         "| model | arm | parcels | runs | score error MAE | fault detection | reproducibility "
         "| tool calls / run | errors (429) |", "|---|---|---|---|---|---|---|---|---|"]
    for k, c in cells.items():
        m, a = k.split("|", 1)
        L.append(f"| {m} | {a} | {c['parcels']} | {c['runs']} | {fmt(c['score_error_mae'])} "
                 f"| {fmt(c['fault_detection'])} | {fmt(c['reproducibility'])} "
                 f"| {c['tool_calls_per_run']:.2f} | {c['errors']} ({c['errors_429']}) |")
    L += ["", "## Arm B block rate: verifier blocks vs API-error blocks", "",
          "A block caused by an API error (e.g. HTTP 429) falls back to the receipt answer but is "
          "not a verifier decision, so it is reported separately.", "",
          "| model | B runs | total blocked | verifier blocks | verifier block rate "
          "| API 429 blocks | other API-error blocks |", "|---|---|---|---|---|---|---|"]
    for k, c in cells.items():
        if k.endswith("|B"):
            b = c["blocks"]
            L.append(f"| {k.split('|')[0]} | {c['runs']} | {b['total']} | {b['verifier']} "
                     f"| {fmt(b['rate_verifier'])} | {b['api_429']} | {b['api_error_other']} |")
    L += ["", "## Coverage", "", "| model | parcels | arms | missing parcels | missing arms |",
          "|---|---|---|---|---|"]
    for m, c in cov.items():
        L.append(f"| {m} | {c['parcels']} | {', '.join(c['arms'])} | "
                 f"{_ranges(c['missing_parcels'])} | {', '.join(c['missing_arms']) or 'none'} |")
    rp = stats["replacements"]
    L += ["", "## Replacement rows", "",
          f"{rp['count']} row(s) replaced by re-runs; originals are kept in replaced_rows.json."
          if rp["count"] else "No replacement rows supplied; all rows are original."]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", default=str(EVIDENCE))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    ev = Path(a.evidence)
    out = Path(a.out) if a.out else ev / "combined-150"
    src = gather(ev)
    rows, replaced, sources = build_rows(src["groups"], src["replacements"])
    stats = {"parcels": "P001-P150", "total_rows": len(rows), "metrics": list(METRICS),
             "cells": compute(rows), "coverage": coverage(rows),
             "replacements": {"count": len(replaced), "keys": [r["key"] for r in replaced]},
             "sources": sources}
    out.mkdir(parents=True, exist_ok=True)
    (out / "combined_stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    (out / "combined_150.md").write_text(render_md(stats), encoding="utf-8")
    if replaced:
        (out / "replaced_rows.json").write_text(json.dumps(replaced, indent=1), encoding="utf-8")
    print(f"wrote {out} ({len(rows)} rows, {len(replaced)} replaced)")


if __name__ == "__main__":
    main()
