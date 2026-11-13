"""Policy: weights, normalisation ranges, bands and provenance rules, pinned by SHA-256."""

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .contracts import ContractError, Provenance, sha256_json, to_decimal


@dataclass(frozen=True)
class Factor:
    name: str
    metric: str
    unit: str
    weight: Decimal
    low: Decimal
    high: Decimal
    invert: bool
    max_age_hours: int
    allowed_provenance: tuple

    def wire(self) -> dict:
        return {
            "name": self.name, "metric": self.metric, "unit": self.unit,
            "weight": str(self.weight), "low": str(self.low), "high": str(self.high),
            "invert": self.invert, "max_age_hours": self.max_age_hours,
            "allowed_provenance": [p.value for p in self.allowed_provenance],
        }


@dataclass(frozen=True)
class Policy:
    name: str
    version: str
    factors: tuple
    bands: tuple  # ((upper_exclusive, label), ...) ascending; last upper exceeds 100

    @classmethod
    def from_dict(cls, document: dict) -> "Policy":
        factors = tuple(
            Factor(
                name=item["name"], metric=item["metric"], unit=item["unit"],
                weight=to_decimal(item["weight"], "weight"),
                low=to_decimal(item["low"], "low"), high=to_decimal(item["high"], "high"),
                invert=bool(item.get("invert", False)),
                max_age_hours=int(item["max_age_hours"]),
                allowed_provenance=tuple(
                    Provenance(p) for p in item.get("allowed_provenance", ["measured"])
                ),
            )
            for item in document["factors"]
        )
        bands = tuple((to_decimal(upper, "band"), label) for upper, label in document["bands"])
        policy = cls(document["name"], document["version"], factors, bands)
        policy.validate()
        return policy

    @classmethod
    def load(cls, path) -> "Policy":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def validate(self) -> None:
        if not self.factors:
            raise ContractError("policy needs at least one factor")
        if sum(f.weight for f in self.factors) != Decimal(1):
            raise ContractError("factor weights must sum to exactly 1")
        names = [f.name for f in self.factors]
        if len(set(names)) != len(names):
            raise ContractError("duplicate factor name")
        for factor in self.factors:
            if factor.high <= factor.low:
                raise ContractError(f"{factor.name}: high must exceed low")
            if factor.weight <= 0 or factor.max_age_hours <= 0:
                raise ContractError(f"{factor.name}: weight and max_age_hours must be positive")
        uppers = [upper for upper, _ in self.bands]
        if uppers != sorted(uppers) or uppers[-1] <= 100:
            raise ContractError("bands must ascend and the last upper bound must exceed 100")

    def wire(self) -> dict:
        return {
            "name": self.name, "version": self.version,
            "factors": [f.wire() for f in self.factors],
            "bands": [[str(upper), label] for upper, label in self.bands],
        }

    @property
    def sha256(self) -> str:
        return sha256_json(self.wire())

    def band(self, score: Decimal) -> str:
        for upper, label in self.bands:
            if score < upper:
                return label
        raise ContractError("score outside bands")
