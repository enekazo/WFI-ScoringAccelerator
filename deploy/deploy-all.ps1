<#
.SYNOPSIS
  One-command end-to-end installer and verifier for the Pinned Scoring Accelerator TEST environment
  (Azure Foundry + Fabric Lakehouse + Fabric IQ Ontology + GraphModel + Foundry IQ connection + agent + full tests).
.DESCRIPTION
  Runs all 9 ordered stages using an existing active Fabric capacity (creating the configured Fabric
  workspace in stage 4 if absent, or safely reusing it when present) and then executes every
  verification suite:
    1. preflight                 — az login, Python SDKs, target workspace/capacity and installer SKU minimum F4
    2. parameters                — read untracked deploy\parameters.json (never commit IDs/secrets) and pin policy SHA-256
    3. azure-foundry             — deploy Resource Group, Storage, Functions, App Insights, Foundry account/project and model
    4. fabric-lakehouse-data     — resolve or create Fabric workspace on configured capacity + ensure Lakehouse + load medallion and 5 chain tables
    5. fabric-ontology-graph     — define Ontology + patch GraphModel composite keys in all 3 places + edge fix + MANUAL RefreshGraph only
    6. foundry-fabric-connection — create the independent receipt MCP and Fabric IQ RemoteTool connections
    7. foundry-fabric-agent      — create both independent agents without running test questions
    8. verification-tests        — pytest, configured receipt coverage/hash parity, GQL C1-C5 and independent agent/runtime checks
    9. cost-capacity-summary     — print deployed endpoints, cost breakdown, and reminder to scale down/pause Fabric capacity

  Checkpointed and re-runnable. A partially completed stage is retried, not rolled back.
  -Resume selects the first incomplete stage; -StartStep 1..9 selects an explicit restart.
  -SkipValidation (alias of -SkipTests) skips final acceptance only. -Force/-Yes accepts
  installer confirmation prompts, not sign-in, MFA, permission or service policy gates.
  Mandatory parameter, policy, identity and capacity checks are never bypassed.
  Honours Retry-After on Fabric HTTP 429/202 responses.
  Use -WhatIf or -DryRun for a Pester-free dry-run that validates parameters, composite-key builders,
  Foundry connection payload, and the 9-step plan without making any Azure or Fabric network calls.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$ParametersFile,
    [switch]$DryRun,
    [ValidateRange(1, 9)]
    [int]$StartStep = 1,
    [switch]$Resume,
    [string]$StateFile,
    [Alias('Yes')]
    [switch]$Force,
    [Alias('SkipValidation')]
    [switch]$SkipTests,
    [switch]$SkipFabricProvision
)

$ErrorActionPreference = 'Stop'
$DeployDir = $PSScriptRoot
$RepoRoot  = Split-Path $DeployDir -Parent
if (-not $ParametersFile) { $ParametersFile = Join-Path $DeployDir 'parameters.json' }
$IsDryRun  = [bool]($DryRun -or $WhatIfPreference)
if ($Resume -and $PSBoundParameters.ContainsKey('StartStep')) { throw 'Use -Resume or -StartStep, not both.' }
$ParametersFile = [IO.Path]::GetFullPath($ParametersFile)
if (-not $StateFile) { $StateFile = Join-Path $DeployDir '.out\deployment-state.json' }
$StateFile = [IO.Path]::GetFullPath($StateFile)
. (Join-Path $DeployDir 'deployment-state.ps1')

