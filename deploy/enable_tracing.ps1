<#
.SYNOPSIS
  Enables Foundry agent tracing: Log Analytics + workspace-based Application Insights + an AppInsights
  project connection on the Foundry project. Idempotent. No keys or connection strings are stored.
.PARAMETER ParametersFile
  deploy\parameters.json (falls back to the template under -WhatIf). Optional fields:
  logAnalyticsName, appInsightsName (defaults: <resourcePrefix>-law / <resourcePrefix>-appi).
.PARAMETER ResourceGroup / AccountName / ProjectName
  Foundry resource group, AI Services account and project (resource group must be the Foundry one).
.PARAMETER WhatIf
  Print the planned az calls and exit; no Azure calls are made.
.NOTES
  Hook for deploy-all.ps1 (after the azure-foundry stage):
    & "$DeployDir\enable_tracing.ps1" -ParametersFile $ParametersFile -ResourceGroup $rg -AccountName $acct -ProjectName $proj
  Agent runs emit traces when the client calls configure_azure_monitor with the connection string read at
  runtime (see scripts\run_traced_question.py).
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ParametersFile,
    [string]$ResourceGroup,
    [string]$AccountName,
    [string]$ProjectName,
    [string]$ApiVersion = '2025-04-01-preview'
)
$ErrorActionPreference = 'Stop'
$DeployDir = $PSScriptRoot
$dry = [bool]$WhatIfPreference
if (-not $ParametersFile) { $ParametersFile = Join-Path $DeployDir 'parameters.json' }
if (-not (Test-Path $ParametersFile)) {
    if ($dry) { $ParametersFile = Join-Path $DeployDir 'parameters.template.json' }
    else { Write-Error "Parameters file not found: $ParametersFile"; exit 1 }
}
$p = Get-Content $ParametersFile -Raw | ConvertFrom-Json
$prefix = if ($p.resourcePrefix) { $p.resourcePrefix } else { 'pinnedscore' }
$law  = if ($p.existingLogAnalyticsName) { $p.existingLogAnalyticsName } elseif ($p.logAnalyticsName) { $p.logAnalyticsName } else { "$prefix-law" }
$appi = if ($p.existingAppInsightsName) { $p.existingAppInsightsName } elseif ($p.appInsightsName) { $p.appInsightsName } else { "$prefix-appi" }
if (-not $ProjectName) { $ProjectName = $p.foundryProjectName }
if (-not $ResourceGroup) { $ResourceGroup = '<foundry-resource-group>' }
if (-not $AccountName)   { $AccountName = '<foundry-account>' }
$sub = $p.subscriptionId
$connUri = "https://management.azure.com/subscriptions/$sub/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts/$AccountName/projects/$ProjectName/connections/$appi`?api-version=$ApiVersion"

$plan = @(
    "az monitor log-analytics workspace create -g $ResourceGroup -n $law",
    "az monitor app-insights component create -g $ResourceGroup -a $appi --workspace <$law id> --kind web",
    "az rest --method put --uri <project connection '$appi', category AppInsights, target = App Insights resource id>"
)
if ($dry) {
    Write-Host '[WhatIf] enable_tracing plan (no Azure calls):' -ForegroundColor Yellow
    $plan | ForEach-Object { Write-Host "  $_" }
    return
}
foreach ($v in @($sub, $ResourceGroup, $AccountName, $ProjectName)) {
    if (-not $v -or $v -like '<*' -or $v -like '0000*') { Write-Error 'Subscription, ResourceGroup, AccountName and ProjectName must be set.'; exit 1 }
}

function Invoke-Az {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $o = & az @args 2>$null
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($code -ne 0) { Write-Error "az $($args -join ' ') failed"; exit 1 }
    $o
}

function Try-Az {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $o = & az @args 2>$null
        if ($LASTEXITCODE -eq 0 -and $o) { return "$($o | Select-Object -Last 1)".Trim() }
    } finally {
        $ErrorActionPreference = $prev
    }
    return ''
}

. (Join-Path $PSScriptRoot 'ownership-hooks.ps1')
$loc = if ($p.location) { "$($p.location)".Trim() } else { 'eastus2' }

Write-Host "==> Log Analytics $law"
$lawId = Try-Az monitor log-analytics workspace show -g $ResourceGroup -n $law --query id -o tsv
if (-not $lawId) {
    if ($p.existingLogAnalyticsName) {
        $lawId = "$((Invoke-Az monitor log-analytics workspace show -g $ResourceGroup -n $law --query id -o tsv) | Select-Object -Last 1)".Trim()
    } elseif (-not $p.existingAppInsightsName) {
        $lawId = "$((Invoke-Az monitor log-analytics workspace create -g $ResourceGroup -n $law -l $loc --query id -o tsv) | Select-Object -Last 1)".Trim()
        Record-IfTracked -Kind arm_resource -Id $lawId -Origin created -Evidence 'enable_tracing.ps1 created the Log Analytics workspace after az show found none'
    }
}
Write-Host "==> Application Insights $appi"
$appiId = Try-Az monitor app-insights component show -g $ResourceGroup -a $appi --query id -o tsv
if (-not $appiId) {
    if ($p.existingAppInsightsName) {
        $appiId = "$((Invoke-Az monitor app-insights component show -g $ResourceGroup -a $appi --query id -o tsv) | Select-Object -Last 1)".Trim()
    } else {
        $appiId = "$((Invoke-Az monitor app-insights component create -g $ResourceGroup -a $appi -l $loc --workspace $lawId --kind web --query id -o tsv) | Select-Object -Last 1)".Trim()
        Record-IfTracked -Kind arm_resource -Id $appiId -Origin created -Evidence 'enable_tracing.ps1 created the Application Insights component after az show found none'
    }
}
$connString = "$((Invoke-Az monitor app-insights component show -g $ResourceGroup -a $appi --query connectionString -o tsv) | Select-Object -Last 1)".Trim()

Write-Host "==> Project connection $appi (AppInsights)"
$connArmId = (($connUri -replace '^https://management\.azure\.com', '') -replace '\?.*$', '')
$connProbe = & az rest --method get --uri $connUri --query id -o tsv 2>&1
$connProbeCode = $LASTEXITCODE
if ($connProbeCode -eq 0) { $connExisted = $true }
elseif ("$connProbe" -match 'ResourceNotFound|NotFound|\(404\)|404') { $connExisted = $false }
else { throw "Cannot determine whether project connection '$appi' already exists (az rest failed: $connProbe). Refusing to record ownership without proof." }
$body = @{ properties = @{ category = 'AppInsights'; target = $appiId; authType = 'ApiKey'
        isSharedToAll = $false; credentials = @{ key = $connString }; metadata = @{ ApiType = 'Azure'; ResourceId = $appiId } } } | ConvertTo-Json -Depth 6 -Compress
$tmp = New-TemporaryFile
try {
    Set-Content -Path $tmp -Value $body -Encoding utf8
    Invoke-Az rest --method put --uri $connUri --body "@$tmp" --headers 'Content-Type=application/json' | Out-Null
} finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
if ($connExisted) {
    Record-IfTracked -Kind foundry_connection -Id $connArmId -Origin reused -Evidence 'AppInsights project connection existed before the PUT'
} else {
    Record-IfTracked -Kind foundry_connection -Id $connArmId -Origin created -Evidence 'GET returned 404 immediately before enable_tracing.ps1 PUT'
}
Write-Host "Tracing enabled. Clients fetch the connection string at runtime; nothing was written to the repo."
