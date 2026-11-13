"""Offline unit tests for the one-click installer (deploy/deploy-all.ps1, deploy/deploy_plan.py,
and scripts/fabric_graph_model_fix.py).

Validates:
- Parameter validation (GUIDs, template placeholders, prefix rules, capacity SKU >= F4, policy existence)
- Composite key ['parcel_id', 'crew_type'] set in all 3 places (ontology entity TMDL, GraphModel
  graphType.json node, GraphModel graphDefinition.json edge)
- Foundry IQ connection body & URL builder (RemoteTool, UserEntraToken, audience
  https://api.fabric.microsoft.com, group GenericProtocol, metadata.type fabric_iq_preview,
  isSharedToAll False; never Fabric data agent route)
- Retry-After header parsing for HTTP 429 / 202
- Deterministic 9-step ordering, manual-only graph refresh (no schedule), and C1-C5 ground truth checker
- Cost/capacity summary and scale-down reminder
- Pester-free PowerShell -WhatIf / -DryRun execution of deploy-all.ps1, provision.ps1 -WithOntology, and uninstall.ps1
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "deploy"))

import deploy_plan  # noqa: E402
import fabric_graph_model_fix as gfix  # noqa: E402
import fabric_ontology_define as onto_def  # noqa: E402
import fabric_ontology_define_chain as onto_chain  # noqa: E402


def _sample_valid_params() -> dict:
    return {
        "subscriptionId": "11111111-2222-3333-4444-555555555555",
        "tenantId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "location": "eastus2",
        "resourcePrefix": "wfipin",
        "foundryProjectName": "pinned-scoring",
        "modelDeploymentName": "scoring-agent-model",
        "modelName": "gpt-4o",
        "modelVersion": "2024-11-20",
        "policyPath": "examples/wildfire/policy.json",
        "fabricCapacityName": "wfipinfabcap",
        "fabricCapacitySku": "F8",
        "fabricWorkspaceName": "PinnedScore",
        "fabricLakehouseName": "pinned_scoring_lh",
        "fabricOntologyName": "pinned_scoring_ontology",
    }


def test_validate_parameters_accepts_valid_payload():
    res = deploy_plan.validate_parameters(_sample_valid_params(), allow_template_guids=False, require_ontology=True)
    assert res["ok"] is True
    assert res["errors"] == []
    assert res["normalized"]["resourceGroupName"] == "wfipin-rg"
    assert res["normalized"]["fabricCapacitySku"] == "F8"


def test_validate_parameters_rejects_zero_guid_in_live_mode():
    params = _sample_valid_params()
    params["subscriptionId"] = deploy_plan.ZERO_GUID
    res = deploy_plan.validate_parameters(params, allow_template_guids=False, require_ontology=True)
    assert res["ok"] is False
    assert any("subscriptionId" in e for e in res["errors"])

    res_dry = deploy_plan.validate_parameters(params, allow_template_guids=True, require_ontology=True)
    assert res_dry["ok"] is True


@pytest.mark.parametrize(
    ("sku", "expected_ok"),
    [
        ("F2", False),
        ("F4", True),
        ("F8", True),
        ("F16", True),
        ("F64", True),
        ("P1", False),
        ("", False),
    ],
)
def test_validate_capacity_sku_enforces_f4_minimum(sku: str, expected_ok: bool):
    check = gfix.validate_capacity_sku(sku)
    assert check["ok"] is expected_ok
    if expected_ok:
        assert check["rank"] >= 4
        assert check["hourly_usd_estimate"] > 0


def test_validate_parameters_rejects_under_f4_sku_when_ontology_enabled():
    params = _sample_valid_params()
    params["fabricCapacitySku"] = "F2"
    res = deploy_plan.validate_parameters(params, allow_template_guids=False, require_ontology=True)
    assert res["ok"] is False
    assert any("F4" in e for e in res["errors"])


def test_validate_parameters_requires_capacity_name_in_live_mode():
    params = _sample_valid_params()
    params["fabricCapacityName"] = "<your-fabric-capacity-name>"
    res_live = deploy_plan.validate_parameters(params, allow_template_guids=False, require_ontology=True)
    assert res_live["ok"] is False
    assert any("fabricCapacityName" in e for e in res_live["errors"])

    res_dry = deploy_plan.validate_parameters(params, allow_template_guids=True, require_ontology=True)
    assert res_dry["ok"] is True


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "10"])
def test_model_capacity_rejects_invalid_values(value):
    params = {**_sample_valid_params(), "modelCapacity": value}
    result = deploy_plan.validate_parameters(params)
    assert not result["ok"]
    assert any("modelCapacity" in error for error in result["errors"])


@pytest.mark.parametrize("overrides", [{"unknown_factor": "1"}, {"fuel_load": "0.99"}, []])
def test_offline_plan_rejects_invalid_weight_overrides(overrides):
    result = deploy_plan.validate_parameters({**_sample_valid_params(), "weightOverrides": overrides})
    assert not result["ok"]
    assert any("weightOverrides" in error for error in result["errors"])


def test_composite_key_set_in_all_three_places():
    # Place 1: Ontology entity definition (entities/CrewAccess.tmdl)
    table, key, desc = onto_chain.d.ENTITIES["CrewAccess"]
    assert key == ["parcel_id", "crew_type"]
    tmdl_text = onto_def.entity_tmdl("CrewAccess", table, key, desc)
    assert "keyProperty: parcel_id" in tmdl_text
    assert "keyProperty: crew_type" in tmdl_text

    # Place 2 & 3: GraphModel graphType.json (node) and graphDefinition.json (edge)
    raw_graph_type = {
        "nodeTypes": [
            {"alias": "Parcel", "primaryKeyProperties": ["parcel_id"]},
            {"alias": "CrewAccess", "primaryKeyProperties": ["parcel_id"]},
        ]
    }
    raw_graph_def = {
        "dataSources": [{"name": "crew_access", "type": "DeltaTable"}],
        "nodes": [{"nodeTypeAlias": "CrewAccess", "dataSourceName": "crew_access"}],
        "edges": [
            {
                "edgeTypeAlias": "crew_access",
                "dataSourceName": "crew_access",
                "sourceNodeKeyColumns": ["parcel_id"],
                "destinationNodeKeyColumns": ["parcel_id"],
            }
        ],
    }

    parts = [
        {
            "path": "graphType.json",
            "payload": base64.b64encode(json.dumps(raw_graph_type).encode()).decode(),
            "payloadType": "InlineBase64",
        },
        {
            "path": "graphDefinition.json",
            "payload": base64.b64encode(json.dumps(raw_graph_def).encode()).decode(),
            "payloadType": "InlineBase64",
        },
    ]

    before_check = gfix.verify_composite_key_three_places("keyProperty: parcel_id\n", raw_graph_type, raw_graph_def)
    assert before_check["all_three_ok"] is False

    patched_parts, summary = gfix.patch_graph_model_parts(parts)
    assert summary["graphType_patched"] is True
    assert summary["graphDefinition_patched"] is True
    assert summary["schedules_added"] == 0

    by_path = {
        p["path"]: json.loads(base64.b64decode(p["payload"]).decode())
        for p in patched_parts
    }
    after_check = gfix.verify_composite_key_three_places(
        tmdl_text,
        by_path["graphType.json"],
        by_path["graphDefinition.json"],
    )
    assert after_check == {
        "ontology_entity_key_ok": True,
        "graph_node_key_ok": True,
        "graph_edge_key_ok": True,
        "all_three_ok": True,
    }

    # Re-patching is idempotent
    _, summary_again = gfix.patch_graph_model_parts(patched_parts)
    assert summary_again["graphType_patched"] is False
    assert summary_again["graphDefinition_patched"] is False


def test_foundry_iq_connection_spec_enforces_fabric_iq_preview_route():
    spec = deploy_plan.build_foundry_iq_connection_spec(
        subscription_id="11111111-2222-3333-4444-555555555555",
        resource_group="wfipin-rg",
        foundry_account="wfipin-foundry",
        foundry_project="pinned-scoring",
        workspace_id="22222222-3333-4444-5555-666666666666",
        ontology_id="33333333-4444-5555-6666-777777777777",
    )
    assert spec["agent_route"] == "fabric-iq"
    assert "api-version=2025-10-01-preview" in spec["url"]
    props = spec["body"]["properties"]
    assert props["category"] == "RemoteTool"
    assert props["authType"] == "UserEntraToken"
    assert props["audience"] == "https://api.fabric.microsoft.com"
    assert props["group"] == "GenericProtocol"
    assert props["metadata"] == {"type": "fabric_iq_preview"}
    assert props["isSharedToAll"] is False
    expected_url = (
        "https://api.fabric.microsoft.com/v1/mcp/dataPlane/"
        "workspaces/22222222-3333-4444-5555-666666666666/"
        "items/33333333-4444-5555-6666-777777777777/ontologyEndpoint"
    )
    assert props["target"] == expected_url
    mcp = spec["mcp_tool"]
    assert mcp == {
        "type": "mcp",
        "server_label": "pinnedscore_ontology",
        "server_url": expected_url,
        "project_connection_id": spec["connection_name"],
        "require_approval": "never",
    }
    assert "ask_ontology" in spec["agent_instructions"]
    assert "data agent" not in spec["agent_instructions"].lower()
    assert "dataagent" not in spec["agent_instructions"].lower()


def test_parse_retry_after_honours_header_and_default():
    assert gfix.parse_retry_after("17", default_seconds=5) == 17
    assert gfix.parse_retry_after("3", default_seconds=5) == 3
    assert gfix.parse_retry_after(None, default_seconds=5) == 5
    assert gfix.parse_retry_after("invalid", default_seconds=9) == 9
    assert gfix.parse_retry_after("9999", default_seconds=5) == 300


def test_execution_plan_step_ordering_and_invariants():
    plan = deploy_plan.build_execution_plan(_sample_valid_params(), with_ontology=True, skip_tests=False, dry_run=True)
    assert plan["validation"]["ok"] is True
    assert tuple(plan["step_ids"]) == deploy_plan.ORDERED_STEPS

    onto_step = next(s for s in plan["steps"] if s["id"] == "ontology_and_graph_model")
    assert onto_step["composite_key"] == ["parcel_id", "crew_type"]
    assert len(onto_step["composite_key_places"]) == 3
    assert onto_step["refresh_mode"] == "manual_only"
    assert onto_step["schedule_created"] is False

    agent_step = next(s for s in plan["steps"] if s["id"] == "foundry_iq_agent")
    assert agent_step["agent_route"] == "fabric-iq"
    assert agent_step["uses_fabric_data_agent"] is False
    assert agent_step["mcp_tool"]["type"] == "mcp"
    assert agent_step["mcp_tool"]["server_label"] == "pinnedscore_ontology"
    assert agent_step["mcp_tool"]["require_approval"] == "never"
    assert agent_step["mcp_tool"]["project_connection_id"] == deploy_plan.IQ_CONNECTION_NAME
    assert agent_step["mcp_tool"]["server_url"].endswith("/ontologyEndpoint")
    assert "ask_ontology" in agent_step["agent_instructions"]
    assert "data agent" not in agent_step["agent_instructions"].lower()

    test_step = next(s for s in plan["steps"] if s["id"] == "tests_and_verification")
    assert tuple(test_step["substeps"]) == deploy_plan.TEST_SUBSTEPS
    assert test_step["expected_ground_truth"] == "computed from the configured deployment dataset"
    assert plan["validation"]["normalized"]["parcelCount"] == 150

    cost = plan["cost_capacity_summary"]
    assert "Scale capacity down" in cost["scale_down_reminder"]
    assert "Do NOT re-save the graph" in cost["portal_warning"]


def test_verify_c1_c5_ground_truth_against_synthetic_responses():
    valid_questions = {
        "C1": {"gql_answer": [f"P{i:03d}" for i in range(1, 15)], "match": True},
        "C2": {"gql_answer": [f"P{i:03d}" for i in range(1, 12)], "match": True},
        "C3": {"gql_answer": ["dozer", "engine"], "match": True},
        "C4": {"gql_answer": ["P018"], "gql_value": 42, "match": True},
        "C5": {"gql_answer": ["P020", "P031"], "match": True},
    }
    res = gfix.verify_c1_c5_ground_truth(valid_questions)
    assert res["all_passed"] is True
    assert all(res["checks"].values())

    # If C3 only returns 1 row (the pre-fix 50-node CrewAccess bug), verification fails
    broken = {**valid_questions, "C3": {"gql_answer": ["engine"], "match": False}}
    res_broken = gfix.verify_c1_c5_ground_truth(broken)
    assert res_broken["all_passed"] is False
    assert res_broken["checks"]["C3_P003_dozer_engine"] is False


def test_powershell_dry_run_deploy_all_and_provision_and_uninstall():
    ps_exe = "powershell"
    for script, extra_args in [
        (ROOT / "deploy" / "deploy-all.ps1", ["-WhatIf"]),
        (ROOT / "fabric" / "provision.ps1", ["-WithOntology", "-WhatIf"]),
    ]:
        proc = subprocess.run(
            [ps_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *extra_args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 0, f"{script.name} failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        if script.name == "deploy-all.ps1":
            assert "[1/9]" in proc.stdout
            assert "[9/9]" in proc.stdout
            assert "server_label=pinnedscore_ontology" in proc.stdout
            assert "require_approval=never" in proc.stdout
            assert "ask_ontology" in proc.stdout
            assert "No live Azure or Fabric calls were made" in proc.stdout


def test_execution_plan_supports_existing_rg_and_existing_foundry_resources():
    params = {
        **_sample_valid_params(),
        "resourceGroupName": "example-resource-group",
        "existingFoundryAccountName": "example-foundry-account",
        "existingLogAnalyticsName": "law-pinnedscore",
        "existingAppInsightsName": "appi-pinnedscore",
    }
    plan = deploy_plan.build_execution_plan(params, with_ontology=True, skip_tests=False, dry_run=True)
    assert plan["validation"]["ok"] is True
    assert plan["validation"]["normalized"]["resourceGroupName"] == "example-resource-group"
    assert plan["validation"]["normalized"]["existingFoundryAccountName"] == "example-foundry-account"
    conn_step = next(s for s in plan["steps"] if s["id"] == "foundry_iq_connection")
    assert "/resourceGroups/example-resource-group/providers/Microsoft.CognitiveServices/accounts/example-foundry-account/" in conn_step["connection"]["url"]
    assert "/resourceGroups/example-resource-group/providers/Microsoft.CognitiveServices/accounts/example-foundry-account/" in conn_step["receipts_mcp_connection"]["url"]


@pytest.mark.parametrize("skip_smoke", [False, True])
def test_azure_upload_is_required_even_when_smoke_is_skipped(tmp_path, skip_smoke):
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "examples", root / "examples")
    (root / "deploy").mkdir()
    for name in ("deploy.ps1", "pin_policy.py"):
        shutil.copy2(ROOT / "deploy" / name, root / "deploy" / name)
    params = {
        **_sample_valid_params(), "parcelCount": 3,
        "deployerPrincipalId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "deployerClientIp": "198.51.100.1",
    }
    parameters = root / "parameters.json"
    parameters.write_text(json.dumps(params))
    harness = tmp_path / "run.ps1"
    upload_log = tmp_path / "upload.json"
    harness.write_text(
        """