function Fail([string]$Message) { throw $Message }
function Step([int]$Num, [string]$Id, [string]$Title) {
    if ($IsDryRun) {
        $status = if ($Num -eq 8 -and $SkipTests) { 'SKIPPED' } elseif ($Num -lt $StartStep -and $Num -ne 1) { 'REUSED' } else { 'PREVIEW' }
        $Title = "[$status] $Title"
    }
    Write-Host ("==> [{0}/9] ({1}) {2}" -f $Num, $Id, $Title) -ForegroundColor Cyan
}
function Invoke-Az {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $out = & az @args 2>$errFile
        $code = $LASTEXITCODE
        $errText = if (Test-Path $errFile) { Get-Content $errFile -Raw } else { '' }
    } finally {
        $ErrorActionPreference = $prev
        Remove-Item $errFile -Force -ErrorAction SilentlyContinue
    }
    if ($code -ne 0) { Fail "az $($args -join ' ') failed (exit $code):`n$errText" }
    return $out
}
function Test-Az {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & az @args *> $null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $prev
    }
}
function Get-FabricCollection([string]$Path) {
    $items = @()
    do {
        $page = Invoke-RestMethod -Uri "https://api.fabric.microsoft.com/v1$Path" -Headers $fabHeaders
        $items += @($page.value)
        $Path = if ($page.continuationToken) { ($Path -split '\?')[0] + '?continuationToken=' + [uri]::EscapeDataString($page.continuationToken) } else { $null }
    } while ($Path)
    return $items
}
function Invoke-FabricPost([string]$Path, [hashtable]$Body) {
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        $hdr = $null
        $code = 200
        $jsonBody = $Body | ConvertTo-Json -Depth 10 -Compress
        try {
            $result = Invoke-RestMethod -Method Post -Uri "https://api.fabric.microsoft.com/v1$Path" `
                -Headers $script:fabHeaders -ContentType 'application/json' -Body $jsonBody `
                -ResponseHeadersVariable 'hdr' -StatusCodeVariable 'code'
            if ($code -eq 202 -and $hdr -and $hdr.Location) {
                do {
                    $retryAfter = 3
                    if ($hdr['Retry-After']) { [int]::TryParse(($hdr['Retry-After'] | Select-Object -First 1), [ref]$retryAfter) | Out-Null }
                    Start-Sleep -Seconds $retryAfter
                    $locUri = ($hdr.Location | Select-Object -First 1)
                    $poll = Invoke-RestMethod -Uri $locUri -Headers $script:fabHeaders -StatusCodeVariable 'pc' -ResponseHeadersVariable 'hdr'
                } while ($pc -eq 202 -or ($poll -and $poll.status -in @('Running', 'NotStarted', 'InProgress')))
                if ($poll -and $poll.status -in @('Failed', 'Cancelled', 'Canceled')) {
                    Fail "Fabric async operation on '$Path' failed with status '$($poll.status)'."
                }
                return $poll
            }
            return $result
        } catch {
            $resp = $_.Exception.Response
            if ($resp -and [int]$resp.StatusCode -eq 429 -and $attempt -lt 5) {
                $waitSec = 15 * $attempt
                if ($resp.Headers -and $resp.Headers['Retry-After']) {
                    [int]::TryParse(($resp.Headers['Retry-After'] | Select-Object -First 1), [ref]$waitSec) | Out-Null
                }
                Start-Sleep -Seconds $waitSec
                continue
            }
            throw
        }
    }
}
function Sync-ResolvedWorkspaceArtifacts([string]$WorkspaceId, [string]$CapacityId) {
    $dataPath = Join-Path $DeployDir '.out\deployment-data.json'
    if (Test-Path -LiteralPath $dataPath) {
        $dataObj = Get-Content -LiteralPath $dataPath -Raw | ConvertFrom-Json -AsHashtable
        $dataObj['workspaceId'] = $WorkspaceId
        $dataObj['capacityId'] = $CapacityId
        [IO.File]::WriteAllText($dataPath, ($dataObj | ConvertTo-Json -Depth 20) + "`n", [Text.UTF8Encoding]::new($false))
        Save-DeploymentArtifact 'data' $dataPath
    }
    if ($fabricParamsPath -and (Test-Path -LiteralPath $fabricParamsPath)) {
        $fabObj = Get-Content -LiteralPath $fabricParamsPath -Raw | ConvertFrom-Json -AsHashtable
        $fabObj['workspaceId'] = $WorkspaceId
        $fabObj['capacityId'] = $CapacityId
        if ($p.fabricCapacityName) { $fabObj['capacityName'] = $p.fabricCapacityName }
        [IO.File]::WriteAllText($fabricParamsPath, ($fabObj | ConvertTo-Json -Depth 20) + "`n", [Text.UTF8Encoding]::new($false))
        Save-DeploymentArtifact 'fabricParameters' $fabricParamsPath
    }
    $script:DeploymentState.workspaceId = $WorkspaceId
    $script:DeploymentState.targetCapacityId = $CapacityId
    $script:DeploymentState.Remove('workspaceCreationPending')
    $script:DeploymentState.Remove('capacityCreationPending')
    Save-DeploymentState
}
function Ensure-FabricCapacity([string]$CapacityName, [string]$CapacitySku, [string]$ResourceGroup, [string]$Location, [string]$AdminMember) {
    $freshFabToken = ((Invoke-Az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv) | Select-Object -Last 1).Trim()
    if ($freshFabToken) { $script:fabHeaders = @{ Authorization = ('{0} {1}' -f 'Bearer', $freshFabToken) } }
    $capMatches = @(Get-FabricCollection '/capacities' | Where-Object displayName -eq $CapacityName)
    if ($capMatches.Count -gt 1) {
        Fail "Expected one accessible Fabric capacity named '$CapacityName', found $($capMatches.Count)."
    }
    if ($capMatches.Count -eq 0) {
        if (-not $AdminMember) {
            Fail "Could not resolve an administrator identity to create Fabric capacity '$CapacityName'."
        }
        $armToken = ((Invoke-Az account get-access-token --resource https://management.azure.com/ --query accessToken -o tsv) | Select-Object -Last 1).Trim()
        if (-not $armToken) {
            Fail 'Failed to obtain an Azure Resource Manager access token to provision the Fabric capacity.'
        }
        $armHeaders = @{ Authorization = ('{0} {1}' -f 'Bearer', $armToken); 'Content-Type' = 'application/json' }
        $capArmUri = "https://management.azure.com/subscriptions/$($p.subscriptionId)/resourceGroups/$ResourceGroup/providers/Microsoft.Fabric/capacities/${CapacityName}?api-version=2023-11-01"
        $capBody = @{
            location   = $Location
            sku        = @{ name = $CapacitySku; tier = 'Fabric' }
            properties = @{ administration = @{ members = @($AdminMember) } }
            tags       = @{ workload = 'pinned-scoring'; policySha256 = $p.policySha256 }
        } | ConvertTo-Json -Depth 10 -Compress
        Write-Host "  -> Provisioning Azure Fabric capacity '$CapacityName' ($CapacitySku) in resource group '$ResourceGroup' ($Location) with admin '$AdminMember'..."
        $capExisted = $true
        try {
            $null = Invoke-RestMethod -Method Get -Uri $capArmUri -Headers $armHeaders
        } catch {
            $capStatus = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
            if ($capStatus -ne 404) { throw }
            $capExisted = $false
        }
        $armCap = Invoke-RestMethod -Method Put -Uri $capArmUri -Headers $armHeaders -ContentType 'application/json' -Body $capBody
        $capArmId = "/subscriptions/$($p.subscriptionId)/resourceGroups/$ResourceGroup/providers/Microsoft.Fabric/capacities/$CapacityName"
        $capOrigin = if ($capExisted) { 'reused' } else { 'created' }
        $capEvidence = if ($capExisted) { 'ARM GET found the capacity before the installer PUT' } else { 'ARM GET returned 404, then the installer PUT created it' }
        Record-OwnedResource -LedgerPath $ownershipLedger -Kind arm_resource -Id $capArmId -Origin $capOrigin -Evidence $capEvidence
        for ($armPoll = 1; $armPoll -le 36 -and $armCap -and $armCap.PSObject.Properties['properties'] -and $armCap.properties -and $armCap.properties.PSObject.Properties['provisioningState'] -and $armCap.properties.provisioningState -and $armCap.properties.provisioningState -notin @('Succeeded', 'Failed', 'Canceled', 'Cancelled'); $armPoll++) {
            Start-Sleep -Seconds 5
            $armCap = Invoke-RestMethod -Method Get -Uri $capArmUri -Headers $armHeaders
        }
        if ($armCap -and $armCap.PSObject.Properties['properties'] -and $armCap.properties -and $armCap.properties.PSObject.Properties['provisioningState'] -and $armCap.properties.provisioningState -in @('Failed', 'Canceled', 'Cancelled')) {
            Fail "Azure Fabric capacity '$CapacityName' provisioning failed with state '$($armCap.properties.provisioningState)'."
        }
        for ($capPoll = 1; $capPoll -le 24; $capPoll++) {
            $freshFabToken = ((Invoke-Az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv) | Select-Object -Last 1).Trim()
            if ($freshFabToken) { $script:fabHeaders = @{ Authorization = ('{0} {1}' -f 'Bearer', $freshFabToken) } }
            $capMatches = @(Get-FabricCollection '/capacities' | Where-Object displayName -eq $CapacityName)
            if ($capMatches.Count -gt 1) {
                Fail "Expected one accessible Fabric capacity named '$CapacityName', found $($capMatches.Count)."
            }
            if ($capMatches.Count -eq 1 -and $capMatches[0].state -eq 'Active') { break }
            if ($capPoll -lt 24) { Start-Sleep -Seconds 5 }
        }
    }
    if ($capMatches.Count -ne 1) {
        Fail "Fabric capacity '$CapacityName' was not visible in the Fabric REST API after provisioning."
    }
    $resolvedCap = $capMatches[0]
    $capArmIdForGuid = "/subscriptions/$($p.subscriptionId)/resourceGroups/$ResourceGroup/providers/Microsoft.Fabric/capacities/$CapacityName"
    if ($ownershipLedger -and (Select-String -LiteralPath $ownershipLedger -SimpleMatch -Pattern "Microsoft.Fabric/capacities/$CapacityName" -Quiet)) {
        # Adds the exact Fabric GUID to an ARM capacity recorded earlier (Bicep or the PUT above); never changes its origin.
        Record-OwnedResource -LedgerPath $ownershipLedger -Kind arm_resource -Id $capArmIdForGuid -Origin reused -Evidence 'capacity GUID resolved via Fabric API' -Attrs @{ capacityId = "$($resolvedCap.id)" }
    }
    if ($resolvedCap.sku -ne $CapacitySku) {
        Fail "Workspace capacity name/SKU does not match parameters: actual '$($resolvedCap.displayName)' / '$($resolvedCap.sku)'."
    }
    if ($resolvedCap.state -ne 'Active') {
        Fail "Fabric capacity is not active: $($resolvedCap.state)."
    }
    & $python (Join-Path $RepoRoot 'scripts/fabric_graph_model_fix.py') --check-sku $resolvedCap.sku
    if ($LASTEXITCODE -ne 0) { Fail 'The target capacity is below the installer minimum F4.' }
    return $resolvedCap
}

# Locate Python 3.11+
$python = $null
foreach ($candidate in 'python', 'python3', 'py') {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $python = $candidate; break }
}
if (-not $python) { Fail 'Python 3.11+ not found on PATH.' }
$pyVersion = & $python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$pyVersion -lt [version]'3.11') { Fail "Python 3.11+ required, found $pyVersion." }
$env:PYTHONPATH = "$RepoRoot\src;$RepoRoot\scripts"

