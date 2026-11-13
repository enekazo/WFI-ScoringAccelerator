"""Immutable, self-verifying score receipt. The agent reads receipts, never computes them."""

from dataclasses import dataclass
from decimal import Decimal

from .contracts import sha256_json


@dataclass(frozen=True)
class Receipt:
    entity_id: str
    status: str  # "scored" | "incomplete"
    score: Decimal | None
    band: str | None
    components: tuple  # ((factor, value, provenance, contribution), ...) as strings
    reasons: tuple     # ("missing:metric", "expired:metric", ...)
    as_of: str
    policy_sha256: str
    input_sha256: str

    def body(self) -> dict:
        return {
            "entity_id": self.entity_id, "status": self.status,
            "score": None if self.score is None else str(self.score), "band": self.band,
            "components": [list(c) for c in self.components],
            "reasons": list(self.reasons), "as_of": self.as_of,
            "policy_sha256": self.policy_sha256, "input_sha256": self.input_sha256,
        }

    @property
    def receipt_sha256(self) -> str:
        return sha256_json(self.body())

    def wire(self) -> dict:
        return {**self.body(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_wire(cls, document: dict) -> "Receipt":
        receipt = cls(
            entity_id=document["entity_id"], status=document["status"],
            score=None if document["score"] is None else Decimal(document["score"]),
            band=document["band"],
            components=tuple(tuple(c) for c in document["components"]),
            reasons=tuple(document["reasons"]), as_of=document["as_of"],
            policy_sha256=document["policy_sha256"], input_sha256=document["input_sha256"],
        )
        if receipt.receipt_sha256 != document["receipt_sha256"]:
            raise ValueError("receipt hash mismatch: content was altered")
        return receipt
