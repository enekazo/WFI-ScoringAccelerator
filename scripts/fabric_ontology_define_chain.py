"""Minimal access-chain ontology: 5 entities, 4 relationships (updateDefinition + getDefinition read-back).

  python scripts/fabric_ontology_define_chain.py [ontology_id]
Reuses the TMDL helpers of fabric_ontology_define.py and replaces its scope.
"""

import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.argv = [a for a in sys.argv if a != "--readback"] + (["--readback"] if "--readback" in sys.argv else [])
READBACK = "--readback" in sys.argv
if READBACK:
    sys.argv.remove("--readback")
import fabric_ontology_define as d  # noqa: E402

S, I, B = "string", "int64", "boolean"
d.TABLES = {
    "parcel": [("parcel_id", S), ("status", S), ("band", S), ("score", S), ("provenance", S), ("seed", I)],
    "road_segment": [("road_id", S), ("parcel_id", S), ("passable", B), ("width_class", S),
                     ("provenance", S), ("seed", I)],
    "home_group": [("group_id", S), ("parcel_id", S), ("houses", I), ("has_critical_facility", B),
                   ("distance_to_houses_m", I), ("provenance", S), ("seed", I)],
    "water_source": [("water_id", S), ("road_id", S), ("type", S), ("capacity_l", I), ("provenance", S),
                     ("seed", I)],
    "crew_access": [("parcel_id", S), ("crew_type", S), ("reachable", B), ("via_road_id", S),
                    ("provenance", S), ("seed", I)],
}
d.ENTITIES = {
    "Parcel": (
        "parcel",
        "parcel_id",
        "A land parcel with parcel_id, status (scored or abstained), band (low, moderate, high, extreme, abstained), score, and provenance.",
    ),
    "RoadSegment": (
        "road_segment",
        "road_id",
        "Access road segment serving a parcel (road_id, parcel_id, passable boolean, width_class: narrow, standard, or wide). "
        "Hand crews can use any passable road; engines require passable=true and width_class in ('standard','wide') (not 'narrow'); "
        "dozers require passable=true and width_class='wide'.",
    ),
    "HomeGroup": (
        "home_group",
        "group_id",
        "Home group near a parcel (group_id, parcel_id, houses count, has_critical_facility boolean, distance_to_houses_m integer). "
        "A parcel may have multiple HomeGroup rows; total homes for a parcel is SUM(HomeGroup.houses) grouped by parcel_id.",
    ),
    "WaterSource": (
        "water_source",
        "water_id",
        "Water source on a road segment (water_id, road_id, type: hydrant, tank, or pond, capacity_l integer in liters). "
        "A parcel has water on a passable access road iff EXISTS a RoadSegment for that parcel with passable=true joined to WaterSource on road_id; "
        "a parcel has no water source on a passable access road when NOT EXISTS such a passable RoadSegment + WaterSource pair "
        "(including parcels with no passable road at all).",
    ),
    "CrewAccess": (
        "crew_access",
        ["parcel_id", "crew_type"],
        "Materialized crew reachability per parcel and crew_type (composite key parcel_id + crew_type in {'hand_crew','engine','dozer'}, "
        "reachable boolean, via_road_id). A parcel has passable access for a crew_type (such as engine, hand_crew, or dozer) "
        "if and only if its CrewAccess row for that crew_type has reachable=true (and lacks access when reachable=false).",
    ),
}
RELS = [  # (relationship name, from table.col, to table.col, from entity, to entity, label)
    ("parcel_has_road", "road_segment.parcel_id", "parcel.parcel_id", "RoadSegment", "Parcel", "Road Serves Parcel"),
    ("road_has_water", "water_source.road_id", "road_segment.road_id", "WaterSource", "RoadSegment", "Water On Road"),
    ("parcel_has_homes", "home_group.parcel_id", "parcel.parcel_id", "HomeGroup", "Parcel", "Homes In Parcel"),
    ("parcel_has_crew_access", "crew_access.parcel_id", "parcel.parcel_id", "CrewAccess", "Parcel",
     "Crew Access To Parcel"),
]
d.OUT = Path(os.environ.get(
    "PINNED_VERIFICATION_DIR", str(Path(__file__).resolve().parents[1] / "docs/evidence/scenario-2-ontology")
)) / "ontology_definition_chain.json"

_parts = d.parts


