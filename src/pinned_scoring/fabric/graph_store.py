"""Graph layer in Fabric: the synthetic asset graph as lakehouse tables (nodes, edges, mitigations).

`graph_to_rows` / `rows_to_graph` are pure and round-trip exactly (tested offline). The Livy
loader writes the Delta tables; `fetch_rows` reads them back through the SQL analytics endpoint.
Nodes: parcel, asset, feeder, customer_group. Edges: asset->parcel (on_parcel),
asset->feeder (on_feeder), customer_group->feeder (served_by).
"""

import json
from datetime import datetime, timezone
from decimal import Decimal

from ..graph import Graph

NODE_COLUMNS = ("node_id", "node_type", "props")
EDGE_COLUMNS = ("edge_id", "src_id", "dst_id", "edge_type")
MITIGATION_COLUMNS = ("mitigation_id", "metric", "factor", "cost_usd")
META_COLUMNS = ("key", "value")


def graph_to_rows(graph: Graph) -> dict:
    nodes = [(p, "parcel", "{}") for p in graph.parcels]
    nodes += [(a, "asset", json.dumps({"type": v["type"]})) for a, v in sorted(graph.assets.items())]
    nodes += [(f, "feeder", "{}") for f in graph.feeders]
    nodes += [(g, "customer_group", json.dumps({"customers": v["customers"], "facility": v["facility"]}))
              for g, v in sorted(graph.customer_groups.items())]
    edges = []
    for a, v in sorted(graph.assets.items()):
        edges.append((f"{a}>{v['parcel']}", a, v["parcel"], "on_parcel"))
        edges.append((f"{a}>{v['feeder']}", a, v["feeder"], "on_feeder"))
    for g, v in sorted(graph.customer_groups.items()):
        edges.append((f"{g}>{v['feeder']}", g, v["feeder"], "served_by"))
    mitigations = [(m["id"], m["metric"], m["factor"], m["cost_usd"]) for m in graph.mitigations]
    return {"graph_nodes": nodes, "graph_edges": edges, "graph_mitigations": mitigations,
            "graph_meta": [("seed", str(graph.seed))]}


def rows_to_graph(nodes, edges, mitigations, seed: int) -> Graph:
    node_type = {n["node_id"]: n["node_type"] for n in nodes}
    props = {n["node_id"]: json.loads(n["props"] or "{}") for n in nodes}
    parcels = tuple(sorted(i for i, t in node_type.items() if t == "parcel"))
    feeders = tuple(sorted(i for i, t in node_type.items() if t == "feeder"))
    assets = {i: {"parcel": None, "feeder": None, "type": props[i].get("type")}
              for i, t in node_type.items() if t == "asset"}
    groups = {i: {"feeder": None, "customers": props[i]["customers"], "facility": props[i]["facility"]}
              for i, t in node_type.items() if t == "customer_group"}
    for e in edges:
        if e["edge_type"] == "on_parcel":
            assets[e["src_id"]]["parcel"] = e["dst_id"]
        elif e["edge_type"] == "on_feeder":
            assets[e["src_id"]]["feeder"] = e["dst_id"]
        elif e["edge_type"] == "served_by":
            groups[e["src_id"]]["feeder"] = e["dst_id"]
    mits = tuple({"id": m["mitigation_id"], "metric": m["metric"], "factor": m["factor"],
                  "cost_usd": int(m["cost_usd"])} for m in mitigations)
    return Graph(int(seed), parcels, dict(sorted(assets.items())), feeders,
                 dict(sorted(groups.items())), mits)


def _dicts(cursor):
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def fetch_rows(connect_fn, server: str, database: str) -> dict:
    """Read the four graph tables through the SQL analytics endpoint (Entra auth)."""
    conn = connect_fn(server, database)
    try:
        cur = conn.cursor()
        out = {}
        for table in ("graph_nodes", "graph_edges", "graph_mitigations", "graph_meta"):
            cur.execute(f"SELECT * FROM {table}")
            out[table] = _dicts(cur)
        return out
    finally:
        conn.close()


def graph_from_fabric(connect_fn, server: str, database: str) -> Graph:
    t = fetch_rows(connect_fn, server, database)
    seed = next(r["value"] for r in t["graph_meta"] if r["key"] == "seed")
    return rows_to_graph(t["graph_nodes"], t["graph_edges"], t["graph_mitigations"], int(seed))


def silver_observations_from_fabric(connect_fn, server: str, database: str) -> dict:
    """Silver rows -> {parcel: [Observation]} (duplicate-free load assumed; see docs)."""
    from .transforms import silver_to_observations
    conn = connect_fn(server, database)
    try:
        cur = conn.cursor()
        cur.execute("SELECT entity_id, metric, value, unit, observed_at, valid_until, provenance, "
                    "quality, source FROM silver_observations")
        rows = _dicts(cur)
    finally:
        conn.close()
    for r in rows:
        if isinstance(r["value"], Decimal):
            r["value"] = str(r["value"])
        for k in ("observed_at", "valid_until"):
            if isinstance(r[k], datetime):
                # the SQL endpoint returns naive UTC; Observation requires aware timestamps
                r[k] = (r[k] if r[k].tzinfo else r[k].replace(tzinfo=timezone.utc)).isoformat()
    by_parcel: dict = {}
    for o in silver_to_observations(rows):
        by_parcel.setdefault(o.entity_id, []).append(o)
    return by_parcel


def create_table_statements() -> list:
    return [
        "CREATE TABLE IF NOT EXISTS graph_nodes (node_id string, node_type string, props string) USING DELTA",
        "CREATE TABLE IF NOT EXISTS graph_edges (edge_id string, src_id string, dst_id string, "
        "edge_type string) USING DELTA",
        "CREATE TABLE IF NOT EXISTS graph_mitigations (mitigation_id string, metric string, factor string, "
        "cost_usd int) USING DELTA",
        "CREATE TABLE IF NOT EXISTS graph_meta (key string, value string) USING DELTA",
    ]


def load_via_livy(session, graph: Graph) -> dict:
    """Replace the graph tables' contents (idempotent: overwrite) and return row counts."""
    rows = graph_to_rows(graph)
    cols = {"graph_nodes": NODE_COLUMNS, "graph_edges": EDGE_COLUMNS,
            "graph_mitigations": MITIGATION_COLUMNS, "graph_meta": META_COLUMNS}
    counts = {}
    for table, data in rows.items():
        session.run(f"df = spark.createDataFrame({data!r}, {list(cols[table])!r}); "
                    f"df.write.mode('overwrite').option('overwriteSchema','true')"
                    f".format('delta').saveAsTable({table!r})")
        counts[table] = int(session.run(f"print(spark.table({table!r}).count())").strip())
    return counts
