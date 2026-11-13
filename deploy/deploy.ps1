<#
.SYNOPSIS
  One-command deployment of the pinned-scoring Azure layer into a new subscription/tenant.
.DESCRIPTION
  Validates prerequisites, reads deploy\parameters.json, pins the policy (applying weightOverrides),
  runs az deployment sub what-if / create against deploy\main.bicep, smoke-tests the scorer and
  uploads the configured receipts, dataset and policy, then publishes the Function runtime.
.PARAMETER WhatIf
  Preview changes only (az deployment sub what-if). Nothing is created or uploaded.
.PARAMETER ParametersFile
  Path to parameters JSON. Default: deploy\parameters.json.
.PARAMETER SkipSmokeTest
  Skip only the extra local scorer check; required runtime inputs are always uploaded.
.PARAMETER SkipAgentSetup
  Defer receipt connection/agent creation to the full installer's separate stages.
.PARAMETER WithOntology
  Also provision the Fabric Lakehouse, 5 chain tables, Fabric IQ Ontology, GraphModel (with 3-place
  composite key fix and manual RefreshGraph), Foundry Fabric IQ connection, and Foundry agent.
#>
[CmdletBinding()]
param(
    [switch]$WhatIf,
    [string]$ParametersFile,
    [switch]$SkipSmokeTest,
    [switch]$SkipAgentSetup,
    [switch]$WithOntology
)

$ErrorActionPreference = 'Stop'
$DeployDir = $PSScriptRoot
$RepoRoot = Split-Path $DeployDir -Parent
if (-not $ParametersFile) { $ParametersFile = Join-Path $DeployDir 'parameters.json' }

function Fail([string]$Message) { Write-Error $Message; exit 1 }
function Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Invoke-Az {
    # Runs az, returns stdout; fails on non-zero exit. Ignores stderr warnings from az CLI on PS 5.1.
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

# 1. Prerequisites
Step 'Checking prerequisites'
if ($PSVersionTable.PSVersion.Major -lt 5) { Fail 'PowerShell 5.1 or later is required.' }
if (-not (Get-Command az -ErrorAction SilentlyContinue)) { Fail 'Azure CLI (az) not found. Install: https://aka.ms/installazurecli' }
$python = $null
foreach ($candidate in 'python', 'python3', 'py') {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $python = $candidate; break }
}
if (-not $python) { Fail 'Python 3.11+ not found on PATH.' }
$pyVersion = & $python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$pyVersion -lt [version]'3.11') { Fail "Python 3.11+ required, found $pyVersion." }
if (-not (Test-Az bicep version)) { Step 'Installing Bicep'; Invoke-Az bicep install | Out-Null }

# 2. Parameters
if (-not (Test-Path $ParametersFile)) {
    Fail "Parameters file not found: $ParametersFile`nCopy deploy\parameters.template.json to deploy\parameters.json and fill it in (see deploy\README.md)."
}
$p = Get-Content $ParametersFile -Raw | ConvertFrom-Json
foreach ($key in 'subscriptionId', 'tenantId', 'location', 'resourcePrefix', 'foundryProjectName', 'modelDeploymentName', 'policyPath') {
    if (-not $p.$key) { Fail "Parameter '$key' is missing in $ParametersFile." }
}
$guidPattern = '^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$'
if ($p.subscriptionId -notmatch $guidPattern -or $p.subscriptionId -eq '00000000-0000-0000-0000-000000000000') { Fail 'subscriptionId must be your real subscription GUID.' }
if ($p.tenantId -notmatch $guidPattern -or $p.tenantId -eq '00000000-0000-0000-0000-000000000000') { Fail 'tenantId must be your real tenant GUID.' }
if ($p.resourcePrefix -notmatch '^[a-z0-9]{3,10}$') { Fail 'resourcePrefix must be 3-10 lowercase letters/digits.' }
$parcelCount = & $python (Join-Path $DeployDir 'pin_policy.py') count $ParametersFile
if ($LASTEXITCODE -ne 0) { Fail 'Invalid parcelCount in deployment parameters.' }
$parcelCount = [int]$parcelCount