def _existing_part_map() -> dict[str, str]:
    base = f"https://api.fabric.microsoft.com/v1/workspaces/{d.WS}/ontologies/{d.ONTOLOGY_ID}"
    s, b, loc = d.call("POST", base + "/getDefinition")
    if s == 202 and loc:
        st, _ = d.wait(loc)
        if st == "Succeeded":
            s, b, _ = d.call("GET", loc + "/result")
    got = json.loads(b).get("definition", {}).get("parts", []) if s == 200 and b else []
    return {x["path"]: base64.b64decode(x["payload"]).decode("utf-8") for x in got if "path" in x and "payload" in x}


def _with_updated_entity_doc(existing_tmdl: str, doc: str) -> str:
    lines = existing_tmdl.splitlines()
    if lines and lines[0].startswith("///"):
        lines[0] = f"/// {doc}"
    else:
        lines.insert(0, f"/// {doc}")
    return "\n".join(lines) + ("\n" if existing_tmdl.endswith("\n") else "")


def parts(existing: dict[str, str] | None = None):
    p = _parts()
    keep_map = _existing_part_map() if existing is None else existing
    if "expressions.tmdl" in keep_map:
        p["expressions.tmdl"] = keep_map["expressions.tmdl"]
    for t in d.TABLES:
        t_path = f"tables/{t}.tmdl"
        if t_path in keep_map and f"table {t}" in keep_map[t_path]:
            p[t_path] = keep_map[t_path]
    for e, (t, key, doc) in d.ENTITIES.items():
        e_path = f"entities/{e}.tmdl"
        keys = [key] if isinstance(key, str) else list(key)
        if e_path in keep_map and all(f"keyProperty: {k}" in keep_map[e_path] for k in keys):
            p[e_path] = _with_updated_entity_doc(keep_map[e_path], doc)
    p["relationships.tmdl"] = "\n".join(
        f"relationship {n}\n\tfromColumn: {f}\n\ttoColumn: {t}\n" for n, f, t, *_ in RELS)
    if (
        "entityRelationships.tmdl" in keep_map
        and all(f"entityRelationship '{lbl}'" in keep_map["entityRelationships.tmdl"] for *_, lbl in RELS)
    ):
        p["entityRelationships.tmdl"] = keep_map["entityRelationships.tmdl"]
    else:
        p["entityRelationships.tmdl"] = "\n".join(
            f"entityRelationship '{lbl}'\n\tlineageTag: {d.tag()}\n\tfromEntity: {fe}\n\ttoEntity: {te}\n\n"
            f"\tbackingConfiguration\n\t\trelationship: {n}\n" for n, _, _, fe, te, lbl in RELS)
    return p


d.parts = parts

if __name__ == "__main__":
    base = f"https://api.fabric.microsoft.com/v1/workspaces/{d.WS}/ontologies/{d.ONTOLOGY_ID}"
    body = {"definition": {"parts": [
        {"path": k, "payload": base64.b64encode(v.encode()).decode(), "payloadType": "InlineBase64"}
        for k, v in parts().items()]}}
    log = {"relationships": [r[0] for r in RELS], "entities": list(d.ENTITIES)}
    s, b, loc = d.call("POST", base + "/updateDefinition", body) if not READBACK else (None, "", None)
    log["update"] = {"status": s, "body": b[:1500]} if s else {
        "skipped": "--readback; first apply returned HTTP 200, re-applying with new lineage tags returns 400 NotFound"}
    if s == 202 and loc:
        st, detail = d.wait(loc)
        log["update"]["operation"] = {"state": st, "detail": detail}
    s, b, loc = d.call("POST", base + "/getDefinition")
    if s == 200:
        got = json.loads(b)["definition"]["parts"]
        log["get"] = {"status": 200}
        loc = None
    elif s == 202 and loc:
        st, _ = d.wait(loc)
        s2, b2, _ = d.call("GET", loc + "/result")
        log["get"] = {"status": s2, "state": st}
        got = json.loads(b2)["definition"]["parts"] if s2 == 200 else None
    else:
        got = None
        log["get"] = {"status": s, "body": b[:500]}
    if got:
        log["get"]["parts"] = sorted(x["path"] for x in got)
        log["get"]["entities"] = sorted(x["path"] for x in got if x["path"].startswith("entities/"))
        rel = [x for x in got if x["path"] == "entityRelationships.tmdl"]
        log["get"]["relationship_labels_read_back"] = [
            ln.split("'")[1] for ln in base64.b64decode(rel[0]["payload"]).decode().splitlines()
            if ln.startswith("entityRelationship '")] if rel else []
    d.OUT.write_text(json.dumps(log, indent=2))
    print(json.dumps(log, indent=2)[:3000])
