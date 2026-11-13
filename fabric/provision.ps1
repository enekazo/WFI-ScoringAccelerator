[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ParametersFile,
    [switch]$SkipTableCreation,
    [switch]$SkipLoad,
    [switch]$WithOntology,
    [switch]$DataOnly,
    [switch]$OntologyOnly
)
if ($DataOnly -and $OntologyOnly) { throw 'Use -DataOnly or -OntologyOnly, not both.' }
if ($DataOnly -or $OntologyOnly) { $WithOntology = $true }
if ($OntologyOnly) { $SkipTableCreation = $true; $SkipLoad = $true }
# Provisions a Fabric lakehouse, its medallion tables and loads the example data. Idempotent.
# With -WithOntology: also loads the 5 synthetic chain tables, defines the Fabric IQ ontology
# (5 entities, 4 relationships with composite key on CrewAccess), patches the GraphModel node and
# edge keys to ["parcel_id", "crew_type"], and runs a single MANUAL RefreshGraph (never scheduled).
# Auth: az CLI (az login). Use -WhatIf to print the plan without any network or az calls.
$ErrorActionPreference = 'Stop'
$api = 'https://api.fabric.microsoft.com/v1'
if (-not $ParametersFile) { $ParametersFile = Join-Path $PSScriptRoot 'parameters.json' }

if (-not (Test-Path $ParametersFile)) {
    if ($WhatIfPreference) {
        $ParametersFile = Join-Path $PSScriptRoot 'parameters.template.json'
        Write-Host "[WhatIf] parameters.json missing; reading template instead."
    } else { throw "Parameters file not found: $ParametersFile (copy parameters.template.json)" }
}
$p = Get-Content $ParametersFile -Raw | ConvertFrom-Json
foreach ($k in 'workspaceName', 'lakehouseName') {
    if (-not $p.$k) { throw "Missing parameter: $k" }
}
$ontologyName = if ($p.ontologyName) { $p.ontologyName } else { 'pinned_scoring_ontology' }
$repo = Split-Path $PSScriptRoot -Parent
. (Join-Path $repo 'deploy\ownership-hooks.ps1')
$python = 'python'
foreach ($candidate in 'python', 'python3', 'py') {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $python = $candidate; break }
}

