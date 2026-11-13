"""Run the real installer/checkpoint code with isolated local Azure/Fabric stand-ins."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))


@pytest.fixture
def installer(tmp_path):
    root = tmp_path / "repo"
    for folder in ("src", "scripts", "examples", "deploy"):
        shutil.copytree(ROOT / folder, root / folder, ignore=shutil.ignore_patterns("__pycache__", ".out", "parameters.json"))
    (root / "fabric").mkdir()
    params = json.loads((root / "deploy/parameters.template.json").read_text())
    params.update(
        subscriptionId="11111111-2222-3333-4444-555555555555",
        tenantId="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        fabricCapacityName="test-capacity", parcelCount=3,
    )
    parameters = root / "parameters.json"
    parameters.write_text(json.dumps(params))
    (root / "deploy/deploy.ps1").write_text("""
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param($ParametersFile, [switch]$SkipAgentSetup)
if (-not $SkipAgentSetup) { throw 'Azure stage must defer agent setup' }
if (-not $PSCmdlet.ShouldProcess('offline resources', 'test')) { throw 'not approved' }
Add-Content $env:FAKE_LOG 'azure'
if ($env:FAKE_FAIL_STEP -eq '3') { throw 'Injected step 3 failure' }
$p = Get-Content $ParametersFile -Raw | ConvertFrom-Json
$data = Get-Content "$PSScriptRoot\\.out\\deployment-data.json" -Raw | ConvertFrom-Json
@{ resourceGroupName="$($p.resourcePrefix)-rg"; foundryAccountName='test-foundry';
   foundryEndpoint='https://test-foundry.services.ai.azure.com';
   functionAppEndpoint='https://test-function.azurewebsites.net/api/mcp';
   policySha256=$data.policySha256; receiptsPrefix=$data.receiptsPrefix } |
   ConvertTo-Json | Set-Content "$PSScriptRoot\\.out\\azure-outputs.json"
$global:LASTEXITCODE = 0
""")
    (root / "deploy/enable_tracing.ps1").write_text("""
param($ParametersFile, $ResourceGroup, $AccountName, $ProjectName)
Add-Content $env:FAKE_LOG 'tracing'
$global:LASTEXITCODE = 0
""")
    (root / "fabric/provision.ps1").write_text("""
[CmdletBinding(SupportsShouldProcess=$true)]
param($ParametersFile, [switch]$DataOnly, [switch]$OntologyOnly, [switch]$WithOntology)
if ($WhatIfPreference) { return }
if ($DataOnly -eq $OntologyOnly) { throw 'Stages must be separate' }
$stage = if ($DataOnly) { 4 } else { 5 }
Add-Content $env:FAKE_LOG "fabric-$stage"
if ($env:FAKE_FAIL_STEP -eq "$stage") { throw "Injected step $stage failure" }
$global:LASTEXITCODE = 0
""")
    harness = tmp_path / "harness.ps1"
    harness.write_text("""
