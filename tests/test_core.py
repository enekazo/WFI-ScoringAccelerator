from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pinned_scoring import ContractError, Observation, Policy, Receipt, score

ROOT = Path(__file__).resolve().parents[1]
POLICY = Policy.load(ROOT / "examples" / "wildfire" / "policy.json")
NOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)

BASE = {
    "fuel_load": ("20", "t/ha"), "dead_fuel_pct": ("50", "%"),
    "fuel_moisture_pct": ("10", "%"), "weather_danger": ("40", "index"),
    "wind_kmh": ("30", "km/h"),
}


def rows(entity="P1", overrides=None, provenance="measured", age_hours=1):
    out = []
    for metric, (value, unit) in BASE.items():
        spec = {"value": value, "unit": unit, "provenance": provenance, "quality": "valid",
                "age": age_hours}
        spec.update((overrides or {}).get(metric, {}))
        observed = NOW - timedelta(hours=spec["age"])
        out.append(Observation(entity, metric, spec["value"], spec["unit"], observed,
                               observed + timedelta(days=60), spec["provenance"], spec["quality"]))
    return out


def test_known_value_and_determinism():
    # 15 + 7.5 + 21.4286 + 9.4118 + 5 = 58.3404
    first = score("P1", rows(), POLICY, NOW)
    second = score("P1", list(reversed(rows())), POLICY, NOW)
    assert first.status == "scored"
    assert first.score == Decimal("58.3") and first.band == "high"
    assert first.receipt_sha256 == second.receipt_sha256


@pytest.mark.parametrize("value,expected", [("24.9", "low"), ("25", "moderate"),
                                            ("50", "high"), ("75", "extreme")])
def test_band_boundaries(value, expected):
    assert POLICY.band(Decimal(value)) == expected


def test_missing_input_is_incomplete_not_guessed():
    receipt = score("P1", [r for r in rows() if r.metric != "wind_kmh"], POLICY, NOW)
    assert receipt.status == "incomplete" and receipt.score is None
    assert "missing:wind_kmh" in receipt.reasons


def test_expired_input_rejected():
    receipt = score("P1", rows(overrides={"fuel_moisture_pct": {"age": 48}}), POLICY, NOW)
    assert "expired:fuel_moisture_pct" in receipt.reasons


def test_fixture_cannot_pass_where_policy_forbids():
    receipt = score("P1", rows(overrides={"wind_kmh": {"provenance": "fixture"}}), POLICY, NOW)
    assert receipt.status == "incomplete"
    assert "provenance:wind_kmh" in receipt.reasons


def test_generated_allowed_only_where_policy_says():
    ok = score("P1", rows(overrides={"weather_danger": {"provenance": "generated"}}), POLICY, NOW)
    assert ok.status == "scored"
    assert any(c[0] == "weather_danger" and c[2] == "generated" for c in ok.components)


def test_unit_mismatch_rejected():
    receipt = score("P1", rows(overrides={"wind_kmh": {"unit": "mph"}}), POLICY, NOW)
    assert "unit:wind_kmh" in receipt.reasons


def test_invalid_quality_rejected():
    receipt = score("P1", rows(overrides={"dead_fuel_pct": {"quality": "invalid"}}), POLICY, NOW)
    assert "invalid:dead_fuel_pct" in receipt.reasons


def test_policy_change_changes_hash_and_receipt():
    doc = POLICY.wire()
    doc["factors"][0]["weight"] = "0.25"
    doc["factors"][1]["weight"] = "0.20"
    altered = Policy.from_dict(doc)
    assert altered.sha256 != POLICY.sha256
    assert (score("P1", rows(), altered, NOW).receipt_sha256
            != score("P1", rows(), POLICY, NOW).receipt_sha256)


def test_weights_must_sum_to_one():
    doc = POLICY.wire()
    doc["factors"][0]["weight"] = "0.50"
    with pytest.raises(ContractError):
        Policy.from_dict(doc)


def test_receipt_roundtrip_and_tamper_detection():
    receipt = score("P1", rows(), POLICY, NOW)
    assert Receipt.from_wire(receipt.wire()).receipt_sha256 == receipt.receipt_sha256
    forged = receipt.wire()
    forged["score"] = "5.0"
    with pytest.raises(ValueError):
        Receipt.from_wire(forged)


def test_float_inputs_rejected():
    with pytest.raises(ContractError):
        Observation("P1", "wind_kmh", 30.5, "km/h", NOW, NOW + timedelta(days=1), "measured")


def test_monotonic_in_wind():
    low = score("P1", rows(overrides={"wind_kmh": {"value": "10"}}), POLICY, NOW)
    high = score("P1", rows(overrides={"wind_kmh": {"value": "50"}}), POLICY, NOW)
    assert high.score > low.score
