"""Empty the four medallion tables of the demo lakehouse so provision.ps1 can reload them once.

The loader appends raw rows with a fresh batch id, so running provision.ps1 twice duplicates
observations and changes input hashes. Run this, then provision.ps1, to get a clean single load.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pinned_scoring.fabric.livy import LivySession  # noqa: E402

WORKSPACE_ID = "00000000-0000-4000-8000-000000000019"  # PinnedScore
LAKEHOUSE_ID = "00000000-0000-4000-8000-000000000015"  # pinned_scoring_lh

if __name__ == "__main__":
    with LivySession(WORKSPACE_ID, LAKEHOUSE_ID) as s:
        for t in ("raw_observations", "silver_observations", "gold_policy", "gold_receipts"):
            # tables are append-only by design (no DELETE), so the demo reset drops and recreates them
            s.run(f"spark.sql('DROP TABLE IF EXISTS {t}')")
            print("dropped", t)
