"""Add the `quality` column to the live raw_observations table (schema drift fix), then re-run provision.ps1."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pinned_scoring.fabric.livy import LivySession  # noqa: E402

WORKSPACE_ID = "00000000-0000-4000-8000-000000000019"  # PinnedScore
LAKEHOUSE_ID = "00000000-0000-4000-8000-000000000015"  # pinned_scoring_lh

if __name__ == "__main__":
    with LivySession(WORKSPACE_ID, LAKEHOUSE_ID) as s:
        for t in ("raw_observations", "silver_observations", "gold_policy", "gold_receipts"):
            print(t, s.run(f"print([f.name for f in spark.table('{t}').schema.fields], spark.table('{t}').count())"))
        cols = s.run("print('quality' in spark.table('raw_observations').columns)").strip()
        if cols == "False":
            s.run("spark.sql('ALTER TABLE raw_observations ADD COLUMNS (quality string)')")
            print("added quality column")
