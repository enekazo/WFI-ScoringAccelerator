"""Refresh the SQL endpoint, read the graph tables back and compare with data/graph.json."""

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pinned_scoring.fabric.graph_store import graph_from_fabric  # noqa: E402
from pinned_scoring.fabric.sql_endpoint import connect_auto  # noqa: E402
from pinned_scoring.graph import read_graph  # noqa: E402

WS = "00000000-0000-4000-8000-000000000019"
EP = "00000000-0000-4000-8000-000000000023"
SERVER = "sql.example.invalid"
DB = "pinned_scoring_lh"
OUT = ROOT / "docs" / "evidence" / "scenario-2-ontology" / "graph_readback.json"


def token():
    return subprocess.run(
        "az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv",
        capture_output=True, text=True, shell=True, check=True).stdout.strip()


def refresh():
    req = urllib.request.Request(
        f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/sqlEndpoints/{EP}/refreshMetadata?preview=true",
        data=b"{}", method="POST",
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    return urllib.request.urlopen(req).status


if __name__ == "__main__":
    print("refresh", refresh())
    local = read_graph()
    result = {}
    for attempt in range(8):
        time.sleep(30)
        try:
            g = graph_from_fabric(connect_auto, SERVER, DB)
            result = {"equal_to_local": g == local, "parcels": len(g.parcels), "assets": len(g.assets),
                      "feeders": len(g.feeders), "customer_groups": len(g.customer_groups),
                      "mitigations": len(g.mitigations)}
            if result["equal_to_local"]:
                break
        except Exception as e:  # endpoint lag
            result = {"error": repr(e)[:300]}
    OUT.write_text(json.dumps(result, indent=2))
    print(result)
