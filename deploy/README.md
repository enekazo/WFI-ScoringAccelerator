# Detailed deployment instructions

The one-command entry point is `deploy\deploy-all.ps1`. It deploys Azure resources into your chosen subscription and tenant, publishes the Azure Functions receipt tools **and deterministic scoring/answer-verification routes**, resolves or creates the configured Fabric capacity/workspace, loads the dataset into Fabric, creates the ontology and graph, connects two independent Foundry agents and runs verification. It reuses an accessible active capacity and existing workspace; when both are absent, it provisions the configured Azure Fabric capacity and creates the workspace. It does not create an Entra tenant or subscription, activate/resize a paused or undersized capacity, grant tenant feature access, or deploy an operational execution/approval agent.

**Readiness:** a full Step 1-9 installation completed successfully on **12 November 2026**, with **`status: accepted`** and **`accepted: true`**. The [installation evidence index](../docs/evidence/installation-acceptance/summary.json) is the single acceptance record, with the matching source fingerprint, checkpoint and verification outputs. See [verification status](#verification-status).

For the short installation path, see the [README Quick start](../README.md#quick-start). Run all commands below from the repository root in PowerShell 7.

## Prerequisites
- PowerShell 7 for the full installer
- [Azure CLI](https://aka.ms/installazurecli) (Bicep is auto-installed), signed in to the target tenant: `az login --tenant <tenantId>`
- Python 3.11+
- Rights: Owner (or Contributor + User Access Administrator) on the target subscription, plus Foundry project/agent access; model/version/SKU availability and quota in `location` must be confirmed before installation.
- A configured Fabric capacity SKU of **F4 or higher**, with permission to reuse an active capacity or provision a missing Azure Fabric capacity in the target resource group. Fill in `fabricCapacityName` and `fabricCapacitySku`. When both capacity and workspace are absent, the installer provisions them; a new capacity name must contain 3–63 lowercase letters/digits, starting with a letter. Optional `fabricCapacityAdmin` selects its administrator; otherwise the signed-in operator is used. Creating a workspace requires `fabricWorkspaceId` to be blank. An existing workspace must already be assigned to the configured accessible, active capacity; missing/inaccessible assignments fail closed. You also need permission to create/update lakehouse, ontology and graph items, use Spark/Livy, and read the SQL endpoint.
- Enable **Users can create Fabric items** and **Users can create ontology (preview) items** for the operator. [Microsoft Learn's ontology MCP prerequisites](https://learn.microsoft.com/fabric/iq/ontology/how-to-use-ontology-mcp-server) currently specify paid F2+ or qualifying P capacity. **F4+ is this installer's conservative minimum, not a universal product minimum.** This installer supports F SKUs only. Ontology MCP is preview and currently does not support service-principal access; use the signed-in operator/delegated user path.
- Access to Azure management, Entra sign-in/JWKS, Foundry, Functions, Blob, Fabric, OneLake and Fabric SQL endpoints. Subscription policies, resource-provider registration, naming restrictions, network controls and preview availability can block an otherwise valid configuration.

After cloning the repository, install the Python packages used by the installer and its verification scripts:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]" -r deploy\requirements.txt
```

Use Python 3.11 for the deployment environment when possible (the Function runtime is configured separately by Bicep). `deploy\requirements.txt` pins the operator-side Foundry/OpenAI/SQL SDKs used for preparation. The Foundry SDK must provide `AIProjectClient`, `PromptAgentDefinition` and `MCPTool`. Fabric SQL checks use `mssql-python`, which bundles its driver; alternatively, the existing connector can use `pyodbc` with Microsoft ODBC Driver 18 installed. The Function App's dependencies are packaged separately from `cloud\tool_host\requirements.txt`.

## Steps

### 1. Prepare the private configuration

```powershell
Copy-Item deploy\parameters.template.json deploy\parameters.json
notepad deploy\parameters.json
```

Do not overwrite an existing private file. Fill in `subscriptionId`, `tenantId`, `location`,
a unique `resourcePrefix`, model settings, and the actual `fabricCapacityName`,
`fabricCapacitySku` and `fabricWorkspaceName`. Leave `fabricWorkspaceId` blank when
the workspace should be created in stage 4 (or resolved by unique display name); set
`fabricWorkspaceId` when targeting a specific pre-existing workspace (especially if
names repeat — an explicit ID that is missing or name-mismatched fails closed). Leave
other existing-resource fields and item IDs blank when creating those items. Set
`parcelCount` (default 150). The template model/version is an example, not a guarantee
of current regional availability.

The real file and all `deploy\.out\` outputs are gitignored. Keep them private.
For another file, pass `-ParametersFile "C:\path\parameters.json"` to every command.

### 2. Run the offline preparation checks

```powershell
python -m pytest -q
.\deploy\deploy-all.ps1 -ParametersFile .\deploy\parameters.json -WhatIf
```

The full installer's `-WhatIf` and `-DryRun` do not call Azure or Fabric. They validate
the local configuration/plan; they cannot establish live quota, permissions, model
availability, preview access or capacity state. Do not treat this as deployment success.

### 3. Sign in to and confirm the target

```powershell
$config = Get-Content deploy\parameters.json -Raw | ConvertFrom-Json
az extension add --name application-insights
az login --tenant $config.tenantId
az account set --subscription $config.subscriptionId
az account show --query "{subscription:id,tenant:tenantId,name:name}" -o json
```

Confirm these are the intended target IDs and the model/version/SKU has quota in the
chosen region. For reuse, confirm the workspace is assigned to the named active
capacity. For creation, confirm the capacity name, administrator, region and permissions.
If reusing Foundry, the configured account must be in the target resource group
and already contain the specified project and model deployment; the template does
not create missing children in an existing account.

Optional **live ARM preview**, after sign-in:

```powershell
.\deploy\deploy.ps1 -ParametersFile .\deploy\parameters.json -WhatIf
```

Unlike `deploy-all.ps1 -WhatIf`, this calls Azure ARM, resolves identity/network inputs
and writes ignored local preparation files. It does not create resources or upload
runtime inputs. It previews only the Azure layer, not a live Fabric dry-run.

### 4. Deploy the new architecture and run acceptance

```powershell
.\deploy\deploy-all.ps1 -ParametersFile .\deploy\parameters.json
if ($LASTEXITCODE -ne 0) { throw 'Deployment or acceptance failed; inspect the first failing stage.' }
```

This step is **mutating and billable**. Do not use `-SkipTests` for the first acceptance
run. Do not use `-SkipFabricProvision` for a new workspace: that option assumes the
matching lakehouse, ontology, graph and data already exist. Rerunning the installer
can update those items and create new agent versions; it is not a read-only test.
The installer asks for one confirmation showing the target tenant, subscription,
resource group and workspace. Use `-Force` (alias `-Yes`) only when you want to
accept that installer confirmation automatically.

The expected runtime is:

| Component | Placement |
|---|---|
| Receipt MCP tools, deterministic scorer, mandatory receipt-answer verifier/fallback | Azure Functions: `/api/mcp`, `/api/score`, `/api/verify` |
| Independent receipt and Fabric IQ agents | Microsoft Foundry, each with its own MCP connection |
| Pinned policy, source dataset and authoritative receipts | Azure Blob Storage, one dataset/policy namespace |
| Medallion evidence, relationships and reference human-decision log | Fabric lakehouse, ontology and manually refreshed graph |

### 5. Capture the result, not just a green health probe

```powershell
$data = Get-Content deploy\.out\deployment-data.json -Raw | ConvertFrom-Json
$azure = Get-Content deploy\.out\azure-outputs.json -Raw | ConvertFrom-Json
$runtime = Get-Content deploy\.out\verification\cloud-runtime.json -Raw | ConvertFrom-Json
$data | Select-Object parcelCount, datasetSha256, policySha256, receiptsPrefix
$azure | Select-Object resourceGroupName, foundryEndpoint, functionAppEndpoint
$runtime | Select-Object ok, parcelCount, scoreHashParity, unauthenticatedStatus
```

Require successful installer completion **and** fresh verification output from that
run. `azure-outputs.json` records Azure publication, not full acceptance. The Function
check requires Blob-backed inputs, matching policy/prefix/count, HTTP 401 on all
three protected routes, exact remote-score receipt hash/ID parity for **every**
configured parcel, permitted narration, and deliberately contradicted narration
replaced by receipt fallback. SQL/GQL checks and both independent agent smoke tests
must also pass. A receipt-agent smoke exit 2 means the guard blocked narration and
returned fallback; the installer stops for review rather than silently accepting
that as a normal permitted-answer result.

If a stage fails, keep the failing report, correct its cause and rerun the appropriate
stage or installer. Do not copy previous reports into a new run or relabel historical
experiment evidence. Broader missing/tampered-receipt, bypass and trace acceptance
remains in the [release gate](../docs/testing.md#cloud-query-release-gate).

## Optional: reproduce the planning-calculation experiment

This is a separate experiment, not an installer stage or cloud acceptance check.
It uses the committed synthetic electricity-network dataset rather than the
dataset generated from your deployment's `parcelCount`.

| Mode | Where it runs | Requirements |
|---|---|---|
| Offline calculation/harness check | Python on your computer, with a simulated agent | Python 3.11+; no Azure login or cloud resources |
| Foundry-agent comparison with local data | Python tools on your computer; agents and model calls in Foundry | Foundry/OpenAI SDKs, an isolated Foundry project, Entra sign-in and model access; billable |

From the repository root, the offline check is:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$out = Join-Path $env:TEMP ('wfi-scenarios-' + [guid]::NewGuid().ToString('N'))
python scripts\run_scenarios.py --dry-run --repeats 1 --workers 2 --out $out
Get-Content (Join-Path $out 'scenarios-dryrun-summary.md')
```

Do not present simulated-agent metrics as an AI evaluation. For the actual
comparison, follow the [local-data Foundry run](../docs/experiment.md#run-the-foundry-comparison-with-local-data),
including dependency installation, explicit project/model selection, isolated
experiment-agent names and result checks. No Fabric capacity, ontology or Function
deployment is required for that local-data mode. Keep new outputs separate from
the committed historical evidence.

**Remaining gap:** Experiment 3 (Foundry Agent Tools) used locally executed Python
tools rather than running entirely on cloud services. Integrating these tools
into the provided cloud deployment remains to do.

## Console status, restarting and unattended confirmation

The console prints the stage number/name and **RUNNING**, **DONE**, **REUSED**,
**SKIPPED** or **FAILED**, with elapsed time when a stage completes. PowerShell's
progress display shows the current stage while child tools print their own detail.
A failure prints the failed stage, checkpoint path and a copyable restart command.

| Step | Work |
|---|---|
| 1 | Mandatory preflight: tools, sign-in, target tenant/subscription, active capacity (`F4+`) and existing or planned workspace |
| 2 | Pin the policy and prepare the matching dataset, receipts and Fabric parameters |
| 3 | Deploy Azure resources, upload runtime inputs, publish Functions and configure tracing |
| 4 | Resolve or create the Fabric workspace on the configured capacity, provision the lakehouse and load medallion and relationship data |
| 5 | Define the ontology, fix graph composite keys and manually refresh the graph |
| 6 | Create/update the two independent MCP connections |
| 7 | Create/update the two agents without asking model test questions |
| 8 | Run automated tests and live data, Function and agent acceptance checks |
| 9 | Print endpoints, acceptance status and capacity/cleanup reminders |

Progress is saved in ignored `deploy\.out\deployment-state.json`. Keep the entire
`deploy\.out\` folder for a restart. Do not run installers concurrently from the
same checkout. An interrupted or failed stage is retried in full; there is no
rollback and no mid-stage resume. A hard interruption can leave its state as
`running`; `-Resume` retries that stage.

```powershell
# Continue at the first incomplete/failed stage.
.\deploy\deploy-all.ps1 -ParametersFile .\deploy\parameters.json -Resume

# Preview that restart without changing the saved checkpoint or calling the cloud.
.\deploy\deploy-all.ps1 -ParametersFile .\deploy\parameters.json -Resume -WhatIf

# Explicitly restart at step 2; step 1 must already have a successful checkpoint.
.\deploy\deploy-all.ps1 -ParametersFile .\deploy\parameters.json -StartStep 2

# Resume and automatically accept installer confirmations.
.\deploy\deploy-all.ps1 -ParametersFile .\deploy\parameters.json -Resume -Force

# Provision without the final tests/acceptance stage. NOT an accepted deployment.
.\deploy\deploy-all.ps1 -ParametersFile .\deploy\parameters.json -SkipValidation -Force

# Later, run the previously skipped acceptance stage and finish the summary.
.\deploy\deploy-all.ps1 -ParametersFile .\deploy\parameters.json -Resume -Force
```

`-StartStep` accepts 1 through 9 and requires completed prerequisite stages in a
matching checkpoint when greater than 1. Do not combine it with `-Resume`.
Earlier mutating stages are not rerun. **Mandatory preflight checks always run
again**, even when restarting after step 1. Parameters, policy, source and saved
prerequisite outputs must match; missing/changed outputs fail explicitly rather
than silently deploying to an old target. To recover a missing output, restart at
its producing stage, or start a full run at step 1. Changed configuration, policy
or source requires a full run; the previous checkpoint is preserved as a
`.previous` file. An optional `-StateFile` selects another checkpoint location, but
does not isolate the shared generated outputs.

`-SkipValidation` is an alias of the existing `-SkipTests`: it skips **only step 8**,
not parameter/policy validation, tenant/subscription checks, capacity checks or
service-side errors. The final status is `provisioned_unverified`, never accepted.
Old verification files are not proof that this run passed. A resume of an already
accepted run prints its summary and reuses the recorded acceptance; use
`-StartStep 8` when you need fresh acceptance checks.

`-Force` / `-Yes` suppresses installer/child PowerShell confirmations only. It
does not sign in, answer MFA/consent, grant permissions, bypass policy or quota,
or suppress failures. For a fully noninteractive process, authenticate beforehand
and use `pwsh -NonInteractive -File .\deploy\deploy-all.ps1 ... -Force`; it will
fail rather than answering an unexpected interactive prompt.

## What the installer does

| Stage | Action |
|---|---|
| Preflight and parameters | Validate configuration, the signed-in tenant, tools and the capacity SKU; pin the policy with any `weightOverrides` |
| Azure and receipt service | Create or reuse configured Azure resources, prepare requested parcels, upload receipts/policy/dataset, publish all Function routes and configure tracing |
| Fabric data | Resolve or provision the configured capacity, reuse or create the workspace, create the lakehouse as needed, and load Bronze, Silver, Gold and relationship tables from the prepared dataset |
| Ontology and graph | Define five entity types and four relationships, apply the CrewAccess composite key in all three required places, and run a manual graph refresh |
| Connections and agents | Configure `pinnedscore-receipts-mcp-entraid` and `pinnedscore-fabric-iq-ontology-v2`, then create the independent receipt and Fabric IQ agents |
| Verification | Run the automated suite, Function authentication/scoring/verifier checks, receipt parity with Fabric, live GQL ground truth and both independent agents |
| Summary | Print target resources, endpoints, parcel count and teardown instructions |

`deploy\deploy.ps1` is the Azure-layer implementation. Its `-SkipSmokeTest` skips only
an extra local scorer check: required receipts, policy and dataset still upload.
Its `-WhatIf` is the **live ARM preview** described above.

## Configured dataset and generated files

`parcelCount` is a positive integer and defaults to **150**. Set it in `deploy\parameters.json`; no separate Fabric parameter file is needed for the full installer. The prepared dataset is deterministic and preserves the recorded observations for P001–P150 when those parcels are included.

Generated files live under ignored `deploy\.out\`:

| File or manifest field | Purpose |
|---|---|
| `policy.resolved.json` | Policy after applying overrides, pinned by SHA-256 |
| `deployment-data.json` | Current parcel count, dataset and policy hashes, input paths and receipt prefix |
| `azure-outputs.json` | Azure-layer deployment outputs, including Function and Foundry endpoints; not full acceptance |
| `datasetPath`, `receiptsPath`, `graphPath` | Matching data, receipts and decision-graph input files for this deployment |
| `fabric.parameters.resolved.json` | Fabric parameters generated from the same configuration and inputs |
| `verification\` | Fresh deployment verification output, separate from committed experiment evidence |

Receipts, `_policy.json` and `_dataset.json` are uploaded under `deployments/<dataset SHA>/<policy SHA>/receipts`. The Function App uses this namespace through `RECEIPTS_PREFIX`. Repeating the same configuration reuses the namespace; changing the count or policy selects another one without deleting earlier receipts. Existing medallion history is retained, and verification checks the current configured dataset.

Keep GraphModel refresh **manual**. Preserve `["parcel_id", "crew_type"]` in the ontology entity, graph node and graph edge; re-saving the graph in the portal can regenerate a single-column edge key. The expected CrewAccess coverage is **three entries per configured parcel**.

## Parameters (`parameters.json`; JSON has no comments, so they are documented here)
| Name | Required | Description |
|---|---|---|
| `subscriptionId` | yes | Target subscription GUID |
| `tenantId` | yes | Target tenant GUID; must match `az account show` |
| `location` | yes | Azure region, e.g. `centralus` or `eastus2` |
| `resourcePrefix` | yes | 3-10 lowercase alphanumerics; resource group is `<prefix>-rg` unless `resourceGroupName` is set |
| `resourceGroupName` | no | Optional existing or explicit resource group name (e.g. `example-resource-group`) |
| `foundryProjectName` | yes | Foundry project name |
| `modelDeploymentName` | yes | Model deployment name |
| `modelName` / `modelVersion` | no | Default `gpt-4o` / `2024-11-20` |
| `modelSkuName` / `modelCapacity` | no | ARM deployment SKU/capacity; defaults `GlobalStandard` / `10`; confirm quota and supported model/SKU before deployment |
| `existingFoundryAccountName` | no | Optional existing Azure AI Foundry (`AIServices`) account name in the target resource group to reuse |
| `existingLogAnalyticsName` / `existingAppInsightsName` | no | Optional existing Log Analytics workspace and Application Insights component names to reuse |
| `deployerClientIp` | no | Deployment client IP/CIDR allowed through the Network Security Perimeter (`<prefix>-nsp`) during receipt upload (auto-detected if omitted). This is the only IP-based inbound rule supplied by the template; no corporate VPN ranges are included. |
| `deployerPrincipalId` | no | Operator/service principal object ID for receipt-upload RBAC; auto-resolved for a signed-in user if omitted |
| `mcpAllowedAudiences` | no | Optional comma-separated Entra audiences accepted by `/api/mcp` (auto-resolved from Foundry project `agentIdentityBlueprintId` if omitted) |
| `fabricCapacityName` / `fabricCapacitySku` | for ontology | Configured Fabric capacity name and SKU (`>= F4`, e.g. `F4` or `F8`); reused when accessible, or provisioned when both capacity/workspace are absent |
| `fabricCapacityAdmin` | no | Optional administrator for capacity creation; defaults to the signed-in operator. Add this property to the private JSON when overriding the default. |
| `fabricWorkspaceName` / `fabricLakehouseName` | for ontology | Target Fabric workspace name (created in stage 4 on `fabricCapacityName` if absent and `fabricWorkspaceId` is empty, or reused if present, e.g. `PinnedScore`) and lakehouse name (`pinned_scoring_lh`) |
| `fabricOntologyName` | no | Default `pinned_scoring_ontology` |
| `functionAppName` | no | Optional Function App name override for the MCP receipt tool host (default `<prefix>-mcp-<suffix>`) |
| `functionPlanSku` | no | App Service plan SKU for the Function App (`FC1` Flex Consumption default, or `Y1`/`B1`) |
| `logAnalyticsName` / `appInsightsName` | no | Optional Log Analytics workspace and Application Insights names when creating new ones |
| `fabricWorkspaceId` / `fabricLakehouseId` | no | Optional explicit resolution IDs for pre-existing items, checked against configured names (fails closed if missing or mismatched); also recorded as Azure resource tags |
| `fabricOntologyId` / `fabricGraphModelId` / `fabricSqlServer` | no | Optional existing-target identifiers/checks; leave blank for discovery when creating a new deployment |
| `policyPath` | yes | Policy JSON (repo-relative or absolute) |
| `parcelCount` | no | Positive integer; defaults to **150**. Shared by receipt preparation, Fabric loading and relationship verification |
| `weightOverrides` | no | `{ "factor_name": "0.35", ... }`; weights must sum to exactly 1 (override every factor you change) |

The Foundry endpoint is an output of the deployment (`foundryEndpoint`), not an input.

## Verification and querying the cloud agent

The full installer runs verification unless `-SkipTests` is supplied. Receipt checks require complete coverage of the configured parcels and matching hashes. GQL checks use C1–C5 answers computed from those same receipts and verify the graph's parcel and crew-access coverage. They do not reuse fixed answers from a committed experiment.

Load the matching endpoints and configuration in a fresh shell before querying:

```powershell
$config = Get-Content deploy\parameters.json -Raw | ConvertFrom-Json
$azure = Get-Content deploy\.out\azure-outputs.json -Raw | ConvertFrom-Json
$projectEndpoint = $azure.foundryEndpoint.TrimEnd('/') + "/api/projects/$($config.foundryProjectName)"
$functionEndpoint = $azure.functionAppEndpoint -replace '/api/mcp/?$', ''
python scripts\run_cloud_agent.py --endpoint $projectEndpoint --function-endpoint $functionEndpoint --entity-id P001 --verify --json "Explain parcel P001 using its receipt."
```

Choose a parcel in the configured dataset. The receipt agent calls the remote Function
MCP tools. Every answer is then checked by Function `/api/verify`; `--verify` additionally
requires a remote MCP trace, not local verification. Deliver `text`/`delivered`, never
diagnostic `raw_text`. Exit 0 means permitted narration; 2 means blocked narration
with receipt fallback; 1 means request/runtime failure.

To repeat the Function acceptance checks without provisioning or a model call:

```powershell
python scripts\verify_cloud_deployment.py --function-endpoint $functionEndpoint --manifest deploy\.out\deployment-data.json --output deploy\.out\verification\cloud-runtime.json
```

To query the **existing, separate Fabric IQ agent** without recreating it (live and
billable; no deployment/update):

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = $projectEndpoint
@'
import json
from pinned_scoring import foundry_agent as fa
project = fa.make_project_client()
trace = []
answer = fa.run_cloud_agent_turn(
    project.get_openai_client(),
    "Which parcels have a critical facility but no passable access road that an engine can use?",
    agent_name="pinned-score-agent-fabric", trace=trace,
)
fa.validate_mcp_trace(trace, expected_server_label="pinnedscore_ontology")
if not any(call.get("name") == "ask_ontology" for call in trace):
    raise RuntimeError("Missing ask_ontology call")
print(json.dumps({"text": answer, "mcp_trace": trace}, indent=2))
'@ | python -
```

This is a relationship query, not receipt-answer verification or combined-agent
orchestration. Do not use an agent-creation script for a read-only query.

## Tracing (required step)
`deploy\enable_tracing.ps1 -ParametersFile deploy\parameters.json -ResourceGroup <foundry-rg> -AccountName <foundry-account> -ProjectName <project>` creates Log Analytics + workspace-based Application Insights and an `AppInsights` connection on the Foundry project, so agent runs appear in the Traces tab/App Insights. Optional parameters: `logAnalyticsName`, `appInsightsName` (defaults `<resourcePrefix>-law` / `-appi`). `-WhatIf` prints the plan without Azure calls. No keys or connection strings are committed; clients read the connection string at runtime (`scripts/run_traced_question.py`).
`deploy-all.ps1` runs this automatically right after the Foundry deployment (step 3).

## Security model
- No secrets in the repo; storage and Foundry have key/local auth disabled (`allowSharedKeyAccess: false`, Entra ID only) and storage accounts are placed inside a Network Security Perimeter (`<prefix>-nsp`, `publicNetworkAccess: 'SecuredByPerimeter'`).
- Agent identity (`<prefix>-agent-id`) gets only *Storage Blob Data Reader* on the receipts storage account (`pinscorercpthg4m5tlr`) and *Cognitive Services OpenAI User* on Foundry (when creating a new Foundry account). Function runtime state uses a separate host storage account (`pinscorehosthg4m5tlr`) so the agent identity retains zero write permissions on the receipts store.
- `/api/mcp` enforces Microsoft Entra ID Bearer JWT verification (`verify_entra_bearer_token` in `src/pinned_scoring/cloud_tool_host.py`) against the tenant's Entra OIDC JWKS endpoint (`MCP_ALLOWED_TENANT_ID` and `MCP_ALLOWED_AUDIENCES`, scoped to the Foundry project's `agentIdentityBlueprintId`). Unauthenticated or invalid-token requests are rejected with `401 Unauthorized`.
- The MCP surface exposes only four read-only receipt tools (`get_receipt`, `list_receipts`, `list_reasons`, `compare`). Separate authenticated Function routes provide deterministic scoring (`/api/score`) and mandatory receipt-answer verification/fallback (`/api/verify`). Neither route writes authoritative receipts. Local deployment preparation generates the seed receipts; runtime scoring reuses the same unit/quality normalization as Fabric. No planning/execution or authority-write tool is exposed by this shared host.
- Your deploying identity gets *Storage Blob Data Contributor* on the receipts storage account solely so the deployment script can upload the pinned policy and receipts.
- Receipts container: blob versioning, soft delete, and WORM/immutable storage are disabled by default so the storage accounts remain cleanly deletable by cleanup.

## Verification status

The accelerator completed a full installation run from **Step 1 through Step 9** on **12 November 2026**,
deploying and configuring the Azure, Microsoft Fabric and Microsoft Foundry components
and completing the installer's verification stage. All nine stages completed
successfully, and the installer recorded **`status: accepted`** and **`accepted: true`**.

The run took **35 minutes 17 seconds**, including **7 minutes 28 seconds** for
automated tests and live verification. Checks covered the configured 150-parcel
dataset, receipt-hash consistency, Fabric relationships, Function scoring and answer
verification, and both Foundry agent paths. The [installation evidence index](../docs/evidence/installation-acceptance/summary.json)
is the single acceptance record, linking the accepted checkpoint, matching source
fingerprint and verification outputs. The [README results](../README.md#live-cloud-verification)
summarize the checks. Private configuration and raw originals remain outside the repository.

## Uninstall
`deploy\uninstall.ps1` removes only resources recorded as **created** by this installation in the ownership ledger (`deploy\.out\ownership.json`, exact IDs plus creation evidence; override with `-OwnershipFile`). It never matches by name, tag or prefix, never falls back to template parameters, and always preserves the subscription, tenant, any **reused** Fabric capacity, reused resources (Foundry account, Log Analytics, App Insights, resource group, workspace) and unrelated contents of created containers. A created resource group or workspace is deleted only after a fresh check shows nothing else remains in it.

- Preview (offline, no cloud calls): `.\deploy\uninstall.ps1 -ParametersFile <your real parameters file> -RemoveFabricItems -WhatIf`. `-ParametersFile` cross-checks tenant and subscription against the ledger; deletion targets come only from the ledger's exact IDs. Add `-Purge` to the preview only if you intend to review irreversible soft-delete purging too.
- Approved execution: `.\deploy\uninstall.ps1 -ParametersFile <file> -RemoveFabricItems [-Purge]`. It verifies the signed-in tenant and subscription first, polls each deletion until absence is confirmed, and exits nonzero on any failure (403, retention/immutability lock, timeout) or unresolved item; reruns are idempotent.
- Nested resources and ignored types: a Foundry project (`<account>/projects/<name>`) is deleted explicitly before its parent account. `Microsoft.Network/networkSecurityPerimeters*` resources are ignored and preserved (never deleted or modified by cleanup). A child is reported as cascaded only when its parent was actually deleted; otherwise it is reported as failed. Fabric items adopted for an owned workspace are removed with that workspace. Progress is printed per step to stderr.
- Protected deletions: by default, a storage account that fails with `AccountProtectedFromDeletion` fails closed with read-only diagnostics (`isVersioningEnabled`, blob/container soft-delete, `immutableStorageWithVersioning`, and `hasImmutabilityPolicy`/`hasLegalHold`) and modifies nothing. If you explicitly review and pass `-ClearStorageDeleteProtection` (`--clear-storage-delete-protection`), cleanup disables soft-delete/versioning and deletes unlocked blob versions/containers on that owned storage account before retrying deletion, while still refusing any container with an active immutability policy or legal hold and never modifying Network Security Perimeters or `publicNetworkAccess`.
- `-Force` only suppresses the confirmation prompt; it never bypasses `-WhatIf` or ownership checks. `-Purge` (irreversible) is shown separately and only purges a soft-deleted account whose `accountId` equals the ledger's.
- Items without provenance (older deployments) are reported as *unresolved* and not deleted. If you personally know the installer created one, attest it with `python deploy\cleanup_plan.py --ledger deploy\.out\ownership.json adopt ...` and re-preview.
- Capacity: a capacity is deleted only when the ledger records it as `created` (`kind fabric_capacity`, exact ARM ID plus `attrs.capacityId`) and `-RemoveFabricItems` is given. It is deleted after the workspace and before the resource group, and only if the Fabric **admin** workspace listing for that capacity (`GET /v1/admin/workspaces?capacityId=`, paginated; the normal `GET /v1/workspaces` lists only caller-visible workspaces and cannot prove a capacity is unshared) shows no workspace outside this installation. If that listing is not permitted (Fabric administrator required) or any unrelated workspace is assigned, the capacity is refused/failed closed, reported, and the run exits nonzero; cleanup never grants permissions. A capacity without a recorded GUID is unresolved. Reused capacities are never touched. The installer provisions a missing capacity (32f6542) and records it as `created` or `reused`; the delete path is verified offline only. Malformed or incomplete inventory responses (missing `workspaces`/`value`, repeated tokens, missing `capacityId`) and unrecognised errors fail closed with no DELETE; only positively recognised not-found errors count as absence. Nested/RBAC resources with no pre-write existence evidence are recorded as unresolved, never created. An owned parent (Foundry account, storage) is not deleted while a preserved or unresolved descendant would be cascade-deleted. Foreign resource types (e.g. Event Grid system topics, alert rules) are preserved and block resource-group deletion. Legacy installs without a ledger need read-only evidence recovery and explicit per-resource adoption. The `Microsoft.Fabric/capacities` api-version `2023-11-01` was confirmed in Microsoft Learn's published REST reference (no live call made).
- Existing installations without provenance (anything deployed before ledger recording exists, or resumed from such a run) are **not covered** just because fresh-install tracking will exist. They may need read-only evidence recovery (e.g. `az deployment operation sub list`, resource/workspace creation timestamps and creator metadata, Foundry connection/agent listings) and then explicit per-resource `adopt` with an exact ID and your attestation. Nothing is deleted by name, tag or prefix, and unattested items stay *unresolved*.
- Installer coverage (recorded by `deploy-all.ps1` and the stage scripts through `deploy\ownership-hooks.ps1` and `scripts\ownership_record.py`, active whenever `deploy-all.ps1` sets `PINNED_OWNERSHIP_LEDGER`): the resource group snapshot/Bicep operations (including account, project and OpenAI role assignments), runtime-created RBAC assignments, installer-created Fabric capacity (exact `Microsoft.Fabric/capacities` ARM ID, api-version `2023-11-01`, plus capacity GUID), workspace, lakehouse, ontology and GraphModel, Foundry connections and agent versions. IDs must be recorded immediately after each successful creation (before later stages can fail) and preserved across retries/resumes.
- `deploy-all.ps1` now writes the ledger during deployment (`cleanup_plan.py init/record/record-azure`: pre-deployment RG snapshot, Bicep operations, Fabric workspace/items, Foundry connections and agent versions). Preview with `.\deploy\uninstall.ps1 -ParametersFile <file> -RemoveFabricItems -WhatIf`. Deployments run before this integration have no ledger, so only `adopt`-ed entries are removable.

Remember to pause or scale down your Fabric capacity after testing.

## Troubleshooting
- *parameters.json not found*: copy the template.
- *Tenant mismatch*: `az login --tenant <tenantId>`.
- *Upload fails / 403*: role assignments take minutes to propagate; the script retries, then re-run.
- *Model deployment fails*: no quota or unsupported model/version in the region; change `location`, `modelName`, or `modelVersion`.
- *Name conflict after uninstall*: Foundry accounts are soft-deleted; use `-Purge` (only for an account recorded in the ledger).
- *Policy validation error*: weights must sum exactly to 1 and factor names must exist in the policy.

## Testing without reinstalling

Use the [testing and operations runbook](../docs/testing.md) for offline full/focused
tests, installer previews, independent current-dataset SQL/GQL checks and interpretation
of failures. A full installer rerun is mutating and billable, not a tests-only command.
[Experiment reproduction](../docs/experiment.md) keeps new runs separate from recorded
evidence. The [cloud status matrix](../docs/azure-mapping.md#verified-baseline-and-release-gates)
distinguishes the receipt-MCP live baseline from pending Function-hosted scoring and
mandatory answer-verification acceptance.
