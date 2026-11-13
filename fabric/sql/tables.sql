-- Medallion tables for the pinned scoring accelerator (Fabric Lakehouse, Delta, Spark SQL).
-- Statements end with a semicolon. Never put one inside comments or string literals.

-- BRONZE: evidence exactly as received. Append-only. never updated or deleted.
CREATE TABLE IF NOT EXISTS raw_observations (
  raw_id       STRING NOT NULL COMMENT 'sha256 of the canonical payload',
  parcel_id    STRING,
  metric       STRING,
  value        STRING COMMENT 'as received, not parsed',
  unit         STRING,
  observed_at  STRING COMMENT 'as received, not parsed',
  valid_until  STRING COMMENT 'as received, not parsed',
  provenance   STRING,
  source       STRING,
  quality      STRING COMMENT 'quality flag declared by the source, as received',
  batch_id     STRING NOT NULL,
  ingested_at  TIMESTAMP NOT NULL
) USING DELTA
TBLPROPERTIES ('delta.appendOnly' = 'true');

-- SILVER: validated and typed. Bad rows are flagged (quality), never dropped.
CREATE TABLE IF NOT EXISTS silver_observations (
  raw_id          STRING NOT NULL,
  entity_id       STRING,
  metric          STRING,
  value           DECIMAL(38, 10),
  unit            STRING COMMENT 'normalised to the policy unit where a conversion is known',
  observed_at     TIMESTAMP,
  valid_until     TIMESTAMP,
  provenance      STRING COMMENT 'measured | generated | fixture',
  quality         STRING NOT NULL COMMENT 'valid | suspect | invalid',
  quality_reason  STRING,
  source          STRING,
  validated_at    TIMESTAMP NOT NULL
) USING DELTA;

-- GOLD: pinned policies by hash.
CREATE TABLE IF NOT EXISTS gold_policy (
  policy_sha256  STRING NOT NULL,
  name           STRING NOT NULL,
  version        STRING NOT NULL,
  policy_json    STRING NOT NULL,
  pinned_at      TIMESTAMP NOT NULL
) USING DELTA
TBLPROPERTIES ('delta.appendOnly' = 'true');

-- GOLD: self-verifying receipts. components and reasons are JSON arrays stored as strings.
-- A scored/incomplete receipt is appended per run. readers take the latest scored_at per entity.
CREATE TABLE IF NOT EXISTS gold_receipts (
  receipt_sha256  STRING NOT NULL,
  entity_id       STRING NOT NULL,
  status          STRING NOT NULL COMMENT 'scored | incomplete',
  score           STRING COMMENT 'decimal as text, null when incomplete',
  band            STRING,
  components      STRING NOT NULL,
  reasons         STRING NOT NULL,
  as_of           STRING NOT NULL COMMENT 'ISO-8601 text exactly as hashed',
  policy_sha256   STRING NOT NULL,
  input_sha256    STRING NOT NULL,
  scored_at       TIMESTAMP NOT NULL
) USING DELTA
TBLPROPERTIES ('delta.appendOnly' = 'true');

-- AUTHORITY: append-only, hash-chained decision log (proposed -> reviewed -> approved / rejected).
CREATE TABLE IF NOT EXISTS authority_decisions (
  proposal_id     STRING NOT NULL,
  state           STRING NOT NULL COMMENT 'proposed | reviewed | approved | rejected',
  actor           STRING NOT NULL,
  entity_id       STRING NOT NULL,
  receipt_sha256  STRING NOT NULL,
  policy_sha256   STRING NOT NULL,
  narration       STRING,
  note            STRING,
  at              TIMESTAMP NOT NULL,
  prev            STRING NOT NULL,
  hash            STRING NOT NULL,
  mode            STRING COMMENT 'required | single_person'
) USING DELTA
TBLPROPERTIES ('delta.appendOnly' = 'true');
