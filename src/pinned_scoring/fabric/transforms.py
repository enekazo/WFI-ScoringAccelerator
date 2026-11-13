"""Pure-Python medallion transforms (no Spark, no Fabric). Rows are plain dicts.

raw row    : parcel_id, metric, value, unit, observed_at, valid_until, provenance, source
             (+ raw_id, batch_id, ingested_at added at ingestion)
silver row : raw_id, entity_id, metric, value, unit, observed_at, valid_until, provenance,
             quality, quality_reason, source, validated_at
gold row   : Receipt.wire() fields + scored_at, components/reasons as JSON strings
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ..contracts import (
    ContractError, Observation, Provenance, Quality, sha256_json, to_decimal, utc,
)
from ..policy import Policy
from ..scorer import score

RAW_FIELDS = ("parcel_id", "metric", "value", "unit", "observed_at", "valid_until",
              "provenance", "source", "quality")

# (from_unit, to_unit) -> multiplier. Applied only when the policy wants to_unit.
CONVERSIONS = {
    ("m/s", "km/h"): Decimal("3.6"),
    ("mph", "km/h"): Decimal("1.609344"),
    ("kg/m2", "t/ha"): Decimal("10"),
    ("fraction", "%"): Decimal("100"),
}

FUTURE_TOLERANCE = timedelta(minutes=5)


def _text(value) -> str | None:
    return None if value is None else str(value)


def to_raw(row: dict, batch_id: str, ingested_at: datetime) -> dict:
    """Wrap an incoming row as received. Values are kept as text, nothing is parsed or rejected."""
    payload = {k: _text(row.get(k)) for k in RAW_FIELDS}
    return {
        "raw_id": sha256_json(payload), **payload,
        "batch_id": batch_id, "ingested_at": utc(ingested_at).isoformat(),
    }


def _parse_ts(value, name: str) -> datetime:
    if not value:
        raise ContractError(f"{name} missing")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{name} not ISO-8601") from exc
    return utc(parsed)


def _normalise_unit(value: Decimal, unit: str, target: str | None):
    if target is None or unit == target:
        return value, unit
    factor = CONVERSIONS.get((unit, target))
    if factor is None:
        return value, unit  # left as-is; the scorer will report unit:<metric>
    if unit == "fraction" and not (0 <= value <= 1):
        return value, unit  # mislabelled unit: refuse to convert, the scorer reports unit:<metric>
    return value * factor, target


def raw_to_silver(raw_rows, policy: Policy, validated_at: datetime) -> list[dict]:
    """Validate and type raw rows. Every raw row yields exactly one silver row."""
    now = utc(validated_at)
    targets = {f.metric: f.unit for f in policy.factors}
    out = []
    for raw in raw_rows:
        silver = {
            "raw_id": raw["raw_id"], "entity_id": raw.get("parcel_id"),
            "metric": raw.get("metric"), "value": None, "unit": raw.get("unit"),
            "observed_at": None, "valid_until": None, "provenance": None,
            "quality": Quality.VALID.value, "quality_reason": None,
            "source": raw.get("source") or "", "validated_at": now.isoformat(),
        }
        try:
            value = to_decimal(raw.get("value"), "value")
            observed = _parse_ts(raw.get("observed_at"), "observed_at")
            valid_until = _parse_ts(raw.get("valid_until"), "valid_until")
            provenance = Provenance(raw.get("provenance"))
            unit = raw.get("unit")
            value, unit = _normalise_unit(value, unit, targets.get(raw.get("metric")))
            # Constructing the Observation applies the shared evidence contract.
            Observation(
                entity_id=raw.get("parcel_id") or "", metric=raw.get("metric") or "",
                value=value, unit=unit or "", observed_at=observed,
                valid_until=valid_until, provenance=provenance, source=silver["source"],
            )
        except (ContractError, ValueError) as exc:
            silver["quality"] = Quality.INVALID.value
            silver["quality_reason"] = str(exc)
            out.append(silver)
            continue
        silver.update(
            value=str(value), unit=unit, observed_at=observed.isoformat(),
            valid_until=valid_until.isoformat(), provenance=provenance.value,
        )
        declared = raw.get("quality")
        if declared in (Quality.INVALID.value, Quality.SUSPECT.value):
            silver["quality"] = declared
            silver["quality_reason"] = "declared by source"
        elif observed > now + FUTURE_TOLERANCE:
            silver["quality"] = Quality.SUSPECT.value
            silver["quality_reason"] = "observed_at is in the future"
        out.append(silver)
    return out


def silver_to_observations(silver_rows) -> list[Observation]:
    """Typed rows -> Observations. Rows too broken to type (nulls) are omitted and so score as missing."""
    observations = []
    for row in silver_rows:
        if row["value"] is None or row["observed_at"] is None or row["provenance"] is None:
            continue
        observations.append(Observation(
            entity_id=row["entity_id"], metric=row["metric"], value=row["value"],
            unit=row["unit"] or "", observed_at=datetime.fromisoformat(row["observed_at"]),
            valid_until=datetime.fromisoformat(row["valid_until"]),
            provenance=row["provenance"], quality=row["quality"], source=row["source"],
        ))
    return observations


def policy_to_gold(policy: Policy, pinned_at: datetime) -> dict:
    return {
        "policy_sha256": policy.sha256, "name": policy.name, "version": policy.version,
        "policy_json": json.dumps(policy.wire(), sort_keys=True),
        "pinned_at": utc(pinned_at).isoformat(),
    }


def silver_to_gold(silver_rows, policy: Policy, as_of: datetime, scored_at: datetime | None = None):
    """Score every entity present in silver with scorer.score. Returns gold_receipts rows."""
    observations = silver_to_observations(silver_rows)
    entities = sorted({r["entity_id"] for r in silver_rows if r.get("entity_id")})
    stamp = utc(scored_at or as_of).isoformat()
    rows = []
    for entity in entities:
        receipt = score(entity, observations, policy, as_of)
        wire = receipt.wire()
        rows.append({
            **wire,
            "components": json.dumps(wire["components"]),
            "reasons": json.dumps(wire["reasons"]),
            "scored_at": stamp,
        })
    return rows
