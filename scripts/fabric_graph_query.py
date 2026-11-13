"""Query the Fabric GraphModel auto-created with the ontology via the documented GQL Query API.

POST /v1/workspaces/{ws}/graphModels/{id}/executeQuery?beta=true  (Entra token, no keys).
Records exact status/body to docs/evidence/scenario-2-ontology/fabric_graph_query.json.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

WS = os.environ.get("FABRIC_WORKSPACE_ID", "00000000-0000-4000-8000-000000000019")
BASE = "https://api.fabric.microsoft.com/v1"
OUT = "docs/evidence/scenario-2-ontology/fabric_graph_query.json"


def token():
    return subprocess.check_output(
        "az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv",
        shell=True, text=True).strip()


def call(method, path, body=None, max_retries=4):
    for attempt in range(max_retries):
        req = urllib.request.Request(
            BASE + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json",
                     "Accept": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, r.read().decode(), time.time() - t0
        except urllib.error.HTTPError as e:
            body_text = e.read().decode()
            dt = time.time() - t0
            if e.code == 429 and attempt < max_retries - 1:
                retry_after = e.headers.get("Retry-After") if e.headers else None
                wait_s = int(retry_after) if (retry_after and str(retry_after).isdigit()) else 15 * (attempt + 1)
                time.sleep(wait_s)
                continue
            return e.code, body_text, dt


def main():
    s, t, _ = call("GET", f"/workspaces/{WS}/graphModels")
    models = json.loads(t).get("value", []) if s == 200 else []
    rec = {"list_status": s, "models": [(m["id"], m["displayName"]) for m in models], "queries": []}
    queries = sys.argv[1:] or ["MATCH (n) RETURN count(n) AS n"]
    if models:
        gid = models[0]["id"]
        for q in queries:
            s, t, dt = call("POST", f"/workspaces/{WS}/graphModels/{gid}/executeQuery?beta=true", {"query": q})
            rec["queries"].append({"query": q, "status": s, "seconds": round(dt, 2), "body": t[:1500]})
            print(s, t[:600])
    with open(OUT, "w") as f:
        json.dump(rec, f, indent=1)


if __name__ == "__main__":
    main()
