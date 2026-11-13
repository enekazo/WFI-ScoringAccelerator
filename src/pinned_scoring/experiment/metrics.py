"""Scoring of model answers against ground truth, plus bootstrap confidence intervals."""

import random
from statistics import fmean

METRICS = ("score_error", "band_mismatch", "provenance_misstatement", "fault_detection",
           "false_alarm", "parse_failure", "verifier_block")
MAX_SCORE_ERROR = 100.0


def provenance_truth(case) -> dict:
    """factor name -> provenance of the latest observation for it, from the injected data."""
    by_metric = {f.metric: f.name for f in case.pinned_policy.factors}
    latest = {}
    for o in case.observations:
        if o["metric"] in by_metric and (
                o["metric"] not in latest or o["observed_at"] >= latest[o["metric"]]["observed_at"]):
            latest[o["metric"]] = o
    return {by_metric[m]: o["provenance"] for m, o in latest.items()}


def score_answer(case, answer: dict | None, blocked: bool = False) -> dict:
    """Per-run metric values. Values are numbers or None when not applicable."""
    t = case.truth
    m = {k: None for k in METRICS}
    m["parse_failure"] = 1.0 if answer is None else 0.0
    m["verifier_block"] = 1.0 if blocked else 0.0
    status = answer["status"] if answer else None
    got_score = answer["score"] if answer else None
    got_band = answer["band"] if answer else None

    if t["expected_status"] == "scored":
        m["score_error"] = (MAX_SCORE_ERROR if got_score is None else
                            min(MAX_SCORE_ERROR, abs(float(got_score) - float(t["expected_score"]))))
        m["band_mismatch"] = float(got_band != t["expected_band"])
    else:
        m["band_mismatch"] = float(got_band is not None)

    if answer is None:
        m["provenance_misstatement"] = 1.0
    else:
        truth = provenance_truth(case)
        bad = any(k in truth and v != truth[k] for k, v in answer["provenance"].items())
        m["provenance_misstatement"] = float(bad)

    if t["fault"] is None:
        m["false_alarm"] = float(status != "scored")
    elif t["expected_status"] == "policy_mismatch":
        m["fault_detection"] = float(status == "policy_mismatch")
    else:
        reasons = answer["reasons"] if answer else []
        m["fault_detection"] = float(status == "incomplete"
                                     and all(r.lower() in reasons for r in t["expected_reasons"]))
    return m


def signature(answer: dict | None) -> tuple:
    if answer is None:
        return ("unparsed",)
    return (answer["status"], None if answer["score"] is None else str(answer["score"]),
            answer["band"])


def bootstrap_ci(values, n: int = 2000, seed: int = 0, level: float = 0.95) -> dict:
    """Mean with a percentile bootstrap CI over the given independent units."""
    values = [float(v) for v in values if v is not None]
    if not values:
        return {"mean": None, "lo": None, "hi": None, "n": 0}
    mean = fmean(values)
    rng = random.Random(seed)
    k = len(values)
    means = sorted(fmean(rng.choices(values, k=k)) for _ in range(n))
    tail = (1 - level) / 2
    return {"mean": mean, "lo": means[int(tail * n)], "hi": means[min(n - 1, int((1 - tail) * n))],
            "n": k}


def aggregate(records: list) -> dict:
    """records: [{parcel, repeat, metrics:{...}, signature}] for one (model, arm).

    The resampling unit is the parcel: repeats are averaged first.
    """
    by_parcel: dict = {}
    for r in records:
        by_parcel.setdefault(r["parcel"], []).append(r)
    out = {}
    for name in METRICS:
        per_parcel = []
        for runs in by_parcel.values():
            vals = [r["metrics"][name] for r in runs if r["metrics"][name] is not None]
            if vals:
                per_parcel.append(fmean(vals))
        out[name] = bootstrap_ci(per_parcel)
    repro = [float(len({tuple(r["signature"]) for r in runs}) == 1)
             for runs in by_parcel.values() if len(runs) > 1]
    out["reproducibility"] = bootstrap_ci(repro)
    return out
