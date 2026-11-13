# Fabric notebook: bronze -> silver. Thin wrapper; logic lives in pinned_scoring.fabric.
# Attach the pinned_scoring wheel to the notebook Environment, and a default lakehouse.
from datetime import datetime, timezone
from pathlib import Path

from pinned_scoring import Policy
from pinned_scoring.fabric import raw_to_silver

POLICY = Policy.load(Path("/lakehouse/default/Files/policy.json"))  # pinned policy file
now = datetime.now(timezone.utc)

raw = [r.asDict() for r in spark.table("raw_observations").collect()]  # noqa: F821
silver = raw_to_silver(raw, POLICY, now)
done = {r["raw_id"] for r in spark.table("silver_observations").collect()}  # noqa: F821
new = [s for s in silver if s["raw_id"] not in done]
if new:
    spark.createDataFrame(new).write.mode("append").saveAsTable("silver_observations")  # noqa: F821
