# Fabric notebook: silver -> gold. Thin wrapper; logic lives in pinned_scoring.fabric.
from datetime import datetime, timezone
from pathlib import Path

from pinned_scoring import Policy
from pinned_scoring.fabric import policy_to_gold, silver_to_gold

POLICY = Policy.load(Path("/lakehouse/default/Files/policy.json"))
now = datetime.now(timezone.utc)

pinned = {r["policy_sha256"] for r in spark.table("gold_policy").collect()}  # noqa: F821
if POLICY.sha256 not in pinned:
    spark.createDataFrame([policy_to_gold(POLICY, now)]).write.mode("append").saveAsTable("gold_policy")  # noqa: F821

silver = [r.asDict() for r in spark.table("silver_observations").collect()]  # noqa: F821
gold = silver_to_gold(silver, POLICY, as_of=now)
if gold:
    spark.createDataFrame(gold).write.mode("append").saveAsTable("gold_receipts")  # noqa: F821