param($Installer, $Parameters, $Log, [switch]$SkipSmoke)
$ErrorActionPreference = 'Stop'
function global:az {
    if ($args[0] -eq 'account' -and $args[1] -eq 'show') {
        return '{"id":"11111111-2222-3333-4444-555555555555","tenantId":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","name":"Offline"}'
    }
    if ($args[0] -eq 'deployment') {
        return '{"properties":{"outputs":{"storageAccountName":{"value":"offline"},"receiptsContainer":{"value":"receipts"}}}}'
    }
    if ($args[0] -eq 'storage') {
        if ($args[2] -ne 'upload-batch') { throw 'Unexpected upload operation' }
        $source = $args[[array]::IndexOf($args, '--source') + 1]
        $prefix = $args[[array]::IndexOf($args, '--destination-path') + 1]
        @{ files = @(Get-ChildItem $source -File | Select-Object -ExpandProperty Name); prefix = $prefix } |
            ConvertTo-Json | Set-Content $Log
    }
    $global:LASTEXITCODE = 0
}
& $Installer -ParametersFile $Parameters -SkipSmokeTest:$SkipSmoke
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness),
         "-Installer", str(root / "deploy" / "deploy.ps1"), "-Parameters", str(parameters),
         "-Log", str(upload_log), *(["-SkipSmoke"] if skip_smoke else [])],
        capture_output=True, text=True, timeout=60, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    uploaded = json.loads(upload_log.read_text(encoding="utf-8-sig"))
    assert set(uploaded["files"]) == {"P001.json", "P002.json", "P003.json", "_policy.json", "_dataset.json"}
    manifest = json.loads((root / "deploy/.out/deployment-data.json").read_text())
    assert uploaded["prefix"] == manifest["receiptsPrefix"]
    assert (root / "deploy/.out/smoke-receipt.json").exists() is not skip_smoke


