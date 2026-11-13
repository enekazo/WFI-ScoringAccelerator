"""Evidence contract: every input carries provenance, quality and a validity window."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class ContractError(ValueError):
    """Raised when input violates the evidence contract."""


class Provenance(StrEnum):
    MEASURED = "measured"
    GENERATED = "generated"
    FIXTURE = "fixture"


class Quality(StrEnum):
    VALID = "valid"
    SUSPECT = "suspect"
    INVALID = "invalid"


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ContractError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def to_decimal(value, name: str) -> Decimal:
    if isinstance(value, float):
        raise ContractError(f"{name}: floats are not accepted; pass str or Decimal")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ContractError(f"{name}: not a number") from exc
    if not number.is_finite():
        raise ContractError(f"{name}: must be finite")
    return number


def sha256_json(document) -> str:
    body = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Observation:
    entity_id: str
    metric: str
    value: Decimal
    unit: str
    observed_at: datetime
    valid_until: datetime
    provenance: Provenance
    quality: Quality = Quality.VALID
    source: str = ""

    def __post_init__(self):
        object.__setattr__(self, "value", to_decimal(self.value, self.metric))
        object.__setattr__(self, "observed_at", utc(self.observed_at))
        object.__setattr__(self, "valid_until", utc(self.valid_until))
        object.__setattr__(self, "provenance", Provenance(self.provenance))
        object.__setattr__(self, "quality", Quality(self.quality))
        if not self.entity_id or not self.metric:
            raise ContractError("entity_id and metric are required")
        if self.valid_until <= self.observed_at:
            raise ContractError("valid_until must be after observed_at")

    def wire(self) -> dict:
        return {
            "entity_id": self.entity_id, "metric": self.metric,
            "value": str(self.value), "unit": self.unit,
            "observed_at": self.observed_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "provenance": self.provenance.value, "quality": self.quality.value,
            "source": self.source,
        }