# Resolve parameters file (in -WhatIf / -DryRun mode, fall back to parameters.template.json if parameters.json is absent)
$allowTemplatePlaceholders = $false
if (-not (Test-Path $ParametersFile)) {
    if ($IsDryRun) {
        if ($Resume) {
            Fail "Restart preview requires the original parameters file: $ParametersFile. Restore it rather than substituting the template."
        }
        $ParametersFile = Join-Path $DeployDir 'parameters.template.json'
        $allowTemplatePlaceholders = $true
        Write-Host "[DryRun/WhatIf] deploy\parameters.json not found; using committed template '$ParametersFile'." -ForegroundColor Yellow
    } else {
        Fail "Parameters file not found: $ParametersFile`nCopy deploy\parameters.template.json to deploy\parameters.json and fill in your IDs (never commit deploy\parameters.json)."
    }
}

$p = Get-Content $ParametersFile -Raw | ConvertFrom-Json
$validateArgs = @(
    (Join-Path $DeployDir 'deploy_plan.py'),
    '--parameters-file', $ParametersFile,
    '--mode', 'plan'
)
if ($allowTemplatePlaceholders -or $IsDryRun) { $validateArgs += '--allow-template-guids' }
if ($SkipTests) { $validateArgs += '--skip-tests' }

$planJsonRaw = & $python @validateArgs
if ($LASTEXITCODE -ne 0) {
    Fail "Parameter or deployment plan validation failed:`n$planJsonRaw"
}
$planObj = $planJsonRaw | ConvertFrom-Json
if (-not $planObj.validation.ok) {
    $errList = ($planObj.validation.errors -join "`n  - ")
    Fail "Parameter validation failed:`n  - $errList"
}
$connStep = $planObj.steps | Where-Object id -eq 'foundry_iq_connection'
$p = $planObj.validation.normalized
$connSpec = $connStep.connection
$receiptsMcpSpec = $connStep.receipts_mcp_connection

if ($IsDryRun) {
    if ($Resume) {
        if (-not (Test-Path -LiteralPath $StateFile)) { Fail "No checkpoint for -Resume: $StateFile" }
        $previewState = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
        Assert-Checkpoint $previewState
        $StartStep = Get-ResumeStep $previewState
    }
    if ($StartStep -gt 1) {
        Write-Host "[DryRun/WhatIf] Preview would start at step $StartStep and reuse earlier deployment stages. Mandatory preflight runs again during a live deployment."
        Write-Host '[DryRun/WhatIf] A matching checkpoint and unchanged prerequisite outputs are required for an actual restart.'
    }
    Step 1 'preflight' 'Validating local configuration and installer capacity minimum (DryRun)'
    Write-Host "    [DryRun] Python $pyVersion OK; capacity SKU '$($p.fabricCapacitySku)' >= F4 OK."
    Write-Host '    [DryRun] Live permissions, tenant features, model availability/quota and actual capacity are NOT checked.'
    Step 2 'parameters' 'Validating parameters and pinning policy SHA-256 (DryRun)'
    $policyPath = if ([System.IO.Path]::IsPathRooted($p.policyPath)) { $p.policyPath } else { Join-Path $RepoRoot $p.policyPath }
    $policySha = $p.policySha256
    Write-Host "    [DryRun] Policy '$($p.policyPath)' SHA-256: $policySha"
    Step 3 'azure_foundry_deploy' 'Previewing Azure Resource Group + Function MCP tool host + Foundry + model deployment (DryRun)'
    $dryRunRg = $planObj.validation.normalized.resourceGroupName
    Write-Host "    [DryRun] Would deploy deploy\main.bicep into RG '$dryRunRg' in '$($p.location)' (model: $($p.modelDeploymentName))"
    Write-Host "    [DryRun] Would publish cloud\tool_host (/api/mcp, /api/score, /api/verify, /api/health) and upload $($p.parcelCount) receipts + _dataset.json + pinned policy to a dataset/policy-scoped Blob prefix."
    Write-Host "    [DryRun] Would run deploy\enable_tracing.ps1 (Log Analytics + Application Insights + AppInsights project connection; no secrets stored)."
    Step 4 'fabric_workspace_lakehouse_load' 'Previewing Fabric Workspace + Lakehouse + synthetic data load (DryRun)'
    $dryRunCapName = if ($p.fabricCapacityName) { $p.fabricCapacityName } else { '<capacity-name>' }
    if ($p.fabricWorkspaceId) {
        Write-Host "    [DryRun] Would resolve explicit Fabric workspace '$($p.fabricWorkspaceName)' ($($p.fabricWorkspaceId)) on capacity '$dryRunCapName' ($($p.fabricCapacitySku))."
    } else {
        Write-Host "    [DryRun] Would reuse Fabric workspace '$($p.fabricWorkspaceName)' if present or create it on capacity '$dryRunCapName' ($($p.fabricCapacitySku)) if absent."
    }
    & (Join-Path $RepoRoot 'fabric/provision.ps1') -DataOnly -WhatIf
    Step 5 'ontology_and_graph_model' 'Previewing Ontology + GraphModel 3-place composite key & edge fix + MANUAL RefreshGraph (DryRun)'
    Write-Host "    [DryRun] Composite key ['parcel_id', 'crew_type'] verified in (1) entities/CrewAccess.tmdl, (2) graphType.json nodeTypes, (3) graphDefinition.json edges."
    Write-Host "    [DryRun] Graph refresh mode: MANUAL ONLY (never scheduled; never re-save graph in Fabric portal)."
    Step 6 'foundry_iq_connection' 'Previewing Foundry RemoteTool connection payloads (DryRun)'
    Write-Host ("    [DryRun] Receipts MCP Connection PUT URL: " + $receiptsMcpSpec.url)
    Write-Host ("    [DryRun] Ontology IQ Connection PUT URL:  " + $connSpec.url)
    Write-Host ("    [DryRun] Connection properties: category=$($connSpec.body.properties.category), authType=$($connSpec.body.properties.authType), audience=$($connSpec.body.properties.audience), group=$($connSpec.body.properties.group), metadata.type=$($connSpec.body.properties.metadata.type), isSharedToAll=$($connSpec.body.properties.isSharedToAll)")
    Step 7 'foundry_iq_agent' 'Previewing independent Foundry agents with connection-based MCP tools (DryRun)'
    $mcpSpec = $connSpec.mcp_tool
    $rcptMcp = $receiptsMcpSpec.mcp_tool
    Write-Host ("    [DryRun] Would run scripts\create_foundry_agent.py --mcp-url $($rcptMcp.server_url) (server_label=$($rcptMcp.server_label), project_connection_id=$($rcptMcp.project_connection_id), require_approval=$($rcptMcp.require_approval))")
    Write-Host ("    [DryRun] Would run scripts\create_foundry_fabric_agent.py --mode fabric-iq with MCP tool: type=$($mcpSpec.type), server_label=$($mcpSpec.server_label), server_url=$($mcpSpec.server_url), require_approval=$($mcpSpec.require_approval), project_connection_id=$($mcpSpec.project_connection_id) (instructions answer only from ask_ontology; never data agent).")
    Step 8 'tests_and_verification' 'Previewing verification suites (DryRun)'
    if ($SkipTests) { Write-Host '    [DryRun] Final tests/acceptance are skipped. The deployment would remain unverified.' }
    else { Write-Host "    [DryRun] Would run: pytest, hash verification of all $($p.parcelCount) deployment receipts, live GQL C1-C5 against generated ground truth, and smoke tests for both cloud agents (including Function /api/score and /api/verify)." }
    Step 9 'cost_capacity_summary' 'Printing cost and capacity reminder (DryRun)'
    Write-Host "    $($planObj.cost_capacity_summary.scale_down_reminder)" -ForegroundColor Yellow
    Write-Host '[DryRun/WhatIf] All 9 steps validated cleanly. No live Azure or Fabric calls were made.' -ForegroundColor Green
    exit 0
}