# 3. Azure context (never hard-coded; always from parameters)
Step 'Selecting Azure subscription'
if (-not (Test-Az account show)) { Fail "Not signed in. Run: az login --tenant $($p.tenantId)" }
Invoke-Az account set --subscription $p.subscriptionId | Out-Null
$account = Invoke-Az account show -o json | ConvertFrom-Json
if ($account.tenantId -ne $p.tenantId) { Fail "Signed-in tenant $($account.tenantId) does not match parameters tenantId $($p.tenantId). Run: az login --tenant $($p.tenantId)" }
Write-Host "    subscription: $($account.name) ($($account.id))"

# 4. Pin policy
Step 'Validating and pinning policy'
$policySource = $p.policyPath
if (-not [System.IO.Path]::IsPathRooted($policySource)) { $policySource = Join-Path $RepoRoot $policySource }
if (-not (Test-Path $policySource)) { Fail "Policy file not found: $policySource" }
$overrides = '{}'
if ($p.weightOverrides) { $overrides = $p.weightOverrides | ConvertTo-Json -Compress }
if ($overrides -eq '' -or $null -eq $overrides) { $overrides = '{}' }
$outDir = Join-Path $DeployDir '.out'
New-Item -ItemType Directory -Force $outDir | Out-Null
$resolvedPolicy = Join-Path $outDir 'policy.resolved.json'
$overridesFile = Join-Path $outDir 'overrides.json'
Set-Content -Path $overridesFile -Value $overrides -Encoding UTF8
$overridesArg = Get-Content $overridesFile -Raw
$policySha = & $python (Join-Path $DeployDir 'pin_policy.py') pin $policySource $resolvedPolicy $overridesArg
if ($LASTEXITCODE -ne 0) { Fail "Policy validation failed:`n$policySha" }
$policySha = "$($policySha | Select-Object -Last 1)".Trim()
Write-Host "    resolved policy: $resolvedPolicy"
Write-Host "    policy SHA-256:  $policySha" -ForegroundColor Green
if (-not $WhatIf) {
    & $python (Join-Path $DeployDir 'pin_policy.py') prepare_data $resolvedPolicy $ParametersFile $outDir
    if ($LASTEXITCODE -ne 0) { Fail 'Preparing the configured deployment dataset failed.' }
    $deploymentData = Get-Content (Join-Path $outDir 'deployment-data.json') -Raw | ConvertFrom-Json
}

