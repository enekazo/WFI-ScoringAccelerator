from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pinned_scoring import Observation, Policy, score
from pinned_scoring.verifier import verify_narration

ROOT = Path(__file__).resolve().parents[1]
POLICY = Policy.load(ROOT / "examples" / "wildfire" / "policy.json")
NOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
BASE = {
    "fuel_load": ("20", "t/ha"), "dead_fuel_pct": ("50", "%"),
    "fuel_moisture_pct": ("10", "%"), "weather_danger": ("40", "index"),
    "wind_kmh": ("30", "km/h"),
}


def rows(entity="P1", drop=(), generated=()):
    out = []
    for metric, (value, unit) in BASE.items():
        if metric in drop:
            continue
        observed = NOW - timedelta(hours=1)
        prov = "generated" if metric in generated else "measured"
        out.append(Observation(entity, metric, value, unit, observed,
                               observed + timedelta(days=60), prov))
    return out


@pytest.fixture
def scored():
    return score("P1", rows(), POLICY, NOW)


@pytest.fixture
def incomplete():
    return score("P1", rows(drop=("wind_kmh",)), POLICY, NOW)


def kinds(result):
    return {v.kind for v in result.violations}


@pytest.mark.parametrize("text", [
    "P1 scored 58.3 and sits in the high band.",
    "The score is 58.30 out of 100, band: high.",
    "Risk for P1 is 58.3/100 (high risk).",
    "Wind is measured and weather_danger is measured. Fuel load of 20 contributes 15.00.",
])
def test_honest_narration_passes(scored, text):
    result = verify_narration(text, scored)
    assert result.ok, result.violations


def test_wrong_score_blocked(scored):
    assert "score_mismatch" in kinds(verify_narration("P1 has a score of 72.1.", scored))
    assert "score_mismatch" in kinds(verify_narration("It is 91/100 overall", scored))


def test_wrong_band_blocked(scored):
    assert "band_mismatch" in kinds(verify_narration("P1 is in the extreme band.", scored))
    assert "band_mismatch" in kinds(verify_narration("Risk level: low", scored))


def test_wrong_provenance_blocked():
    receipt = score("P1", rows(generated=("weather_danger",)), POLICY, NOW)
    assert verify_narration("weather_danger is generated.", receipt).ok
    assert "provenance_mismatch" in kinds(verify_narration("weather_danger is measured.", receipt))
    assert "provenance_mismatch" in kinds(verify_narration("All inputs are measured.", receipt))


def test_provenance_fixture_claim_blocked(scored):
    assert "provenance_mismatch" in kinds(verify_narration("The wind reading is a fixture.", scored))


def test_incomplete_with_score_or_band_blocked(incomplete):
    assert "score_on_incomplete" in kinds(verify_narration("P1 scored 58.3.", incomplete))
    assert "band_on_incomplete" in kinds(verify_narration("P1 is in the high band.", incomplete))
    assert "completeness_mismatch" in kinds(verify_narration("The assessment is complete.", incomplete))


def test_incomplete_honest_passes(incomplete):
    text = "P1 cannot be scored: wind_kmh is missing. No score is given."
    result = verify_narration(text, incomplete)
    assert result.ok, result.violations


def test_wrong_reason_and_unsupported_provenance(incomplete):
    assert "reason_mismatch" in kinds(verify_narration("wind_kmh is expired.", incomplete))
    assert "reason_mismatch" in kinds(verify_narration("The wind_kmh input is missing, and fuel_load is missing.", incomplete))
    assert "provenance_unsupported" in kinds(verify_narration("wind_kmh is measured.", incomplete))


def test_scored_receipt_claimed_incomplete(scored):
    assert "completeness_mismatch" in kinds(verify_narration("P1 is incomplete.", scored))
    assert verify_narration("Nothing is missing.", scored).ok


@pytest.mark.parametrize("text", [
    "P1 scored 58.3 with no missing or invalid inputs.",
    "There are no issues or missing data for P1.",
    "All inputs are measured or generated, and the score is 58.3.",
    "The score of 58.3 is based on measured and generated data for fuel load, dead fuel and wind.",
])
def test_hedged_but_true_scored_narrations_pass(scored, text):
    result = verify_narration(text, scored)
    assert result.ok, result.violations


def test_disjunction_must_contain_actual_provenance():
    receipt = score("P1", rows(generated=("weather_danger",)), POLICY, NOW)
    assert verify_narration("All inputs are measured or generated.", receipt).ok
    assert "provenance_mismatch" in kinds(verify_narration("All inputs are generated or fixture.", receipt))


def test_incomplete_clause_scoping(incomplete):
    ok = ("P1 is incomplete because wind_kmh is missing, but other factors like fuel_load and "
          "dead_fuel are present, and the missing current wind data prevents a complete assessment.")
    assert verify_narration(ok, incomplete).ok
    assert verify_narration("All other factors are measured, but wind_kmh is missing.", incomplete).ok
    assert verify_narration("Fix the input to complete the scoring.", incomplete).ok


def test_relaxations_do_not_hide_real_errors(incomplete, scored):
    assert not verify_narration("Although all factors are measured, wind_kmh is expired.", incomplete).ok
    assert "provenance_mismatch" in kinds(verify_narration("All inputs are measured.", incomplete))
    assert "completeness_mismatch" in kinds(verify_narration("The assessment is complete but wind_kmh is missing.", incomplete))
    assert "score_mismatch" in kinds(verify_narration("There is no problem, the score is 72.1.", scored))

