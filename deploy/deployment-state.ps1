function Save-DeploymentState {
    $script:DeploymentState.updatedAt = [DateTimeOffset]::UtcNow.ToString('o')
    $temporary = "$StateFile.$PID.tmp"
    [IO.File]::WriteAllText($temporary, ($script:DeploymentState | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($temporary, $StateFile, $true)
}

function Get-ResumeStep($State) {
    foreach ($number in 1..9) {
        if ($State.steps."$number".status -notin @('completed', 'reused')) { return $number }
    }
    return 9
}

function Assert-Checkpoint($State) {
    if ($State.schemaVersion -ne 1 -or
        $State.parametersSha256 -ne (Get-FileHash -LiteralPath $ParametersFile).Hash -or
        $State.policySha256 -ne $p.policySha256 -or
        $State.sourceSha256 -ne (Get-DeploymentSourceHash)) {
        throw 'Checkpoint does not match these parameters, policy or deployment source. Start a full run from step 1 instead.'
    }
    foreach ($number in 1..9) {
        if ($State.steps."$number".id -ne $planObj.steps[$number - 1].id) {
            throw 'Checkpoint stage definitions do not match this installer.'
        }
    }
}

function Initialize-DeploymentState {
    if ($Resume -or $StartStep -gt 1) {
        if (-not (Test-Path -LiteralPath $StateFile)) {
            throw "Restart requires the matching checkpoint: $StateFile. Run from step 1 first."
        }
        $candidate = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json -AsHashtable
        Assert-Checkpoint $candidate
        if ($Resume) { $script:StartStep = Get-ResumeStep $candidate }
        foreach ($number in 1..9) {
            if ($number -lt $StartStep -and $candidate.steps."$number".status -notin @('completed', 'reused')) {
                throw "Cannot start at step ${StartStep}: prerequisite step $number is not complete."
            }
        }
        $script:DeploymentState = $candidate
        foreach ($number in $StartStep..9) {
            $script:DeploymentState.steps."$number".status = 'pending'
            $script:DeploymentState.steps."$number".Remove('error')
        }
    } else {
        if (Test-Path -LiteralPath $StateFile) {
            Copy-Item -LiteralPath $StateFile -Destination "$StateFile.$([guid]::NewGuid().ToString('N')).previous"
        }
        $script:DeploymentState = @{
            schemaVersion = 1
            parametersSha256 = (Get-FileHash -LiteralPath $ParametersFile).Hash
            policySha256 = $p.policySha256
            sourceSha256 = Get-DeploymentSourceHash
            startedAt = [DateTimeOffset]::UtcNow.ToString('o')
            steps = @{}
            artifacts = @{}
        }
        foreach ($stage in $planObj.steps) {
            $script:DeploymentState.steps."$($stage.order)" = @{ id = $stage.id; status = 'pending' }
        }
    }
    $script:DeploymentState.status = 'in_progress'
    $script:DeploymentState.accepted = $false
    New-Item -ItemType Directory -Force (Split-Path $StateFile -Parent) | Out-Null
    Save-DeploymentState
}

function Get-DeploymentArtifactHash([string]$Path, [switch]$Receipts) {
    if ($Receipts) {
        $files = @(Get-ChildItem -LiteralPath $Path -Filter 'P*.json' -File | Sort-Object Name)
        if ($files.Count -ne $p.parcelCount) { throw 'Checkpoint receipt count does not match the deployment.' }
        return Get-DeploymentHash (($files | ForEach-Object { "$($_.Name):$((Get-FileHash -LiteralPath $_.FullName).Hash)" }) -join "`n")
    }
    return (Get-FileHash -LiteralPath $Path).Hash
}

function Save-DeploymentArtifact([string]$Name, [string]$Path, [switch]$Receipts) {
    if (-not (Test-Path -LiteralPath $Path)) { throw "Required deployment output is missing: $Path" }
    $script:DeploymentState.artifacts[$Name] = @{ path = $Path; receipts = [bool]$Receipts; sha256 = (Get-DeploymentArtifactHash $Path -Receipts:$Receipts) }
}

function Read-DeploymentArtifact([string]$Name) {
    $artifact = $script:DeploymentState.artifacts[$Name]
    if (-not $artifact -or -not (Test-Path -LiteralPath $artifact.path)) {
        throw "Checkpoint output '$Name' is missing. Restart from its producing stage."
    }
    if ((Get-DeploymentArtifactHash $artifact.path -Receipts:$artifact.receipts) -ne $artifact.sha256) {
        throw "Checkpoint output '$Name' has changed. Restart from its producing stage."
    }
    if (-not $artifact.receipts) { return Get-Content -LiteralPath $artifact.path -Raw | ConvertFrom-Json }
}

function Invoke-DeploymentStage([int]$Number, [string]$Title, [scriptblock]$Action, [switch]$Always, [switch]$Skip) {
    $id = $planObj.steps[$Number - 1].id
    $script:CurrentStage = $Number
    Write-Progress -Activity 'WFI deployment' -Status "Step ${Number}/9: $Title" -PercentComplete ([int](100 * ($Number - 1) / 9))
    if ($Number -lt $StartStep -and -not $Always -and -not ($Number -eq 8 -and $Skip)) {
        Step $Number $id "[REUSED] $Title (checkpoint)"
        return
    }
    $entry = $script:DeploymentState.steps."$Number"
    foreach ($field in @('error', 'startedAt', 'completedAt', 'durationSeconds')) { $entry.Remove($field) }
    if ($Skip) {
        $entry.status = if ($Number -eq 8) { 'skipped' } else { 'reused' }
        Step $Number $id "[SKIPPED] $Title (explicit flag; not executed)"
        Save-DeploymentState
        return
    }
    $entry.status = 'running'
    $entry.startedAt = [DateTimeOffset]::UtcNow.ToString('o')
    Save-DeploymentState
    Step $Number $id "[RUNNING] $Title"
    $timer = [Diagnostics.Stopwatch]::StartNew()
    & $Action
    $timer.Stop()
    $entry.status = 'completed'
    $entry.durationSeconds = [Math]::Round($timer.Elapsed.TotalSeconds, 2)
    $entry.completedAt = [DateTimeOffset]::UtcNow.ToString('o')
    Save-DeploymentState
    Step $Number $id "[DONE] $Title ($($timer.Elapsed.ToString('hh\:mm\:ss')))"
}
function Get-DeploymentHash([string]$Text) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))).Replace('-', '') }
    finally { $hasher.Dispose() }
}

function Get-DeploymentSourceHash {
    $roots = @('deploy', 'fabric', 'src', 'scripts', 'cloud\tool_host', 'tests') |
        ForEach-Object { Join-Path $RepoRoot $_ } | Where-Object { Test-Path -LiteralPath $_ }
    $records = Get-ChildItem -LiteralPath $roots -Recurse -File |
        Where-Object {
            $_.Extension -in @('.ps1', '.py', '.bicep') -or $_.Name -in @('parameters.template.json', 'requirements.txt')
        } | Sort-Object FullName | ForEach-Object {
            "$($_.FullName.Substring($RepoRoot.Length)):$((Get-FileHash -LiteralPath $_.FullName).Hash)"
        }
    return Get-DeploymentHash ($records -join "`n")
}