param($Installer, $Parameters, [string[]]$Extra)
$ErrorActionPreference = 'Stop'
function global:az {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'account' -and $args[1] -eq 'show') {
        $tenant = if ($env:FAKE_WRONG_TENANT) { 'wrong-tenant' } else { 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee' }
        return (@{id='11111111-2222-3333-4444-555555555555';tenantId=$tenant} | ConvertTo-Json)
    }
    if ($args[1] -eq 'get-access-token') { return 'offline-token' }
    if ($args[0] -eq 'group' -and $args[1] -eq 'exists') { return 'false' }
    if ($args[0] -eq 'deployment' -and $args[1] -eq 'operation') { return '[]' }
    if ($args[0] -ne 'account') { throw "Unexpected az call: $args" }
}
function global:Invoke-RestMethod {
    param($Method = 'Get', $Uri, $Headers, $ContentType, $Body, $ResponseHeadersVariable, $StatusCodeVariable)
    $m = if ($Method) { "$Method".ToUpperInvariant() } else { 'GET' }
    $statePath = Join-Path (Split-Path $env:FAKE_LOG -Parent) 'fabric-cloud-state.json'
    $cloud = if (Test-Path $statePath) { Get-Content $statePath -Raw | ConvertFrom-Json -AsHashtable } else { $null }
    if ($m -eq 'GET') {
        Add-Content $env:FAKE_LOG "read:$Uri"
    } else {
        Add-Content $env:FAKE_LOG "$($m.ToLowerInvariant()):$Uri"
    }
    if ($StatusCodeVariable) { Set-Variable -Name $StatusCodeVariable -Value 200 -Scope 1 }
    if ($ResponseHeadersVariable) { Set-Variable -Name $ResponseHeadersVariable -Value @{} -Scope 1 }
    if ($cloud) {
        switch -Regex ($Uri) {
            '/workspaces$' {
                if ($m -eq 'POST') {
                    $req = $Body | ConvertFrom-Json
                    if ($StatusCodeVariable) { Set-Variable -Name $StatusCodeVariable -Value 201 -Scope 1 }
                    $newWs = @{ id = 'aaaaaaaa-0000-0000-0000-00000000000a'; displayName = $req.displayName }
                    if (-not $cloud['omitCapacityOnCreate']) { $newWs['capacityId'] = $req.capacityId }
                    $cloud['workspaces'] = @($cloud['workspaces']) + @($newWs)
                    $cloud | ConvertTo-Json -Depth 10 | Set-Content $statePath
                    if ($cloud['failAssignOnCreate']) {
                        $cloud['failAssignOnCreate'] = $false
                        $cloud | ConvertTo-Json -Depth 10 | Set-Content $statePath
                    }
                    return [pscustomobject]$newWs
                }
                return @{ value = @($cloud['workspaces'] | ForEach-Object { [pscustomobject]$_ }) }
            }
            '/workspaces/([^/]+)/assignToCapacity$' {
                $wsId = $Matches[1]
                if ($cloud['failNextAssign']) {
                    $cloud['failNextAssign'] = $false
                    $cloud | ConvertTo-Json -Depth 10 | Set-Content $statePath
                    throw 'Simulated assignToCapacity failure'
                }
                $req = $Body | ConvertFrom-Json
                foreach ($w in $cloud['workspaces']) {
                    if ($w['id'] -eq $wsId) { $w['capacityId'] = $req.capacityId }
                }
                $cloud | ConvertTo-Json -Depth 10 | Set-Content $statePath
                return $null
            }
            '/workspaces/([^/?]+)$' {
                $wsId = $Matches[1]
                $found = @($cloud['workspaces'] | Where-Object { $_['id'] -eq $wsId })
                if ($found.Count -ne 1) { throw "Workspace '$wsId' not found (404)" }
                return [pscustomobject]$found[0]
            }
            '/capacities$' {
                return @{ value = @($cloud['capacities'] | ForEach-Object { [pscustomobject]$_ }) }
            }
            '/providers/Microsoft\\.Fabric/capacities/([^/?]+)' {
                $capName = $Matches[1]
                if ($m -eq 'GET') {
                    $existingCap = @($cloud['capacities'] | Where-Object { $_['displayName'] -eq $capName })
                    if ($existingCap.Count -eq 1) { return [pscustomobject]@{ name = $capName } }
                    $notFound = [System.Exception]::new('404 Not Found')
                    $notFound | Add-Member -NotePropertyName Response -NotePropertyValue ([pscustomobject]@{ StatusCode = 404 })
                    throw $notFound
                }
                if ($m -eq 'PUT') {
                    $req = $Body | ConvertFrom-Json
                    $newCap = @{
                        id = 'bbbbbbbb-0000-0000-0000-00000000000b'
                        displayName = $capName
                        sku = $req.sku.name
                        state = 'Active'
                    }
                    $cloud['capacities'] = @($cloud['capacities'] | Where-Object { $_['displayName'] -ne $capName }) + @($newCap)
                    $cloud | ConvertTo-Json -Depth 10 | Set-Content $statePath
                    return [pscustomobject]@{
                        name = $capName
                        sku = @{ name = $req.sku.name; tier = 'Fabric' }
                        properties = @{ provisioningState = 'Succeeded'; state = 'Active' }
                    }
                }
            }
            '/lakehouses$' { return @{value=@(@{id='lh';displayName='pinned_scoring_lh'})} }
            '/lakehouses/lh$' { return @{id='lh';properties=@{sqlEndpointProperties=@{connectionString='test-sql'}}} }
            '/ontologies$' { return @{value=@(@{id='ont';displayName='pinned_scoring_ontology'})} }
        }
    }
    switch -Regex ($Uri) {
        '/workspaces$' { return @{value=@();continuationToken='next'} }
        '/workspaces\\?continuationToken=next$' { return @{value=@(@{id='ws';displayName='PinnedScore';capacityId='cap'})} }
        '/capacities$' { return @{value=@(@{id='cap';displayName='test-capacity';sku='F4';state='Active'})} }
        '/lakehouses$' { return @{value=@(@{id='lh';displayName='pinned_scoring_lh'})} }
        '/lakehouses/lh$' { return @{id='lh';properties=@{sqlEndpointProperties=@{connectionString='test-sql'}}} }
        '/ontologies$' { return @{value=@(@{id='ont';displayName='pinned_scoring_ontology'})} }
        default { throw "Unexpected REST call: $Uri" }
    }
}
function global:python {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq '-c' -and $args[1] -like 'from azure*') { return }
    if ($args[0] -eq '-m' -and $args[1] -eq 'pytest') {
        if ($env:PINNED_RECEIPTS_DIR) { throw "pytest must not inherit PINNED_RECEIPTS_DIR: $($env:PINNED_RECEIPTS_DIR)" }
        if ($env:PINNED_GRAPH_PATH) { throw "pytest must not inherit PINNED_GRAPH_PATH: $($env:PINNED_GRAPH_PATH)" }
        Add-Content $env:FAKE_LOG 'unit-tests'
        if ($env:FAKE_FAIL_STEP -eq '8') { throw 'Injected step 8 failure' }
        return
    }
    $name = Split-Path $args[0] -Leaf
    if ($name -eq 'pin_policy.py' -and $args[1] -eq 'pin' -and $env:FAKE_FAIL_STEP -eq '2') {
        throw 'Injected step 2 failure'
    }
    if ($args[0] -eq '-c' -or $name -in @('deploy_plan.py','pin_policy.py','cleanup_plan.py')) {
        & $env:ACTUAL_PYTHON @args
        $global:LASTEXITCODE = $LASTEXITCODE
        return
    }
    if ($name -eq 'fabric_graph_model_fix.py' -and $args -contains '--check-sku') { return }
    if ($name -in @('create_foundry_agent.py','create_foundry_fabric_agent.py')) {
        $mode = if ($args -contains '--connection-only') {'connection'} elseif ($args -contains '--setup-only' -or $name -eq 'create_foundry_agent.py') {'agent'} else {'query'}
        $step = if ($mode -eq 'connection') {6} elseif ($mode -eq 'agent') {7} else {8}
        if ($step -eq 8 -and (-not $env:PINNED_RECEIPTS_DIR -or -not (Test-Path -LiteralPath $env:PINNED_RECEIPTS_DIR))) {
            throw "Step 8 $name requires PINNED_RECEIPTS_DIR to point to deployment receipts"
        }
        Add-Content $env:FAKE_LOG "$name`:$mode"
        if ($env:FAKE_FAIL_STEP -eq "$step") { throw "Injected step $step failure" }
        return
    }
    if ($name -in @('fabric_hash_check.py','fabric_graph_model_fix.py','verify_cloud_deployment.py','run_cloud_agent.py')) {
        if (-not $env:PINNED_RECEIPTS_DIR -or -not (Test-Path -LiteralPath $env:PINNED_RECEIPTS_DIR)) {
            throw "Step 8 $name requires PINNED_RECEIPTS_DIR to point to deployment receipts"
        }
        Add-Content $env:FAKE_LOG $name
        if ($name -eq 'run_cloud_agent.py' -and $env:FAKE_LOCK_STATE) {
            $global:checkpointLock = [IO.File]::Open($env:FAKE_STATE, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        }
        return
    }
    throw "Unexpected python call: $args"
}
$flags = @{}
$options = ConvertFrom-Json $env:INSTALLER_FLAGS
foreach ($option in $options.PSObject.Properties) { $flags[$option.Name] = $option.Value }
& $Installer -ParametersFile $Parameters @flags
""")
    log = tmp_path / "calls.log"

    def run(*, fail=None, wrong_tenant=False, lock_state=False, **flags):
        env = {**os.environ, "ACTUAL_PYTHON": sys.executable, "FAKE_LOG": str(log),
               "INSTALLER_FLAGS": json.dumps(flags), "FAKE_FAIL_STEP": str(fail or ""),
               "FAKE_WRONG_TENANT": "1" if wrong_tenant else "",
               "FAKE_LOCK_STATE": "1" if lock_state else "",
               "FAKE_STATE": str(root / "deploy/.out/deployment-state.json")}
        return subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(harness),
             "-Installer", str(root / "deploy/deploy-all.ps1"), "-Parameters", str(parameters)],
            cwd=root, env=env, text=True, capture_output=True, timeout=90,
        )

    return root, parameters, log, run


def state(root):
    return json.loads((root / "deploy/.out/deployment-state.json").read_text())


def events(log):
    return log.read_text(encoding="utf-8-sig").splitlines() if log.exists() else []


def test_failure_resume_skips_completed_mutations_and_reports_status(installer):
    root, _, log, run = installer
    failed = run(fail=5, Force=True)
    assert failed.returncode != 0
    assert "[DONE]" in failed.stdout and "[FAILED] Step 5/9" in failed.stdout
    assert "-Resume" in failed.stdout and "Checkpoint:" in failed.stdout
    assert state(root)["steps"]["4"]["status"] == "completed"
    assert state(root)["steps"]["5"]["status"] == "failed"
    resumed = run(Resume=True, Force=True)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert "[REUSED]" in resumed.stdout
    assert "[RUNNING] Checking tools" in resumed.stdout
    assert state(root)["accepted"] is True
    assert events(log).count("azure") == events(log).count("fabric-4") == 1
    assert events(log).count("fabric-5") == 2
    assert events(log).count("create_foundry_agent.py:connection") == 1
    assert events(log).count("create_foundry_agent.py:agent") == 1


def test_explicit_start_step_two_after_failed_preparation(installer):
    root, _, log, run = installer
    assert run(fail=2, Force=True).returncode != 0
    result = run(StartStep=2, Force=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Starting at step 2" in result.stdout
    assert events(log).count("azure") == 1
    assert state(root)["accepted"] is True


def test_skip_validation_alias_and_yes_do_not_run_acceptance_or_setup_queries(installer):
    root, _, log, run = installer
    result = run(SkipValidation=True, Yes=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[SKIPPED]" in result.stdout and "not an accepted deployment" in result.stdout
    assert state(root)["status"] == "provisioned_unverified"
    assert state(root)["steps"]["8"]["status"] == "skipped"
    assert "unit-tests" not in events(log)
    assert not any(line.endswith(":query") or line == "verify_cloud_deployment.py" for line in events(log))
    resumed = run(Resume=True, Force=True)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert state(root)["accepted"] is True
    assert events(log).count("azure") == events(log).count("fabric-4") == 1
    assert events(log).count("unit-tests") == 1


@pytest.mark.parametrize("change", ["parameters", "source", "policy"])
def test_restart_rejects_changed_inputs_without_overwriting_checkpoint(installer, change):
    root, parameters, log, run = installer
    assert run(fail=5, Force=True).returncode != 0
    checkpoint = (root / "deploy/.out/deployment-state.json").read_bytes()
    if change == "parameters":
        parameters.write_text(parameters.read_text() + "\n")
    elif change == "source":
        source = root / "scripts/create_foundry_agent.py"
        source.write_text(source.read_text() + "\n")
    else:
        policy = json.loads((root / "examples/wildfire/policy.json").read_text())
        policy["factors"][0]["weight"] = "0.001"
        (root / "examples/wildfire/policy.json").write_text(json.dumps(policy))
    result = run(Resume=True, Force=True)
    assert result.returncode != 0
    assert (root / "deploy/.out/deployment-state.json").read_bytes() == checkpoint
    assert events(log).count("azure") == 1


@pytest.mark.parametrize("artifact", ["azure-outputs.json", "data", "receipts"])
def test_restart_rejects_missing_or_changed_prerequisite_outputs(installer, artifact):
    root, _, log, run = installer
    assert run(fail=5, Force=True).returncode != 0
    if artifact == "azure-outputs.json":
        (root / "deploy/.out" / artifact).unlink()
    else:
        deployment = json.loads((root / "deploy/.out/deployment-data.json").read_text())
        path = Path(deployment["datasetPath"] if artifact == "data" else deployment["receiptsPath"])
        if artifact == "receipts":
            path = next(path.glob("P*.json"))
        path.write_text("{}")
    result = run(Resume=True, Force=True)
    assert result.returncode != 0
    assert "Checkpoint output" in result.stdout
    assert state(root)["accepted"] is False
    assert events(log).count("azure") == 1


def test_cannot_jump_over_an_incomplete_stage(installer):
    root, _, _, run = installer
    assert run(fail=3, Force=True).returncode != 0
    original = state(root)
    result = run(StartStep=4, Force=True)
    assert result.returncode != 0
    assert "prerequisite step 3 is not complete" in result.stdout
    assert state(root) == original


def test_preflight_is_mandatory_even_with_validation_bypass(installer):
    root, _, log, run = installer
    result = run(SkipValidation=True, Force=True, wrong_tenant=True)
    assert result.returncode != 0
    assert "[FAILED] Step 1/9" in result.stdout
    assert "azure" not in events(log)
    assert state(root)["accepted"] is False


def test_confirmation_is_required_without_force_and_preview_is_read_only(installer):
    root, _, log, run = installer
    refused = run()
    assert refused.returncode != 0
    assert not events(log)
    assert not (root / "deploy/.out").exists()
    preview = run(WhatIf=True, StartStep=2)
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert "No live Azure or Fabric calls were made" in preview.stdout
    assert not events(log)
    assert not (root / "deploy/.out").exists()


def test_resume_requires_checkpoint_and_rejects_combined_start_flags(installer):
    _, _, _, run = installer
    missing = run(Resume=True, Force=True)
    assert missing.returncode != 0 and "Restart requires" in missing.stdout
    combined = run(Resume=True, StartStep=2, Force=True)
    assert combined.returncode != 0 and "Use -Resume or -StartStep" in combined.stderr


def test_resume_preview_uses_checkpoint_without_changing_it(installer):
    root, parameters, log, run = installer
    assert run(fail=5, Force=True).returncode != 0
    checkpoint_path = root / "deploy/.out/deployment-state.json"
    original = checkpoint_path.read_bytes()
    calls = events(log)
    preview = run(Resume=True, WhatIf=True)
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert "Preview would start at step 5" in preview.stdout
    assert "[REUSED]" in preview.stdout
    assert checkpoint_path.read_bytes() == original
    assert events(log) == calls
    parameters.unlink()
    missing = run(Resume=True, WhatIf=True)
    assert missing.returncode != 0
    assert "requires the original parameters file" in missing.stderr
    assert checkpoint_path.read_bytes() == original


def test_skip_validation_never_reuses_an_old_accepted_status(installer):
    root, _, log, run = installer
    initial = run(Force=True)
    assert initial.returncode == 0, initial.stdout + initial.stderr
    bypassed = run(Resume=True, SkipValidation=True, Force=True)
    assert bypassed.returncode == 0, bypassed.stdout + bypassed.stderr
    assert state(root)["accepted"] is False
    assert state(root)["steps"]["8"]["status"] == "skipped"
    accepted = run(Resume=True, Force=True)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert events(log).count("azure") == 1
    assert events(log).count("unit-tests") == 2


def test_checkpoint_write_failure_is_not_reported_as_success(installer):
    root, _, log, run = installer
    result = run(Force=True, lock_state=True)
    assert result.returncode != 0
    assert "Could not save failure checkpoint" in result.stdout
    assert "[FAILED] Step 8/9" in result.stdout
    assert state(root)["accepted"] is False
    assert state(root)["steps"]["8"]["status"] == "running"
    resumed = run(Resume=True, Force=True)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert state(root)["accepted"] is True
    assert events(log).count("azure") == 1


@pytest.mark.parametrize(("mode", "missing"), [("DataOnly", False), ("OntologyOnly", False), ("OntologyOnly", True)])
def test_actual_fabric_stage_boundaries_are_separate(tmp_path, mode, missing):
    parameters = tmp_path / "fabric.json"
    parameters.write_text(json.dumps({
        "workspaceId": "ws", "workspaceName": "PinnedScore", "lakehouseId": "" if missing else "lh",
        "lakehouseName": "pinned_scoring_lh", "datasetPath": "unused.json",
        "receiptsPath": str(tmp_path), "graphPath": "unused.json",
    }))
    log = tmp_path / "fabric-calls.log"
    harness = tmp_path / "fabric-harness.ps1"
    harness.write_text("""