# 5. Deploy
$deployerId = if ($p.deployerPrincipalId) { "$($p.deployerPrincipalId)".Trim() } else { '' }
if (-not $deployerId) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $deployerId = (& az ad signed-in-user show --query id -o tsv 2>$null | Select-Object -Last 1)
        if ($LASTEXITCODE -ne 0 -or -not $deployerId) { $deployerId = '' } else { $deployerId = "$deployerId".Trim() }
        if (-not $deployerId) {
            $armTok = (& az account get-access-token --resource https://management.azure.com/ --query accessToken -o tsv 2>$null | Select-Object -Last 1)
            if ($LASTEXITCODE -eq 0 -and $armTok) {
                $oid = (& $python -c "import jwt, sys; print(jwt.decode(sys.argv[1].strip(), options={'verify_signature': False}).get('oid') or '')" "$armTok" 2>$null | Select-Object -Last 1)
                if ($LASTEXITCODE -eq 0 -and $oid) { $deployerId = "$oid".Trim() }
            }
        }
    } finally {
        $ErrorActionPreference = $prev
    }
}
$deployerIp = if ($p.deployerClientIp) { $p.deployerClientIp } else { '' }
if (-not $deployerId) { Fail 'Could not resolve the deploying principal. Set deployerPrincipalId in your private parameters before deployment.' }
if (-not $deployerIp) {
    try {
        $deployerIp = (Invoke-RestMethod -Uri 'https://api.ipify.org' -TimeoutSec 10).Trim()
    } catch {
        Fail "Could not detect the deployment client IP: $($_.Exception.Message). Set deployerClientIp in your private parameters."
    }
}
$fabricWs = ''; if ($p.fabricWorkspaceId) { $fabricWs = $p.fabricWorkspaceId }
$fabricLh = ''; if ($p.fabricLakehouseId) { $fabricLh = $p.fabricLakehouseId }
$bicepParams = @(
    "location=$($p.location)", "resourcePrefix=$($p.resourcePrefix)",
    "foundryProjectName=$($p.foundryProjectName)", "modelDeploymentName=$($p.modelDeploymentName)",
    "policySha256=$policySha", "deployerPrincipalId=$deployerId",
    "fabricWorkspaceId=$fabricWs", "fabricLakehouseId=$fabricLh"
)
if ($deployerIp) { $bicepParams += "deployerClientIp=$deployerIp" }
if ($p.modelName) { $bicepParams += "modelName=$($p.modelName)" }
if ($p.modelVersion) { $bicepParams += "modelVersion=$($p.modelVersion)" }
if ($p.modelSkuName) { $bicepParams += "modelSkuName=$($p.modelSkuName)" }
if ($p.modelCapacity) { $bicepParams += "modelCapacity=$($p.modelCapacity)" }
if ($p.resourceGroupName) { $bicepParams += "resourceGroupName=$($p.resourceGroupName)" }
if ($p.functionAppName) { $bicepParams += "functionAppName=$($p.functionAppName)" }
if ($p.functionPlanSku) { $bicepParams += "functionPlanSku=$($p.functionPlanSku)" }
if ($p.logAnalyticsName) { $bicepParams += "logAnalyticsName=$($p.logAnalyticsName)" }
if ($p.appInsightsName) { $bicepParams += "appInsightsName=$($p.appInsightsName)" }
$targetRg = if ($p.resourceGroupName) { $p.resourceGroupName } else { "$($p.resourcePrefix)-rg" }
if ($p.existingFoundryAccountName) {
    $bicepParams += "existingFoundryAccountName=$($p.existingFoundryAccountName)"
    if (-not $p.mcpAllowedAudiences) {
        $projUrl = "https://management.azure.com/subscriptions/$($p.subscriptionId)/resourceGroups/$targetRg/providers/Microsoft.CognitiveServices/accounts/$($p.existingFoundryAccountName)/projects/$($p.foundryProjectName)?api-version=2025-04-01-preview"
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $blueprintId = (& az rest --method get --url $projUrl --query "properties.agentIdentity.agentIdentityBlueprintId" -o tsv 2>$null | Select-Object -Last 1)
            if ($LASTEXITCODE -eq 0 -and $blueprintId) {
                $blueprintId = $blueprintId.Trim()
                $bicepParams += "mcpAllowedAudiences=api://$blueprintId,$blueprintId,https://management.azure.com/,https://management.azure.com"
            }
        } finally {
            $ErrorActionPreference = $prev
        }
    }
}
if ($p.mcpAllowedAudiences) { $bicepParams += "mcpAllowedAudiences=$($p.mcpAllowedAudiences)" }
if ($p.existingLogAnalyticsName) { $bicepParams += "existingLogAnalyticsName=$($p.existingLogAnalyticsName)" }
if ($p.existingAppInsightsName) { $bicepParams += "existingAppInsightsName=$($p.existingAppInsightsName)" }
if ($p.fabricCapacityName -and "$($p.fabricCapacityName)".Trim() -match '^[a-z][a-z0-9]{2,62}$') {
    $capName = "$($p.fabricCapacityName)".Trim()
    $capSku = if ($p.fabricCapacitySku) { "$($p.fabricCapacitySku)".Trim() } else { 'F4' }
    $capAdmin = if ($p.PSObject.Properties['fabricCapacityAdmin'] -and $p.fabricCapacityAdmin) {
        "$($p.fabricCapacityAdmin)".Trim()
    } elseif ($account.PSObject.Properties['user'] -and $account.user -and $account.user.PSObject.Properties['name'] -and $account.user.name) {
        "$($account.user.name)".Trim()
    } else {
        $deployerId
    }
    $targetRg = if ($p.resourceGroupName) { $p.resourceGroupName } else { "$($p.resourcePrefix)-rg" }
    $provisionCapInRg = $true
    if (-not $WhatIf) {
        $capListUrl = "https://management.azure.com/subscriptions/$($p.subscriptionId)/providers/Microsoft.Fabric/capacities?api-version=2023-11-01"
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $capListRaw = (& az rest --method get --url $capListUrl -o json 2>$null)
            if ($LASTEXITCODE -eq 0 -and $capListRaw) {
                $capListObj = $capListRaw | ConvertFrom-Json
                foreach ($c in @($capListObj.value)) {
                    if ($c.name -eq $capName -and $c.id -notmatch "/resourceGroups/$([regex]::Escape($targetRg))/") {
                        $provisionCapInRg = $false
                    }
                }
            }
        } finally {
            $ErrorActionPreference = $prev
        }
    }
    if ($provisionCapInRg -and $capAdmin) {
        $bicepParams += "fabricCapacityName=$capName"
        $bicepParams += "fabricCapacitySku=$capSku"
        $bicepParams += "fabricCapacityAdmin=$capAdmin"
    }
}
$template = Join-Path $DeployDir 'main.bicep'
$deploymentName = "pinned-scoring-$($p.resourcePrefix)"

