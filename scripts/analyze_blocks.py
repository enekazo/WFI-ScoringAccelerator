"""Categorise WHY Arm B raw answers were blocked by the verifier (reads docs/evidence only).

A blocked run is assigned exactly one category, in priority order:
  infra_error       API/transport error, no model answer at all
  unparseable       model output was not valid JSON
  wrong_structured  the model's own status/score/band/reasons differ from the receipt (genuinely wrong)
  number_in_prose   structured fields right, but prose states a wrong score/band
  reason_wording    structured fields right, prose blocked on reason_mismatch/completeness wording
  provenance_wording structured fields right, prose blocked only on provenance wording
  other             anything else
Pass --rerun to re-verify each parsed answer with the CURRENT verifier instead of reading stored violations.
"""

import argparse
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pinned_scoring.agent import ReceiptStore, guarded_answer  # noqa: E402
from pinned_scoring.experiment.arms import _narration  # noqa: E402

EV = ROOT / "docs" / "evidence"
S1 = EV / "scenario-1-locking-the-score"
NUMBER = {"score_mismatch", "band_mismatch", "score_on_incomplete", "band_on_incomplete"}
REASON = {"reason_mismatch", "completeness_mismatch"}
PROV = {"provenance_mismatch", "provenance_unsupported"}


def structured_correct(ans, rec) -> bool:
    score_ok = (ans["score"] is None and rec["score"] is None) or (
        ans["score"] is not None and rec["score"] is not None
        and Decimal(str(ans["score"])) == Decimal(rec["score"]))
    return (ans["status"] == rec["status"] and score_ok and ans["band"] == (rec["band"] or None)
            and sorted(ans["reasons"]) == sorted(r.lower() for r in rec["reasons"]))


def reverify(ans, rec):
    store = ReceiptStore()
    store.load(rec)
    v = guarded_answer(lambda q, c: _narration({**ans, "score": ans["score"]}), "explain", store, rec["entity_id"])
    return [{"kind": x.kind, "detail": x.detail} for x in v.violations]


def categorise(run, rec, rerun):
    if run["error"]:
        return "infra_error", []
    ans = run["raw_answer"]
    if ans is None:
        return "unparseable", []
    if rerun:
        ans = {**ans, "score": None if ans["score"] is None else Decimal(str(ans["score"]))}
        viol = reverify(ans, rec)
        if not viol:
            return "passes", []
    else:
        viol = run["violations"]
    kinds = {v["kind"] for v in viol}
    if not structured_correct({**ans, "score": ans["score"]}, rec):
        return "wrong_structured", viol
    if kinds & NUMBER:
        return "number_in_prose", viol
    if kinds & REASON:
        return "reason_wording", viol
    if kinds & PROV:
        return "provenance_wording", viol
    return "other", viol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerun", action="store_true")
    ap.add_argument("--runs", default=str(S1 / "runs.json"))
    ap.add_argument("--out")
    ap.add_argument("--examples", type=int, default=0)
    args = ap.parse_args()
    runs = [r for r in json.load(open(args.runs)) if r["arm"] == "B"]
    recs = {p.stem: json.load(open(p)) for p in (EV / "receipts").glob("*.json")}
    out = {}
    for model in sorted({r["model"] for r in runs}):
        sub = [r for r in runs if r["model"] == model]
        counts, blocked, ex = Counter(), 0, {}
        for r in sub:
            cat, viol = categorise(r, recs[r["parcel"]], args.rerun)
            if cat == "passes":
                continue
            if args.rerun is False and not r["blocked"]:
                continue
            blocked += 1
            counts[cat] += 1
            ex.setdefault(cat, []).append({"parcel": r["parcel"], "violations": viol,
                                           "explanation": (r["raw_answer"] or {}).get("explanation")})
        out[model] = {"runs": len(sub), "blocked": blocked, "categories": dict(counts)}
        print(f"{model}: {blocked}/{len(sub)} blocked")
        for k, v in counts.most_common():
            print(f"   {k:20s} {v:4d}  {100 * v / blocked:5.1f}% of blocked")
        for cat, items in ex.items():
            for it in items[:args.examples]:
                print(f"\n[{model} / {cat}] {it['parcel']}\n  {it['violations']}\n  {it['explanation']}")
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
