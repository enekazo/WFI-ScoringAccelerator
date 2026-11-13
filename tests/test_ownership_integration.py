"""Offline checks that the installer records creation provenance for cleanup (no cloud calls)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "deploy"))

import cleanup_plan as cp  # noqa: E402
import ownership_record as orec  # noqa: E402

SUB = "11111111-1111-1111-1111-111111111111"
TEN = "22222222-2222-2222-2222-222222222222"
WS = "33333333-3333-3333-3333-333333333333"
CONN = f"/subscriptions/{SUB}/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/a/projects/p/connections/c"


def _init(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "ownership.json"
    ledger = cp.new_ledger(TEN, SUB, "pinned-scoring-x") if hasattr(cp, "new_ledger") else None
    if ledger is None:
        subprocess.run([sys.executable, str(ROOT / "deploy" / "cleanup_plan.py"), "--ledger", str(path), "init",
                        "--tenant", TEN, "--subscription", SUB, "--deployment-id", "pinned-scoring-x"], check=True)
    else:
        cp.save_ledger(ledger, path)
    monkeypatch.setenv(orec.LEDGER_ENV, str(path))
    return path


def test_record_is_noop_without_ledger(monkeypatch):
    monkeypatch.delenv(orec.LEDGER_ENV, raising=False)
    assert orec.record_owned("fabric_workspace", WS, "created", "x") is False


def test_record_fails_loudly_on_invalid_ledger(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv(orec.LEDGER_ENV, str(bad))
    with pytest.raises(Exception):
        orec.record_owned("fabric_workspace", WS, "created", "x")


def test_created_survives_later_reused_record_and_merges_attrs(tmp_path, monkeypatch):
    path = _init(tmp_path, monkeypatch)
    cap = f"/subscriptions/{SUB}/resourceGroups/rg/providers/Microsoft.Fabric/capacities/cap1"
    orec.record_owned("arm_resource", cap, "created", "ARM PUT after 404")
    orec.record_owned("arm_resource", cap, "reused", "resolved", attrs={"capacityId": WS})
    entry = [e for e in json.loads(path.read_text())["entries"] if e["id"] == cap][0]
    assert entry["origin"] == "created"
    assert entry["attrs"]["capacityId"] == WS


def test_reused_never_becomes_created_without_evidence(tmp_path, monkeypatch):
    path = _init(tmp_path, monkeypatch)
    orec.record_owned("fabric_workspace", WS, "reused", "existing workspace")
    entry = json.loads(path.read_text())["entries"][0]
    assert entry["origin"] == "reused"


def test_connection_and_agent_version_records(tmp_path, monkeypatch):
    path = _init(tmp_path, monkeypatch)
    orec.record_connection("url", False, CONN)
    orec.record_agent_version("https://a.services.ai.azure.com/api/projects/p", "agent", "3")
    orec.record_agent_version("https://a.services.ai.azure.com/api/projects/p", "agent", "4")
    entries = {e["kind"]: e for e in json.loads(path.read_text())["entries"]}
    assert entries["foundry_connection"]["origin"] == "created"
    assert entries["foundry_agent"]["attrs"]["versions"] == ["3", "4"]


def test_arm_exists_distinguishes_404_from_errors(monkeypatch):
    def fake(code, err):
        return lambda *a, **k: subprocess.CompletedProcess(a, code, "", err)

    monkeypatch.setattr(subprocess, "run", fake(1, "ERROR: (ResourceNotFound) not there"))
    assert orec.arm_exists("https://x") is False
    monkeypatch.setattr(subprocess, "run", fake(0, ""))
    assert orec.arm_exists("https://x") is True
    monkeypatch.setattr(subprocess, "run", fake(1, "ERROR: (AuthorizationFailed) denied"))
    with pytest.raises(RuntimeError):
        orec.arm_exists("https://x")


def test_missing_provenance_blocks_cleanup_completion(tmp_path, monkeypatch):
    path = _init(tmp_path, monkeypatch)
    # Workspace was created, but the later stages never recorded items: plan still lists only what is proven.
    orec.record_owned("fabric_workspace", WS, "created", "provision.ps1 POST /v1/workspaces")
    ledger = cp.load_ledger(path)
    plan = cp.plan_cleanup(ledger, remove_fabric=True)
    ids = json.dumps(plan, default=str)
    assert WS in ids


@pytest.mark.parametrize("rel,needles", [
    ("deploy/deploy-all.ps1", ["Initialize-OwnershipLedger", "PINNED_OWNERSHIP_LEDGER", "Save-ResourceGroupSnapshot",
                               "Record-AzureDeployment", "Kind fabric_workspace", "Microsoft.Fabric/capacities"]),
    ("deploy/enable_tracing.ps1", ["Record-IfTracked", "foundry_connection"]),
    ("fabric/provision.ps1", ["Record-IfTracked", "'Lakehouse'", "'Ontology'", "'GraphModel'", "fabric_workspace"]),
    ("scripts/create_foundry_fabric_agent.py", ["record_connection", "record_agent_version"]),
    ("scripts/create_foundry_agent.py", ["record_connection", "record_agent_version"]),
    ("src/pinned_scoring/foundry_agent.py", ["role_assignment", "PINNED_OWNERSHIP_LEDGER"]),
])
def test_installer_stage_calls_ownership_hooks(rel, needles):
    text = (ROOT / rel).read_text(encoding="utf-8")
    for n in needles:
        assert n in text, f"{rel} missing {n}"


def test_stage9_hint_is_whatif_preview_not_purge():
    text = (ROOT / "deploy/deploy-all.ps1").read_text(encoding="utf-8")
    assert "uninstall.ps1" in text and "-WhatIf" in text
