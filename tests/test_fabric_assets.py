import re
import shutil
import subprocess
from pathlib import Path

import pytest

FABRIC = Path(__file__).resolve().parents[1] / "fabric"
DDL = (FABRIC / "sql" / "tables.sql").read_text()


def columns(table):
    body = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\) USING DELTA", DDL, re.S).group(1)
    return [line.split()[0] for line in body.splitlines() if line.strip()]


def test_ddl_has_all_tables_and_required_columns():
    assert set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", DDL)) == {
        "raw_observations", "silver_observations", "gold_receipts", "gold_policy", "authority_decisions"}
    assert {"source", "ingested_at"} <= set(columns("raw_observations"))
    assert {"provenance", "quality", "valid_until", "unit"} <= set(columns("silver_observations"))
    assert {"score", "band", "components", "reasons", "status", "policy_sha256",
            "input_sha256", "receipt_sha256"} <= set(columns("gold_receipts"))
    assert "policy_sha256" in columns("gold_policy")
    assert {"proposal_id", "state", "actor", "entity_id", "receipt_sha256", "policy_sha256",
            "prev", "hash"} <= set(columns("authority_decisions"))


def test_gold_columns_cover_transform_output():
    from datetime import datetime, timezone
    from pinned_scoring import Policy
    from pinned_scoring.fabric import FIXTURE_ROWS, load_raw, raw_to_silver, silver_to_gold
    now = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    policy = Policy.load(FABRIC.parent / "examples" / "wildfire" / "policy.json")
    silver = raw_to_silver(load_raw(FIXTURE_ROWS, "b", now), policy, now)
    gold = silver_to_gold(silver, policy, now)
    assert set(gold[0]) == set(columns("gold_receipts"))
    assert set(silver[0]) == set(columns("silver_observations"))
    assert set(load_raw(FIXTURE_ROWS, "b", now)[0]) == set(columns("raw_observations"))


def test_no_semicolons_in_comments():
    assert not [l for l in DDL.splitlines() if l.lstrip().startswith("--") and ";" in l]


def test_template_parameters_and_gitignore():
    assert (FABRIC / "parameters.template.json").exists()
    assert "parameters.json" in (FABRIC / ".gitignore").read_text()


@pytest.mark.skipif(not (shutil.which("pwsh") or shutil.which("powershell")), reason="no PowerShell")
def test_provision_whatif_makes_no_calls():
    shell = shutil.which("pwsh") or shutil.which("powershell")
    out = subprocess.run(
        [shell, "-NoProfile", "-File", str(FABRIC / "provision.ps1"), "-WhatIf"],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "No calls made" in out.stdout and "gold_receipts" in out.stdout


def test_read_rows_accepts_dataset_parcels_shape():
    from pathlib import Path
    from pinned_scoring.fabric.loader import read_rows

    rows = read_rows(Path(__file__).resolve().parents[1] / "data" / "dataset.json")
    assert len(rows) == 245
    assert all("parcel_id" in r for r in rows)


def test_livy_load_script_compiles_and_provision_has_no_notebook_job():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "fabric"
    script_text = (root / "livy" / "load_medallion.py").read_text(encoding="utf-8")
    compile(script_text, "load_medallion.py", "exec")
    assert "abfss://" in script_text and "onelake.dfs.fabric.microsoft.com" in script_text
    provision_text = (root / "provision.ps1").read_text(encoding="utf-8")
    assert "livyapi" in provision_text
    assert "WORKSPACE_ID =" in provision_text and "LAKEHOUSE_ID =" in provision_text