if ($WhatIf) {
    Step 'Running what-if (no changes will be made)'
    Invoke-Az deployment sub what-if --name $deploymentName --location $p.location --template-file $template --parameters @bicepParams | Write-Host
    Write-Host "  [WhatIf] Would package cloud\tool_host + src\pinned_scoring and publish Function App via az functionapp deployment source config-zip"
    Write-Host "  [WhatIf] Would upload pinned policy + $parcelCount receipts + _dataset.json to a dataset/policy-scoped blob prefix"
    Write-Host "  [WhatIf] Would create Foundry RemoteTool MCP connection 'pinnedscore-receipts-mcp-entraid' and cloud agent 'pinned-score-agent' via scripts\create_foundry_agent.py --mcp-url ..."
    if ($WithOntology) {
        Step 'Previewing Fabric + Ontology + Foundry IQ agent plan (-WithOntology)'
        $fabricProvision = Join-Path $RepoRoot 'fabric/provision.ps1'
        & $fabricProvision -WithOntology -WhatIf
        Write-Host "  [WhatIf] Would create Foundry RemoteTool connection (UserEntraToken, audience https://api.fabric.microsoft.com, group GenericProtocol, metadata.type=fabric_iq_preview, isSharedToAll=false) and Foundry agent via scripts\create_foundry_fabric_agent.py --mode fabric-iq"
    }
    Write-Host 'What-if complete. Re-run without -WhatIf to deploy.' -ForegroundColor Green
    exit 0
}

function Resolve-ArmApiVersion([string]$ResourceId) {
    $m = [regex]::Match($ResourceId, '(?i)/providers/([^/]+(?:/[^/]+)+)$')
    if (-not $m.Success) { return '2023-07-01' }
    $segs = @($m.Groups[1].Value.Split('/') | Where-Object { $_ })
    if ($segs.Count -lt 2) { return '2023-07-01' }
    $typeParts = @($segs[0])
    for ($i = 1; $i -lt $segs.Count; $i += 2) {
        $typeParts += $segs[$i]
    }
    $armType = ($typeParts -join '/').ToLowerInvariant()
    $known = @{
        'microsoft.cognitiveservices/accounts'                             = '2025-06-01'
        'microsoft.cognitiveservices/accounts/projects'                    = '2025-06-01'
        'microsoft.cognitiveservices/accounts/deployments'                 = '2025-06-01'
        'microsoft.cognitiveservices/accounts/projects/connections'        = '2025-10-01-preview'
        'microsoft.storage/storageaccounts'                                = '2023-05-01'
        'microsoft.web/sites'                                              = '2023-12-01'
        'microsoft.web/serverfarms'                                        = '2023-12-01'
        'microsoft.fabric/capacities'                                      = '2023-11-01'
        'microsoft.operationalinsights/workspaces'                         = '2023-09-01'
        'microsoft.insights/components'                                    = '2020-02-02'
        'microsoft.managedidentity/userassignedidentities'                 = '2023-01-31'
        'microsoft.authorization/roleassignments'                          = '2022-04-01'
        'microsoft.network/networksecurityperimeters'                      = '2023-08-01-preview'
        'microsoft.network/networksecurityperimeters/resourceassociations' = '2023-08-01-preview'
    }
    if ($known.ContainsKey($armType)) { return $known[$armType] }
    return '2023-07-01'
}

