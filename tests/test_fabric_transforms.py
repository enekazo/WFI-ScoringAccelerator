import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pinned_scoring import Policy
from pinned_scoring.fabric import (
    FIXTURE_ROWS, GoldReader, load_raw, load_raw_from_file, policy_to_gold,
    raw_to_silver, silver_to_gold,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY = Policy.load(ROOT / "examples" / "wildfire" / "policy.json")
NOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)


def pipeline():
    raw = load_raw(FIXTURE_ROWS, "b1", NOW)
    silver = raw_to_silver(raw, POLICY, NOW)
    gold = silver_to_gold(silver, POLICY, NOW)
    return raw, silver, gold


def test_raw_keeps_everything_as_received():
    raw, _, _ = pipeline()
    assert len(raw) == len(FIXTURE_ROWS)
    assert any(r["value"] == "abc" for r in raw)
    assert all(r["batch_id"] == "b1" and r["ingested_at"] for r in raw)


def test_silver_flags_not_drops_and_normalises_units():
    raw, silver, _ = pipeline()
    assert len(silver) == len(raw)
    bad = [s for s in silver if s["entity_id"] == "P-002"][0]
    assert bad["quality"] == "invalid" and bad["quality_reason"]
    wind = [s for s in silver if s["metric"] == "wind_kmh"][0]
    assert wind["unit"] == "km/h" and wind["value"] == "36.0"


def test_future_observation_is_suspect():
    row = dict(FIXTURE_ROWS[0], observed_at="2026-07-02T00:00:00+00:00",
               valid_until="2026-07-04T00:00:00+00:00")
    silver = raw_to_silver(load_raw([row], "b", NOW), POLICY, NOW)
    assert silver[0]["quality"] == "suspect"


@pytest.mark.parametrize("patch", [
    {"observed_at": "2026-07-01T06:00:00"},      # naive timestamp
    {"valid_until": "2026-06-01T00:00:00+00:00"},  # before observed_at
    {"provenance": "rumour"},
    {"value": None},
])
def test_broken_rows_are_invalid(patch):
    silver = raw_to_silver(load_raw([dict(FIXTURE_ROWS[0], **patch)], "b", NOW), POLICY, NOW)
    assert silver[0]["quality"] == "invalid"


def test_gold_scores_complete_entity_and_marks_incomplete():
    _, _, gold = pipeline()
    by = {g["entity_id"]: g for g in gold}
    assert by["P-001"]["status"] == "scored" and by["P-001"]["band"]
    assert by["P-002"]["status"] == "incomplete" and by["P-002"]["score"] is None
    assert json.loads(by["P-002"]["reasons"])


def test_gold_matches_direct_scorer():
    from pinned_scoring import score
    from pinned_scoring.fabric.transforms import silver_to_observations
    _, silver, gold = pipeline()
    direct = score("P-001", silver_to_observations(silver), POLICY, NOW)
    assert [g for g in gold if g["entity_id"] == "P-001"][0]["receipt_sha256"] == direct.receipt_sha256


def test_policy_gold_row_pins_hash():
    row = policy_to_gold(POLICY, NOW)
    assert row["policy_sha256"] == POLICY.sha256
    assert Policy.from_dict(json.loads(row["policy_json"])).sha256 == POLICY.sha256


def test_loader_reads_file_both_shapes(tmp_path):
    for i, doc in enumerate([FIXTURE_ROWS, {"observations": FIXTURE_ROWS}]):
        p = tmp_path / f"d{i}.json"
        p.write_text(json.dumps(doc))
        assert len(load_raw_from_file(p, "b", NOW)) == len(FIXTURE_ROWS)


def test_reader_gold_only_and_verifies():
    _, _, gold = pipeline()
    reader = GoldReader(lambda: gold)
    assert reader.get_receipt("P-001").status == "scored"
    assert reader.get_receipt("nope") is None
    assert [r.entity_id for r in reader.list_receipts(status="incomplete")] == ["P-002"]
    assert reader.list_receipts(band="nonexistent") == []
    assert not hasattr(reader, "raw") and not hasattr(reader, "silver")


def test_reader_latest_wins_and_detects_tampering():
    _, _, gold = pipeline()
    old = dict(gold[0], scored_at="2026-06-01T00:00:00+00:00")
    assert len(GoldReader(lambda: [old, gold[0]]).list_receipts()) == 1
    tampered = dict(gold[0], band="low", score="1.0")
    with pytest.raises(ValueError):
        GoldReader(lambda: [tampered]).list_receipts()
