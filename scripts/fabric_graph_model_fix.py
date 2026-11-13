"""Pure helpers and API orchestration for Fabric IQ ontology and GraphModel composite-key fixes.

Requirements enforced here:
- Composite key ["parcel_id", "crew_type"] must be set in ALL THREE places:
  1. Ontology entity CrewAccess (entities/CrewAccess.tmdl -> keyProperty: parcel_id and keyProperty: crew_type)
  2. GraphModel node type CrewAccess (graphType.json -> primaryKeyProperties: ["parcel_id", "crew_type"])
  3. GraphModel edge for crew_access (graphDefinition.json -> sourceNodeKeyColumns: ["parcel_id", "crew_type"],
     destinationNodeKeyColumns: ["parcel_id"])
- Graph refresh is MANUAL only (jobs/instances?jobType=RefreshGraph or auto-queued by updateDefinition);
  never create or enable a refresh schedule, and never re-save the graph from the portal (which reverts the edge fix).
- Honours Retry-After on HTTP 429 from Fabric REST calls.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.fabric.microsoft.com/v1"
COMPOSITE_KEY = ["parcel_id", "crew_type"]
PARCEL_KEY = ["parcel_id"]
MIN_CAPACITY_SKU_F = 4

SKU_RANKS = {f"F{2**i}": 2**i for i in range(1, 12)}
SKU_HOURLY_USD_ESTIMATE = {
    "F2": 0.36, "F4": 0.72, "F8": 1.44, "F16": 2.88, "F32": 5.76, "F64": 11.52,
}


def validate_capacity_sku(sku: str) -> dict:
    """Apply this installer's conservative F4 minimum; not the universal product minimum."""
    norm = (sku or "").strip().upper()
    rank = SKU_RANKS.get(norm, 0)
    ok = rank >= MIN_CAPACITY_SKU_F
    return {
        "sku": norm,
        "rank": rank,
        "ok": ok,
        "min_sku": f"F{MIN_CAPACITY_SKU_F}",
        "hourly_usd_estimate": SKU_HOURLY_USD_ESTIMATE.get(norm),
        "reason": None if ok else f"Capacity SKU '{sku}' is below this installer's minimum F{MIN_CAPACITY_SKU_F}.",
    }


def parse_retry_after(header_value: str | int | None, default_seconds: int = 15) -> int:
    """Parse a Retry-After header value into integer seconds (bounded to [1, 300])."""
    if header_value is None:
        return default_seconds
    try:
        val = int(str(header_value).strip())
        return max(1, min(300, val))
    except (ValueError, TypeError):
        return default_seconds


