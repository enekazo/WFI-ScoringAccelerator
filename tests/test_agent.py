from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pinned_scoring import Observation, Policy, score
from pinned_scoring.agent import (ReceiptStore, compare, get_receipt, guarded_answer,
                                  list_reasons, what_if)

ROOT = Path(__file__).resolve().parents[1]
POLICY = Policy.load(ROOT / "examples" / "wildfire" / "policy.json")
NOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
BASE = {
    "fuel_load": ("20", "t/ha"), "dead_fuel_pct": ("50", "%"),
    "fuel_moisture_pct": ("10", "%"), "weather_danger": ("40", "index"),
    "wind_kmh": ("30", "km/h"),
}


def rows(entity, drop=(), wind="30"):
    out = []
    for metric, (value, unit) in BASE.items():
        if metric in drop:
            continue
        observed = NOW - timedelta(hours=1)
        out.append(Observation(entity, metric, wind if metric == "wind_kmh" else value, unit,
                               observed, observed + timedelta(days=60), "measured"))
    return out


@pytest.fixture
def store():
    obs = rows("P1") + rows("P2", drop=("wind_kmh",), ) + rows("P3", wind="60")
    s = ReceiptStore(POLICY, obs, NOW)
    for e in ("P1", "P2", "P3"):
        s.load(score(e, obs, POLICY, NOW).wire())
    return s


def test_tampered_receipt_rejected():
    wire = score("P1", rows("P1"), POLICY, NOW).wire()
    wire["score"] = "5.0"
    with pytest.raises(ValueError):
        ReceiptStore().load(wire)


def test_tools(store):
    assert get_receipt(store, "P1")["receipt"]["score"] == "58.3"
    assert "missing:wind_kmh" in list_reasons(store, "P2")["reasons"]
    cmp = compare(store, "P1", "P3")
    assert cmp["score_difference"] == "-5.0" and cmp["counterfactual"] is False
    assert compare(store, "P1", "P2")["score_difference"] is None
    with pytest.raises(KeyError):
        get_receipt(store, "nope")


def test_what_if_is_counterfactual_and_does_not_mutate(store):
    before = store.get("P1").receipt_sha256
    result = what_if(store, "P1", {"wind_kmh": "60"})
    assert result["counterfactual"] is True
    assert result["hypothetical"]["score"] == "63.3"
    assert result["stored_score"] == "58.3"
    assert store.get("P1").receipt_sha256 == before and store.get("P1").score == Decimal("58.3")
    # Missing input can be supplied hypothetically for an incomplete entity
    fixed = what_if(store, "P2", {"wind_kmh": "30"})
    assert fixed["hypothetical"]["status"] == "scored"
    assert store.get("P2").status == "incomplete"
    with pytest.raises(ValueError):
        what_if(store, "P1", {"bogus": "1"})


def fake(text):
    return lambda question, context: text


def test_guard_passes_honest(store):
    out = guarded_answer(fake("P1 scored 58.3 in the high band. Wind is measured."),
                         "Explain P1", store)
    assert not out.blocked and out.violations == ()


@pytest.mark.parametrize("lie,kind", [
    ("P1 scored 80.0.", "score_mismatch"),
    ("P1 is in the extreme band.", "band_mismatch"),
    ("P1 wind is generated.", "provenance_mismatch"),
])
def test_guard_blocks_lies(store, lie, kind):
    out = guarded_answer(fake(lie), "Explain P1", store)
    assert out.blocked and out.text is None
    assert kind in {v.kind for v in out.violations}


def test_guard_incomplete(store):
    assert guarded_answer(fake("P2 scored 50.0."), "Explain P2", store).blocked
    ok = guarded_answer(fake("P2 cannot be scored; wind_kmh is missing."), "Explain P2", store)
    assert not ok.blocked


def test_guard_multi_entity_and_unknown(store):
    ok = guarded_answer(fake("P1 scored 58.3. P3 scored 63.3."), "Compare P1 and P3", store)
    assert not ok.blocked
    bad = guarded_answer(fake("P1 scored 63.3. P3 scored 63.3."), "Compare P1 and P3", store)
    assert bad.blocked
    assert guarded_answer(fake("Fine."), "Hello", store).blocked
    exp_unk = guarded_answer(fake("Parcel P999999 has score 50."), "Explain parcel P999999.", store, entity_id="P999999")
    assert exp_unk.blocked and exp_unk.text is None
    assert any(v.kind == "no_receipt" for v in exp_unk.violations)