function Get-ConflictingArmResourceIds([string]$ErrorText, [string]$ResourceGroup) {
    $ids = [System.Collections.Generic.List[string]]::new()
    $normalized = ($ErrorText -replace '\r?\n\s*', '')
    $pattern = '(?i)/subscriptions/[^''"\s\\]+/resourceGroups/[^''"\s\\]+/providers/[A-Za-z0-9_.]+/[^''"\s\\]+'
    foreach ($match in [regex]::Matches($normalized, $pattern)) {
        $candidate = $match.Value.TrimEnd('.', ',', ';', ')', ']', '}')
        if ($candidate -notmatch '(?i)/providers/Microsoft\.Resources/deployments(/|$)') {
            if (-not ($ids | Where-Object { $_.ToLowerInvariant() -eq $candidate.ToLowerInvariant() })) {
                $ids.Add($candidate)
            }
        }
    }
    if ($ids.Count -eq 0 -and $ResourceGroup) {
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $opsRaw = (& az deployment operation group list -g $ResourceGroup --name pinned-scoring-resources -o json 2>$null)
            if ($LASTEXITCODE -eq 0 -and $opsRaw) {
                foreach ($op in @($opsRaw | ConvertFrom-Json)) {
                    $props = $op.properties
                    $targetId = if ($props -and $props.targetResource) { "$($props.targetResource.id)".Trim() } else { '' }
                    $statusMsg = if ($props -and $props.statusMessage) { ($props.statusMessage | ConvertTo-Json -Depth 10 -Compress) } else { '' }
                    if ($targetId -and $targetId -notmatch '(?i)/providers/Microsoft\.Resources/deployments(/|$)' -and
                        (($props.provisioningState -eq 'Failed') -or ($statusMsg -match 'RequestConflict|not terminal|Another operation is in progress'))) {
                        if (-not ($ids | Where-Object { $_.ToLowerInvariant() -eq $targetId.ToLowerInvariant() })) {
                            $ids.Add($targetId)
                        }
                    }
                }
            }
        } finally {
            $ErrorActionPreference = $prev
        }
    }
    return @($ids)
}

