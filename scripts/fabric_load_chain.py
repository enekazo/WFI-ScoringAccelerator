"""Chain tables: generate (seeded, synthetic), load to the Fabric lakehouse, read back and compare.

  python scripts/fabric_load_chain.py            # generate + overwrite tables + read back + compare
  python scripts/fabric_load_chain.py --local    # print the local ground truth only (no cloud)
Writes docs/evidence/scenario-2-ontology/chain_tables.json.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pinned_scoring.access_chain import TABLES, Chain, generate, questions, truth  # noqa: E402

WORKSPACE_ID = os.environ.get("FABRIC_WORKSPACE_ID", "00000000-0000-4000-8000-000000000019")  # PinnedScore
LAKEHOUSE_ID = os.environ.get("FABRIC_LAKEHOUSE_ID", "00000000-0000-4000-8000-000000000015")  # pinned_scoring_lh
SERVER = os.environ.get("FABRIC_SQL_SERVER", "sql.example.invalid")
DB = os.environ.get("FABRIC_SQL_DATABASE", "pinned_scoring_lh")
OUT = Path(os.environ.get("PINNED_VERIFICATION_DIR", str(ROOT / "docs/evidence/scenario-2-ontology"))) / "chain_tables.json"
SQL_TYPES = {"passable": "BOOLEAN", "has_critical_facility": "BOOLEAN", "reachable": "BOOLEAN",
             "houses": "INT", "distance_to_houses_m": "INT", "capacity_l": "INT", "seed": "INT",
             "score": "STRING"}


def local_parcels(receipts_dir=None) -> list:
    out = []
    directory = Path(receipts_dir or os.environ.get("PINNED_RECEIPTS_DIR") or ROOT / "docs/evidence/receipts")
    for f in sorted(directory.glob("P*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        out.append({"parcel_id": r["entity_id"], "status": r["status"], "band": r["band"],
                    "score": None if r.get("score") is None else str(r["score"])})
    if not out:
        raise ValueError(f"No deployment receipts found in {directory}.")
    return out


def lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


class _Sql:
    def __init__(self, s):
        self.s = s

    def sql(self, stmt):
        self.s.run(f"spark.sql({stmt!r})")


def load(session, tables):
    session = _Sql(session)
    for name, cols in TABLES.items():
        ddl = ", ".join(f"{c} {SQL_TYPES.get(c, 'STRING')}" for c in cols)
        session.sql(f"DROP TABLE IF EXISTS {name}")
        session.sql(f"CREATE TABLE {name} ({ddl}) USING DELTA")
        rows = tables[name]
        for i in range(0, len(rows), 100):
            vals = ",".join("(" + ",".join(lit(r[c]) for c in cols) + ")" for r in rows[i:i + 100])
            session.sql(f"INSERT INTO {name} VALUES {vals}")


def fabric_tables() -> dict:
    from pinned_scoring.fabric.sql_endpoint import connect_auto
    conn = connect_auto(SERVER, DB)
    out = {}
    try:
        for name, cols in TABLES.items():
            cur = conn.cursor()
            cur.execute(f"SELECT {', '.join(cols)} FROM {name}")
            out[name] = [dict(zip(cols, (_py(v) for v in row))) for row in cur.fetchall()]
    finally:
        conn.close()
    return out


def _py(v):
    if type(v).__name__ == "Decimal":
        return int(v)
    return v


def norm(rows, cols):
    return sorted(json.dumps({c: r[c] for c in cols}, sort_keys=True, default=str) for r in rows)


def resolve_sql_endpoint_id(workspace_id: str, lakehouse_id: str, token: str) -> str | None:
    sql_ep_id = os.environ.get("FABRIC_SQL_ENDPOINT_ID")
    if sql_ep_id:
        return sql_ep_id
    import urllib.request
    req = urllib.request.Request(
        f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/lakehouses/{lakehouse_id}",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "x-ms-fabric-skill": "fabriciq-ontology-cli",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return ((data.get("properties") or {}).get("sqlEndpointProperties") or {}).get("id")


def refresh_sql_endpoint_metadata(workspace_id: str, lakehouse_id: str) -> dict:
    import subprocess
    import time
    import urllib.request

    tok = os.environ.get("FABRIC_ACCESS_TOKEN") or subprocess.run(
        "az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv",
        capture_output=True, text=True, shell=True,
    ).stdout.strip()
    if not tok:
        return {"status": "skipped", "reason": "no_fabric_token"}
    try:
        sql_ep_id = resolve_sql_endpoint_id(workspace_id, lakehouse_id, tok)
        if not sql_ep_id:
            return {"status": "skipped", "reason": "sql_endpoint_id_not_found"}
        req = urllib.request.Request(
            f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/sqlEndpoints/"
            f"{sql_ep_id}/refreshMetadata?preview=true",
            data=b"{}",
            method="POST",
            headers={
                "Authorization": "Bearer " + tok,
                "Content-Type": "application/json",
                "x-ms-fabric-skill": "fabriciq-ontology-cli",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            loc = resp.headers.get("Location")
            status_code = resp.status
        if status_code == 202 and loc:
            for _ in range(20):
                time.sleep(3)
                poll_req = urllib.request.Request(
                    loc,
                    headers={
                        "Authorization": "Bearer " + tok,
                        "x-ms-fabric-skill": "fabriciq-ontology-cli",
                    },
                )
                with urllib.request.urlopen(poll_req, timeout=30) as poll_resp:
                    body = json.loads(poll_resp.read().decode("utf-8") or "{}")
                st = body.get("status")
                if st in ("Succeeded", "Failed", "Completed"):
                    return {"status": st, "sql_endpoint_id": sql_ep_id}
        return {"status": status_code, "sql_endpoint_id": sql_ep_id}
    except Exception as exc:  # noqa: BLE001
        print("refresh:", exc)
        return {"status": "error", "error": str(exc)[:200]}


if __name__ == "__main__":
    tables = generate(local_parcels())
    chain = Chain(tables)
    qs = questions(chain)
    gt = {q.qid: {"question": q.text, **truth(chain, q)} for q in qs}
    rep = {"seed": tables["parcel"][0]["seed"], "rows": {k: len(v) for k, v in tables.items()},
           "ground_truth": gt}
    if "--local" not in sys.argv:
        from pinned_scoring.fabric.livy import LivySession
        with LivySession(WORKSPACE_ID, LAKEHOUSE_ID) as s:
            load(s, tables)
        if "--no-refresh-metadata" not in sys.argv:
            rep["sql_metadata_refresh"] = refresh_sql_endpoint_metadata(WORKSPACE_ID, LAKEHOUSE_ID)
        print("loaded; SQL endpoint metadata refresh triggered")
    if "--local" not in sys.argv and "--no-verify" not in sys.argv:
        import time
        eq = {}
        for _ in range(12):
            time.sleep(15)
            try:
                got = fabric_tables()
                eq = {k: norm(got[k], TABLES[k]) == norm(tables[k], TABLES[k]) for k in TABLES}
                if all(eq.values()):
                    break
            except Exception as exc:  # noqa: BLE001
                eq = {"error": str(exc)[:200]}
        rep["fabric_readback_equal_to_local"] = eq
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    print(json.dumps(rep, indent=1, default=str))