# --- LIVE EXECUTION PATH ---

if ($PSVersionTable.PSVersion.Major -lt 7) { Fail 'PowerShell 7 or later is required for a live deployment.' }
if ($Force) { $ConfirmPreference = 'None' }
$target = "tenant $($p.tenantId), subscription $($p.subscriptionId), resource group $($p.resourceGroupName), Fabric workspace $($p.fabricWorkspaceName)"
if (-not $PSCmdlet.ShouldProcess($target, "Run deployment from step $StartStep (or first incomplete stage with -Resume); cloud changes are billable")) {
    Write-Warning 'Deployment cancelled. No stages were executed.'
    exit 1
}
$ConfirmPreference = 'None'
$script:CurrentStage = 1
$script:DeploymentState = $null
try {
Initialize-DeploymentState
Write-Host "Checkpoint: $StateFile"
Write-Host "Starting at step $StartStep. Mandatory preflight checks run on every attempt."
Invoke-DeploymentStage 1 'Checking tools, login, target workspace and live Fabric capacity' -Always -Action {
& $python -c "from azure.ai.projects import AIProjectClient; from azure.ai.projects.models import PromptAgentDefinition, MCPTool; import openai, mssql_python, azure.identity, azure.storage.blob, jwt"
if ($LASTEXITCODE -ne 0) { Fail 'Missing/incompatible deployment dependencies. Run: python -m pip install -e ".[dev]" -r deploy\requirements.txt' }
if (-not (Get-Command az -ErrorAction SilentlyContinue)) { Fail 'Azure CLI (az) not found. Install: https://aka.ms/installazurecli' }
if (-not (Test-Az account show)) { Fail "Not signed in. Run: az login --tenant $($p.tenantId)" }
Invoke-Az account set --subscription $p.subscriptionId | Out-Null
$acct = (Invoke-Az account show -o json | ConvertFrom-Json)
if ($acct.tenantId -ne $p.tenantId -or $acct.id -ne $p.subscriptionId) { Fail 'Signed-in tenant/subscription does not match deployment parameters.' }
$script:capacityAdmin = if ($p.PSObject.Properties['fabricCapacityAdmin'] -and $p.fabricCapacityAdmin) {
    "$($p.fabricCapacityAdmin)".Trim()
} elseif ($acct.PSObject.Properties['user'] -and $acct.user -and $acct.user.PSObject.Properties['name'] -and $acct.user.name) {
    "$($acct.user.name)".Trim()
} elseif ($p.PSObject.Properties['deployerPrincipalId'] -and $p.deployerPrincipalId) {
    "$($p.deployerPrincipalId)".Trim()
} else {
    'deployer@local'
}

$fabToken = ((Invoke-Az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv) | Select-Object -Last 1).Trim()
if (-not $fabToken) { Fail 'Failed to obtain a Fabric access token in the configured tenant.' }
$script:fabHeaders = @{ Authorization = ('{0} {1}' -f 'Bearer', $fabToken) }
$wsName = $p.fabricWorkspaceName
$allCapacities = @(Get-FabricCollection '/capacities')
$script:wsObj = $null
$script:capObj = $null

if ($p.fabricWorkspaceId) {
    try {
        $script:wsObj = Invoke-RestMethod -Uri "https://api.fabric.microsoft.com/v1/workspaces/$($p.fabricWorkspaceId)" -Headers $fabHeaders
    } catch {
        Fail "Configured fabricWorkspaceId '$($p.fabricWorkspaceId)' was not found or is not accessible: $($_.Exception.Message)"
    }
    if (-not $script:wsObj -or -not $script:wsObj.id) {
        Fail "Configured fabricWorkspaceId '$($p.fabricWorkspaceId)' was not found or is not accessible."
    }
    if ($script:wsObj.displayName -ne $wsName) { Fail 'Fabric workspace ID and configured name do not match.' }
} else {
    $matches = @(Get-FabricCollection '/workspaces' | Where-Object displayName -eq $wsName)
    if ($matches.Count -gt 1) { Fail "Expected one accessible workspace named '$wsName', found $($matches.Count). Set fabricWorkspaceId to disambiguate." }
    if ($matches.Count -eq 1) {
        $script:wsObj = $matches[0]
        if ($script:DeploymentState.workspaceId -and $script:wsObj.id -ne $script:DeploymentState.workspaceId) {
            Fail "Resolved workspace '$wsName' ($($script:wsObj.id)) does not match checkpoint workspaceId '$($script:DeploymentState.workspaceId)'."
        }
    } else {
        if ($SkipFabricProvision) {
            Fail "Expected one accessible workspace named '$wsName', found 0. Cannot use -SkipFabricProvision when the workspace does not exist."
        }
        if ($StartStep -gt 4) {
            Fail "Expected one accessible workspace named '$wsName', found 0. Cannot start at step $StartStep when the workspace has not been created; restart from step 4 or step 1."
        }
    }
}

$pendingCreatedWs = ($script:DeploymentState.workspaceCreationPending -eq $wsName) -and ($StartStep -le 4) -and (-not $SkipFabricProvision)
if ($script:wsObj -and ($script:wsObj.capacityId -or -not $pendingCreatedWs)) {
    if ($script:wsObj.displayName -ne $wsName) { Fail 'Fabric workspace ID and configured name do not match.' }
    $capacities = @($allCapacities | Where-Object id -eq $script:wsObj.capacityId)
    if ($capacities.Count -ne 1) { Fail 'The workspace capacity is not accessible. Confirm capacity permissions and workspace assignment before deploying Azure resources.' }
    $script:capObj = $capacities[0]
    if ($script:capObj.displayName -ne $p.fabricCapacityName -or $script:capObj.sku -ne $p.fabricCapacitySku) { Fail "Workspace capacity name/SKU does not match parameters: actual '$($script:capObj.displayName)' / '$($script:capObj.sku)'." }
} else {
    $capMatches = @($allCapacities | Where-Object displayName -eq $p.fabricCapacityName)
    if ($capMatches.Count -gt 1) { Fail "Expected one accessible Fabric capacity named '$($p.fabricCapacityName)', found $($capMatches.Count)." }
    if ($capMatches.Count -eq 1) {
        $script:capObj = $capMatches[0]
        if ($script:capObj.sku -ne $p.fabricCapacitySku) { Fail "Workspace capacity name/SKU does not match parameters: actual '$($script:capObj.displayName)' / '$($script:capObj.sku)'." }
    } else {
        if ($SkipFabricProvision) {
            Fail "Configured Fabric capacity '$($p.fabricCapacityName)' was not found or is not accessible. Cannot use -SkipFabricProvision when the capacity does not exist."
        }
        if ($StartStep -gt 4) {
            Fail "Configured Fabric capacity '$($p.fabricCapacityName)' was not found or is not accessible. Cannot start at step $StartStep when the capacity has not been created; restart from step 4 or step 1."
        }
        if ($p.fabricCapacityName -notmatch '^[a-z][a-z0-9]{2,62}$') {
            Fail "Configured Fabric capacity '$($p.fabricCapacityName)' was not found and cannot be created in Azure because its name is invalid (Microsoft.Fabric/capacities requires 3-63 lowercase letters or digits starting with a letter)."
        }
    }
}
if ($script:capObj) {
    $capObj = $script:capObj
    if ($capObj.state -ne 'Active') { Fail "Fabric capacity is not active: $($capObj.state)." }
    & $python (Join-Path $RepoRoot 'scripts/fabric_graph_model_fix.py') --check-sku $capObj.sku
    if ($LASTEXITCODE -ne 0) { Fail 'The target capacity is below the installer minimum F4.' }
    $script:DeploymentState.Remove('capacityCreationPending')
    if ($null -eq $script:wsObj) {
        $script:DeploymentState.workspaceCreationPending = $wsName
        $script:DeploymentState.targetCapacityId = $capObj.id
        Save-DeploymentState
        Write-Host "    Fabric workspace '$wsName' not found; it will be created in step 4 and assigned to capacity '$($capObj.displayName)' ($($capObj.id), $($capObj.sku))."
    } elseif (-not $script:wsObj.capacityId -and $pendingCreatedWs) {
        $script:DeploymentState.workspaceId = $script:wsObj.id
        $script:DeploymentState.targetCapacityId = $capObj.id
        Save-DeploymentState
        Write-Host "    Fabric workspace '$wsName' ($($script:wsObj.id)) was partially created; capacity '$($capObj.displayName)' ($($capObj.id)) will be assigned in step 4."
    } else {
        $script:DeploymentState.workspaceId = $script:wsObj.id
        $script:DeploymentState.targetCapacityId = $capObj.id
        $script:DeploymentState.Remove('workspaceCreationPending')
        Save-DeploymentState
        Write-Host "    Using existing Fabric workspace '$wsName' ($($script:wsObj.id)) on capacity '$($capObj.displayName)' ($($capObj.id), $($capObj.sku))."
    }
} else {
    & $python (Join-Path $RepoRoot 'scripts/fabric_graph_model_fix.py') --check-sku $p.fabricCapacitySku
    if ($LASTEXITCODE -ne 0) { Fail 'The target capacity is below the installer minimum F4.' }
    $script:DeploymentState.capacityCreationPending = $p.fabricCapacityName
    if ($null -eq $script:wsObj) {
        $script:DeploymentState.workspaceCreationPending = $wsName
    } else {
        $script:DeploymentState.workspaceId = $script:wsObj.id
    }
    Save-DeploymentState
    $targetRgForCap = if ($p.resourceGroupName) { $p.resourceGroupName } else { "$($p.resourcePrefix)-rg" }
    Write-Host "    Fabric capacity '$($p.fabricCapacityName)' ($($p.fabricCapacitySku)) and workspace '$wsName' not found; capacity will be provisioned in resource group '$targetRgForCap' ($($p.location)) with admin '$script:capacityAdmin' and assigned to workspace '$wsName' in step 4."
}
Write-Host '    Confirm model region/version/quota and preview tenant settings before proceeding; ARM/model deployment remains the live availability gate.'
}

Invoke-DeploymentStage 2 'Pinning policy and preparing matching deployment data' -Action {
    $policySource = if ([IO.Path]::IsPathRooted($p.policyPath)) { $p.policyPath } else { Join-Path $RepoRoot $p.policyPath }
    $outDir = Join-Path $DeployDir '.out'
    New-Item -ItemType Directory -Force $outDir | Out-Null
    $resolvedPolicy = Join-Path $outDir 'policy.resolved.json'
    $overrides = if ($p.weightOverrides) { $p.weightOverrides | ConvertTo-Json -Compress } else { '{}' }
    & $python (Join-Path $DeployDir 'pin_policy.py') pin $policySource $resolvedPolicy $overrides
    if ($LASTEXITCODE -ne 0) { Fail 'Policy pinning failed.' }
    & $python (Join-Path $DeployDir 'pin_policy.py') prepare_data $resolvedPolicy $ParametersFile $outDir
    if ($LASTEXITCODE -ne 0) { Fail 'Preparing deployment data failed.' }
    $data = Get-Content (Join-Path $outDir 'deployment-data.json') -Raw | ConvertFrom-Json
    Save-DeploymentArtifact 'data' (Join-Path $outDir 'deployment-data.json')
    Save-DeploymentArtifact 'fabricParameters' $data.fabricParametersPath
    Save-DeploymentArtifact 'policy' $resolvedPolicy
    Save-DeploymentArtifact 'dataset' $data.datasetPath
    Save-DeploymentArtifact 'graph' $data.graphPath
    Save-DeploymentArtifact 'receipts' $data.receiptsPath -Receipts
}
$deploymentData = Read-DeploymentArtifact 'data'
$null = Read-DeploymentArtifact 'fabricParameters'
$null = Read-DeploymentArtifact 'policy'
$null = Read-DeploymentArtifact 'dataset'
$null = Read-DeploymentArtifact 'graph'
$null = Read-DeploymentArtifact 'receipts'
if ($deploymentData.policySha256 -ne $p.policySha256 -or $deploymentData.parcelCount -ne $p.parcelCount) {
    Fail 'Prepared deployment data does not match the current pinned policy/count.'
}
$resolvedRg = if ($p.resourceGroupName) { $p.resourceGroupName } else { "$($p.resourcePrefix)-rg" }
. (Join-Path $DeployDir 'ownership-hooks.ps1')
$ownershipLedger = Join-Path $DeployDir '.out\ownership.json'
$rgSnapshotPath = Join-Path $DeployDir '.out\rg-snapshot.json'
New-Item -ItemType Directory -Force (Split-Path $ownershipLedger) | Out-Null
Initialize-OwnershipLedger -LedgerPath $ownershipLedger -TenantId $p.tenantId -SubscriptionId $p.subscriptionId -DeploymentId "pinned-scoring-$($p.resourcePrefix)"
$existingLedger = Get-Content -LiteralPath $ownershipLedger -Raw | ConvertFrom-Json
if ($existingLedger.tenantId -ne $p.tenantId -or $existingLedger.subscriptionId -ne $p.subscriptionId) {
    Fail "Ownership ledger $ownershipLedger belongs to tenant $($existingLedger.tenantId) / subscription $($existingLedger.subscriptionId), not this deployment. Move or remove it deliberately; it is never overwritten."
}
$env:PINNED_OWNERSHIP_LEDGER = $ownershipLedger
Invoke-DeploymentStage 3 'Deploying Azure resources, Function runtime and tracing' -Action {
# The pre-deployment snapshot decides created-vs-reused; keep the first one across retries and resumes.
if (-not (Test-Path -LiteralPath $rgSnapshotPath)) {
    Save-ResourceGroupSnapshot -ResourceGroup $resolvedRg -Path $rgSnapshotPath
}
& (Join-Path $DeployDir 'deploy.ps1') -ParametersFile $ParametersFile -SkipAgentSetup
$deployRc = $LASTEXITCODE
$bicepDeployment = "pinned-scoring-$($p.resourcePrefix)"
$opsPath = Join-Path $DeployDir '.out\azure-operations.json'
try {
    $subOps = @(Invoke-Az deployment operation sub list --name $bicepDeployment -o json | ConvertFrom-Json)
    $modOps = @(Invoke-Az deployment operation group list -g $resolvedRg --name pinned-scoring-resources -o json | ConvertFrom-Json)
    ConvertTo-Json -InputObject @($subOps + $modOps) -Depth 20 | Set-Content -LiteralPath $opsPath -Encoding utf8
    Record-AzureDeployment -LedgerPath $ownershipLedger -SnapshotPath $rgSnapshotPath -OperationsPath $opsPath
} catch {
    # A failed deployment may have partially created resources; surface that we could not record them.
    if ($deployRc -ne 0) { Write-Warning "Could not record partially created Azure resources: $($_.Exception.Message)" } else { throw }
}
if ($deployRc -ne 0) { Fail 'Azure deployment failed.' }
$azure = Get-Content (Join-Path $DeployDir '.out\azure-outputs.json') -Raw | ConvertFrom-Json
if (-not $azure.foundryAccountName) { Fail 'Azure deployment did not return a Foundry account.' }
& (Join-Path $DeployDir 'enable_tracing.ps1') -ParametersFile $ParametersFile -ResourceGroup $resolvedRg -AccountName $azure.foundryAccountName -ProjectName $p.foundryProjectName
if ($LASTEXITCODE -ne 0) { Fail 'Enabling tracing failed.' }
Save-DeploymentArtifact 'azure' (Join-Path $DeployDir '.out\azure-outputs.json')
$data = Get-Content (Join-Path $DeployDir '.out\deployment-data.json') -Raw | ConvertFrom-Json
Save-DeploymentArtifact 'data' (Join-Path $DeployDir '.out\deployment-data.json')
Save-DeploymentArtifact 'fabricParameters' $data.fabricParametersPath
Save-DeploymentArtifact 'policy' $data.policyPath
Save-DeploymentArtifact 'dataset' $data.datasetPath
Save-DeploymentArtifact 'graph' $data.graphPath
Save-DeploymentArtifact 'receipts' $data.receiptsPath -Receipts
}
$deploymentData = Read-DeploymentArtifact 'data'
$azure = Read-DeploymentArtifact 'azure'
if ($azure.resourceGroupName -ne $resolvedRg -or $azure.policySha256 -ne $p.policySha256 -or
    $azure.receiptsPrefix -ne $deploymentData.receiptsPrefix -or -not $azure.foundryEndpoint -or
    -not $azure.foundryAccountName -or -not $azure.functionAppEndpoint) {
    Fail 'Azure outputs are missing or do not match this deployment.'
}
$resolvedFoundryAccount = $azure.foundryAccountName
$env:PINNED_RECEIPTS_DIR = $deploymentData.receiptsPath
$env:PINNED_GRAPH_PATH = $deploymentData.graphPath
$env:RECEIPTS_PREFIX = $deploymentData.receiptsPrefix
$env:PINNED_VERIFICATION_DIR = Join-Path $DeployDir '.out\verification'
New-Item -ItemType Directory -Force $env:PINNED_VERIFICATION_DIR | Out-Null
$fabricParamsPath = $deploymentData.fabricParametersPath

Invoke-DeploymentStage 4 'Provisioning Fabric lakehouse and loading medallion/relationship data' -Skip:$SkipFabricProvision -Action {
    $wsName = if ($p.fabricWorkspaceName) { $p.fabricWorkspaceName } else { 'PinnedScore' }
    if (-not $script:capObj) {
        $script:capObj = Ensure-FabricCapacity $p.fabricCapacityName $p.fabricCapacitySku $resolvedRg $p.location $script:capacityAdmin
        $script:DeploymentState.targetCapacityId = $script:capObj.id
        $script:DeploymentState.Remove('capacityCreationPending')
        Save-DeploymentState
    }
    if (-not $script:wsObj) {
        $existing = @(Get-FabricCollection '/workspaces' | Where-Object displayName -eq $wsName)
        if ($existing.Count -gt 1) {
            Fail "Expected one accessible workspace named '$wsName', found $($existing.Count). Set fabricWorkspaceId to disambiguate."
        }
        if ($existing.Count -eq 1) {
            $script:wsObj = $existing[0]
        } else {
            Write-Host "  -> Creating Fabric workspace '$wsName' on capacity '$($script:capObj.displayName)' ($($script:capObj.id))..."
            $created = Invoke-FabricPost '/workspaces' @{
                displayName = $wsName
                capacityId  = $script:capObj.id
            }
            if ($created -and $created.id) {
                $script:wsObj = $created
            } else {
                $afterCreate = @(Get-FabricCollection '/workspaces' | Where-Object displayName -eq $wsName)
                if ($afterCreate.Count -ne 1) {
                    Fail "Workspace '$wsName' was not found after creation."
                }
                $script:wsObj = $afterCreate[0]
            }
            Record-OwnedResource -LedgerPath $ownershipLedger -Kind fabric_workspace -Id "$($script:wsObj.id)" -Origin created -Evidence "Installer POST /v1/workspaces created '$wsName' (no workspace with that name existed before)"
        }
        if ($existing.Count -eq 1) {
            Record-OwnedResource -LedgerPath $ownershipLedger -Kind fabric_workspace -Id "$($script:wsObj.id)" -Origin reused -Evidence "Workspace '$wsName' already existed before this install step"
        }
        $script:DeploymentState.workspaceId = $script:wsObj.id
        Save-DeploymentState
    }
    if ($script:wsObj.capacityId -ne $script:capObj.id) {
        if ($script:wsObj.capacityId) {
            Fail "Workspace '$wsName' ($($script:wsObj.id)) is assigned to capacity '$($script:wsObj.capacityId)', not '$($script:capObj.id)'."
        }
        Write-Host "  -> Assigning Fabric workspace '$wsName' ($($script:wsObj.id)) to capacity '$($script:capObj.displayName)' ($($script:capObj.id))..."
        Invoke-FabricPost "/workspaces/$($script:wsObj.id)/assignToCapacity" @{ capacityId = $script:capObj.id } | Out-Null
        $verifiedWs = Invoke-RestMethod -Uri "https://api.fabric.microsoft.com/v1/workspaces/$($script:wsObj.id)" -Headers $fabHeaders
        if ($verifiedWs -and $verifiedWs.id) { $script:wsObj = $verifiedWs }
        if ($script:wsObj.capacityId -ne $script:capObj.id) {
            Fail "Workspace '$wsName' ($($script:wsObj.id)) capacity assignment to '$($script:capObj.displayName)' ($($script:capObj.id)) was not confirmed."
        }
    }
    Sync-ResolvedWorkspaceArtifacts $script:wsObj.id $script:capObj.id
    & (Join-Path $RepoRoot 'fabric/provision.ps1') -ParametersFile $fabricParamsPath -DataOnly
    if ($LASTEXITCODE -ne 0) { Fail 'Fabric data provisioning failed.' }
}
Invoke-DeploymentStage 5 'Defining ontology, fixing composite keys and manually refreshing the graph' -Skip:$SkipFabricProvision -Action {
    & (Join-Path $RepoRoot 'fabric/provision.ps1') -ParametersFile $fabricParamsPath -OntologyOnly
    if ($LASTEXITCODE -ne 0) { Fail 'Fabric ontology/graph provisioning failed.' }
}
$deploymentData = Read-DeploymentArtifact 'data'
$null = Read-DeploymentArtifact 'fabricParameters'
if (-not $script:wsObj -or -not $script:wsObj.id) { Fail "Fabric workspace '$($p.fabricWorkspaceName)' was not resolved." }

# Resolve workspace, ontology, and SQL endpoint IDs for connection + verification steps
Write-Host '[CONTEXT] Resolving live Fabric items and SQL endpoint for the remaining stages.'
$fabToken = ((Invoke-Az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv) | Select-Object -Last 1).Trim()
if (-not $fabToken) { Fail 'Failed to obtain Fabric access token.' }
$fabHeaders = @{ Authorization = "Bearer $fabToken" }
$wsName  = if ($p.fabricWorkspaceName) { $p.fabricWorkspaceName } else { 'PinnedScore' }
$lhName  = if ($p.fabricLakehouseName) { $p.fabricLakehouseName } else { 'pinned_scoring' }
$ontName = if ($p.fabricOntologyName)  { $p.fabricOntologyName }  else { 'pinned_scoring_ontology' }

$lakehouses = @(Get-FabricCollection "/workspaces/$($wsObj.id)/lakehouses" | Where-Object displayName -eq $lhName)
if ($lakehouses.Count -ne 1) { Fail "Expected one lakehouse '$lhName', found $($lakehouses.Count)." }
$lhObj = Invoke-RestMethod -Uri "https://api.fabric.microsoft.com/v1/workspaces/$($wsObj.id)/lakehouses/$($lakehouses[0].id)" -Headers $fabHeaders
if ($p.fabricLakehouseId -and $lhObj.id -ne $p.fabricLakehouseId) { Fail 'Resolved lakehouse does not match fabricLakehouseId.' }
$ontologies = @(Get-FabricCollection "/workspaces/$($wsObj.id)/ontologies" | Where-Object displayName -eq $ontName)
if ($ontologies.Count -ne 1) { Fail "Expected one ontology '$ontName', found $($ontologies.Count)." }
$ontObj = $ontologies[0]
if ($p.fabricOntologyId -and $ontObj.id -ne $p.fabricOntologyId) { Fail 'Resolved ontology does not match fabricOntologyId.' }
if (-not $lhObj.properties.sqlEndpointProperties.connectionString) { Fail 'The current lakehouse SQL endpoint is missing; refusing to reuse an old endpoint.' }
if ($p.fabricSqlServer -and $p.fabricSqlServer -ne $lhObj.properties.sqlEndpointProperties.connectionString) { Fail 'Configured fabricSqlServer does not match the current SQL endpoint.' }

$env:FABRIC_WORKSPACE_ID  = $wsObj.id
$env:FABRIC_LAKEHOUSE_ID  = $lhObj.id
$env:FABRIC_ONTOLOGY_ID   = $ontObj.id
$env:FABRIC_ONTOLOGY_NAME = $ontName
$env:FABRIC_SQL_DATABASE  = $lhName
if ($lhObj -and $lhObj.properties -and $lhObj.properties.sqlEndpointProperties -and $lhObj.properties.sqlEndpointProperties.connectionString) {
    $env:FABRIC_SQL_SERVER = $lhObj.properties.sqlEndpointProperties.connectionString
}
$env:AZURE_SUBSCRIPTION_ID = $p.subscriptionId
$env:AZURE_RESOURCE_GROUP  = $resolvedRg
$env:FOUNDRY_RESOURCE_GROUP = $resolvedRg
$env:FOUNDRY_ACCOUNT_NAME  = $resolvedFoundryAccount
$env:FOUNDRY_PROJECT_NAME  = $p.foundryProjectName
$env:FOUNDRY_PROJECT_ENDPOINT = $azure.foundryEndpoint.TrimEnd('/') + "/api/projects/$($p.foundryProjectName)"
$env:FOUNDRY_MODEL_DEPLOYMENT = $p.modelDeploymentName

$funcEndpoint = $azure.functionAppEndpoint
if (-not $funcEndpoint) { Fail 'The deployed Function MCP endpoint is missing.' }
$env:PINNED_FUNCTION_ENDPOINT = ($funcEndpoint -replace '/api/mcp/?$', '')
$receiptArgs = @('--endpoint', $env:FOUNDRY_PROJECT_ENDPOINT, '--model', $p.modelDeploymentName,
    '--mcp-url', $funcEndpoint, '--subscription', $p.subscriptionId, '--resource-group', $resolvedRg)
$fabricArgs = @('--mode', 'fabric-iq', '--endpoint', $env:FOUNDRY_PROJECT_ENDPOINT,
    '--model', $p.modelDeploymentName, '--workspace-id', $wsObj.id, '--ontology-id', $ontObj.id,
    '--resource-group', $resolvedRg, '--subscription', $p.subscriptionId)
Invoke-DeploymentStage 6 'Creating independent receipt and Fabric IQ MCP connections' -Action {
    & $python (Join-Path $RepoRoot 'scripts/create_foundry_agent.py') @receiptArgs --connection-only
    if ($LASTEXITCODE -ne 0) { Fail 'Receipt MCP connection setup failed.' }
    & $python (Join-Path $RepoRoot 'scripts/create_foundry_fabric_agent.py') @fabricArgs --connection-only --out (Join-Path $env:PINNED_VERIFICATION_DIR 'fabric-connection.json')
    if ($LASTEXITCODE -ne 0) { Fail 'Fabric IQ connection setup failed.' }
}
Invoke-DeploymentStage 7 'Creating independent receipt and Fabric IQ agents without running test questions' -Action {
    & $python (Join-Path $RepoRoot 'scripts/create_foundry_agent.py') @receiptArgs --skip-connection-put
    if ($LASTEXITCODE -ne 0) { Fail 'Receipt agent setup failed.' }
    & $python (Join-Path $RepoRoot 'scripts/create_foundry_fabric_agent.py') @fabricArgs --skip-connection-put --setup-only --out (Join-Path $env:PINNED_VERIFICATION_DIR 'fabric-agent-setup.json')
    if ($LASTEXITCODE -ne 0) { Fail 'Fabric IQ agent setup failed.' }
}

Invoke-DeploymentStage 8 "Verifying $($p.parcelCount) receipts, live GQL relationships, Function runtime and both agents" -Skip:$SkipTests -Action {
    $env:PINNED_RECEIPTS_DIR = $deploymentData.receiptsPath
    $env:PINNED_GRAPH_PATH = $deploymentData.graphPath
    Write-Host '  -> Running pytest unit & offline control suite...'
    $savedLedgerEnv = $env:PINNED_OWNERSHIP_LEDGER
    $savedReceiptsDir = $env:PINNED_RECEIPTS_DIR
    $savedGraphPath = $env:PINNED_GRAPH_PATH
    Push-Location $RepoRoot
    try {
        Remove-Item Env:\PINNED_OWNERSHIP_LEDGER -ErrorAction SilentlyContinue
        Remove-Item Env:\PINNED_RECEIPTS_DIR -ErrorAction SilentlyContinue
        Remove-Item Env:\PINNED_GRAPH_PATH -ErrorAction SilentlyContinue
        & $python -m pytest -q
        if ($LASTEXITCODE -ne 0) { Fail 'pytest suite failed.' }
    } finally {
        if ($null -ne $savedLedgerEnv -and $savedLedgerEnv -ne '') {
            $env:PINNED_OWNERSHIP_LEDGER = $savedLedgerEnv
        }
        $env:PINNED_RECEIPTS_DIR = $savedReceiptsDir
        $env:PINNED_GRAPH_PATH = $savedGraphPath
        Pop-Location
    }

    Write-Host "  -> Verifying $($p.parcelCount) Lakehouse receipts against this deployment..."
    & $python (Join-Path $RepoRoot 'scripts/fabric_hash_check.py')
    if ($LASTEXITCODE -ne 0) { Fail 'Fabric receipt verification failed.' }

    Write-Host '  -> Running GQL ground-truth verification C1-C5 against GraphModel...'
    $graphCheckArgs = @('--workspace-id', $wsObj.id, '--ontology-id', $ontObj.id, '--verify-c1-c5')
    if ($p.fabricGraphModelId) { $graphCheckArgs += @('--graph-model-id', $p.fabricGraphModelId) }
    & $python (Join-Path $RepoRoot 'scripts/fabric_graph_model_fix.py') @graphCheckArgs
    if ($LASTEXITCODE -ne 0) { Fail 'GQL ground-truth verification (C1-C5) failed.' }

    Write-Host '  -> Running end-to-end Foundry agent-through-ontology C1-C5 verification (ask_ontology)...'
    & $python (Join-Path $RepoRoot 'scripts/create_foundry_fabric_agent.py') @fabricArgs --skip-connection-put --verify-c1-c5 --out (Join-Path $env:PINNED_VERIFICATION_DIR 'fabric-agent-smoke.json')
    if ($LASTEXITCODE -ne 0) { Fail 'Agent-through-ontology smoke test failed.' }

    Write-Host '  -> Checking Function scoring, receipt parity, authentication and verifier fallback...'
    & $python (Join-Path $RepoRoot 'scripts/verify_cloud_deployment.py') --function-endpoint $funcEndpoint --manifest (Join-Path $DeployDir '.out\deployment-data.json') --output (Join-Path $env:PINNED_VERIFICATION_DIR 'cloud-runtime.json')
    if ($LASTEXITCODE -ne 0) { Fail 'Function runtime acceptance checks failed.' }

    Write-Host '  -> Running cloud MCP receipt agent + Function-hosted verifier smoke test (zero local tool execution/scoring/verification)...'
    & $python (Join-Path $RepoRoot 'scripts/run_cloud_agent.py') --endpoint $env:FOUNDRY_PROJECT_ENDPOINT --function-endpoint $funcEndpoint --verify 'Get the receipt for parcel P001 and state its score, band, and receipt_sha256.'
    if ($LASTEXITCODE -ne 0) { Fail 'Cloud MCP receipt agent smoke test failed.' }
}

Invoke-DeploymentStage 9 'Deployment and verification summary' -Action {
Write-Host "  Resource Group:   $resolvedRg"
Write-Host "  Parcels:          $($p.parcelCount)"
Write-Host "  Function runtime: $($env:PINNED_FUNCTION_ENDPOINT)"
Write-Host "  Foundry Project:  $($env:FOUNDRY_PROJECT_ENDPOINT)"
Write-Host "  Fabric Workspace: $wsName ($($wsObj.id))"
Write-Host "  Fabric Ontology:  $ontName ($($ontObj.id))"
Write-Host "  Graph Refresh:    MANUAL ONLY (never add a schedule; never re-save graph in Fabric portal)"
Write-Host "  Teardown preview: .\deploy\uninstall.ps1 -ParametersFile `"$ParametersFile`" -RemoveFabricItems -WhatIf  (review it, then rerun without -WhatIf only with explicit approval; -Purge is a separate irreversible step)"
Write-Host ""
Write-Host "  COST / CAPACITY REMINDER: $($planObj.cost_capacity_summary.scale_down_reminder)" -ForegroundColor Yellow
}
$DeploymentState.accepted = (-not $SkipTests -and $DeploymentState.steps.'8'.status -eq 'completed')
$DeploymentState.status = if ($DeploymentState.accepted) { 'accepted' } else { 'provisioned_unverified' }
Save-DeploymentState
Write-Progress -Activity 'WFI deployment' -Completed
if (-not $DeploymentState.accepted) { Write-Warning 'Provisioning completed with verification SKIPPED. This run is not an accepted deployment.' }
else { Write-Host 'Provisioning and all requested verification steps complete.' -ForegroundColor Green }
Write-Host "Final status: $($DeploymentState.status). Checkpoint: $StateFile"
} catch {
    Write-Progress -Activity 'WFI deployment' -Completed
    $failure = $_
    if ($script:DeploymentState) {
        $script:DeploymentState.status = 'failed'
        $script:DeploymentState.accepted = $false
        $entry = $script:DeploymentState.steps."$CurrentStage"
        $entry.status = 'failed'
        $entry.error = $failure.Exception.Message
        try { Save-DeploymentState }
        catch { Write-Warning "Could not save failure checkpoint: $($_.Exception.Message)" }
    }
    Write-Host "[FAILED] Step ${CurrentStage}/9: $($failure.Exception.Message)" -ForegroundColor Red
    Write-Host "Checkpoint: $StateFile"
    Write-Host "Restart: .\deploy\deploy-all.ps1 -ParametersFile `"$ParametersFile`" -StateFile `"$StateFile`" -Resume"
    throw
}
