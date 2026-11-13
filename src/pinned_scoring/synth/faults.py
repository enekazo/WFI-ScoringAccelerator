"""Fault injectors. Each takes a parcel's clean observations and returns faulted ones."""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

from ..contracts import Observation, Provenance, Quality
from ..policy import Policy

FAULTS = (
    "missing_input",
    "expired_input",
    "fixture_provenance",
    "unit_mismatch",
    "invalid_quality",
    "policy_tamper",
)

# Reason prefix the real scorer emits for each observation-level fault.
REASON_PREFIX = {
    "missing_input": "missing",
    "expired_input": "expired",
    "fixture_provenance": "provenance",
    "unit_mismatch": "unit",
    "invalid_quality": "invalid",
}

# Metrics eligible for faulting (measured-only, so provenance faults are detectable).
TARGET_METRICS = ("fuel_load", "dead_fuel_pct", "fuel_moisture_pct", "wind_kmh")

_WRONG_UNIT = {
    "fuel_load": "kg/m2",
    "dead_fuel_pct": "fraction",
    "fuel_moisture_pct": "g/kg",
    "wind_kmh": "mph",
}


def inject(fault: str, observations: list, metric: str, as_of: datetime) -> list:
    """Return a new observation list with `fault` applied to `metric`."""
    if fault == "policy_tamper":
        return list(observations)
    out = []
    for obs in observations:
        if obs.metric != metric:
            out.append(obs)
            continue
        if fault == "missing_input":
            continue
        if fault == "expired_input":
            obs = replace(obs, observed_at=as_of - timedelta(days=60),
                          valid_until=as_of - timedelta(days=30))
        elif fault == "fixture_provenance":
            # Source string deliberately looks like a field measurement.
            obs = replace(obs, provenance=Provenance.FIXTURE,
                          source="sensor:" + obs.source.split(":", 1)[-1])
        elif fault == "unit_mismatch":
            obs = replace(obs, unit=_WRONG_UNIT[metric])
        elif fault == "invalid_quality":
            obs = replace(obs, quality=Quality.INVALID)
        else:
            raise ValueError(f"unknown fault: {fault}")
        out.append(obs)
    return out


def tamper_policy(policy: Policy) -> Policy:
    """Shift 0.05 of weight from the first to the last factor; weights still sum to 1."""
    first, last = policy.factors[0], policy.factors[-1]
    shift = Decimal("0.05")
    factors = (replace(first, weight=first.weight - shift),) + policy.factors[1:-1] + (
        replace(last, weight=last.weight + shift),
    )
    return replace(policy, factors=factors)
