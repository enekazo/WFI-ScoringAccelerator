# Dot-source from deploy-all.ps1 / enable_tracing.ps1 to record creation provenance.
# Every function fails loudly: a ledger write error must stop the install, otherwise cleanup would be silently incomplete.
$script:OwnershipHooksDir = $PSScriptRoot
function Invoke-OwnershipLedger {
    param([string]$LedgerPath, [string[]]$CliArgs)
    $py = Join-Path $script:OwnershipHooksDir 'cleanup_plan.py'
    $out = & python $py --ledger $LedgerPath @CliArgs 2>&1
    if ($LASTEXITCODE -ne 0) { throw "ownership ledger update failed ($($CliArgs[0])): $out" }
}

# True only when tracking is on and the ledger already records this exact resource as created by this install.
function Test-LedgerCreated {
    param([Parameter(Mandatory)][string]$Kind, [Parameter(Mandatory)][string]$Id)
    $ledger = $env:PINNED_OWNERSHIP_LEDGER
    if (-not $ledger -or -not (Test-Path -LiteralPath $ledger)) { return $false }
    $doc = Get-Content -LiteralPath $ledger -Raw | ConvertFrom-Json
    foreach ($e in @($doc.entries)) {
        if ($e.kind -eq $Kind -and "$($e.id)".ToLowerInvariant() -eq $Id.ToLowerInvariant() -and $e.origin -in @('created', 'adopted')) { return $true }
    }
    return $false
}

function Initialize-OwnershipLedger {
    param([Parameter(Mandatory)][string]$LedgerPath, [Parameter(Mandatory)][string]$TenantId,
          [Parameter(Mandatory)][string]$SubscriptionId, [Parameter(Mandatory)][string]$DeploymentId)
    if (Test-Path -LiteralPath $LedgerPath) { return }
    Invoke-OwnershipLedger $LedgerPath @('init', '--tenant', $TenantId, '--subscription', $SubscriptionId, '--deployment-id', $DeploymentId)
}

# Call BEFORE the Bicep deployment: the resource IDs that already exist decide created-vs-reused.
function Save-ResourceGroupSnapshot {
    # `az resource list` is not guaranteed to include nested resources or role assignments: the snapshot is
    # existence evidence for top-level resources only; the classifier marks nested/RBAC items unresolved.
    param([Parameter(Mandatory)][string]$ResourceGroup, [Parameter(Mandatory)][string]$Path)
    $exists = (& az group exists -n $ResourceGroup 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "az group exists failed: $exists" }
    $ids = @()
    if ("$exists".Trim() -eq 'true') {
        $json = & az resource list -g $ResourceGroup --query '[].id' -o json 2>&1
        if ($LASTEXITCODE -ne 0) { throw "az resource list failed: $json" }
        $ids = @($json | ConvertFrom-Json)
        $rgId = & az group show -n $ResourceGroup --query id -o tsv 2>&1
        if ($LASTEXITCODE -ne 0) { throw "az group show failed: $rgId" }
        $ids += "$rgId".Trim()
    }
    ConvertTo-Json -InputObject @($ids) | Set-Content -LiteralPath $Path -Encoding utf8
}

# Call AFTER the Bicep deployment succeeds.
function Record-AzureDeployment {
    param([Parameter(Mandatory)][string]$LedgerPath, [Parameter(Mandatory)][string]$SnapshotPath,
          [Parameter(Mandatory)][string]$OperationsPath)
    Invoke-OwnershipLedger $LedgerPath @('record-azure', '--pre-snapshot', $SnapshotPath, '--operations', $OperationsPath)
}

# Used by scripts that also run standalone: records only when deploy-all exported PINNED_OWNERSHIP_LEDGER.
# When tracking is on, a failed write throws (no silent gaps).
function Record-IfTracked {
    param([Parameter(Mandatory)][string]$Kind, [Parameter(Mandatory)][string]$Id,
          [Parameter(Mandatory)][ValidateSet('created', 'reused')][string]$Origin,
          [Parameter(Mandatory)][string]$Evidence, [hashtable]$Attrs = @{})
    $ledger = $env:PINNED_OWNERSHIP_LEDGER
    if (-not $ledger) { return }
    Record-OwnedResource -LedgerPath $ledger -Kind $Kind -Id $Id -Origin $Origin -Evidence $Evidence -Attrs $Attrs
}

function Record-OwnedResource {
    param([Parameter(Mandatory)][string]$LedgerPath, [Parameter(Mandatory)][string]$Kind,
          [Parameter(Mandatory)][string]$Id, [Parameter(Mandatory)][ValidateSet('created', 'reused')][string]$Origin,
          [Parameter(Mandatory)][string]$Evidence, [hashtable]$Attrs = @{})
    $attrJson = ConvertTo-Json -InputObject $Attrs -Compress
    Invoke-OwnershipLedger $LedgerPath @('record', '--kind', $Kind, '--id', $Id, '--origin', $Origin,
        '--evidence', $Evidence, '--attrs', $attrJson)
}
