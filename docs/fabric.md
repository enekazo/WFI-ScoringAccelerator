# Microsoft Fabric medallion layer

Fabric is the evidence, relationship and human-decision-log layer of the
[cloud architecture](azure-mapping.md). It is not a separate local product.
Use the [canonical installer](../deploy/README.md) with an existing active capacity
(the configured workspace is reused if present or created on that capacity if absent).
The deployment dataset is configurable (`parcelCount`, default 150); historical counts
below remain the original 50-parcel live observation, not new deployment acceptance.

## Layer mapping

| Layer | Delta table | Written by | Content |
|---|---|---|---|
| Bronze | `raw_observations` | `01_load_raw` (loader) | Evidence as received, all text, append-only, with `source`, `batch_id`, `ingested_at` |
| Silver | `silver_observations` | `02_raw_to_silver` | Typed, unit-normalised, `provenance` and `quality` (valid/suspect/invalid) + `quality_reason`. Bad rows are flagged, never dropped |
| Gold | `gold_policy` | `03_silver_to_gold` | Pinned policy versions keyed by `policy_sha256` |
| Gold | `gold_receipts` | `03_silver_to_gold` | Receipts from `scorer.score`: score, band, components, reasons, status, `policy_sha256`, `input_sha256`, `receipt_sha256` |
| Authority | `authority_decisions` | Separate human approval workflow | Append-only, hash-chained proposals and human review/approval records; approval is not execution |

DDL: `fabric/sql/tables.sql`. Logic is pure Python in `src/pinned_scoring/fabric/` (`transforms.py`, `loader.py`, `access.py`) and is unit-tested without Spark (`tests/test_fabric*.py`). Notebooks in `fabric/notebooks/` are thin wrappers.

Row schema accepted by the loader (JSON list or `{"observations": [...]}`): `parcel_id, metric, value, unit, observed_at, valid_until, provenance, source`.

## Who writes what

The deployment/Livy pipeline writes medallion and relationship data; the separate
[authority workflow](authority.md) writes human decision records. Silver is derived
from raw and Gold records the pinned policy and receipts. Raw and Gold tables are
append-only (`delta.appendOnly`). History is retained: verification must select the
current dataset and policy, not assume a global table count or latest row is sufficient.
Fabric transformation calculations are not proof that the Function-hosted scorer has
passed its [runtime release gate](azure-mapping.md#verified-baseline-and-release-gates).

## What the agent can read

The deployed receipt MCP host reads Azure Blob, not the Fabric SQL endpoint.
`GoldReader(fetch)` in `pinned_scoring.fabric.access` is a hash-checking adapter over
`gold_receipts`; it has no raw/Silver methods. That code boundary does not grant or
prove platform permissions. Gold-only SQL access must be configured and verified
separately. The independent Fabric IQ agent queries the ontology relationship surface.

### Reading Gold through the SQL endpoint (Entra auth)

`pinned_scoring.fabric.sql_endpoint.fetcher(server, database)` returns a callable for `GoldReader`. It gets an Entra token with `DefaultAzureCredential` (scope `https://database.windows.net/.default`), connects with `pyodbc` and ODBC Driver 18 (optional installs), and runs one `SELECT` on `gold_receipts`. `server` is the SQL connection string of the lakehouse's SQL analytics endpoint. See [Entra authentication](https://learn.microsoft.com/fabric/data-warehouse/entra-id-authentication) and [connectivity](https://learn.microsoft.com/fabric/data-warehouse/connectivity). Tested with a fake connection (`tests/test_microsoft_paths.py`); **not yet run against the live endpoint**. Full service mapping: [azure-mapping.md](azure-mapping.md).

## Deploy

Follow [deploy/README.md](../deploy/README.md), which owns the full install instructions.
The full installer generates resolved Fabric parameters from the same private
configuration, dataset and pinned policy as the receipt deployment. Do not independently
select the committed 50-parcel inputs for a differently sized deployment.

The Fabric layer creates the lakehouse if absent, stages inputs and Python sources in
OneLake, and uses Livy to create/load medallion and relationship tables. DDL uses
`IF NOT EXISTS` and loads deduplicate by their row/hash keys. This is not a guarantee
that every interrupted deployment or changed policy is automatically reconciled;
inspect [current deployment verification](testing.md#independent-post-deployment-checks).

No wheel or Fabric Environment is needed: the Livy load stages the package sources from `Files/code` onto the driver. The notebooks in `fabric/notebooks/` remain as an alternative for interactive use.

## Live run (verified)

**Historical 50-parcel baseline.** The following counts are evidence about the recorded
run only. They are not acceptance thresholds for the configurable installer.

`./fabric/provision.ps1` was run against an existing workspace `PinnedScore` (Central US) and lakehouse `pinned_scoring_lh`, with `az login` identity only (no keys). Row counts after the load, and unchanged after a second run (idempotence):

| Table | Rows |
|---|---|
| `raw_observations` | 245 |
| `silver_observations` | 245 |
| `gold_policy` | 1 |
| `gold_receipts` | 50 (32 `scored`, 18 `incomplete`) |

Spot check: the Gold receipt for `P007` is `scored`, score 58.8, band `high`, `receipt_sha256` `161b004a…8d99`, identical to `docs/evidence/receipts/P007.json`. The earlier caveat that 7 receipt hashes differed (P002, P004, P025, P026, P032, P037, P043) is **resolved**: the medallion transforms now reproduce all 50 committed receipt hashes, locked by `tests/test_medallion_matches_evidence.py`. The causes were a unit-fault guard in `_normalise_unit` (a mislabelled fraction is no longer silently converted) and the source quality flag, which the pipeline had dropped and now carries through.

**Historical refresh caveat:** the counts above predated the raw `quality` column.
Do not infer the current state of your workspace from that record. Inspect schema and
matching hashes first; use the installer only when a deliberate data update is required.
Receipt freshness is evaluated at the dataset's fixed `as_of`, not today's wall clock.

Not verified: live end-to-end workspace creation on a fresh target capacity (implemented and covered by offline fake-cloud tests), granting the agent identity SQL-endpoint read on gold only, reading gold through `GoldReader` against the live SQL endpoint, and the Fabric notebook-job path. The earlier notebook-job approach failed live with "System cancelled the Spark session due to statement execution failures" (cause undiagnosed) and was replaced by Livy. Livy session start can take several minutes on a cold capacity; leftover idle sessions block new ones, so delete stale sessions if start stalls.

## Known limitations

- The provisioner does not create tenants, subscriptions or capacities (it creates the configured Fabric workspace only when an existing active capacity is configured and `workspaceId` is not pinned to a missing ID).
  Azure RBAC is configured by the Azure deployment; this is not proof of Fabric
  SQL-endpoint gold-only grants.
- The load collects to the driver; fine for the synthetic dataset, not for large volumes.
- Unit conversions are a small fixed table (m/s, mph, kg/m2, fraction); unknown unit mismatches pass through and are reported by the scorer as `unit:<metric>`.
- Silver is append-only with idempotence by `raw_id`; re-validation under a changed policy is not modelled.
