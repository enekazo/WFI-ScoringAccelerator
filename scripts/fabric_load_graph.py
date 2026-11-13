"""Load the synthetic graph (data/graph.json) into the lakehouse as graph_nodes/graph_edges/... tables."""

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pinned_scoring.fabric.graph_store import load_via_livy  # noqa: E402
from pinned_scoring.fabric.livy import LivySession  # noqa: E402
from pinned_scoring.graph import DEFAULT_GRAPH_PATH, read_graph  # noqa: E402

WORKSPACE_ID = os.environ.get("FABRIC_WORKSPACE_ID", "")
LAKEHOUSE_ID = os.environ.get("FABRIC_LAKEHOUSE_ID", "")

if __name__ == "__main__":
    if not WORKSPACE_ID or not LAKEHOUSE_ID:
        raise SystemExit("Set FABRIC_WORKSPACE_ID and FABRIC_LAKEHOUSE_ID to the deployment target.")
    with LivySession(WORKSPACE_ID, LAKEHOUSE_ID) as s:
        print(load_via_livy(s, read_graph(os.environ.get("PINNED_GRAPH_PATH") or DEFAULT_GRAPH_PATH)))