function Wait-ConflictingArmResources([string[]]$ResourceIds, [int]$Attempt, [int]$MaxAttempts, [string]$OriginalError) {
    if (-not $ResourceIds -or $ResourceIds.Count -eq 0) {
        Fail ("az deployment sub create --name $deploymentName failed with RequestConflict, and no conflicting resource ID could be identified for state polling:`n$OriginalError")
    }
    $maxPolls = if ($env:PINNED_CONFLICT_MAX_POLLS) { [int]$env:PINNED_CONFLICT_MAX_POLLS } else { 18 }
    $pollSec = if ($env:PINNED_CONFLICT_POLL_SECONDS) { [int]$env:PINNED_CONFLICT_POLL_SECONDS } else { 10 }
    if ($maxPolls -lt 1) { $maxPolls = 1 }
    if ($pollSec -lt 0) { $pollSec = 0 }
    foreach ($resId in $ResourceIds) {
        $apiVer = Resolve-ArmApiVersion $resId
        $url = "https://management.azure.com${resId}?api-version=${apiVer}"
        $lastState = 'unknown'
        $resolved = $false
        Write-Warning "ARM reported RequestConflict on '$resId' (attempt $Attempt/$MaxAttempts); polling provisioningState (api-version=$apiVer, up to $maxPolls polls x ${pollSec}s)..."
        for ($poll = 1; $poll -le $maxPolls; $poll++) {
            $prev = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            $errFile = [System.IO.Path]::GetTempFileName()
            try {
                $bodyRaw = & az rest --method get --url $url -o json 2>$errFile
                $rc = $LASTEXITCODE
                $restErr = if (Test-Path $errFile) { Get-Content $errFile -Raw } else { '' }
            } finally {
                $ErrorActionPreference = $prev
                Remove-Item $errFile -Force -ErrorAction SilentlyContinue
            }
            if ($rc -ne 0) {
                if ($restErr -match '(?i)ResourceNotFound|NotFound|\(404\)') {
                    Write-Host "    Conflicting resource '$resId' is now absent (404); ready to retry deployment."
                    $resolved = $true
                    break
                }
                Fail ("Cannot verify provisioningState for conflicting resource '$resId' after RequestConflict (az rest GET failed with exit $rc):`n$restErr`nOriginal deployment error:`n$OriginalError")
            }
            $obj = if ($bodyRaw) { $bodyRaw | ConvertFrom-Json } else { $null }
            $state = if ($obj -and $obj.PSObject.Properties['properties'] -and $obj.properties -and $obj.properties.PSObject.Properties['provisioningState'] -and $obj.properties.provisioningState) {
                "$($obj.properties.provisioningState)".Trim()
            } elseif ($obj -and $obj.PSObject.Properties['provisioningState'] -and $obj.provisioningState) {
                "$($obj.provisioningState)".Trim()
            } else {
                ''
            }
            if (-not $state) {
                Fail ("Cannot determine provisioningState for conflicting resource '$resId' after RequestConflict (missing properties.provisioningState in GET response).`nOriginal deployment error:`n$OriginalError")
            }
            $lastState = $state
            if ($state -eq 'Succeeded') {
                Write-Host "    Conflicting resource '$resId' reached terminal provisioningState 'Succeeded' (poll $poll/$maxPolls)."
                $resolved = $true
                break
            }
            if ($state -in @('Failed', 'Canceled', 'Cancelled')) {
                Fail ("Conflicting resource '$resId' reached terminal failure provisioningState '$state' while resolving RequestConflict; refusing to retry deployment.`nOriginal deployment error:`n$OriginalError")
            }
            Write-Warning "    Conflicting resource '$resId' provisioningState is '$state' (non-terminal, poll $poll/$maxPolls); waiting ${pollSec}s..."
            if ($poll -lt $maxPolls -and $pollSec -gt 0) {
                Start-Sleep -Seconds $pollSec
            }
        }
        if (-not $resolved) {
            $totalWait = $maxPolls * $pollSec
            Fail ("Timed out after $maxPolls polls (${totalWait}s) waiting for conflicting resource '$resId' to reach a terminal provisioningState (last observed state: '$lastState').`nOriginal deployment error:`n$OriginalError")
        }
    }
}

Step 'Deploying (az deployment sub create)'
$raw = $null
for ($armAttempt = 1; $armAttempt -le 3; $armAttempt++) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $out = & az deployment sub create --name $deploymentName --location $p.location --template-file $template --parameters @bicepParams -o json 2>$errFile
        $code = $LASTEXITCODE
        $errText = if (Test-Path $errFile) { Get-Content $errFile -Raw } else { '' }
    } finally {
        $ErrorActionPreference = $prev
        Remove-Item $errFile -Force -ErrorAction SilentlyContinue
    }
    if ($code -eq 0) {
        $raw = $out
        break
    }
    if ($armAttempt -lt 3 -and $errText -match 'RequestConflict|Another operation is in progress|provisioning state is not terminal') {
        $conflictIds = @(Get-ConflictingArmResourceIds $errText $targetRg)
        Wait-ConflictingArmResources $conflictIds $armAttempt 3 $errText
        continue
    }
    if ($errText -match 'FlagMustBeSetForRestore') {
        $purgeHint = "`nHint: A previous CognitiveServices/Foundry account with this name is in Azure's soft-deleted state. Purge it first with:`n" +
                     "  pwsh -File .\deploy\uninstall.ps1 -ParametersFile `"$ParametersFile`" -OwnershipFile .\deploy\.out\ownership.json -Purge`n" +
                     "or: az cognitiveservices account purge --name <account-name> --resource-group $targetRg --location $($p.location)"
        Fail "az deployment sub create --name $deploymentName failed (exit $code):`n$errText$purgeHint"
    }
    Fail "az deployment sub create --name $deploymentName failed (exit $code):`n$errText"
}
$outputs = ($raw | ConvertFrom-Json).properties.outputs

