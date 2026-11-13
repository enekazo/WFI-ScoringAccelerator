"""Read-only, gold-only data access for the agent. There is no path to raw or silver here."""

import json
from typing import Callable, Iterable

from ..receipt import Receipt


class GoldReceiptReader:
    """Adapts a GoldReader to the agent's reader interface (get_receipt -> wire dict,
    list_receipts -> summaries). Reads the gold table once (one snapshot) and is read-only.
    """

    def __init__(self, gold: "GoldReader"):
        self._receipts = gold.snapshot()

    def get_receipt(self, parcel: str) -> dict:
        receipt = self._receipts.get(parcel)
        if receipt is None:
            raise KeyError(f"no receipt for parcel {parcel!r}")
        return receipt.wire()

    def list_receipts(self, band=None, status=None) -> list:
        out = []
        for parcel, r in sorted(self._receipts.items()):
            if band is not None and (r.band or "").lower() != str(band).lower():
                continue
            if status is not None and r.status.lower() != str(status).lower():
                continue
            out.append({"parcel": parcel, "status": r.status,
                        "score": None if r.score is None else str(r.score), "band": r.band})
        return out


class GoldReader:
    """Wraps a callable returning gold_receipts rows (dicts). In Fabric, pass a function that
    runs a SELECT against the gold_receipts table; in tests, pass a list-returning lambda.
    Every receipt is hash-verified on read, so tampered rows raise ValueError.
    """

    def __init__(self, fetch_gold_receipts: Callable[[], Iterable[dict]]):
        self._fetch = fetch_gold_receipts

    def _latest(self) -> dict[str, Receipt]:
        newest: dict[str, tuple[str, Receipt]] = {}
        for row in self._fetch():
            wire = {
                "entity_id": row["entity_id"], "status": row["status"], "score": row["score"],
                "band": row["band"], "components": json.loads(row["components"]),
                "reasons": json.loads(row["reasons"]), "as_of": row["as_of"],
                "policy_sha256": row["policy_sha256"], "input_sha256": row["input_sha256"],
                "receipt_sha256": row["receipt_sha256"],
            }
            receipt = Receipt.from_wire(wire)
            stamp = row.get("scored_at") or ""
            current = newest.get(receipt.entity_id)
            if current is None or stamp >= current[0]:
                newest[receipt.entity_id] = (stamp, receipt)
        return {entity: pair[1] for entity, pair in newest.items()}

    def get_receipt(self, entity_id: str) -> Receipt | None:
        return self._latest().get(entity_id)

    def snapshot(self) -> dict[str, Receipt]:
        """One hash-verified read of the whole gold table (latest receipt per entity)."""
        return self._latest()

    def list_receipts(self, band: str | None = None, status: str | None = None) -> list[Receipt]:
        return [
            r for _, r in sorted(self._latest().items())
            if (band is None or r.band == band) and (status is None or r.status == status)
        ]
