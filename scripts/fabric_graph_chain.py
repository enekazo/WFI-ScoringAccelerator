"""Run chain questions C1-C5 against the Fabric graph with GQL traversals and compare to code ground truth.

GQL (documented executeQuery API) retrieves the traversed rows (edges run child -> Parcel in the
materialised graph). Set logic (no-passable-road, etc.) is done client-side on those rows; this is
stated in the evidence. Entra token via az, no keys. Evidence: fabric_graph_chain_gql.json.
"""
import json
import os
import re
import argparse
from pathlib import Path
import sys
import time

from fabric_graph_query import WS, call, token  # noqa: F401
from pinned_scoring import access_chain as ac

OUT = Path(os.environ.get("PINNED_VERIFICATION_DIR", "docs/evidence/scenario-2-ontology")) / "fabric_graph_chain_gql.json"
Q = {
    "parcels": "MATCH (p:Parcel) RETURN p.parcel_id AS pid, p.band AS band",
    "roads": "MATCH (r:RoadSegment)-[e]->(p:Parcel) RETURN p.parcel_id AS pid, r.road_id AS rid, r.passable AS ok",
    "homes": "MATCH (h:HomeGroup)-[e]->(p:Parcel) RETURN p.parcel_id AS pid, h.houses AS houses, "
             "h.has_critical_facility AS crit",
    "water": "MATCH (w:WaterSource)-[e1]->(r:RoadSegment)-[e2]->(p:Parcel) RETURN p.parcel_id AS pid, "
             "r.road_id AS rid, r.passable AS ok, w.water_id AS wid",
    "crew": "MATCH (c:CrewAccess)-[e]->(p:Parcel) RETURN p.parcel_id AS pid, c.crew_type AS ct, "
            "c.reachable AS reach",
}


def run(gid, name):
    import urllib.parse

    data = []
    total_dt = 0.0
    next_page = None
    for _ in range(40):
        path = f"/workspaces/{WS}/graphModels/{gid}/executeQuery?beta=true"
        if next_page:
            path += f"&continuationToken={urllib.parse.quote(next_page, safe='')}"
        for attempt in range(4):
            s, t, dt = call("POST", path, {"query": Q[name]})
            if s == 429:
                time.sleep(15)
                continue
            break
        total_dt += dt
        body = json.loads(t)
        code = (body.get("status") or {}).get("code")
        result = body.get("result") or {}
        if s != 200 or code not in ("00000", "02000"):
            raise SystemExit(f"{name}: {s} {t[:500]}")
        data.extend(result.get("data") or [])
        next_page = result.get("nextPage")
        if not next_page:
            return data, round(total_dt, 2)
        if code == "02000":
            time.sleep(2)
    raise SystemExit(f"{name}: exceeded continuation token polling limit")


def main(gid=None):
    if not gid:
        s, t, _ = call("GET", f"/workspaces/{WS}/graphModels")
        if s != 200:
            raise RuntimeError(f"Cannot list graph models: HTTP {s}: {t[:500]}")
        models = json.loads(t)["value"]
        if len(models) != 1:
            raise ValueError("Set FABRIC_GRAPH_MODEL_ID or --graph-model-id to select exactly one graph.")
        gid = models[0]["id"]
    rows, lat = {}, {}
    for k in Q:
        rows[k], lat[k] = run(gid, k)
    parcels = {r["pid"]: r["band"] for r in rows["parcels"]}
    houses, crit, passable, water_ok, crew = {}, {}, {}, {}, {}
    for r in rows["homes"]:
        houses[r["pid"]] = houses.get(r["pid"], 0) + r["houses"]
        crit[r["pid"]] = crit.get(r["pid"], False) or r["crit"]
    for r in rows["roads"]:
        passable[r["pid"]] = passable.get(r["pid"], False) or r["ok"]
    for r in rows["water"]:
        if r["ok"]:
            water_ok[r["pid"]] = True
    for r in rows["crew"]:
        crew.setdefault(r["pid"], []).append((r["ct"], r["reach"]))
    ids = sorted(parcels)
    ans = {
        "C1": [p for p in ids if houses.get(p, 0) > 0 and not passable.get(p)],
        "C2": [p for p in ids if parcels[p] in ac.HIGH_BANDS and not water_ok.get(p)],
        "C5": [p for p in ids if crit.get(p) and not any(rc for ct, rc in crew.get(p, []) if ct == "engine")],
    }
    out =  {"graph_model": gid, "row_counts": {k: len(v) for k, v in rows.items()}, "latency_s": lat,
           "queries": Q, "questions": {}}
    return out, ans, ids, parcels, houses, water_ok, crew, rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-model-id", default=os.environ.get("FABRIC_GRAPH_MODEL_ID"))
    args = parser.parse_args()
    out, ans, ids, parcels, houses, water_ok, crew, rows = main(args.graph_model_id)
    from fabric_load_chain import local_parcels
    from pinned_scoring.access_chain import Chain, generate, questions, truth
    chain = Chain(generate(local_parcels()))  # seeded ground truth, same as lakehouse load
    qs = {q.qid: q for q in questions(chain)}
    q3 = re.search(r"parcel (P\d+)", qs["C3"].text).group(1)
    ans["C3"] = sorted(ct for ct, rc in crew.get(q3, []) if not rc)
    cand = [(houses.get(p, 0), p) for p in ids if water_ok.get(p)]
    best = max((h for h, _ in cand), default=None)
    ans["C4"] = [sorted(p for h, p in cand if h == best)[0]] if cand else []
    for qid in ("C1", "C2", "C3", "C4", "C5"):
        t = truth(chain, qs[qid])
        out["questions"][qid] = {"gql_answer": ans[qid], "truth": t["parcels"], "match": ans[qid] == t["parcels"],
                                 "count": len(ans[qid])}
    out["questions"]["C4"]["gql_value"] = best
    out["questions"]["C4"]["truth_value"] = truth(chain, qs["C4"])["value"]
    out["questions"]["C4"]["match"] = (
        out["questions"]["C4"]["match"] and best == out["questions"]["C4"]["truth_value"]
    )
    out["dataset_matches"] = ids == sorted(chain.parcels) and len(rows["crew"]) == 3 * len(chain.parcels)
    out["note"] = "GQL returns traversed rows; set logic done client-side."
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out["questions"], indent=1), out["latency_s"])
    sys.exit(0 if out["dataset_matches"] and all(v["match"] for v in out["questions"].values()) else 1)
