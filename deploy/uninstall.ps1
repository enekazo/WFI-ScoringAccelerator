<#
.SYNOPSIS
  Removes ONLY resources proven (by the ownership ledger) to have been created by deploy-all.ps1.
.DESCRIPTION
  Reads deploy\.out\ownership.json (exact IDs + creation evidence). Preview is fully offline.
  Reused/pre-existing resources, reused Fabric capacity, and unrelated contents of created containers are
  never deleted. Missing ledger or provenance => error / unresolved report, never name guessing.
  -Force only suppresses the confirmation prompt; it does not bypass -WhatIf or ownership checks.
  Soft-deleted Foundry accounts are purged only with -Purge (irreversible, exact account id only).
.EXAMPLE
  .\deploy\uninstall.ps1 -ParametersFile deploy\parameters.json -RemoveFabricItems -Purge -WhatIf
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
  [string]$ParametersFile,
  [string]$OwnershipFile = (Join-Path $PSScriptRoot '.out\ownership.json'),
  [switch]$Purge,
  [switch]$RemoveFabricItems,
  [switch]$ClearStorageDeleteProtection,
  [switch]$Force
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$tool = Join-Path $PSScriptRoot 'cleanup_plan.py'
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) { throw 'Python 3.11+ is required to run cleanup.' }

$common = @('--ledger', $OwnershipFile)
$opts = @()
if ($RemoveFabricItems) { $opts += '--remove-fabric' }
if ($Purge) { $opts += '--purge' }
if ($ClearStorageDeleteProtection) { $opts += '--clear-storage-delete-protection' }
if ($ParametersFile) {
  if (-not (Test-Path -LiteralPath $ParametersFile)) { throw "Parameters file not found: $ParametersFile" }
  $p = Get-Content -Raw -LiteralPath $ParametersFile | ConvertFrom-Json
  if ($p.tenantId) { $opts += @('--expect-tenant', [string]$p.tenantId) }
  if ($p.subscriptionId) { $opts += @('--expect-subscription', [string]$p.subscriptionId) }
  if ($p.location) { $opts += @('--default-location', [string]$p.location) }
}

Write-Host '=== Cleanup deletion plan (offline preview, no cloud calls) ===' -ForegroundColor Cyan
& $python.Source $tool @common plan @opts
if ($LASTEXITCODE -ne 0) { throw "Cleanup plan failed (exit $LASTEXITCODE); nothing was changed." }

$target = "ledger $OwnershipFile"
if ($Force) { $ConfirmPreference = 'None' }
$actionDesc = 'Delete the installer-created resources listed above' +
  $(if ($Purge) { ' AND irreversibly purge the soft-deleted Foundry account' } else { '' }) +
  $(if ($ClearStorageDeleteProtection) { ' AND disable blob soft-delete/versioning and empty unlocked containers on owned storage accounts' } else { '' })
if (-not $PSCmdlet.ShouldProcess($target, $actionDesc)) {
  Write-Host 'No resources were changed.' -ForegroundColor Yellow
  return
}

& $python.Source $tool @common execute @opts
$rc = $LASTEXITCODE
if ($rc -eq 0) { Write-Host 'Cleanup complete.' -ForegroundColor Green; return }
if ($rc -eq 2) { Write-Error 'Cleanup incomplete: see failed/unresolved entries above. Resolve and re-run (idempotent).'; exit 2 }
Write-Error "Cleanup failed (exit $rc)."; exit $rc
