"""Weight overrides: produce a new, re-validated, re-hashed Policy without mutating the original."""

import json
from dataclasses import replace
from pathlib import Path

from .contracts import ContractError, to_decimal
from .policy import Policy


def apply_overrides(policy: Policy, overrides: dict) -> Policy:
    """Return a new Policy with factor weights replaced by name.

    Weights are passed as strings/Decimals (never floats). The result must satisfy every
    Policy rule, in particular weights summing to exactly 1.
    """
    names = {f.name for f in policy.factors}
    unknown = sorted(set(overrides) - names)
    if unknown:
        raise ContractError(f"unknown factor(s) in weightOverrides: {', '.join(unknown)}")
    factors = tuple(
        replace(f, weight=to_decimal(overrides[f.name], f"weight[{f.name}]"))
        if f.name in overrides else f
        for f in policy.factors
    )
    total = sum(f.weight for f in factors)
    if total != 1:
        raise ContractError(
            f"weights after overrides sum to {total}, must sum to exactly 1; "
            "override every factor you change so the total stays 1"
        )
    updated = replace(policy, factors=factors)
    updated.validate()
    return updated


def write_policy(policy: Policy, path) -> str:
    """Write the policy as canonical JSON (loadable by Policy.load); return its SHA-256."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(policy.wire(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return policy.sha256