# Table DDL comes from tables.sql (one spark.sql call per statement).
$ddlPath = Join-Path $PSScriptRoot 'sql/tables.sql'
$ddl = (Get-Content $ddlPath -Raw) -split "`n" | Where-Object { $_.TrimStart() -notlike '--*' }
$statements = ($ddl -join "`n") -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
$loadScript = Join-Path $PSScriptRoot 'livy/load_medallion.py'
$policyPath = if ($p.policyPath) { $p.policyPath } else { Join-Path $repo 'examples/wildfire/policy.json' }
$datasetPath = if ($p.datasetPath) { $p.datasetPath } else { Join-Path $repo 'data/dataset.json' }
$uploads = @(
    @{ Local = $policyPath; Remote = 'policy.json' }
    @{ Local = $datasetPath; Remote = 'dataset.json' }
)
$uploads += Get-ChildItem (Join-Path $repo 'src/pinned_scoring') -Recurse -Filter *.py | ForEach-Object {
    @{ Local = $_.FullName; Remote = 'code/pinned_scoring/' + $_.FullName.Substring((Join-Path $repo 'src/pinned_scoring').Length + 1).Replace('\', '/') }
}
Write-Host "Plan:"
if ($OntologyOnly) {
    Write-Host "  1. Require existing workspace '$($p.workspaceName)' from the completed data stage"
} elseif ($p.workspaceId) {
    Write-Host "  1. Find workspace '$($p.workspaceName)' ($($p.workspaceId)) (must already exist)"
} else {
    $planCap = if ($p.capacityName) { $p.capacityName } elseif ($p.capacityId) { $p.capacityId } else { 'configured capacity' }
    Write-Host "  1. Find workspace '$($p.workspaceName)' or create it on capacity '$planCap' if absent"
}
if ($OntologyOnly) { Write-Host "  2. Require existing lakehouse '$($p.lakehouseName)' from the completed data stage" }
else { Write-Host "  2. Create lakehouse '$($p.lakehouseName)' if absent" }
if (-not $SkipTableCreation) {
    Write-Host "  3. Run $($statements.Count) CREATE TABLE IF NOT EXISTS statements through Livy"
}
if (-not $SkipLoad) {
    Write-Host "  4. Upload policy.json, dataset.json and the pinned_scoring sources ($($uploads.Count) files) to OneLake Files/"
    Write-Host "  5. Run fabric/livy/load_medallion.py via Livy: raw -> silver -> gold, then print row counts"
}
if ($WithOntology) {
    if (-not $SkipLoad) { Write-Host "  6. Load synthetic graph + 5 chain tables (parcel, road_segment, home_group, water_source, crew_access)" }
    if (-not $DataOnly) {
        Write-Host "  7. Create/update ontology '$ontologyName' (5 entities, 4 relationships; CrewAccess composite key parcel_id+crew_type)"
        Write-Host "  8. Patch GraphModel composite keys and run MANUAL RefreshGraph (no schedule; do not re-save graph in portal)"
    } else { Write-Host '  Ontology definition and graph refresh are deferred to the next stage.' }
}
if ($WhatIfPreference) {
    $count = if ($null -ne $p.parcelCount) { $p.parcelCount } else { 150 }
    Write-Host "[WhatIf] Dataset: $count parcels, with policy and receipts shared across Azure and Fabric."
    if (-not $SkipTableCreation) { $statements | ForEach-Object { Write-Host ("     DDL: " + ($_ -split "`n")[0]) } }
    Write-Host "[WhatIf] No calls made."
    return
}
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'PowerShell 7 or later is required for live Fabric provisioning.' }
if (-not $env:PINNED_VERIFICATION_DIR) { $env:PINNED_VERIFICATION_DIR = Join-Path $repo 'deploy/.out/verification' }
New-Item -ItemType Directory -Force $env:PINNED_VERIFICATION_DIR | Out-Null
if (-not $p.datasetPath) {
    $outDir = Join-Path $repo 'deploy/.out/fabric-data'
    & $python (Join-Path $repo 'deploy/pin_policy.py') prepare_data $policyPath $ParametersFile $outDir
    if ($LASTEXITCODE -ne 0) { throw 'Preparing the configured Fabric dataset failed.' }
    $manifest = Get-Content (Join-Path $outDir 'deployment-data.json') -Raw | ConvertFrom-Json
    $p = Get-Content $manifest.fabricParametersPath -Raw | ConvertFrom-Json
    $uploads[1].Local = $p.datasetPath
}
$env:PINNED_RECEIPTS_DIR = $p.receiptsPath
$env:PINNED_GRAPH_PATH = $p.graphPath

function Invoke-Fabric($Method, $Path, $Body = $null) {
    $token = az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv
    if (-not $token) { throw 'az login required' }
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        $args = @{ Method = $Method; Uri = "$api$Path"; Headers = @{ Authorization = "Bearer $token" }
                   ContentType = 'application/json'; ResponseHeadersVariable = 'hdr'; StatusCodeVariable = 'code' }
        if ($Body) { $args.Body = ($Body | ConvertTo-Json -Depth 20) }
        try {
            $result = Invoke-RestMethod @args
            if ($code -eq 202 -and $hdr.Location) {
                do {
                    $retryAfter = 3
                    if ($hdr['Retry-After']) { [int]::TryParse(($hdr['Retry-After'] | Select-Object -First 1), [ref]$retryAfter) | Out-Null }
                    Start-Sleep -Seconds $retryAfter
                    $poll = Invoke-RestMethod -Uri ($hdr.Location | Select-Object -First 1) -Headers @{ Authorization = "Bearer $token" } -StatusCodeVariable pc -ResponseHeadersVariable hdr
                } while ($pc -eq 202 -or $poll.status -in 'Running', 'NotStarted', 'InProgress')
                if ($poll -and $poll.status -in 'Failed', 'Cancelled', 'Canceled') {
                    throw "Fabric async operation on '$Path' failed with status '$($poll.status)'."
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
                Write-Host "  [429] Fabric throttled; waiting ${waitSec}s (Retry-After) before retry ($attempt/5)..."
                Start-Sleep -Seconds $waitSec
                continue
            }
            throw
        }
    }
}

function Get-StorageToken { az account get-access-token --resource https://storage.azure.com --query accessToken -o tsv }

# Livy is used instead of a notebook job: notebook jobs on a fresh workspace were observed to fail with
# "System cancelled the Spark session due to statement execution failures".
function Start-LivySession($WorkspaceId, $LakehouseId) {
    $base = "/workspaces/$WorkspaceId/lakehouses/$LakehouseId/livyapi/versions/2023-12-01/sessions"
    $s = Invoke-Fabric Post $base @{
        kind           = 'pyspark'
        driverCores    = 4
        driverMemory   = '28g'
        executorCores  = 4
        executorMemory = '28g'
        numExecutors   = 1
    }
    $deadline = (Get-Date).AddMinutes(25)
    do {
        Start-Sleep -Seconds 10
        $state = (Invoke-Fabric Get "$base/$($s.id)").state
        Write-Host "  Livy session $($s.id): $state"
        if ($state -in 'dead', 'error', 'killed') { throw "Livy session failed: $state" }
        if ((Get-Date) -gt $deadline) { throw 'Livy session start timed out' }
    } while ($state -ne 'idle')
    return "$base/$($s.id)"
}

function Invoke-LivyStatement($SessionPath, $Code) {
    $st = Invoke-Fabric Post "$SessionPath/statements" @{ code = $Code; kind = 'pyspark' }
    do {
        Start-Sleep -Seconds 3
        $st = Invoke-Fabric Get "$SessionPath/statements/$($st.id)"
    } while ($st.state -notin 'available', 'error', 'cancelled')
    if ($st.output.status -ne 'ok') { throw "Livy statement failed: $($st.output.evalue)`n$($st.output.traceback -join "`n")" }
    return $st.output.data.'text/plain'
}

# OneLake speaks the ADLS Gen2 DFS API; ids in the path avoid name-resolution issues.
function Send-OneLakeFile($WorkspaceId, $LakehouseId, $LocalPath, $RemotePath) {
    $headers = @{ Authorization = "Bearer $(Get-StorageToken)"; 'x-ms-version' = '2023-11-03' }
    $uri = "https://onelake.dfs.fabric.microsoft.com/$WorkspaceId/$LakehouseId/Files/$RemotePath"
    $bytes = [IO.File]::ReadAllBytes($LocalPath)
    Invoke-RestMethod -Method Put -Uri "${uri}?resource=file" -Headers $headers | Out-Null
    if ($bytes.Length) {
        Invoke-RestMethod -Method Patch -Uri "${uri}?action=append&position=0" -Headers $headers -Body $bytes `
            -ContentType 'application/octet-stream' | Out-Null
    }
    Invoke-RestMethod -Method Patch -Uri "${uri}?action=flush&position=$($bytes.Length)" -Headers $headers | Out-Null
}

if ($p.workspaceId) {
    $ws = Invoke-Fabric Get "/workspaces/$($p.workspaceId)"
    if (-not $ws -or -not $ws.id) {
        throw "Configured workspaceId '$($p.workspaceId)' was not found or is not accessible."
    }
    if ($ws.displayName -ne $p.workspaceName) { throw 'Fabric workspace ID and configured name do not match.' }
} else {
    $workspaces = @()
    $pagePath = '/workspaces'
    do {
        $page = Invoke-Fabric Get $pagePath
        $workspaces += @($page.value)
        $pagePath = if ($page.continuationToken) { '/workspaces?continuationToken=' + [uri]::EscapeDataString($page.continuationToken) } else { $null }
    } while ($pagePath)
    $matches = @($workspaces | Where-Object displayName -eq $p.workspaceName)
    if ($matches.Count -gt 1) { throw "Expected one accessible workspace named '$($p.workspaceName)', found $($matches.Count). Set fabricWorkspaceId in deployment parameters to disambiguate." }
    if ($matches.Count -eq 1) {
        $ws = $matches[0]
    } else {
        if ($OntologyOnly) { throw "Workspace '$($p.workspaceName)' not found; run the data provisioning stage first." }
        $targetCapId = $p.capacityId
        $targetCapName = $p.capacityName
        if (-not $targetCapId -and -not $targetCapName) {
            throw "Expected one accessible workspace named '$($p.workspaceName)', found 0. Configure capacityName or capacityId to create the workspace."
        }
        $capacities = @()
        $capPath = '/capacities'
        do {
            $capPage = Invoke-Fabric Get $capPath
            $capacities += @($capPage.value)
            $capPath = if ($capPage.continuationToken) { '/capacities?continuationToken=' + [uri]::EscapeDataString($capPage.continuationToken) } else { $null }
        } while ($capPath)
        $capMatches = if ($targetCapId) {
            @($capacities | Where-Object id -eq $targetCapId)
        } else {
            @($capacities | Where-Object displayName -eq $targetCapName)
        }
        if ($capMatches.Count -ne 1) {
            $capLabel = if ($targetCapName) { $targetCapName } else { $targetCapId }
            throw "Expected one accessible Fabric capacity matching '$capLabel', found $($capMatches.Count)."
        }
        $capObj = $capMatches[0]
        if ($targetCapName -and $capObj.displayName -ne $targetCapName) {
            throw "Fabric capacity ID and configured capacityName do not match: actual '$($capObj.displayName)'."
        }
        if ($capObj.state -ne 'Active') {
            throw "Fabric capacity '$($capObj.displayName)' is not active: $($capObj.state)."
        }
        if ($PSCmdlet.ShouldProcess($p.workspaceName, "Create Fabric workspace on capacity '$($capObj.displayName)' ($($capObj.id))")) {
            $ws = Invoke-Fabric Post '/workspaces' @{
                displayName = $p.workspaceName
                capacityId  = $capObj.id
            }
            if (-not $ws -or -not $ws.id) {
                $recheck = @((Invoke-Fabric Get '/workspaces').value | Where-Object displayName -eq $p.workspaceName)
                if ($recheck.Count -ne 1) { throw "Workspace '$($p.workspaceName)' was not found after creation." }
                $ws = $recheck[0]
            }
            Record-IfTracked -Kind fabric_workspace -Id "$($ws.id)" -Origin created -Evidence "provision.ps1 POST /v1/workspaces created '$($p.workspaceName)'"
            if ($ws.capacityId -ne $capObj.id) {
                Invoke-Fabric Post "/workspaces/$($ws.id)/assignToCapacity" @{ capacityId = $capObj.id } | Out-Null
                $ws = Invoke-Fabric Get "/workspaces/$($ws.id)"
                if ($ws.capacityId -ne $capObj.id) {
                    throw "Workspace '$($p.workspaceName)' ($($ws.id)) capacity assignment to '$($capObj.id)' was not confirmed."
                }
            }
        }
    }
}
if ($ws.displayName -ne $p.workspaceName) { throw 'Fabric workspace ID and configured name do not match.' }
if (($p.capacityId -or $p.capacityName) -and -not $ws.capacityId -and -not $OntologyOnly) {
    $capToAssign = $p.capacityId
    if (-not $capToAssign) {
        $capList = @((Invoke-Fabric Get '/capacities').value | Where-Object displayName -eq $p.capacityName)
        if ($capList.Count -ne 1) { throw "Expected one accessible Fabric capacity named '$($p.capacityName)', found $($capList.Count)." }
        if ($capList[0].state -ne 'Active') { throw "Fabric capacity '$($capList[0].displayName)' is not active: $($capList[0].state)." }
        $capToAssign = $capList[0].id
    }
    if ($PSCmdlet.ShouldProcess($ws.displayName, "Assign Fabric workspace to capacity '$capToAssign'")) {
        Invoke-Fabric Post "/workspaces/$($ws.id)/assignToCapacity" @{ capacityId = $capToAssign } | Out-Null
        $ws = Invoke-Fabric Get "/workspaces/$($ws.id)"
        if ($ws.capacityId -ne $capToAssign) {
            throw "Workspace '$($ws.displayName)' ($($ws.id)) capacity assignment to '$capToAssign' was not confirmed."
        }
    }
}

if ($p.lakehouseId) {
    $lh = Invoke-Fabric Get "/workspaces/$($ws.id)/lakehouses/$($p.lakehouseId)"
    if ($lh.displayName -ne $p.lakehouseName) { throw 'Fabric lakehouse ID and configured name do not match.' }
} else {
    $matches = @((Invoke-Fabric Get "/workspaces/$($ws.id)/lakehouses").value | Where-Object displayName -eq $p.lakehouseName)
    if ($matches.Count -gt 1) { throw "Ambiguous lakehouse name '$($p.lakehouseName)'." }
    $lh = $matches | Select-Object -First 1
}
if (-not $lh) {
    if ($OntologyOnly) { throw 'Ontology-only provisioning requires the lakehouse from the completed data stage.' }
    if ($PSCmdlet.ShouldProcess($p.lakehouseName, 'Create lakehouse')) {
        Write-Host "  -> [Step 4.1] Creating Fabric lakehouse '$($p.lakehouseName)'..."
        Invoke-Fabric Post "/workspaces/$($ws.id)/lakehouses" @{ displayName = $p.lakehouseName } | Out-Null
        $lh = (Invoke-Fabric Get "/workspaces/$($ws.id)/lakehouses").value | Where-Object displayName -eq $p.lakehouseName
        Record-IfTracked -Kind fabric_item -Id "$($lh.id)" -Origin created -Evidence "provision.ps1 created lakehouse '$($p.lakehouseName)' after listing found none" -Attrs @{ workspaceId = "$($ws.id)"; itemType = 'Lakehouse' }
        Write-Host "  -> [Step 4.1] Created lakehouse '$($p.lakehouseName)' ($($lh.id))."
    }
} else { Write-Host "  -> [Step 4.1] Lakehouse exists: $($lh.id)" }
if (-not $lh.id) { throw "Lakehouse '$($p.lakehouseName)' was not resolved." }

$session = $null
try {
    if (-not $SkipTableCreation -or -not $SkipLoad) {
        if ($PSCmdlet.ShouldProcess($ws.displayName, 'Configure workspace Starter Pool Spark settings (1 node, 2m timeout)')) {
            try {
                Invoke-Fabric Patch "/workspaces/$($ws.id)/spark/settings" @{
                    pool = @{ starterPool = @{ maxNodeCount = 1; maxExecutors = 1 } }
                    job  = @{ sessionTimeoutInMinutes = 2 }
                } | Out-Null
            } catch {
                # Non-fatal if caller lacks workspace Spark settings permission or offline test stub omits endpoint
            }
        }
        Write-Host "  -> [Step 4.2] Starting Spark Livy session on lakehouse '$($p.lakehouseName)' (single-node 8 vCores, can take 1-3 min)..."
        $session = Start-LivySession $ws.id $lh.id
        Write-Host "  -> [Step 4.2] Spark Livy session ready ($session)."
        if (-not $SkipTableCreation) {
            Write-Host "  -> [Step 4.3] Creating $($statements.Count) medallion Delta tables via Spark SQL..."
            foreach ($s in $statements) { Invoke-LivyStatement $session ('spark.sql("""' + $s + '""")') | Out-Null }
            Write-Host "Tables ensured: $($statements.Count) statements (IF NOT EXISTS)."
        }
        if (-not $SkipLoad) {
            Write-Host "  -> [Step 4.4] Uploading $($uploads.Count) seed files to OneLake Files/ and loading medallion tables..."
            foreach ($u in $uploads) { Send-OneLakeFile $ws.id $lh.id $u.Local $u.Remote }
            Write-Host "Uploaded $($uploads.Count) files to Files/."
            $loadCode = "WORKSPACE_ID = '$($ws.id)'`nLAKEHOUSE_ID = '$($lh.id)'`n" + (Get-Content $loadScript -Raw)
            Write-Host (Invoke-LivyStatement $session $loadCode)
        }
    }

    if ($WithOntology) {
        # Refresh the item: a new lakehouse's SQL endpoint can provision asynchronously.
        Write-Host "  -> [Step 4.5] Waiting for Lakehouse SQL analytics endpoint to become ready..."
        for ($attempt = 1; $attempt -le 12; $attempt++) {
            $lh = Invoke-Fabric Get "/workspaces/$($ws.id)/lakehouses/$($lh.id)"
            if ($lh.properties.sqlEndpointProperties.connectionString) { break }
            if ($attempt -lt 12) { Start-Sleep -Seconds 10 }
        }
        if (-not $lh.properties.sqlEndpointProperties.connectionString) { throw 'The target lakehouse SQL endpoint is not ready. Retry once it is available; no historical endpoint will be used.' }
        Write-Host "  -> [Step 4.5] SQL analytics endpoint ready: $($lh.properties.sqlEndpointProperties.connectionString)"
        if ($p.sqlServer -and $p.sqlServer -ne $lh.properties.sqlEndpointProperties.connectionString) { throw 'Configured fabricSqlServer does not match the discovered lakehouse SQL endpoint.' }
        $env:FABRIC_WORKSPACE_ID = $ws.id
        $env:FABRIC_LAKEHOUSE_ID = $lh.id
        $env:FABRIC_SQL_DATABASE = $p.lakehouseName
        if ($lh.properties -and $lh.properties.sqlEndpointProperties -and $lh.properties.sqlEndpointProperties.connectionString) {
            $env:FABRIC_SQL_SERVER = $lh.properties.sqlEndpointProperties.connectionString
        }
        $env:PYTHONPATH = "$repo\src;$repo\scripts"

        if (-not $SkipLoad) {
            Write-Host "  -> [Step 4.6] Loading synthetic graph and 5 chain tables into '$($p.lakehouseName)' (reusing Spark session)..."
            if ($session) {
                $env:FABRIC_LIVY_SESSION_URL = "$api$session"
                $env:FABRIC_ACCESS_TOKEN = (az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv | Select-Object -Last 1).Trim()
            }
            if ($lh.properties.sqlEndpointProperties.id) {
                $env:FABRIC_SQL_ENDPOINT_ID = $lh.properties.sqlEndpointProperties.id
            }
            try {
                & $python (Join-Path $repo 'scripts/fabric_load_graph.py')
                if ($LASTEXITCODE -ne 0) { throw 'fabric_load_graph.py failed' }
                & $python (Join-Path $repo 'scripts/fabric_load_chain.py') --no-verify
                if ($LASTEXITCODE -ne 0) { throw 'fabric_load_chain.py failed' }
            } finally {
                Remove-Item Env:FABRIC_LIVY_SESSION_URL -ErrorAction SilentlyContinue
                Remove-Item Env:FABRIC_ACCESS_TOKEN -ErrorAction SilentlyContinue
                Remove-Item Env:FABRIC_SQL_ENDPOINT_ID -ErrorAction SilentlyContinue
            }
        }
    }
} finally {
    if ($session) {
        Invoke-Fabric Delete $session | Out-Null
    }
}

if ($WithOntology) {
    if ($DataOnly) {
        Write-Host 'Data stage complete; ontology definition and graph refresh are deferred to step 5.'
        return
    }
    $ontologies = (Invoke-Fabric Get "/workspaces/$($ws.id)/ontologies").value
    $ont = $ontologies | Where-Object displayName -eq $ontologyName
    if (@($ont).Count -gt 1) { throw "Ambiguous ontology name '$ontologyName'." }
    if ($p.ontologyId -and $ont.id -ne $p.ontologyId) { throw 'Configured fabricOntologyId does not match the target ontology.' }
    $ontCreated = $false
    if (-not $ont) {
        Write-Host "Creating ontology '$ontologyName'..."
        Invoke-Fabric Post "/workspaces/$($ws.id)/ontologies" @{ displayName = $ontologyName } | Out-Null
        $ont = (Invoke-Fabric Get "/workspaces/$($ws.id)/ontologies").value | Where-Object displayName -eq $ontologyName
        Record-IfTracked -Kind fabric_item -Id "$($ont.id)" -Origin created -Evidence "provision.ps1 created ontology '$ontologyName' after listing found none" -Attrs @{ workspaceId = "$($ws.id)"; itemType = 'Ontology' }
        $ontCreated = $true
    } else {
        Write-Host "Ontology exists: $($ont.id)"
    }
    $env:FABRIC_ONTOLOGY_ID = $ont.id

    Write-Host "Updating ontology definition (5 entities, 4 relationships, CrewAccess composite key)..."
    & $python (Join-Path $repo 'scripts/fabric_ontology_define_chain.py')
    if ($LASTEXITCODE -ne 0) { throw 'fabric_ontology_define_chain.py failed' }

    Write-Host "Patching GraphModel composite keys + edge and triggering MANUAL RefreshGraph..."
    $graphArgs = @('--workspace-id', $ws.id, '--lakehouse-id', $lh.id, '--ontology-id', $ont.id, '--apply', '--refresh')
    if ($p.graphModelId) { $graphArgs += @('--graph-model-id', $p.graphModelId) }
    & $python (Join-Path $repo 'scripts/fabric_graph_model_fix.py') @graphArgs
    if ($LASTEXITCODE -ne 0) { throw 'fabric_graph_model_fix.py failed' }
    if ($ontCreated -or (Test-LedgerCreated -Kind fabric_item -Id "$($ont.id)")) {
        # The GraphModel is auto-created with the ontology this install just created.
        $gms = @((Invoke-Fabric Get "/workspaces/$($ws.id)/graphModels").value)
        if ($gms.Count -ne 1) { throw "Expected exactly one GraphModel after creating ontology '$ontologyName', found $($gms.Count); cannot record ownership." }
        Record-IfTracked -Kind fabric_item -Id "$($gms[0].id)" -Origin created -Evidence 'GraphModel auto-created with the ontology created by this install' -Attrs @{ workspaceId = "$($ws.id)"; itemType = 'GraphModel' }
    }
}