def build_default_graph_model_objects(
    workspace_id: str,
    lakehouse_id: str,
    entity_tags: dict[str, str] | None = None,
    rel_tags: dict[str, str] | None = None,
) -> tuple[dict, dict, dict]:
    """Build complete dataSources.json, graphType.json, and graphDefinition.json for a fresh GraphModel."""
    et = {
        "Parcel": "b1de8f10-cb39-42d1-878a-135491a6c708",
        "RoadSegment": "a072c91c-db40-4edf-b131-8c57c251c87a",
        "HomeGroup": "a54ea906-462c-4527-b30a-ff60e13b6592",
        "WaterSource": "107c5201-10f6-432e-bde1-9eb161cb4468",
        "CrewAccess": "00000000-0000-4000-8000-000000000016",
    }
    if entity_tags:
        et.update({k: v for k, v in entity_tags.items() if v})

    rt = {
        "parcel_has_road": "70778db3-8ef4-4033-8ff8-563fb9d6d8df",
        "road_has_water": "8be21b3b-4251-4a0d-b779-2cb83bd671e7",
        "parcel_has_homes": "898001d9-6ce7-4ce9-ac3d-06ad8baa7461",
        "parcel_has_crew_access": "00000000-0000-4000-8000-000000000028",
    }
    if rel_tags:
        rt.update({k: v for k, v in rel_tags.items() if v})

    tables = ["parcel", "road_segment", "home_group", "water_source", "crew_access"]
    data_sources = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/graphIndex/definition/dataSources/1.0.0/schema.json",
        "dataSources": [
            {
                "name": tbl,
                "type": "DeltaTable",
                "properties": {
                    "path": f"abfss://{workspace_id}@onelake.pbidedicated.windows.net/{lakehouse_id}/Tables/{tbl}"
                },
            }
            for tbl in tables
        ],
    }

    entity_specs = [
        ("Parcel", "parcel", ["parcel_id"], [
            ("parcel_id", "STRING"), ("status", "STRING"), ("band", "STRING"),
            ("score", "STRING"), ("provenance", "STRING"), ("seed", "INT"),
        ]),
        ("RoadSegment", "road_segment", ["road_id"], [
            ("road_id", "STRING"), ("parcel_id", "STRING"), ("passable", "BOOLEAN"),
            ("width_class", "STRING"), ("provenance", "STRING"), ("seed", "INT"),
        ]),
        ("HomeGroup", "home_group", ["group_id"], [
            ("group_id", "STRING"), ("parcel_id", "STRING"), ("houses", "INT"),
            ("has_critical_facility", "BOOLEAN"), ("distance_to_houses_m", "INT"),
            ("provenance", "STRING"), ("seed", "INT"),
        ]),
        ("WaterSource", "water_source", ["water_id"], [
            ("water_id", "STRING"), ("road_id", "STRING"), ("type", "STRING"),
            ("capacity_l", "INT"), ("provenance", "STRING"), ("seed", "INT"),
        ]),
        ("CrewAccess", "crew_access", list(COMPOSITE_KEY), [
            ("parcel_id", "STRING"), ("crew_type", "STRING"), ("reachable", "BOOLEAN"),
            ("via_road_id", "STRING"), ("provenance", "STRING"), ("seed", "INT"),
        ]),
    ]

    rel_specs = [
        ("parcel_has_road", "RoadSegment", "Parcel", "road_segment", ["road_id"], ["parcel_id"]),
        ("road_has_water", "WaterSource", "RoadSegment", "water_source", ["water_id"], ["road_id"]),
        ("parcel_has_homes", "HomeGroup", "Parcel", "home_group", ["group_id"], ["parcel_id"]),
        ("parcel_has_crew_access", "CrewAccess", "Parcel", "crew_access", list(COMPOSITE_KEY), list(PARCEL_KEY)),
    ]

    graph_type = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/graphIndex/definition/graphType/1.0.0/schema.json",
        "nodeTypes": [
            {
                "primaryKeyProperties": pk,
                "alias": et[ename],
                "labels": [ename],
                "properties": [{"name": col, "type": ctype} for col, ctype in cols],
            }
            for ename, _tbl, pk, cols in entity_specs
        ],
        "edgeTypes": [
            {
                "alias": rt[rname],
                "labels": [rname],
                "sourceNodeType": {"alias": et[src_entity]},
                "destinationNodeType": {"alias": et[dst_entity]},
                "properties": [],
            }
            for rname, src_entity, dst_entity, _tbl, _sk, _dk in rel_specs
        ],
    }

    graph_def = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/graphIndex/definition/graphDefinition/1.0.0/schema.json",
        "nodeTables": [
            {
                "nodeTypeAlias": et[ename],
                "id": et[ename],
                "dataSourceName": tbl,
                "propertyMappings": [{"propertyName": col, "sourceColumn": col} for col, _ctype in cols],
            }
            for ename, tbl, _pk, cols in entity_specs
        ],
        "edgeTables": [
            {
                "edgeTypeAlias": rt[rname],
                "id": rt[rname],
                "edgeIdMapping": [],
                "dataSourceName": tbl,
                "sourceNodeKeyColumns": sk,
                "propertyMappings": [],
                "destinationNodeKeyColumns": dk,
            }
            for rname, _src_entity, _dst_entity, tbl, sk, dk in rel_specs
        ],
    }
    return data_sources, graph_type, graph_def


