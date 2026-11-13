"""Spark-free loader: compute bronze/silver/gold (and graph/chain tables) locally and write Delta
tables straight to OneLake with the `deltalake` library. Same table names and columns as the
Spark/Livy path, so the SQL endpoint, graph model and ontology keep working.
"""

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ISO = "%Y-%m-%dT%H:%M:%S+00:00"
APPEND_ONLY = {"delta.appendOnly": "true"}
MEDALLION = ("raw_observations", "silver_observations", "gold_policy", "gold_receipts")
GRAPH_TABLES = ("graph_nodes", "graph_edges", "graph_mitigations", "graph_meta")


def table_uri(workspace_id: str, lakehouse_id: str, table: str) -> str:
    return f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}/Tables/{table}"


def storage_options(token: str) -> dict:
    return {"bearer_token": token, "use_fabric_endpoint": "true"}


def _ts(value):
    if value is None:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _arrow(rows, spec):
    """spec: [(name, kind, nullable)] with kind in str|ts|dec|int|bool."""
    import pyarrow as pa
    kinds = {"str": pa.string(), "ts": pa.timestamp("us", tz="UTC"), "dec": pa.decimal128(38, 10),
             "int": pa.int32(), "bool": pa.bool_()}
    schema = pa.schema([pa.field(n, kinds[k], nullable=nl) for n, k, nl in spec])
    cols = []
    for n, k, _ in spec:
        vals = [r.get(n) for r in rows]
        if k == "ts":
            vals = [_ts(v) for v in vals]
        elif k == "dec":
            vals = [None if v is None else Decimal(str(v)) for v in vals]
        cols.append(pa.array(vals, type=kinds[k]))
    return pa.Table.from_arrays(cols, schema=schema)


RAW_SPEC = [("raw_id", "str", False), ("parcel_id", "str", True), ("metric", "str", True),
            ("value", "str", True), ("unit", "str", True), ("observed_at", "str", True),
            ("valid_until", "str", True), ("provenance", "str", True), ("source", "str", True),
            ("quality", "str", True), ("batch_id", "str", False), ("ingested_at", "ts", False)]
SILVER_SPEC = [("raw_id", "str", False), ("entity_id", "str", True), ("metric", "str", True),
               ("value", "dec", True), ("unit", "str", True), ("observed_at", "ts", True),
               ("valid_until", "ts", True), ("provenance", "str", True), ("quality", "str", False),
               ("quality_reason", "str", True), ("source", "str", True), ("validated_at", "ts", False)]
POLICY_SPEC = [("policy_sha256", "str", False), ("name", "str", False), ("version", "str", False),
               ("policy_json", "str", False), ("pinned_at", "ts", False)]
GOLD_SPEC = [("receipt_sha256", "str", False), ("entity_id", "str", False), ("status", "str", False),
             ("score", "str", True), ("band", "str", True), ("components", "str", False),
             ("reasons", "str", False), ("as_of", "str", False), ("policy_sha256", "str", False),
             ("input_sha256", "str", False), ("scored_at", "ts", False)]


def build_medallion(dataset_path, policy_path, now=None) -> dict:
    """Pure: the four medallion tables as row lists, same logic as fabric/livy/load_medallion.py."""
    from ..policy import Policy
    from .loader import load_raw, read_rows
    from .transforms import policy_to_gold, raw_to_silver, silver_to_gold
    now = now or datetime.now(timezone.utc)
    policy = Policy.load(Path(policy_path))
    doc = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    as_of = datetime.fromisoformat(doc["as_of"]) if isinstance(doc, dict) and "as_of" in doc else now
    raw = load_raw(read_rows(dataset_path), str(uuid.uuid4()), now)
    silver = raw_to_silver(raw, policy, as_of)
    silver_rows = []
    for s in silver:
        r = dict(s)
        for c in ("observed_at", "valid_until"):
            r[c] = None if r.get(c) is None else _ts(r[c]).strftime(ISO)
        if r.get("value") is not None:
            r["value"] = format(Decimal(str(r["value"])).quantize(Decimal(1).scaleb(-10)).normalize(), "f")
        silver_rows.append(r)
    gold = silver_to_gold(silver_rows, policy, as_of, scored_at=now)
    return {"raw_observations": raw, "silver_observations": silver,
            "gold_policy": [policy_to_gold(policy, now)], "gold_receipts": gold}


def medallion_arrow(tables: dict) -> dict:
    specs = {"raw_observations": RAW_SPEC, "silver_observations": SILVER_SPEC,
             "gold_policy": POLICY_SPEC, "gold_receipts": GOLD_SPEC}
    return {t: _arrow(tables[t], specs[t]) for t in MEDALLION}


def graph_arrow(rows: dict) -> dict:
    from .graph_store import EDGE_COLUMNS, META_COLUMNS, MITIGATION_COLUMNS, NODE_COLUMNS
    cols = {"graph_nodes": NODE_COLUMNS, "graph_edges": EDGE_COLUMNS,
            "graph_mitigations": MITIGATION_COLUMNS, "graph_meta": META_COLUMNS}
    out = {}
    for t, data in rows.items():
        recs = [dict(zip(cols[t], r)) for r in data]
        spec = [(c, "int" if c == "cost_usd" else "str", True) for c in cols[t]]
        for r in recs:
            if "factor" in r:
                r["factor"] = str(r["factor"])
        out[t] = _arrow(recs, spec)
    return out


def chain_arrow(tables: dict) -> dict:
    from ..access_chain import TABLES
    ints = {"houses", "distance_to_houses_m", "capacity_l", "seed"}
    bools = {"passable", "has_critical_facility", "reachable"}
    out = {}
    for name, cols in TABLES.items():
        spec = [(c, "int" if c in ints else "bool" if c in bools else "str", True) for c in cols]
        out[name] = _arrow(tables[name], spec)
    return out


def write_tables(workspace_id, lakehouse_id, arrow_tables: dict, token: str, append_only=()):
    """Overwrite each table (schema included). Returns {table: row_count}."""
    from deltalake import write_deltalake
    counts = {}
    for name, tbl in arrow_tables.items():
        write_deltalake(table_uri(workspace_id, lakehouse_id, name), tbl, mode="overwrite",
                        schema_mode="overwrite", storage_options=storage_options(token),
                        configuration=APPEND_ONLY if name in append_only else None)
        counts[name] = tbl.num_rows
    return counts
