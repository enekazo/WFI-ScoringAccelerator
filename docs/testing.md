# Testing and operating the cloud deployment

Use [deploy/README.md](../deploy/README.md) for the canonical installation and parameters.
Run commands here from the repository root in PowerShell 7. These checks do **not**
establish a fresh-tenant deployment or production readiness.

## Operation classes

| Operation | Cloud effects | Output / interpretation |
|---|---|---|
| `pytest`, stub experiment dry-runs | Offline; temporary/local files only | Tests and harness plumbing, not live model quality or permissions |
| Installer `-WhatIf`, graph `--dry-run`, uninstall `-WhatIf` | Offline preview; may write ignored planning files | Syntax/plan validation, not quota, access or resource-existence proof |
| Fabric SQL parity and GQL verification | Read-only live data queries; capacity may incur charges | Current dataset/hash/graph checks; no data load or refresh |
| Receipt/Fabric IQ agent query | Live inference, billable; creates service request/trace state | Remote tool evidence and answer checks, not a pure offline test |
| Installer, data loaders, graph apply/refresh, agent creation | Mutating and billable | Changes resources/data/agent versions; **not tests-only** |
| Uninstall without `-WhatIf` | Destructive | Removes configured resources; never use as a validation step |

## Offline automated checks

Install the CI-only test dependencies before running the full offline suite.
No Azure login is needed. Avoid bytecode/cache output when comparing a clean checkout:

```powershell
python -m pip install -e ".[dev]" -r .github\requirements-ci.txt
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = 'src;scripts'
python -m pytest -q -p no:cacheprovider
```

Exit 0 means the selected offline tests passed. It does not show that Foundry can
authenticate, that Fabric is refreshed or that Function-hosted verification is live.
Do not encode a permanent test count in deployment acceptance: it changes with code.

### GitHub Actions

