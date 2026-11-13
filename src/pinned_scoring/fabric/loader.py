"""Loader: synthetic dataset JSON -> raw rows (bronze).

Expected JSON: a list of rows, or {"observations": [...]}, each with
parcel_id, metric, value, unit, observed_at, valid_until, provenance, source.
The generated data/dataset.json ({"parcels": {id: [...]}}) is also accepted.
"""

import json
from datetime import datetime
from pathlib import Path

from ..contracts import ContractError
from .transforms import RAW_FIELDS, to_raw

# Small built-in dataset covering good, unit-converted, expired and broken evidence.
FIXTURE_ROWS = [
    *[
        {"parcel_id": "P-001", "metric": m, "value": v, "unit": u,
         "observed_at": "2026-07-01T06:00:00+00:00", "valid_until": "2026-07-03T06:00:00+00:00",
         "provenance": p, "source": "fixture"}
        for m, v, u, p in [
            ("fuel_load", "30", "t/ha", "measured"), ("dead_fuel_pct", "60", "%", "measured"),
            ("fuel_moisture_pct", "8", "%", "measured"), ("weather_danger", "70", "index", "generated"),
            ("wind_kmh", "10", "m/s", "measured"),
        ]
    ],
    # P-002: only one metric, plus a broken row -> incomplete
    {"parcel_id": "P-002", "metric": "fuel_load", "value": "abc", "unit": "t/ha",
     "observed_at": "2026-07-01T06:00:00+00:00", "valid_until": "2026-07-03T06:00:00+00:00",
     "provenance": "measured", "source": "fixture"},
]


def read_rows(path) -> list[dict]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(document, dict) and isinstance(document.get("parcels"), dict):
        # data/dataset.json shape: {"parcels": {id: [observation, ...]}} with entity_id keys.
        return [{**o, "parcel_id": o.get("parcel_id", o.get("entity_id", pid))}
                for pid, obs in document["parcels"].items() for o in obs]
    rows = document.get("observations") if isinstance(document, dict) else document
    if not isinstance(rows, list):
        raise ContractError("dataset must be a list or {'observations': [...]}")
    return rows


def load_raw(rows, batch_id: str, ingested_at: datetime) -> list[dict]:
    """Rows -> raw rows. Unknown keys are ignored; missing keys become null, never an error."""
    return [to_raw({k: row.get(k) for k in RAW_FIELDS}, batch_id, ingested_at) for row in rows]


def load_raw_from_file(path, batch_id: str, ingested_at: datetime) -> list[dict]:
    return load_raw(read_rows(path), batch_id, ingested_at)