# 6. Publish required runtime inputs independently of the optional scorer check.
if (-not $SkipSmokeTest) {
    Step 'Checking the local deterministic scorer'
    $receiptFile = Join-Path $outDir 'smoke-receipt.json'
    $smoke = & $python (Join-Path $DeployDir 'pin_policy.py') smoke $resolvedPolicy $receiptFile
    if ($LASTEXITCODE -ne 0) { Fail "Scorer smoke test failed:`n$smoke" }
    Write-Host "    scored: $smoke"
}
Step "Uploading $parcelCount receipts, _dataset.json, and pinned policy"
$receiptsBatchDir = $deploymentData.receiptsPath
Copy-Item -Path $resolvedPolicy -Destination (Join-Path $receiptsBatchDir '_policy.json') -Force
Copy-Item -Path $deploymentData.datasetPath -Destination (Join-Path $receiptsBatchDir '_dataset.json') -Force
$uploaded = $false
for ($i = 1; $i -le 8 -and -not $uploaded; $i++) {
    if (Test-Az storage blob upload-batch --auth-mode login --account-name $outputs.storageAccountName.value --destination $outputs.receiptsContainer.value --destination-path $deploymentData.receiptsPrefix --source $receiptsBatchDir --overwrite true --only-show-errors) {
        $uploaded = $true
    } else {
        Write-Warning "Required input upload failed; retrying for RBAC/perimeter propagation ($i/8)."
        if ($i -lt 8) { Start-Sleep -Seconds 15 }
    }
}
if (-not $uploaded) { Fail 'Required receipt/policy/dataset upload failed. Check Storage Blob Data Contributor and the storage network perimeter, then re-run.' }
Write-Host "    uploaded $parcelCount receipts + _policy.json + _dataset.json to $($outputs.storageAccountName.value)/$($outputs.receiptsContainer.value)/$($deploymentData.receiptsPrefix)" -ForegroundColor Green

if ($outputs.functionAppName -and $outputs.functionAppName.value) {
    Invoke-Az functionapp config appsettings set --resource-group $outputs.resourceGroupName.value --name $outputs.functionAppName.value --settings "RECEIPTS_PREFIX=$($deploymentData.receiptsPrefix)" "PINNED_POLICY_SHA256=$policySha" -o none
    $env:RECEIPTS_PREFIX = $deploymentData.receiptsPrefix
    Step "Packaging and publishing Azure Functions MCP tool host ($($outputs.functionAppName.value))"
    $funcZip = Join-Path $outDir 'tool_host.zip'
    & $python (Join-Path $DeployDir 'pin_policy.py') package_func $RepoRoot $funcZip
    if ($LASTEXITCODE -ne 0) { Fail 'Packaging cloud/tool_host failed.' }
    Invoke-Az functionapp deployment source config-zip --resource-group $outputs.resourceGroupName.value --name $outputs.functionAppName.value --src $funcZip --build-remote true | Out-Null
    Write-Host "    published MCP endpoint: $($outputs.functionAppEndpoint.value)" -ForegroundColor Green
}