The [Offline tests workflow](https://github.com/enekazo/WFI-ScoringAccelerator/actions/workflows/tests.yml)
runs the full `pytest` suite on pushes to `main`, pull requests and manual dispatch.
It uses a clean Windows runner with Python 3.11, PowerShell 7 and Windows PowerShell,
and installs the development dependencies plus `.github/requirements-ci.txt`. This
CI-only file reuses the existing deployment SDK requirements and adds the Delta Lake
and Arrow packages and optional tracing support needed by the tests. No Azure credentials,
deployment, live model calls or Fabric capacity are required. The README badge links
to the workflow and reports the result for `main`.

A passing badge is evidence of automated offline tests, not fresh-target installation
or live Function, Fabric or agent acceptance.

Focused checks for receipt host, agent wiring, verifier and deployment:

```powershell
python -m pytest -q -p no:cacheprovider tests\test_cloud_tool_host.py tests\test_foundry_agent.py tests\test_verifier.py tests\test_deploy_all.py
```

After integrating the cloud-runtime change, include its new route/enforcement tests
and deployment-dataset tests in addition to this baseline. Missing tests are a release
gap, not grounds to silently skip the new control.

## Dry-run preview

```powershell
.\deploy\deploy-all.ps1 -WhatIf
python scripts\fabric_graph_model_fix.py --dry-run
.\deploy\uninstall.ps1 -WhatIf
```

The installer preview uses your private parameters, falling back to the template when
none exists. It validates the plan without deploying. The graph dry-run should report
`all_three_ok: true` and manual refresh mode. A preview failure requires configuration
or code correction; a successful preview is not a successful live install.

For stub harness checks with separate outputs, see [experiment reproduction](experiment.md).
A full installer rerun changes resources and data even when tests are included.

## Independent post-deployment checks

Prerequisites: a completed deployment, its generated `deploy\.out\deployment-data.json`,
the **same** resolved policy/dataset/receipt files, Azure login to that tenant, explicit
Fabric SQL access, the SQL adapter dependencies and an active Fabric capacity.
Use the current manifest; do not substitute `docs/evidence/receipts` or fixed 50-parcel
expectations. These commands require the configurable-dataset implementation described
in the canonical deployment guide; older checkouts lack its environment overrides.

Read and validate the manifest before any live query:

```powershell
$data = Get-Content deploy\.out\deployment-data.json -Raw | ConvertFrom-Json
$params = Get-Content deploy\parameters.json -Raw | ConvertFrom-Json
if (-not $data.receiptsPath -or -not $data.datasetPath -or -not $data.policyPath) { throw 'Deployment input paths are missing.' }
if (-not (Test-Path $data.receiptsPath) -or -not (Test-Path $data.datasetPath) -or -not (Test-Path $data.policyPath)) { throw 'Deployment inputs do not exist.' }
$expected = @(Get-ChildItem -LiteralPath $data.receiptsPath -Filter 'P*.json')
if ($expected.Count -ne $data.parcelCount) { throw 'Manifest and receipt counts differ.' }
$env:PINNED_RECEIPTS_DIR = [IO.Path]::GetFullPath($data.receiptsPath)
$env:PINNED_VERIFICATION_DIR = [IO.Path]::GetFullPath('deploy\.out\verification\independent')
```

Check the manifest against its files **offline**, before selecting a cloud target.
This uses the same canonical dataset JSON/hash and policy loader as preparation:

```powershell
@'
import hashlib, json
from pathlib import Path
from pinned_scoring.policy import Policy
from pinned_scoring.receipt import Receipt
m = json.loads((Path("deploy") / ".out" / "deployment-data.json").read_text(encoding="utf-8"))
doc = json.loads(Path(m["datasetPath"]).read_text(encoding="utf-8"))
assert hashlib.sha256(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest() == m["datasetSha256"], "Dataset hash mismatch"
assert Policy.load(m["policyPath"]).sha256 == m["policySha256"], "Policy hash mismatch"
receipts = [Receipt.from_wire(json.loads(p.read_text(encoding="utf-8"))) for p in Path(m["receiptsPath"]).glob("P*.json")]
assert len(receipts) == m["parcelCount"] and all(r.policy_sha256 == m["policySha256"] for r in receipts), "Receipt scope mismatch"
ids = [r.entity_id for r in receipts]
assert len(set(ids)) == len(ids) and set(ids) == set(doc["parcels"]), "Missing, duplicate or unexpected parcel receipts"
print("Deployment dataset, policy and receipt hashes verified.")
'@ | python -
if ($LASTEXITCODE -ne 0) { throw 'Deployment input verification failed.' }
```

Then select the deployed SQL target and run the live, read-only check:

```powershell
# Set these to the SQL endpoint/database of this deployment, not historical defaults.
$env:FABRIC_SQL_SERVER = '<deployed-lakehouse-SQL-endpoint>'
$env:FABRIC_SQL_DATABASE = '<deployed-lakehouse-database>'
az login --tenant $params.tenantId
python scripts\fabric_hash_check.py
if ($LASTEXITCODE -ne 0) { throw 'Fabric receipt verification failed.' }
```

Expected output: `fabric_hash_check.json` in the verification directory, with `ok: true`,
`expected == checked == matched == parcelCount`, no missing/unexpected current IDs and
no mismatched receipt hashes. The offline check also requires exact parcel-ID coverage,
not merely the right number of files. Historical rows may be excluded; their existence is not
a failure. Empty expected receipts, a wrong dataset/policy, a SQL permission error or
stale Gold must not be reported as success. Inspect the error and active input paths;
do not “fix” it by overwriting the committed evidence.

For read-only relationship verification, explicitly select the deployed workspace and
graph. The graph command sends queries, not a refresh:

```powershell
$env:FABRIC_WORKSPACE_ID = '<deployed-workspace-id>'
$env:FABRIC_GRAPH_MODEL_ID = '<deployed-graph-model-id>'
python scripts\fabric_graph_model_fix.py --workspace-id $env:FABRIC_WORKSPACE_ID --graph-model-id $env:FABRIC_GRAPH_MODEL_ID --verify-c1-c5
if ($LASTEXITCODE -ne 0) { throw 'Current graph verification failed.' }
```

Expected output: `fabric_graph_chain_gql.json` plus `all_passed: true`. Ground truth
comes from the current receipt directory. Verify `dataset_matches: true`, exact parcel
coverage, three CrewAccess entries per configured parcel and all five question checks,
including the C4 numeric value. Do not copy historical answers such as C1=14 into a
new acceptance test. `--apply`, `--refresh`, or running the graph patch script without
the verification/dry-run mode is **mutating**. Data-loading scripts are also mutating.

## Cloud query release gate

**Installation verification status:** the
[single installation evidence index](evidence/installation-acceptance/summary.json)
records the accepted end-to-end installation on **12 November 2026**:
all nine installer stages completed in 35 minutes 17 seconds, including 7 minutes
28 seconds of verification. The source fingerprint, private parameters and all seven
checkpoint artifact hashes were independently checked during evidence collection.
The checkpoint records successful completion of pytest and the receipt-agent check;
the separate receipt-agent JSON dated 11 November is not evidence from this new run.

Use the Foundry project and Function endpoints printed by the matching deployment.
Select a parcel present in the dataset identified by `deployment-data.json`; do not
copy an endpoint or a historical parcel count from recorded evidence.

```powershell
# LIVE / READ-ONLY / BILLABLE: Function execution and Blob reads; no LLM turn.
python scripts\run_cloud_agent.py --function-endpoint '<deployed-Function-base-URL>' --score-parcel P003 --json
# Scores every parcel in the matching stored dataset; not a fixed-50 assertion.
python scripts\run_cloud_agent.py --function-endpoint '<deployed-Function-base-URL>' --score-parcel ALL --json
# LIVE / READ-ONLY / BILLABLE: Foundry model, receipt MCP and Function verification.
python scripts\run_cloud_agent.py --endpoint '<deployed-Foundry-project-endpoint>' --function-endpoint '<deployed-Function-base-URL>' --entity-id P003 --verify --json 'Explain parcel P003.'
```

The runner accepts a positional question or `--question`; `--score-parcel` is a single
ID or `ALL`, not a repeatable option. `--receipts` and `--no-server-verify` are not
supported. `PINNED_FUNCTION_ENDPOINT` can supply the Function URL; the CLI can also
discover it from the agent's MCP wiring. `--verify` (or `--require-mcp-call`) requires
at least one remote MCP call **in addition to** mandatory Function verification.
It does not enable a caller-side verifier.

The Function exposes four receipt tools on `/api/mcp`; `/api/score` and `/api/verify`
are separate HTTP routes, not additional MCP tools. Scoring accepts
`{"parcel":"P003"}`, `{"parcels":["P001","P003"]}` or `{"parcels":[]}` for all stored
parcels. A single-ID request returns `receipt`; a list/all request returns `count`
and `receipts`. Compare returned policy/receipt hashes and the exact IDs against the
matching manifest. Optional `submitted_policy_sha256` checks the pinned policy;
`weight_overrides` produces a counterfactual, not a replacement authoritative receipt.
Do not assume arbitrary inline `policy` replacement is supported.

Verification accepts `{"question":"...","answer":"...","entity_id":"P003"}` with
optional `mcp_trace`. A permitted narration has `blocked: false` and
`delivered.mode: "verified_narration"`. A contradicted narration returns HTTP 200
with `blocked: true`, `fallback_used: true` and
`delivered.mode: "receipt_fallback"`; use `text`/`delivered`, never diagnostic
`raw_text`, as the delivered answer. The runner exits 0 on permitted narration,
2 on blocked narration with fallback, and 1 on request/runtime failure.
An HTTP success alone is not a successful narration check.

Protected routes use the configured Entra bearer-token checks. Operator calls acquire
tokens with `DefaultAzureCredential`; token scope is configurable through
`PINNED_FUNCTION_TOKEN_SCOPE` (alias `PINNED_FUNCTION_SCOPE`). HTTP errors, including
invalid authentication, missing observations, policy conflicts and kill-switch failures,
must fail the check rather than return unverified model text. Unauthenticated
`/api/health` is only a probe: advertised capabilities do not prove scoring,
authorization, policy loading or verification works.

**Recorded installation checks:** [150-parcel score/hash parity, valid narration,
contradiction fallback and unauthenticated HTTP 401](evidence/installation-acceptance/cloud-runtime.json);
[SQL receipt parity](evidence/installation-acceptance/fabric_hash_check.json);
[GQL](evidence/installation-acceptance/fabric_graph_chain_gql.json) and
[Foundry ontology questions](evidence/installation-acceptance/fabric-agent-smoke.json);
and [Step 8 completion](evidence/installation-acceptance/checkpoint.json), including
the receipt MCP agent and Function verification check.

**Historical fault probes, not additional installation acceptance:** the
[earlier rejection probes](evidence/cloud-acceptance/2026-11-11/verify-unknown-entity-fix.json)
cover explicit/inferred missing receipts, scored/incomplete receipt verification,
contradictions, invalid-token 401 and policy-conflict 409.
An unknown receipt returns HTTP 200 with `blocked: true`, no score, and
`delivered.status: "no_receipt"`; a successful HTTP status must not be interpreted
as permission to deliver the submitted narration.

**Separate acceptance limits:** disabled-verification and missing-endpoint rejection,
storage authorization/outage propagation and tampered-receipt handling have regression
coverage, not a live fault-injection demonstration in this bundle. A direct
portal/Responses call is not guaranteed to use the supported runner's mandatory
verification. Clean-install completion, full hosted tracing and live cleanup need
their own records.

The second Microsoft Foundry agent uses the Fabric IQ ontology MCP endpoint. Query it through its configured Foundry connection
and confirm an `ask_ontology` MCP trace. Do not call agent-creation scripts just to
query an existing agent: creation/update is a different, mutating operation. Final
copy/paste smoke commands are in the [deployment guide](../deploy/README.md#verification-and-querying-the-cloud-agent).

## Tracing, approval and failure interpretation

Confirm Function and Foundry tracing settings, then correlate a new request in the
configured Application Insights resource. A configured connection is not proof of
complete hosted traces or alerts. Avoid including private prompts or identities in
public diagnostic outputs.

Use [authority.md](authority.md) for the separate human workflow. A single-person
approval is not a two-person demonstration; a hash-chained log is tamper-evident, not
tamper-proof. Approval never constitutes operational execution.

On failure, retain the verification JSON, command, chosen manifest/policy hashes and
sanitized error. Check tenant, target endpoint, RBAC propagation, dataset namespace,
SQL permissions and graph freshness before any deliberate redeployment. Do not change
policy, disable auth or silently use a different model to make a check pass.

## Uninstall

Preview with `.\deploy\uninstall.ps1 -WhatIf`. Actual uninstall and optional
`-RemoveFabricItems`/`-Purge` are destructive, not tests; review their target and
retention consequences in [deploy/README.md](../deploy/README.md#uninstall).
An existing Fabric capacity is not created by this installer and needs separate
operator-managed pause/scale-down and billing review.
