# Fabric notebook: load synthetic dataset JSON -> raw_observations. Thin wrapper.
import uuid
from datetime import datetime, timezone

from pinned_scoring.fabric import load_raw_from_file

DATASET = "/lakehouse/default/Files/dataset.json"
rows = load_raw_from_file(DATASET, batch_id=str(uuid.uuid4()), ingested_at=datetime.now(timezone.utc))
if rows:
    spark.createDataFrame(rows).write.mode("append").saveAsTable("raw_observations")  # noqa: F821
