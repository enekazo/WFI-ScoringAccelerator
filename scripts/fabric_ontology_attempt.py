"""Try to create a Fabric Ontology item (preview) via REST and record the exact outcome."""

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

WS = "00000000-0000-4000-8000-000000000019"
OUT = Path(__file__).resolve().parents[1] / "docs/evidence/scenario-2-ontology/ontology_attempt.json"


def token():
    return subprocess.run(
        "az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv",
        capture_output=True, text=True, shell=True, check=True).stdout.strip()


def call(method, path, body=None):
    req = urllib.request.Request(
        f"https://api.fabric.microsoft.com/v1{path}",
        data=None if body is None else json.dumps(body).encode(), method=method,
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req)
        return {"status": r.status, "body": r.read().decode()[:1500], "location": r.headers.get("Location")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode()[:1500]}


if __name__ == "__main__":
    log = {
        "create_ontology": call("POST", f"/workspaces/{WS}/ontologies", {"displayName": "pinned_scoring_ontology"}),
        "create_via_items": call("POST", f"/workspaces/{WS}/items",
                                 {"displayName": "pinned_scoring_ontology_items", "type": "Ontology"}),
        "list_ontologies": call("GET", f"/workspaces/{WS}/ontologies"),
    }
    OUT.write_text(json.dumps(log, indent=2))
    print(json.dumps(log, indent=2))