def patch_graph_type_json(graph_type: dict) -> tuple[dict, bool]:
    """Ensure CrewAccess nodeType in graphType.json has primaryKeyProperties = ['parcel_id', 'crew_type']."""
    out = json.loads(json.dumps(graph_type))
    changed = False
    for nt in out.get("nodeTypes") or []:
        name = nt.get("name") or nt.get("label") or nt.get("alias") or ""
        props = [p.get("name") if isinstance(p, dict) else p for p in (nt.get("properties") or [])]
        if name == "CrewAccess" or ("crew_type" in props and "parcel_id" in props):
            if nt.get("primaryKeyProperties") != COMPOSITE_KEY:
                nt["primaryKeyProperties"] = list(COMPOSITE_KEY)
                changed = True
    return out, changed


def patch_graph_definition_json(graph_def: dict) -> tuple[dict, bool]:
    """Ensure crew_access edge in graphDefinition.json has sourceNodeKeyColumns = ['parcel_id', 'crew_type']."""
    out = json.loads(json.dumps(graph_def))
    changed = False
    crew_ds_names = set()
    for ds in out.get("dataSources") or []:
        path_or_name = (ds.get("name") or "") + " " + str(ds.get("path") or ds.get("tableName") or "")
        if "crew_access" in path_or_name.lower():
            if ds.get("name"):
                crew_ds_names.add(ds["name"])
            if ds.get("id"):
                crew_ds_names.add(ds["id"])

    for edge in out.get("edgeTables") or out.get("edges") or []:
        ds_ref = edge.get("dataSourceName") or edge.get("dataSource") or edge.get("tableName") or ""
        is_crew_edge = (
            "crew_access" in str(ds_ref).lower()
            or ds_ref in crew_ds_names
            or "crew" in str(edge.get("name") or edge.get("id") or "").lower()
        )
        if is_crew_edge:
            if edge.get("sourceNodeKeyColumns") != COMPOSITE_KEY:
                edge["sourceNodeKeyColumns"] = list(COMPOSITE_KEY)
                changed = True
            if edge.get("destinationNodeKeyColumns") != PARCEL_KEY:
                edge["destinationNodeKeyColumns"] = list(PARCEL_KEY)
                changed = True
    return out, changed