# 7. Outputs
Step 'Deployment outputs'
$summary = [ordered]@{ policySha256 = $policySha; parcelCount = $parcelCount; receiptsPrefix = $deploymentData.receiptsPrefix }
foreach ($name in $outputs.PSObject.Properties.Name) { $summary[$name] = $outputs.$name.value }
$summary | ConvertTo-Json | Set-Content (Join-Path $outDir 'azure-outputs.json') -Encoding UTF8
$summary | ConvertTo-Json | Write-Host

if ($outputs.foundryEndpoint -and $outputs.functionAppEndpoint) {
    if (-not $p.existingFoundryAccountName -and -not $p.mcpAllowedAudiences) {
        $projUrl = "https://management.azure.com/subscriptions/$($p.subscriptionId)/resourceGroups/$($outputs.resourceGroupName.value)/providers/Microsoft.CognitiveServices/accounts/$($outputs.foundryAccountName.value)/projects/$($p.foundryProjectName)?api-version=2025-04-01-preview"
        $rawBlueprintId = (Invoke-Az rest --method get --url $projUrl --query "properties.agentIdentity.agentIdentityBlueprintId" -o tsv | Select-Object -Last 1)
        $blueprintId = if ($rawBlueprintId) { "$rawBlueprintId".Trim() } else { '' }
        if ($blueprintId) {
            Invoke-Az functionapp config appsettings set --resource-group $outputs.resourceGroupName.value --name $outputs.functionAppName.value --settings "MCP_ALLOWED_AUDIENCES=api://$blueprintId,$blueprintId,https://management.azure.com/,https://management.azure.com" -o none
        }
    }
    if (-not $SkipAgentSetup) {
    Step 'Creating Foundry MCP receipt connection and cloud pinned-score-agent'
    $env:PYTHONPATH = "$RepoRoot\src;$RepoRoot\scripts"
    $projEndpoint = ($outputs.foundryEndpoint.value.TrimEnd('/') + "/api/projects/$($p.foundryProjectName)")
    & $python (Join-Path $RepoRoot 'scripts/create_foundry_agent.py') --endpoint $projEndpoint --model $p.modelDeploymentName --mcp-url $outputs.functionAppEndpoint.value --subscription $p.subscriptionId --resource-group $outputs.resourceGroupName.value
    if ($LASTEXITCODE -ne 0) { Fail 'create_foundry_agent.py (cloud MCP mode) failed.' }
    }
}

if ($WithOntology) {
    Step 'Provisioning Fabric Lakehouse + Ontology + GraphModel (-WithOntology)'
    $env:PINNED_VERIFICATION_DIR = Join-Path $outDir 'verification'
    New-Item -ItemType Directory -Force $env:PINNED_VERIFICATION_DIR | Out-Null
    $fabricProvision = Join-Path $RepoRoot 'fabric/provision.ps1'
    & $fabricProvision -ParametersFile $deploymentData.fabricParametersPath -WithOntology
    Step 'Creating Foundry Fabric IQ connection and agent'
    $env:PYTHONPATH = "$RepoRoot\src;$RepoRoot\scripts"
    $env:AZURE_SUBSCRIPTION_ID = $p.subscriptionId
    $env:FOUNDRY_RESOURCE_GROUP = $outputs.resourceGroupName.value
    if ($outputs.foundryEndpoint) {
        $env:FOUNDRY_PROJECT_ENDPOINT = ($outputs.foundryEndpoint.value.TrimEnd('/') + "/api/projects/$($p.foundryProjectName)")
    }
    & $python (Join-Path $RepoRoot 'scripts/create_foundry_fabric_agent.py') --mode fabric-iq --model $p.modelDeploymentName --workspace-id $env:FABRIC_WORKSPACE_ID --ontology-id $env:FABRIC_ONTOLOGY_ID --subscription $p.subscriptionId --out (Join-Path $env:PINNED_VERIFICATION_DIR 'fabric-agent-setup.json')
    if ($LASTEXITCODE -ne 0) { Fail 'create_foundry_fabric_agent.py --mode fabric-iq failed.' }
}

Write-Host 'Done.' -ForegroundColor Green