param($Installer, $Parameters, $Mode, $Log, $Verification, [switch]$Missing)
$ErrorActionPreference = 'Stop'
$env:PINNED_VERIFICATION_DIR = $Verification
function global:az { $global:LASTEXITCODE = 0; return 'offline' }
function global:python {
    Add-Content $Log (Split-Path $args[0] -Leaf)
    $global:LASTEXITCODE = 0
}
function global:Invoke-RestMethod {
    param($Method, $Uri, $Headers, $ContentType, $ResponseHeadersVariable, $StatusCodeVariable)
    if ($Method -ne 'Get') { throw "Unexpected mutating REST call: $Method $Uri" }
    if ($StatusCodeVariable) { Set-Variable -Name $StatusCodeVariable -Value 200 -Scope 1 }
    if ($ResponseHeadersVariable) { Set-Variable -Name $ResponseHeadersVariable -Value @{} -Scope 1 }
    if ($Uri.EndsWith('/workspaces/ws')) { return @{id='ws';displayName='PinnedScore'} }
    if ($Uri.EndsWith('/lakehouses')) { return @{value=@()} }
    if ($Uri.EndsWith('/lakehouses/lh')) {
        if ($Missing) { return $null }
        return @{id='lh';displayName='pinned_scoring_lh';properties=@{sqlEndpointProperties=@{connectionString='test-sql'}}}
    }
    if ($Uri.EndsWith('/ontologies') -and $Mode -eq 'OntologyOnly') {
        return @{value=@(@{id='ont';displayName='pinned_scoring_ontology'})}
    }
    throw "Unexpected REST call: $Uri"
}
if ($Mode -eq 'DataOnly') {
    & $Installer -ParametersFile $Parameters -DataOnly -SkipTableCreation -SkipLoad
} else {
    & $Installer -ParametersFile $Parameters -OntologyOnly
}
""")
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(harness),
         "-Installer", str(ROOT / "fabric/provision.ps1"), "-Parameters", str(parameters),
         "-Mode", mode, "-Log", str(log), "-Verification", str(tmp_path / "verification"),
         *(["-Missing"] if missing else [])],
        capture_output=True, text=True, timeout=30,
    )
    if missing:
        assert result.returncode != 0
        assert "requires the lakehouse" in result.stderr
        assert not events(log)
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert events(log) == ([] if mode == "DataOnly" else [
            "fabric_ontology_define_chain.py", "fabric_graph_model_fix.py",
        ])


def _write_cloud_state(log: Path, payload: dict) -> Path:
    state_path = log.parent / "fabric-cloud-state.json"
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    return state_path


def test_absent_workspace_creates_in_stage4_assigns_capacity_and_resumes_cleanly(installer):
    root, parameters, log, run = installer
    params = json.loads(parameters.read_text())
    params["fabricWorkspaceName"] = "PinnedScore2"
    params["fabricCapacityName"] = "pinnedscoring2"
    params["fabricCapacitySku"] = "F4"
    parameters.write_text(json.dumps(params))
    original_param_bytes = parameters.read_bytes()

    _write_cloud_state(log, {
        "omitCapacityOnCreate": True,
        "workspaces": [
            {"id": "ws-other-1", "displayName": "wildfire-intelligence", "capacityId": "cap-old"},
            {"id": "ws-other-2", "displayName": "PinnedScore", "capacityId": "cap-old"},
        ],
        "capacities": [
            {"id": "cap-new", "displayName": "pinnedscoring2", "sku": "F4", "state": "Active"},
        ],
    })

    # If stage 3 fails, preflight must NOT have created the workspace early
    early_fail = run(fail=3, Force=True)
    assert early_fail.returncode != 0
    assert not any(line.startswith("post:") for line in events(log))
    assert state(root)["workspaceCreationPending"] == "PinnedScore2"

    # Resume and fail at stage 5: stage 4 creates workspace + assigns capacity + syncs artifacts
    fail_stage5 = run(Resume=True, fail=5, Force=True)
    assert fail_stage5.returncode != 0
    call_list = events(log)
    assert sum(1 for c in call_list if c.endswith("post:https://api.fabric.microsoft.com/v1/workspaces")) == 1
    assert sum(1 for c in call_list if c.endswith("/workspaces/aaaaaaaa-0000-0000-0000-00000000000a/assignToCapacity")) == 1
    assert state(root)["workspaceId"] == "aaaaaaaa-0000-0000-0000-00000000000a"
    assert "workspaceCreationPending" not in state(root)

    # Private parameters file is never mutated, while generated artifacts have the resolved IDs
    assert parameters.read_bytes() == original_param_bytes
    dep_data = json.loads((root / "deploy/.out/deployment-data.json").read_text())
    fab_params = json.loads((root / "deploy/.out/fabric.parameters.resolved.json").read_text())
    assert dep_data["workspaceId"] == "aaaaaaaa-0000-0000-0000-00000000000a"
    assert dep_data["capacityId"] == "cap-new"
    assert fab_params["workspaceId"] == "aaaaaaaa-0000-0000-0000-00000000000a"
    assert fab_params["capacityId"] == "cap-new"

    # Resume from stage 5 reuses the created workspace without another POST /workspaces
    resumed = run(Resume=True, Force=True)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    call_list_after = events(log)
    assert sum(1 for c in call_list_after if c.endswith("post:https://api.fabric.microsoft.com/v1/workspaces")) == 1
    assert state(root)["accepted"] is True


def test_absent_workspace_and_absent_capacity_created_and_inactive_existing_capacity_rejected(installer):
    root, parameters, log, run = installer

    # 1. Existing inactive/under-sized capacity configured directly fails preflight before Azure
    params = json.loads(parameters.read_text())
    params["fabricWorkspaceName"] = "PinnedScore2"
    params["fabricCapacityName"] = "wildfiresharedv3"
    params["fabricCapacitySku"] = "F2"
    parameters.write_text(json.dumps(params))

    _write_cloud_state(log, {
        "workspaces": [
            {"id": "ws-1", "displayName": "wildfire-intelligence", "capacityId": "cap-f2"},
            {"id": "ws-2", "displayName": "PinnedScore", "capacityId": "cap-pp3"},
        ],
        "capacities": [
            {"id": "cap-f2", "displayName": "wildfiresharedv3", "sku": "F2", "state": "Inactive"},
            {"id": "cap-pp3", "displayName": "Premium Per User - Reserved", "sku": "PP3", "state": "Active"},
        ],
    })

    result = run(Force=True)
    assert result.returncode != 0
    assert "azure" not in events(log)
    assert not any(line.startswith("put:") or line.startswith("post:") for line in events(log))

    # 2. Absent valid capacity (pinnedscoring2, F4) and absent workspace (PinnedScore2) are provisioned and assigned
    params["fabricCapacityName"] = "pinnedscoring2"
    params["fabricCapacitySku"] = "F4"
    parameters.write_text(json.dumps(params))

    created = run(Force=True)
    assert created.returncode == 0, created.stdout + created.stderr
    call_list = events(log)
    assert sum(1 for c in call_list if "providers/Microsoft.Fabric/capacities/pinnedscoring2" in c and c.startswith("put:")) == 1
    assert sum(1 for c in call_list if c.endswith("post:https://api.fabric.microsoft.com/v1/workspaces")) == 1
    assert state(root)["workspaceId"] == "aaaaaaaa-0000-0000-0000-00000000000a"
    assert state(root)["targetCapacityId"] == "bbbbbbbb-0000-0000-0000-00000000000b"
    assert "capacityCreationPending" not in state(root)
    assert "workspaceCreationPending" not in state(root)


def test_explicit_workspace_id_or_ambiguity_or_partial_creation_retry(installer):
    root, parameters, log, run = installer

    # 1. Explicit workspaceId that does not exist fails closed without creating a replacement
    params = json.loads(parameters.read_text())
    params["fabricWorkspaceName"] = "PinnedScore2"
    params["fabricWorkspaceId"] = "99999999-8888-7777-6666-555555555555"
    parameters.write_text(json.dumps(params))
    _write_cloud_state(log, {
        "workspaces": [],
        "capacities": [{"id": "cap", "displayName": "test-capacity", "sku": "F4", "state": "Active"}],
    })
    missing_explicit = run(Force=True)
    assert missing_explicit.returncode != 0
    assert "Configured fabricWorkspaceId '99999999-8888-7777-6666-555555555555' was not found" in missing_explicit.stdout
    assert "azure" not in events(log)
    assert not any(line.startswith("post:") for line in events(log))

    # 2. Ambiguous workspace displayName fails closed
    params["fabricWorkspaceId"] = ""
    parameters.write_text(json.dumps(params))
    _write_cloud_state(log, {
        "workspaces": [
            {"id": "ws-a", "displayName": "PinnedScore2", "capacityId": "cap"},
            {"id": "ws-b", "displayName": "PinnedScore2", "capacityId": "cap"},
        ],
        "capacities": [{"id": "cap", "displayName": "test-capacity", "sku": "F4", "state": "Active"}],
    })
    ambiguous = run(Force=True)
    assert ambiguous.returncode != 0
    assert "Expected one accessible workspace named 'PinnedScore2', found 2" in ambiguous.stdout
    assert "azure" not in events(log)

    # 3. Partial workspace creation (created without capacity before assignToCapacity failed) recovers on Resume
    _write_cloud_state(log, {
        "omitCapacityOnCreate": True,
        "failNextAssign": True,
        "workspaces": [],
        "capacities": [{"id": "cap", "displayName": "test-capacity", "sku": "F4", "state": "Active"}],
    })
    partial_fail = run(Force=True)
    assert partial_fail.returncode != 0
    assert "[FAILED] Step 4/9" in partial_fail.stdout
    resumed = run(Resume=True, Force=True)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    call_list = events(log)
    assert sum(1 for c in call_list if c.endswith("post:https://api.fabric.microsoft.com/v1/workspaces")) == 1
    assert sum(1 for c in call_list if c.endswith("/workspaces/aaaaaaaa-0000-0000-0000-00000000000a/assignToCapacity")) == 2
    assert state(root)["accepted"] is True

