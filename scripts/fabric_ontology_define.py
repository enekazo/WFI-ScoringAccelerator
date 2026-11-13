"""Define the Fabric Ontology over the lakehouse graph tables (updateDefinition) and read it back."""

import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import os

WS = os.environ.get("FABRIC_WORKSPACE_ID", "00000000-0000-4000-8000-000000000019")
ONTOLOGY_ID = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("FABRIC_ONTOLOGY_ID", "00000000-0000-4000-8000-000000000002")
SERVER = os.environ.get("FABRIC_SQL_SERVER", "sql.example.invalid")
LAKEHOUSE_DB = os.environ.get("FABRIC_SQL_DATABASE", "pinned_scoring_lh")
OUT = Path(__file__).resolve().parents[1] / "docs/evidence/scenario-2-ontology/ontology_definition.json"

TABLES = {
    "graph_nodes": [("node_id", "string"), ("node_type", "string"), ("props", "string")],
    "graph_edges": [("edge_id", "string"), ("src_id", "string"), ("dst_id", "string"), ("edge_type", "string")],
    "gold_receipts": [("entity_id", "string"), ("status", "string"), ("score", "double"), ("band", "string")],
}
ENTITIES = {
    "GraphNode": ("graph_nodes", "node_id", "A parcel, asset, feeder or customer group."),
    "GraphEdge": ("graph_edges", "edge_id", "A typed link: on_parcel, on_feeder or served_by."),
    "ParcelScore": ("gold_receipts", "entity_id", "Pinned score receipt summary of a parcel."),
}


def tag():
    return str(uuid.uuid4())


def table_tmdl(name, cols):
    out = [f"table {name}", f"\tlineageTag: {tag()}", ""]
    for c, t in cols:
        out += [f"\tcolumn {c}", f"\t\tdataType: {t}", f"\t\tlineageTag: {tag()}", f"\t\tsourceColumn: {c}", ""]
    out += [f"\tpartition {name} = entity", "\t\tmode: directLake", "\t\tsource",
            f"\t\t\tentityName: {name}", "\t\t\tschemaName: dbo", "\t\t\texpressionSource: DatabaseQuery", ""]
    return "\n".join(out)


def entity_tmdl(name, table, key, doc):
    keys = [key] if isinstance(key, str) else list(key)
    out = [f"/// {doc}", f"entity {name}", f"\tlineageTag: {tag()}", f"\tbackingTable: {table}"]
    out += [f"\tkeyProperty: {k}" for k in keys] + [""]
    for c, t in TABLES[table]:
        out += [f"\tproperty {c}", f"\t\tdataType: {t}", f"\t\tlineageTag: {tag()}", "",
                "\t\tbackingConfiguration", f"\t\t\tvalueColumn: {table}.{c}", ""]
    return "\n".join(out)


def parts():
    p = {
        ".platform": json.dumps({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": "Ontology", "displayName": "pinned_scoring_ontology"},
            "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"}}),
        "database.tmdl": "database\n\tcompatibilityLevel: 1000000\n",
        "model.tmdl": "model Model\n\n" + "".join(f"ref table {t}\n" for t in TABLES) + "\n"
                      + "".join(f"ref entity {e}\n" for e in ENTITIES) + "\nref namespace default\n",
        "namespaces/default.tmdl": "namespace default\n\tlineageTag: default\n",
        "expressions.tmdl": ("expression DatabaseQuery =\n\t\tlet\n\t\t    database = Sql.Database("
                             f"\"{SERVER}\", \"{LAKEHOUSE_DB}\")\n\t\tin\n\t\t    database\n\tlineageTag: {tag()}\n"),
        "relationships.tmdl": (
            "relationship rel_edge_src\n\tfromColumn: graph_edges.src_id\n\ttoColumn: graph_nodes.node_id\n\n"
            "relationship rel_edge_dst\n\tfromColumn: graph_edges.dst_id\n\ttoColumn: graph_nodes.node_id\n"
            "\tisActive: false\n\n"
            "relationship rel_score_node\n\tfromColumn: gold_receipts.entity_id\n\ttoColumn: graph_nodes.node_id\n"),
        "entityRelationships.tmdl": (
            f"entityRelationship 'Edge From Node'\n\tlineageTag: {tag()}\n\tfromEntity: GraphEdge\n"
            "\ttoEntity: GraphNode\n\n\tbackingConfiguration\n\t\trelationship: rel_edge_src\n\n"
            f"entityRelationship 'Edge To Node'\n\tlineageTag: {tag()}\n\tfromEntity: GraphEdge\n"
            "\ttoEntity: GraphNode\n\n\tbackingConfiguration\n\t\trelationship: rel_edge_dst\n\n"
            f"entityRelationship 'Score For Parcel'\n\tlineageTag: {tag()}\n\tfromEntity: ParcelScore\n"
            "\ttoEntity: GraphNode\n\n\tbackingConfiguration\n\t\trelationship: rel_score_node\n"),
    }
    for t, cols in TABLES.items():
        p[f"tables/{t}.tmdl"] = table_tmdl(t, cols)
    for e, (t, k, d) in ENTITIES.items():
        p[f"entities/{e}.tmdl"] = entity_tmdl(e, t, k, d)
    return p


def token():
    return subprocess.run(
        "az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv",
        capture_output=True, text=True, shell=True, check=True).stdout.strip()


def call(method, url, body=None):
    req = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={
            "Authorization": "Bearer " + token(),
            "Content-Type": "application/json",
            "x-ms-fabric-skill": "fabriciq-ontology-cli",
        },
    )
    try:
        r = urllib.request.urlopen(req)
        return r.status, r.read().decode(), r.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), None


def wait(loc):
    for _ in range(40):
        time.sleep(5)
        s, b, _ = call("GET", loc)
        st = json.loads(b).get("status") if b else None
        if st in ("Succeeded", "Failed"):
            return st, json.loads(b)
    return "Timeout", {}


if __name__ == "__main__":
    base = f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/ontologies/{ONTOLOGY_ID}"
    body = {"definition": {"parts": [
        {"path": k, "payload": base64.b64encode(v.encode()).decode(), "payloadType": "InlineBase64"}
        for k, v in parts().items()]}}
    log = {}
    s, b, loc = call("POST", base + "/updateDefinition", body)
    log["update"] = {"status": s, "body": b[:1500]}
    if s == 202 and loc:
        st, detail = wait(loc)
        log["update"]["operation"] = {"state": st, "detail": detail}
    s, b, loc = call("POST", base + "/getDefinition")
    log["get"] = {"status": s}
    if s == 202 and loc:
        st, _ = wait(loc)
        s2, b2, _ = call("GET", loc + "/result")
        log["get"] = {"status": s2, "state": st}
        if s2 == 200:
            got = json.loads(b2)["definition"]["parts"]
            log["get"]["parts"] = sorted(x["path"] for x in got)
            log["get"]["entities"] = sorted(x["path"] for x in got if x["path"].startswith("entities/"))
    elif s == 200:
        got = json.loads(b)["definition"]["parts"]
        log["get"]["parts"] = sorted(x["path"] for x in got)
    OUT.write_text(json.dumps(log, indent=2))
    print(json.dumps(log, indent=2)[:3000])