def patch_graph_model_parts(
    parts: list[dict],
    workspace_id: str | None = None,
    lakehouse_id: str | None = None,
    entity_tags: dict[str, str] | None = None,
    rel_tags: dict[str, str] | None = None,
) -> tuple[list[dict], dict]:
    """Decode InlineBase64 parts of a GraphModel definition, bootstrap empty definitions when needed,
    patch graphType.json and graphDefinition.json, and return the updated parts list plus a summary dict."""
    new_parts = []
    summary = {
        "dataSources_bootstrapped": False,
        "graphType_patched": False,
        "graphDefinition_patched": False,
        "schedules_added": 0,
    }
    default_ds, default_gt, default_gd = (None, None, None)
    if workspace_id and lakehouse_id:
        default_ds, default_gt, default_gd = build_default_graph_model_objects(
            workspace_id, lakehouse_id, entity_tags=entity_tags, rel_tags=rel_tags
        )

    for part in parts:
        p = dict(part)
        path = p.get("path", "")
        if path.endswith("dataSources.json") and p.get("payload") and default_ds is not None:
            obj = json.loads(base64.b64decode(p["payload"]).decode("utf-8"))
            if obj.get("dataSources") != default_ds.get("dataSources"):
                p["payload"] = base64.b64encode(json.dumps(default_ds, indent=2).encode("utf-8")).decode("ascii")
                summary["dataSources_bootstrapped"] = True
        elif path.endswith("graphType.json") and p.get("payload"):
            obj = json.loads(base64.b64decode(p["payload"]).decode("utf-8"))
            bootstrapped = False
            if default_gt is not None:
                cur_nt_aliases = [n.get("alias") for n in (obj.get("nodeTypes") or [])]
                def_nt_aliases = [n.get("alias") for n in (default_gt.get("nodeTypes") or [])]
                cur_et_aliases = [
                    (
                        e.get("alias"),
                        (e.get("sourceNodeType") or {}).get("alias"),
                        (e.get("destinationNodeType") or {}).get("alias"),
                    )
                    for e in (obj.get("edgeTypes") or [])
                ]
                def_et_aliases = [
                    (
                        e.get("alias"),
                        (e.get("sourceNodeType") or {}).get("alias"),
                        (e.get("destinationNodeType") or {}).get("alias"),
                    )
                    for e in (default_gt.get("edgeTypes") or [])
                ]
                if (
                    not obj.get("nodeTypes")
                    or not obj.get("edgeTypes")
                    or ((entity_tags or rel_tags) and (cur_nt_aliases != def_nt_aliases or cur_et_aliases != def_et_aliases))
                ):
                    obj = default_gt
                    bootstrapped = True
            patched, ch = patch_graph_type_json(obj)
            if ch or bootstrapped:
                p["payload"] = base64.b64encode(json.dumps(patched, indent=2).encode("utf-8")).decode("ascii")
                summary["graphType_patched"] = True
        elif path.endswith("graphDefinition.json") and p.get("payload"):
            obj = json.loads(base64.b64decode(p["payload"]).decode("utf-8"))
            bootstrapped = False
            if default_gd is not None:
                cur_nt_ids = [(n.get("id"), n.get("nodeTypeAlias")) for n in (obj.get("nodeTables") or [])]
                def_nt_ids = [(n.get("id"), n.get("nodeTypeAlias")) for n in (default_gd.get("nodeTables") or [])]
                cur_et_ids = [(e.get("id"), e.get("edgeTypeAlias")) for e in (obj.get("edgeTables") or [])]
                def_et_ids = [(e.get("id"), e.get("edgeTypeAlias")) for e in (default_gd.get("edgeTables") or [])]
                if (
                    not obj.get("nodeTables")
                    or not obj.get("edgeTables")
                    or ((entity_tags or rel_tags) and (cur_nt_ids != def_nt_ids or cur_et_ids != def_et_ids))
                ):
                    obj = default_gd
                    bootstrapped = True
            patched, ch = patch_graph_definition_json(obj)
            if ch or bootstrapped:
                p["payload"] = base64.b64encode(json.dumps(patched, indent=2).encode("utf-8")).decode("ascii")
                summary["graphDefinition_patched"] = True
        new_parts.append(p)
    return new_parts, summary


def verify_composite_key_three_places(ontology_tmdl: str, graph_type: dict, graph_def: dict) -> dict:
    """Verify that the composite key ['parcel_id', 'crew_type'] is set in all 3 required places."""
    tmdl_keys = [
        ln.split(":", 1)[1].strip()
        for ln in (ontology_tmdl or "").splitlines()
        if ln.strip().startswith("keyProperty:")
    ]
    ontology_ok = tmdl_keys == COMPOSITE_KEY

    node_ok = False
    for nt in graph_type.get("nodeTypes") or []:
        name = nt.get("name") or nt.get("label") or nt.get("alias") or ""
        props = [p.get("name") if isinstance(p, dict) else p for p in (nt.get("properties") or [])]
        if name == "CrewAccess" or ("crew_type" in props and "parcel_id" in props):
            node_ok = nt.get("primaryKeyProperties") == COMPOSITE_KEY

    edge_ok = False
    for edge in graph_def.get("edgeTables") or graph_def.get("edges") or []:
        if edge.get("sourceNodeKeyColumns") == COMPOSITE_KEY and edge.get("destinationNodeKeyColumns") == PARCEL_KEY:
            edge_ok = True

    return {
        "ontology_entity_key_ok": ontology_ok,
        "graph_node_key_ok": node_ok,
        "graph_edge_key_ok": edge_ok,
        "all_three_ok": ontology_ok and node_ok and edge_ok,
    }


