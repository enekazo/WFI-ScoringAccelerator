import json
from datetime import datetime
from pathlib import Path

from pinned_scoring import Policy
from pinned_scoring.fabric import load_raw, raw_to_silver, silver_to_gold
from pinned_scoring.fabric.loader import read_rows

ROOT = Path(__file__).resolve().parents[1]


def test_medallion_receipts_match_committed_evidence():
    policy = Policy.load(ROOT / "examples" / "wildfire" / "policy.json")
    ds = ROOT / "data" / "dataset.json"
    as_of = datetime.fromisoformat(json.loads(ds.read_text())["as_of"])
    raw = load_raw(read_rows(ds), "b", as_of)
    gold = silver_to_gold(raw_to_silver(raw, policy, as_of), policy, as_of, scored_at=as_of)
    assert len(gold) == 50
    for g in gold:
        committed = json.loads((ROOT / "docs" / "evidence" / "receipts" / f"{g['entity_id']}.json").read_text())
        assert g["receipt_sha256"] == committed["receipt_sha256"], g["entity_id"]
