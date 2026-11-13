"""Attach the Fabric ontology to a Fabric data agent via REST and record exact results.

Thin wrapper over documented Fabric REST (Data Agent definition). No graph code.
Usage: python scripts/fabric_data_agent_ontology.py [--type ontology]
"""
import argparse
import base64
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

WS = "00000000-0000-4000-8000-000000000019"
ONTOLOGY_ID = "00000000-0000-4000-8000-000000000002"
AGENT_ID = "00000000-0000-4000-8000-000000000024"
OUT = Path("docs/evidence/scenario-2-ontology/data_agent_ontology.json")
API = "https://api.fabric.microsoft.com/v1"


def token():
    return subprocess.check_output(
        "az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv",
        shell=True, text=True).strip()


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def lro(status, headers, text, want_result=False):
    if status != 202:
        return status, text
    loc = headers.get("Location") or headers.get("location")
    path = "/operations/" + loc.rstrip("/").split("/")[-1]
    for _ in range(60):
        time.sleep(3)
        s, _h, t = call("GET", path)
        state = json.loads(t).get("status") if s == 200 and t.startswith("{") else None
        if state == "Failed":
            return 500, t
        if state == "Succeeded":
            if want_result:
                s, _h, t = call("GET", path + "/result")
            return s, t
    return 408, "operation did not finish in 180s"


def part(path, obj):
    return {"path": path, "payloadType": "InlineBase64",
            "payload": base64.b64encode(json.dumps(obj, indent=2).encode()).decode()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", default="ontology")
    a = ap.parse_args()
    parts = [
        part("Files/Config/data_agent.json", {"$schema": "2.1.0"}),
        part("Files/Config/draft/stage_config.json", {
            "$schema": "1.0.0",
            "aiInstructions": "Answer wildfire access-chain questions using only the ontology entities and relationships. Read-only."}),
        part(f"Files/Config/draft/{a.type}-pinned_scoring_ontology/datasource.json", {
            "$schema": "1.0.0", "artifactId": ONTOLOGY_ID, "workspaceId": WS,
            "displayName": "pinned_scoring_ontology", "type": a.type, "elements": []}),
    ]
    status, text = lro(*call("POST", f"/workspaces/{WS}/dataAgents/{AGENT_ID}/updateDefinition",
                             {"definition": {"parts": parts}}))
    res = {"type_tried": a.type, "update_status": status, "update_body": text[:1500]}
    status, text = lro(*call("POST", f"/workspaces/{WS}/dataAgents/{AGENT_ID}/getDefinition"), want_result=True)
    try:
        res["readback_parts"] = [p["path"] for p in json.loads(text)["definition"]["parts"]]
    except Exception:
        res["readback_raw"] = text[:800]
    res["readback_status"] = status
    OUT.write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