def verify_c1_c5_ground_truth(out_questions: dict, expected: dict | None = None) -> dict:
    """Verify the exact C1-C5 ground-truth contract:
    C1: 14 parcels, C2: 11 parcels, C3: P003 -> ['dozer', 'engine'],
    C4: P018 with 42 houses, C5: ['P020', 'P031']."""
    if expected is not None:
        checks = {}
        for qid in ("C1", "C2", "C3", "C4", "C5"):
            actual = out_questions.get(qid, {})
            target = expected[qid]
            checks[qid] = sorted(actual.get("gql_answer", [])) == sorted(target["parcels"]) and bool(actual.get("match"))
        checks["C4_value"] = out_questions.get("C4", {}).get("gql_value") == expected["C4"]["value"]
        return {"checks": checks, "all_passed": all(checks.values())}
    c1 = out_questions.get("C1", {})
    c2 = out_questions.get("C2", {})
    c3 = out_questions.get("C3", {})
    c4 = out_questions.get("C4", {})
    c5 = out_questions.get("C5", {})
    checks = {
        "C1_count_14": len(c1.get("gql_answer", [])) == 14 and bool(c1.get("match")),
        "C2_count_11": len(c2.get("gql_answer", [])) == 11 and bool(c2.get("match")),
        "C3_P003_dozer_engine": c3.get("gql_answer") == ["dozer", "engine"] and bool(c3.get("match")),
        "C4_P018_42_houses": c4.get("gql_answer") == ["P018"] and c4.get("gql_value") == 42 and bool(c4.get("match")),
        "C5_P020_P031": c5.get("gql_answer") == ["P020", "P031"] and bool(c5.get("match")),
    }
    return {"checks": checks, "all_passed": all(checks.values())}


def token() -> str:
    return subprocess.check_output(
        "az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv",
        shell=True, text=True).strip()


