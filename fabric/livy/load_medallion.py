# Run by provision.ps1 through the Livy API (pyspark statement). Idempotent medallion load.
# Expects Files/code/pinned_scoring, Files/policy.json and Files/dataset.json in the default lakehouse.
import json
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

FILES = Path("/lakehouse/default/Files")
if not (FILES / "code").exists():
    # Livy sessions do not always mount the default lakehouse; stage the files on the driver instead.
    import shutil
    from notebookutils import mssparkutils  # noqa: E402

    ws_id = globals().get("WORKSPACE_ID") or spark.conf.get("trident.workspace.id", "")  # noqa: F821
    lh_id = globals().get("LAKEHOUSE_ID") or spark.conf.get("trident.lakehouse.id", "")  # noqa: F821
    remote_files = (
        f"abfss://{ws_id}@onelake.dfs.fabric.microsoft.com/{lh_id}/Files"
        if ws_id and lh_id
        else "Files"
    )
    shutil.rmtree("/tmp/lh", ignore_errors=True)
    mssparkutils.fs.cp(f"{remote_files}/code", "file:/tmp/lh/code", True)
    mssparkutils.fs.cp(f"{remote_files}/policy.json", "file:/tmp/lh/policy.json", True)
    mssparkutils.fs.cp(f"{remote_files}/dataset.json", "file:/tmp/lh/dataset.json", True)
    FILES = Path("/tmp/lh")
sys.path.insert(0, str(FILES / "code"))

from pinned_scoring import Policy  # noqa: E402
from pinned_scoring.fabric import load_raw, policy_to_gold, raw_to_silver, silver_to_gold  # noqa: E402
from pinned_scoring.fabric.loader import read_rows  # noqa: E402

spark.conf.set("spark.sql.session.timeZone", "UTC")  # noqa: F821
policy = Policy.load(FILES / "policy.json")
dataset = json.loads((FILES / "dataset.json").read_text(encoding="utf-8"))
# The dataset's fixed clock keeps receipts reproducible; plain row lists fall back to now.
now = datetime.now(timezone.utc)
as_of = datetime.fromisoformat(dataset["as_of"]) if isinstance(dataset, dict) and "as_of" in dataset else now
ISO = "yyyy-MM-dd'T'HH:mm:ss'+00:00'"


def text_df(rows, columns):
    schema = StructType([StructField(c, StringType(), True) for c in columns])
    return spark.createDataFrame([tuple(r.get(c) for c in columns) for r in rows], schema)  # noqa: F821


def existing(table, column):
    return {r[0] for r in spark.table(table).select(column).collect()}  # noqa: F821


# Bronze: skip rows already present so re-runs do not duplicate.
raw = load_raw(read_rows(FILES / "dataset.json"), str(uuid.uuid4()), now)
new_raw = [r for r in raw if r["raw_id"] not in existing("raw_observations", "raw_id")]
if new_raw:
    text_df(new_raw, list(new_raw[0])).withColumn("ingested_at", F.to_timestamp("ingested_at")) \
        .write.mode("append").saveAsTable("raw_observations")

# Derive this deployment's receipts only; previous deployment batches remain in the audit tables.
silver = raw_to_silver(raw, policy, as_of)
new_silver = [s for s in silver if s["raw_id"] not in existing("silver_observations", "raw_id")]
if new_silver:
    df = text_df(new_silver, list(new_silver[0]))
    for c in ("observed_at", "valid_until", "validated_at"):
        df = df.withColumn(c, F.to_timestamp(c))
    df.withColumn("value", F.col("value").cast("decimal(38,10)")).write.mode("append").saveAsTable("silver_observations")

# Gold: pin the policy once, then append receipts not already stored (by receipt hash).
if policy.sha256 not in existing("gold_policy", "policy_sha256"):
    text_df([policy_to_gold(policy, now)], ["policy_sha256", "name", "version", "policy_json", "pinned_at"]) \
        .withColumn("pinned_at", F.to_timestamp("pinned_at")).write.mode("append").saveAsTable("gold_policy")

gold = silver_to_gold(silver, policy, as_of, scored_at=now)
new_gold = [g for g in gold if g["receipt_sha256"] not in existing("gold_receipts", "receipt_sha256")]
if new_gold:
    text_df(new_gold, list(new_gold[0])).withColumn("scored_at", F.to_timestamp("scored_at")) \
        .write.mode("append").saveAsTable("gold_receipts")

for t in ("raw_observations", "silver_observations", "gold_policy", "gold_receipts", "authority_decisions"):
    print(t, spark.table(t).count())  # noqa: F821