@pytest.mark.parametrize(
    "scenario",
    ["succeeds_after_polling", "succeeds_via_operation_lookup", "terminal_failure", "timeout"],
)
def test_deploy_request_conflict_waits_for_conflicting_resource_state(tmp_path, scenario):
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "examples", root / "examples")
    (root / "deploy").mkdir()
    for name in ("deploy.ps1", "pin_policy.py"):
        shutil.copy2(ROOT / "deploy" / name, root / "deploy" / name)
    params = {
        **_sample_valid_params(), "parcelCount": 3,
        "fabricCapacityName": "",
        "deployerPrincipalId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "deployerClientIp": "198.51.100.1",
    }
    parameters = root / "parameters.json"
    parameters.write_text(json.dumps(params))
    state_file = tmp_path / "conflict-state.json"
    if scenario in ("succeeds_after_polling", "succeeds_via_operation_lookup"):
        poll_states = ["Accepted", "Updating", "Succeeded"]
    elif scenario == "terminal_failure":
        poll_states = ["Accepted", "Failed"]
    else:
        poll_states = ["Updating", "Updating", "Updating"]
    err_msg = (
        'ERROR: {"status":"Failed","error":{"code":"RequestConflict","message":"Another operation is in progress."}}'
        if scenario == "succeeds_via_operation_lookup"
        else 'ERROR: {"status":"Failed","error":{"code":"DeploymentFailed","target":"/subscriptions/11111111-2222-3333-4444-555555555555/providers/Microsoft.Resources/deployments/pinned-scoring-pinnedscore","details":[{"code":"ResourceDeploymentFailure","target":"/subscriptions/11111111-2222-3333-4444-555555555555/resourceGroups/wfipin2/providers/Microsoft.Resources/deployments/pinned-scoring-resources","details":[{"code":"RequestConflict","message":"Cannot modify resource with id ''/subscriptions/11111111-2222-3333-4444-555555555555/resourceGroups/wfipin2/providers/Microsoft.CognitiveServices/accounts/wfipin4-ai-qt5pbx'' because the resource entity provisioning state is not terminal."}]}]}}'
    )
    state_file.write_text(json.dumps({
        "deployAttempts": 0, "pollIndex": 0, "pollStates": poll_states,
        "restUrls": [], "errMsg": err_msg,
    }))
    harness = tmp_path / "run_conflict.ps1"
    harness.write_text(
        """
param($Installer, $Parameters, $StatePath)
$ErrorActionPreference = 'Stop'
$env:PINNED_CONFLICT_MAX_POLLS = '3'
$env:PINNED_CONFLICT_POLL_SECONDS = '0'
function global:az {
    $st = Get-Content $StatePath -Raw | ConvertFrom-Json
    if ($args[0] -eq 'account' -and $args[1] -eq 'show') {
        $global:LASTEXITCODE = 0
        return '{"id":"11111111-2222-3333-4444-555555555555","tenantId":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","name":"Offline"}'
    }
    if ($args[0] -eq 'deployment' -and $args[1] -eq 'sub' -and $args[2] -eq 'create') {
        $st.deployAttempts = [int]$st.deployAttempts + 1
        $st | ConvertTo-Json -Depth 10 | Set-Content $StatePath
        if ($st.deployAttempts -eq 1) {
            Write-Error $st.errMsg -ErrorAction Continue
            $global:LASTEXITCODE = 1
            return ''
        }
        $global:LASTEXITCODE = 0
        return '{"properties":{"outputs":{"storageAccountName":{"value":"offline"},"receiptsContainer":{"value":"receipts"}}}}'
    }
    if ($args[0] -eq 'deployment' -and $args[1] -eq 'operation' -and $args[2] -eq 'group' -and $args[3] -eq 'list') {
        $global:LASTEXITCODE = 0
        return '[{"properties":{"provisioningState":"Failed","targetResource":{"id":"/subscriptions/11111111-2222-3333-4444-555555555555/resourceGroups/wfipin2/providers/Microsoft.CognitiveServices/accounts/wfipin4-ai-qt5pbx"},"statusMessage":{"error":{"code":"RequestConflict"}}}}]'
    }
    if ($args[0] -eq 'rest' -and $args[1] -eq '--method' -and $args[2] -eq 'get') {
        $url = $args[[array]::IndexOf($args, '--url') + 1]
        $st.restUrls = @($st.restUrls) + @($url)
        $idx = [int]$st.pollIndex
        $pState = $st.pollStates[$idx]
        if ($idx + 1 -lt $st.pollStates.Count) { $st.pollIndex = $idx + 1 }
        $st | ConvertTo-Json -Depth 10 | Set-Content $StatePath
        $global:LASTEXITCODE = 0
        return (@{ properties = @{ provisioningState = $pState } } | ConvertTo-Json -Compress)
    }
    $global:LASTEXITCODE = 0
}
& $Installer -ParametersFile $Parameters -SkipSmokeTest
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness),
         "-Installer", str(root / "deploy" / "deploy.ps1"), "-Parameters", str(parameters),
         "-StatePath", str(state_file)],
        capture_output=True, text=True, timeout=60, cwd=tmp_path,
    )
    recorded = json.loads(state_file.read_text(encoding="utf-8-sig"))
    expected_url = (
        "https://management.azure.com/subscriptions/11111111-2222-3333-4444-555555555555"
        "/resourceGroups/wfipin2/providers/Microsoft.CognitiveServices/accounts/wfipin4-ai-qt5pbx"
        "?api-version=2025-06-01"
    )
    assert recorded["restUrls"] and all(u == expected_url for u in recorded["restUrls"])
    if scenario in ("succeeds_after_polling", "succeeds_via_operation_lookup"):
        assert result.returncode == 0, result.stdout + result.stderr
        assert recorded["deployAttempts"] == 2
        assert len(recorded["restUrls"]) == 3
    elif scenario == "terminal_failure":
        assert result.returncode != 0
        assert recorded["deployAttempts"] == 1
        assert "reached terminal failure provisioningState 'Failed'" in (result.stdout + result.stderr)
    else:
        assert result.returncode != 0
        assert recorded["deployAttempts"] == 1
        assert "Timed out after 3 polls" in (result.stdout + result.stderr)
        assert "last observed state: 'Updating'" in (result.stdout + result.stderr)