def fabric_call(method: str, path: str, body: dict | None = None, max_retries: int = 5) -> tuple[int, dict, str]:
    """Call Fabric REST API honouring Retry-After on HTTP 429."""
    url = path if path.startswith("http") else f"{API}{path}"
    for attempt in range(max_retries):
        req = urllib.request.Request(
            url,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            method=method,
            headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, dict(r.headers), r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")
            hdrs = dict(e.headers) if e.headers else {}
            if e.code == 429 and attempt < max_retries - 1:
                wait_s = parse_retry_after(hdrs.get("Retry-After") or hdrs.get("retry-after"), 15 * (attempt + 1))
                time.sleep(wait_s)
                continue
            return e.code, hdrs, text
    return 429, {}, "Exceeded max retries on HTTP 429"


def wait_lro(headers: dict, want_result: bool = False) -> tuple[int, str]:
    loc = headers.get("Location") or headers.get("location")
    if not loc:
        return 500, "Missing Location header on 202 response"
    op_url = loc if loc.startswith("http") else f"{API}/operations/{loc.rstrip('/').split('/')[-1]}"
    for _ in range(80):
        time.sleep(5)
        s, _h, t = fabric_call("GET", op_url)
        state = json.loads(t).get("status") if s == 200 and t.lstrip().startswith("{") else None
        if state in ("Succeeded", "Completed"):
            if want_result:
                s2, _h2, t2 = fabric_call("GET", op_url.rstrip("/") + "/result")
                return s2, t2
            return 200, t
        if state in ("Failed", "Cancelled"):
            return 500, t
    return 408, "LRO timed out"


def resolve_ontology_tags_and_lakehouse(
    workspace_id: str,
    ontology_id: str | None = None,
    lakehouse_id: str | None = None,
) -> tuple[str | None, dict[str, str], dict[str, str]]:
    """Read Ontology definition to extract entity/relationship lineageTags and Lakehouse ID if needed."""
    entity_tags: dict[str, str] = {}
    rel_tags: dict[str, str] = {}
    resolved_lh = lakehouse_id or os.environ.get("FABRIC_LAKEHOUSE_ID") or None

    if ontology_id:
        s, h, t = fabric_call("POST", f"/workspaces/{workspace_id}/ontologies/{ontology_id}/getDefinition")
        if s == 202:
            s, t = wait_lro(h, want_result=True)
        if s == 200 and t.lstrip().startswith("{"):
            for part in json.loads(t).get("definition", {}).get("parts", []):
                path = part.get("path", "")
                payload = part.get("payload", "")
                if not payload:
                    continue
                text = base64.b64decode(payload).decode("utf-8", errors="replace")
                if path.startswith("entities/") and path.endswith(".tmdl"):
                    ename = Path(path).stem
                    for ln in text.splitlines():
                        if ln.strip().startswith("lineageTag:"):
                            entity_tags[ename] = ln.split(":", 1)[1].strip()
                            break
                elif path.endswith("entityRelationships.tmdl"):
                    display_to_rel = {
                        "road serves parcel": "parcel_has_road",
                        "water on road": "road_has_water",
                        "homes in parcel": "parcel_has_homes",
                        "crew access to parcel": "parcel_has_crew_access",
                    }
                    cur_display = None
                    cur_tag = None
                    for ln in text.splitlines():
                        s_ln = ln.strip()
                        if s_ln.startswith("entityRelationship "):
                            cur_display = s_ln.split("entityRelationship ", 1)[1].strip().strip("'\"").lower()
                            cur_tag = None
                        elif cur_display and s_ln.startswith("lineageTag:"):
                            cur_tag = s_ln.split(":", 1)[1].strip()
                            mapped = display_to_rel.get(cur_display)
                            if mapped and cur_tag:
                                rel_tags[mapped] = cur_tag
                        elif cur_tag and s_ln.startswith("relationship:"):
                            rname = s_ln.split(":", 1)[1].strip().strip("'\"")
                            if rname:
                                rel_tags[rname] = cur_tag
                elif not resolved_lh and path.startswith("tables/") and path.endswith(".tmdl"):
                    for ln in text.splitlines():
                        s_ln = ln.strip()
                        if s_ln.startswith("itemId:"):
                            resolved_lh = s_ln.split(":", 1)[1].strip()
                            break
                        if "ONT_ItemId" in s_ln and "=" in s_ln:
                            cand = s_ln.split("=", 1)[1].strip().strip("\"'")
                            if cand:
                                resolved_lh = cand
                                break

    if not resolved_lh:
        s, _h, t = fabric_call("GET", f"/workspaces/{workspace_id}/lakehouses")
        if s == 200 and t.lstrip().startswith("{"):
            lhs = json.loads(t).get("value", [])
            if lhs:
                resolved_lh = lhs[0].get("id")

    return resolved_lh, entity_tags, rel_tags


def apply_graph_model_composite_key_and_refresh(
    workspace_id: str,
    graph_model_id: str | None = None,
    ontology_id: str | None = None,
    lakehouse_id: str | None = None,
) -> dict:
    """Fetch GraphModel definition, bootstrap empty definitions when needed, patch node primaryKeyProperties
    + edge sourceNodeKeyColumns for crew_access to ['parcel_id', 'crew_type'], apply updateDefinition,
    and wait for the manual refresh."""
    if not graph_model_id:
        s, _h, t = fabric_call("GET", f"/workspaces/{workspace_id}/graphModels")
        models = json.loads(t).get("value", []) if s == 200 else []
        if not models:
            return {"status": "error", "error": f"No GraphModel found in workspace {workspace_id} (HTTP {s})"}
        graph_model_id = models[0]["id"]

    s, h, t = fabric_call("POST", f"/workspaces/{workspace_id}/graphModels/{graph_model_id}/getDefinition")
    if s == 202:
        s, t = wait_lro(h, want_result=True)
    if s != 200:
        return {"status": "error", "graph_model_id": graph_model_id, "error": f"getDefinition failed: {s} {t[:500]}"}

    parts = json.loads(t).get("definition", {}).get("parts", [])
    resolved_lh, entity_tags, rel_tags = resolve_ontology_tags_and_lakehouse(
        workspace_id,
        ontology_id=ontology_id or os.environ.get("FABRIC_ONTOLOGY_ID") or None,
        lakehouse_id=lakehouse_id,
    )
    patched_parts, summary = patch_graph_model_parts(
        parts,
        workspace_id=workspace_id,
        lakehouse_id=resolved_lh,
        entity_tags=entity_tags,
        rel_tags=rel_tags,
    )
    result = {"graph_model_id": graph_model_id, "patch_summary": summary, "refresh_mode": "manual_only"}

    print(f"  -> [Step 5] Updating GraphModel definition ({graph_model_id})...", flush=True)
    u_s, u_h, u_t = fabric_call(
        "POST",
        f"/workspaces/{workspace_id}/graphModels/{graph_model_id}/updateDefinition",
        {"definition": {"parts": patched_parts}},
    )
    if u_s == 202:
        u_s, u_t = wait_lro(u_h, want_result=False)
    result["updateDefinition"] = {"status": u_s, "body": u_t[:500]}
    if u_s not in (200, 202):
        result["status"] = "error"
        return result

    # Trigger or join manual RefreshGraph (never create a schedule).
    # Note: updateDefinition on a GraphModel often auto-queues a RefreshGraph job (status NotStarted/InProgress).
    # Check existing jobs first so we join an already-running refresh instead of creating a duplicate Deduped entry.
    existing_active = None
    j_s0, _jh0, j_t0 = fabric_call("GET", f"/workspaces/{workspace_id}/items/{graph_model_id}/jobs/instances")
    if j_s0 == 200 and j_t0.lstrip().startswith("{"):
        for inst in json.loads(j_t0).get("value", []):
            if inst.get("status") in ("NotStarted", "InProgress"):
                existing_active = inst
                break

    if existing_active is not None:
        print(
            f"  -> [Step 5] Joining active RefreshGraph job {existing_active.get('id')} "
            f"(status={existing_active.get('status')})...",
            flush=True,
        )
        result["refresh_trigger"] = {"status": "joined_existing", "job_id": existing_active.get("id")}
    else:
        print(f"  -> [Step 5] Triggering manual RefreshGraph job on GraphModel {graph_model_id}...", flush=True)
        r_s, r_h, r_t = fabric_call(
            "POST", f"/workspaces/{workspace_id}/items/{graph_model_id}/jobs/instances?jobType=RefreshGraph"
        )
        result["refresh_trigger"] = {"status": r_s, "location": r_h.get("Location") or r_h.get("location")}

    for poll_idx in range(60):
        time.sleep(10)
        j_s, _jh, j_t = fabric_call("GET", f"/workspaces/{workspace_id}/items/{graph_model_id}/jobs/instances")
        if j_s == 200 and j_t.lstrip().startswith("{"):
            instances = json.loads(j_t).get("value", [])
            if instances:
                # Skip Deduped entries ("A job instance of the same job type is already running")
                latest = next((i for i in instances if i.get("status") != "Deduped"), instances[0])
                st = latest.get("status")
                print(
                    f"  -> [Step 5] RefreshGraph poll {poll_idx + 1}/60: "
                    f"job={latest.get('id')} status={st}",
                    flush=True,
                )
                if st == "Completed":
                    result["refresh_job"] = latest
                    result["status"] = "ok"
                    return result
                if st in ("Failed", "Cancelled"):
                    result["refresh_job"] = latest
                    result["status"] = "error"
                    return result
    result["status"] = "timeout"
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace-id", default=os.environ.get("FABRIC_WORKSPACE_ID", ""))
    ap.add_argument("--lakehouse-id", default=os.environ.get("FABRIC_LAKEHOUSE_ID", ""))
    ap.add_argument("--ontology-id", default=os.environ.get("FABRIC_ONTOLOGY_ID", ""))
    ap.add_argument("--graph-model-id", default=os.environ.get("FABRIC_GRAPH_MODEL_ID", ""))
    ap.add_argument("--apply", action="store_true", help="Apply composite-key patch to GraphModel.")
    ap.add_argument("--refresh", action="store_true", help="Trigger/wait for manual RefreshGraph.")
    ap.add_argument("--check-sku", default="", help="Validate that a Fabric capacity SKU is >= F4.")
    ap.add_argument("--verify-c1-c5", action="store_true", help="Run fabric_graph_query.py and verify C1-C5 ground truth.")
    ap.add_argument("--dry-run", action="store_true", help="Validate patch logic on sample payload without calling Fabric.")
    args = ap.parse_args(argv)

    if args.check_sku:
        res = validate_capacity_sku(args.check_sku)
        print(json.dumps(res, indent=2))
        return 0 if res["ok"] else 1

    if args.dry_run:
        sample_gt = {"nodeTypes": [{"name": "CrewAccess", "properties": ["parcel_id", "crew_type"], "primaryKeyProperties": ["parcel_id"]}]}
        sample_gd = {"edgeTables": [{"name": "crew_access_edge", "dataSourceName": "crew_access", "sourceNodeKeyColumns": ["parcel_id"], "destinationNodeKeyColumns": ["parcel_id"]}]}
        gt_patched, _ = patch_graph_type_json(sample_gt)
        gd_patched, _ = patch_graph_definition_json(sample_gd)
        tmdl = "entity CrewAccess\n\tkeyProperty: parcel_id\n\tkeyProperty: crew_type\n"
        check = verify_composite_key_three_places(tmdl, gt_patched, gd_patched)
        print(json.dumps({"dry_run": True, "three_places": check, "refresh_mode": "manual_only"}, indent=2))
        return 0 if check["all_three_ok"] else 1

    if args.verify_c1_c5:
        env = dict(os.environ)
        if args.workspace_id:
            env["FABRIC_WORKSPACE_ID"] = args.workspace_id
        q_args = [sys.executable, str(ROOT / "scripts" / "fabric_graph_chain.py")]
        graph_id = args.graph_model_id or env.get("FABRIC_GRAPH_MODEL_ID")
        if graph_id:
            q_args.extend(["--graph-model-id", graph_id])
        subprocess.run(q_args, env=env, check=True)
        out_path = Path(env.get("PINNED_VERIFICATION_DIR", str(ROOT / "docs/evidence/scenario-2-ontology"))) / "fabric_graph_chain_gql.json"
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        qs = payload["questions"]
        expected = {qid: {"parcels": q["truth"], "value": q.get("truth_value")} for qid, q in qs.items()}
        ver = verify_c1_c5_ground_truth(qs, expected)
        ver["checks"]["dataset_matches"] = payload["dataset_matches"]
        ver["all_passed"] = all(ver["checks"].values())
        print(json.dumps(ver, indent=2))
        return 0 if ver["all_passed"] else 1

    if not args.workspace_id:
        ap.error("--workspace-id or FABRIC_WORKSPACE_ID is required")
    res = apply_graph_model_composite_key_and_refresh(
        args.workspace_id,
        args.graph_model_id or None,
        ontology_id=args.ontology_id or None,
        lakehouse_id=args.lakehouse_id or None,
    )
    print(json.dumps(res, indent=2))
    return 0 if res.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
