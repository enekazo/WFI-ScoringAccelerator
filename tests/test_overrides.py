from pathlib import Path

import pytest

from pinned_scoring.contracts import ContractError
from pinned_scoring.overrides import apply_overrides, write_policy
from pinned_scoring.policy import Policy

POLICY = Path(__file__).resolve().parent.parent / "examples" / "wildfire" / "policy.json"


def base():
    return Policy.load(POLICY)


def test_valid_override_sets_weights():
    new = apply_overrides(base(), {"fuel_load": "0.35", "wind": "0.05"})
    weights = {f.name: str(f.weight) for f in new.factors}
    assert weights["fuel_load"] == "0.35" and weights["wind"] == "0.05"
    assert sum(f.weight for f in new.factors) == 1


def test_invalid_sum_rejected():
    with pytest.raises(ContractError, match="sum"):
        apply_overrides(base(), {"fuel_load": "0.50"})


def test_unknown_factor_rejected():
    with pytest.raises(ContractError, match="unknown factor"):
        apply_overrides(base(), {"nope": "0.1"})


def test_hash_changes_and_original_untouched():
    original = base()
    before = original.sha256
    new = apply_overrides(original, {"fuel_load": "0.35", "wind": "0.05"})
    assert new.sha256 != before
    assert original.sha256 == before
    assert apply_overrides(original, {}).sha256 == before


def test_write_policy_roundtrip(tmp_path):
    new = apply_overrides(base(), {"fuel_load": "0.35", "wind": "0.05"})
    digest = write_policy(new, tmp_path / "out" / "policy.json")
    assert Policy.load(tmp_path / "out" / "policy.json").sha256 == digest == new.sha256
