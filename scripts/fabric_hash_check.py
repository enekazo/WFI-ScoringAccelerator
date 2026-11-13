"""Verify Fabric Gold receipts reproduce the repository receipts byte-for-byte (hash check).

Reads gold_receipts through the Lakehouse SQL analytics endpoint (Entra auth, no keys),
rebuilds each Receipt (which re-verifies its own sha256) and compares with docs/evidence/receipts.
Writes docs/evidence/scenario-2-ontology/fabric_hash_check.json.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pinned_scoring.fabric import GoldReader, sql_endpoint  # noqa: E402
from pinned_scoring.receipt import Receipt  # noqa: E402

SERVER = os.environ.get("FABRIC_SQL_SERVER", "sql.example.invalid")
DATABASE = os.environ.get("FABRIC_SQL_DATABASE", "pinned_scoring_lh")
OUT = Path(os.environ.get("PINNED_VERIFICATION_DIR", str(ROOT / "docs/evidence/scenario-2-ontology"))) / "fabric_hash_check.json"


def compare(receipts, local_dir: Path) -> dict:
    expected = {
        r.entity_id: r for r in (
            Receipt.from_wire(json.loads(path.read_text(encoding="utf-8")))
            for path in local_dir.glob("P*.json")
        )
    }
    actual = {r.entity_id: r for r in receipts}
    mismatches = []
    for pid in sorted(expected.keys() & actual.keys()):
        if expected[pid].receipt_sha256 != actual[pid].receipt_sha256:
            mismatches.append(pid)
    missing = sorted(expected.keys() - actual.keys())
    unexpected = sorted(actual.keys() - expected.keys())
    checked = len(expected.keys() & actual.keys())
    return {
        "expected": len(expected), "checked": checked, "matched": checked - len(mismatches),
        "mismatches": mismatches, "missing": missing, "unexpected": unexpected,
        "ok": bool(expected) and not (mismatches or missing or unexpected),
    }


if __name__ == "__main__":
    directory = Path(os.environ.get("PINNED_RECEIPTS_DIR") or ROOT / "docs/evidence/receipts")
    expected = [Receipt.from_wire(json.loads(p.read_text(encoding="utf-8"))) for p in directory.glob("P*.json")]
    expected_ids = {r.entity_id for r in expected}
    policies = {r.policy_sha256 for r in expected}
    rows = sql_endpoint.fetcher(SERVER, DATABASE, connect_fn=sql_endpoint.connect_auto)()
    current = [r for r in rows if r["entity_id"] in expected_ids and r["policy_sha256"] in policies]
    result = {"source": "Fabric lakehouse SQL analytics endpoint", "gold_rows": len(rows),
              "historical_rows_excluded": len(rows) - len(current),
              **compare(GoldReader(lambda: current).list_receipts(), directory)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    sys.exit(0 if result["ok"] else 1)
