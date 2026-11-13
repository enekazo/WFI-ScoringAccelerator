"""Four decision scenarios over the graph. Every number is produced by the real scorer
(scorer.score) or by integer arithmetic over graph attributes. Nothing here is a model output.

Each function returns a plain dict: the decision, the figures behind it, the policy hash the
scores were computed under, and whether the scores are counterfactual (re-scored on modified
inputs) or the baseline pinned scores.
"""

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal

from .graph import FACILITY_WEIGHT, Graph
from .scorer import score
from .synth import weight_sensitivity

HIGH_SCORE = Decimal("50")
RED_FLAG_WEATHER = Decimal("90")
_CENT = Decimal("0.01")


def _q(value) -> str:
    return str(Decimal(value).quantize(_CENT, ROUND_HALF_UP))


@dataclass
class Context:
    graph: Graph
    observations: dict   # parcel id -> list[Observation]
    policy: object
    as_of: object

    def receipt(self, pid, policy=None, overrides=None, scale=None):
        rows = self.observations[pid]
        if overrides or scale:
            out = []
            for o in rows:
                if overrides and o.metric in overrides:
                    o = replace(o, value=Decimal(str(overrides[o.metric])))
                if scale and o.metric == scale[0]:
                    o = replace(o, value=o.value * Decimal(scale[1]))
                out.append(o)
            rows = out
        return score(pid, rows, policy or self.policy, self.as_of)

    def baseline(self, policy=None) -> dict:
        return {p: self.receipt(p, policy) for p in self.graph.parcels}


def _scored(receipts: dict) -> dict:
    return {p: r for p, r in receipts.items() if r.status == "scored"}


def _meta(ctx, policy, counterfactual) -> dict:
    return {"policy_sha256": (policy or ctx.policy).sha256, "counterfactual": counterfactual,
            "as_of": ctx.as_of.isoformat(), "source": "scorer.py"}


def exposure_if_ignite(ctx: Context, min_score="50", policy=None) -> dict:
    """(a) Which customers and critical facilities sit behind parcels scoring >= min_score?"""
    receipts = ctx.baseline(policy)
    scored = _scored(receipts)
    hot = sorted(p for p, r in scored.items() if r.score >= Decimal(str(min_score)))
    feeders = {f for p in hot for f in ctx.graph.feeders_of(p)}
    exp = ctx.graph.exposure(feeders)
    return {"scenario": "exposure", "min_score": str(min_score), "parcels": hot,
            "feeders": exp["feeders"], "customers": exp["customers"],
            "critical_facilities": exp["critical_facilities"],
            "parcels_unscored_excluded": sorted(set(receipts) - set(scored)),
            "scores": {p: str(scored[p].score) for p in hot},
            "receipt_sha256": {p: scored[p].receipt_sha256 for p in hot},
            **_meta(ctx, policy, False)}


def best_mitigation(ctx: Context, budget_usd=None, policy=None) -> dict:
    """(b) Parcel + mitigation with the largest exposure-weighted risk reduction per dollar."""
    base = _scored(ctx.baseline(policy))
    options = []
    for pid, r0 in base.items():
        weight = ctx.graph.exposure_weight(pid)
        for m in ctx.graph.mitigations:
            if budget_usd is not None and m["cost_usd"] > int(budget_usd):
                continue
            r1 = ctx.receipt(pid, policy, scale=(m["metric"], m["factor"]))
            if r1.status != "scored":
                continue
            reduction = (r0.score - r1.score) * weight
            per_1000 = reduction * 1000 / m["cost_usd"]
            options.append({"parcel": pid, "mitigation": m["id"], "cost_usd": m["cost_usd"],
                            "score_before": str(r0.score), "score_after": str(r1.score),
                            "exposure_weight": weight,
                            "risk_reduction": _q(reduction),
                            "risk_reduction_per_1000usd": _q(per_1000),
                            "_k": (per_1000, pid, m["id"])})
    options.sort(key=lambda o: (-o["_k"][0], o["_k"][1], o["_k"][2]))
    for o in options:
        del o["_k"]
    top = options[0] if options else None
    return {"scenario": "mitigation", "budget_usd": budget_usd, "best": top,
            "top3": options[:3], "facility_weight": FACILITY_WEIGHT,
            **_meta(ctx, policy, True)}


def deenergise_first(ctx: Context, wind_kmh=60, policy=None) -> dict:
    """(c) Red-flag day: rank feeders by high-risk score mass per customer interrupted."""
    overrides = {"weather_danger": RED_FLAG_WEATHER, "wind_kmh": Decimal(str(wind_kmh))}
    flag = _scored({p: ctx.receipt(p, policy, overrides) for p in ctx.graph.parcels})
    rows = []
    for feeder in ctx.graph.feeders:
        hot = [p for p in ctx.graph.parcels_on(feeder)
               if p in flag and flag[p].score >= HIGH_SCORE]
        load = ctx.graph.feeder_load(feeder)
        if not hot or load["customers"] == 0:
            continue
        mass = sum(flag[p].score for p in hot)
        rows.append({"feeder": feeder, "high_risk_parcels": hot, "customers": load["customers"],
                     "critical_facilities": load["critical_facilities"],
                     "score_mass": _q(mass),
                     "risk_per_100_customers": _q(mass * 100 / load["customers"]),
                     "_k": (mass / load["customers"], feeder)})
    rows.sort(key=lambda r: (-r["_k"][0], r["_k"][1]))
    for r in rows:
        del r["_k"]
    top = rows[0] if rows else None
    return {"scenario": "deenergise", "wind_kmh": int(wind_kmh), "red_flag_weather_danger":
            str(RED_FLAG_WEATHER), "first": top, "ranking": rows[:3],
            "needs_human_signoff": bool(top and top["critical_facilities"]),
            **_meta(ctx, policy, True)}


def variant_labels(policy) -> list:
    return [label for label, _ in weight_sensitivity(policy)]


def decision_sensitivity(ctx: Context, variant: str, wind_kmh=60, budget_usd=None) -> dict:
    """(d) Re-run (b) and (c) under a +/-20% weight variant: does either decision change?"""
    variants = dict(weight_sensitivity(ctx.policy))
    if variant not in variants:
        raise KeyError(f"unknown weight variant {variant!r}; choose from {sorted(variants)}")
    vp = variants[variant]
    base_c, var_c = deenergise_first(ctx, wind_kmh), deenergise_first(ctx, wind_kmh, vp)
    base_b, var_b = best_mitigation(ctx, budget_usd), best_mitigation(ctx, budget_usd, vp)
    pick = lambda d, k: d[k] and (d[k]["feeder"] if "feeder" in d[k] else
                                  (d[k]["parcel"], d[k]["mitigation"]))
    feeder_base, feeder_var = pick(base_c, "first"), pick(var_c, "first")
    mit_base, mit_var = pick(base_b, "best"), pick(var_b, "best")
    return {"scenario": "sensitivity", "variant": variant, "wind_kmh": int(wind_kmh),
            "budget_usd": budget_usd,
            "feeder_base": feeder_base, "feeder_variant": feeder_var,
            "feeder_decision_changes": feeder_base != feeder_var,
            "mitigation_base": list(mit_base) if mit_base else None,
            "mitigation_variant": list(mit_var) if mit_var else None,
            "mitigation_decision_changes": mit_base != mit_var,
            "base_policy_sha256": ctx.policy.sha256,
            **_meta(ctx, vp, True)}
