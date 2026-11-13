# Where each part lives in Azure

The supported solution is **cloud-hosted**: Foundry hosts two independent agents; the
Azure Function App owns deterministic scoring, mandatory receipt-answer verification
and receipt MCP tools; Blob holds pinned policy and receipts; Fabric holds evidence,
ontology/graph and human decision logs. Entra ID provides identity and Application
Insights provides tracing. Operator-run tests and deployment scripts are development
and installation, not a second product architecture.

## Component-to-service mapping

| Component | Cloud responsibility | Source / evidence |
|---|---|---|
| Pinned policy, scorer and verifier | Deterministic code in the Function App; a receipt fixes policy, inputs, score, status and provenance. Mandatory verification must run before delivering receipt-agent answers, with receipt-based fallback on mismatch. | [Core code](../src/pinned_scoring/), [Function adapter](../cloud/tool_host/function_app.py), [release status below](#verified-baseline-and-release-gates) |
| Receipt MCP host | Azure Functions streamable HTTP `/api/mcp`; four read-only inspection tools: `get_receipt`, `list_receipts`, `list_reasons`, `compare`. No authority writes or planning-calculation tools. | [Host](../src/pinned_scoring/cloud_tool_host.py), [deployment](../deploy/README.md) |
| Receipt agent | Foundry `pinned-score-agent`, calling the Function MCP connection directly. The shared tool surface cannot approve or execute work. | [Foundry integration](../src/pinned_scoring/foundry_agent.py), [agent guide](foundry-agent.md) |
| Relationship agent | Separate Foundry `pinned-score-agent-fabric`, using the Fabric IQ ontology connection and `ask_ontology`. Relationship answers are not score-receipt verification evidence. | [Creation script](../scripts/create_foundry_fabric_agent.py), [recorded relationship report](reports/scenario-2-ontology.md) |
| Evidence and connections | Existing Fabric workspace/capacity; lakehouse Bronze/Silver/Gold, five ontology entity types, four relationships and graph. | [Fabric guide](fabric.md), [DDL](../fabric/sql/tables.sql) |
| Policy and receipt storage | Azure Blob, dataset/policy-scoped active receipt namespace; managed-identity reads; no versioning, soft delete, or WORM/immutability by default. | [Bicep](../deploy/resources.bicep), [configured dataset](../deploy/README.md) |
| Human decision log | Fabric `authority_decisions`; separate propose/review/approve workflow, not an MCP approval tool or operational executor. | [Authority guide](authority.md), [recorded evidence](evidence/authority-plane/summary.md) |
| Identity | Entra ID; `DefaultAzureCredential` for operator scripts, managed identities for hosted access, resource-scoped roles and token checks. | [Security](../SECURITY.md), [Bicep](../deploy/resources.bicep) |
| Tracing | Workspace-based Application Insights and Log Analytics; Function settings and a Foundry project connection are wired by deployment. | [Tracing script](../deploy/enable_tracing.ps1), [traced query](../scripts/run_traced_question.py) |

## Verified baseline and release gates

These are different kinds of evidence. A target diagram is not a deployment receipt,
an offline test is not a live check, and one live environment is not a fresh-tenant test.

| State | What is supported by the baseline |
|---|---|
| Approved target | Function-hosted scoring and mandatory server-side verification/fallback; Foundry agents, Blob policy/receipts, Fabric evidence/relationships/decision log. No laptop/caller-host product alternative. |
| Live Function runtime | [150-parcel scoring/hash parity](evidence/installation-acceptance/cloud-runtime.json), valid narration, contradiction fallback and unauthenticated HTTP 401. |
| Live Microsoft Foundry paths | [Step 8 completion](evidence/installation-acceptance/checkpoint.json) includes the receipt MCP agent plus Function verification check; no separate console transcript was saved. The [ontology-connected agent](evidence/installation-acceptance/fabric-agent-smoke.json) matched C1–C5 5/5 with remote `ask_ontology` calls. |
| Live Fabric data | [150/150 SQL receipt hashes](evidence/installation-acceptance/fabric_hash_check.json); [150 parcels, 450 CrewAccess records and direct GQL C1–C5 5/5](evidence/installation-acceptance/fabric_graph_chain_gql.json). |
| Experiment scopes | [Score comparison](reports/scenario-1-locking-the-score.md): pooled 150 parcels. [Latest relationship checks](reports/scenario-2-ontology.md): five questions against 150 parcels; usefulness is a separate 20-question comparison. [Decision comparison](reports/scenario-3-four-tools.md): 20 questions per setup per model, with the recorded local/Fabric execution-source distinctions. |
| End-to-end installation accepted | The [single installation evidence index](evidence/installation-acceptance/summary.json) records all nine stages completed, `status: accepted`, `accepted: true`, and 35 minutes 17 seconds elapsed. Source, private parameters and all seven checkpoint artifact hashes matched at collection. The tested working tree includes implementation changes not yet committed; its source-file fingerprint identifies that source independently of Git HEAD. |
| Not demonstrated here | An empty subscription/new tenant, every tenant/model/size configuration, live storage-failure injection/cleanup, production performance/availability, real-world policy calibration, complete hosted tracing or live two-person approval. |

The configurable installer defaults to `parcelCount: 150`, matching the current live
runtime evidence. Other configurations require their own checks. The canonical install instructions are in
[deploy/README.md](../deploy/README.md); independent checks are in [testing.md](testing.md).

## Foundry tracing and evaluation hooks

Hosted tracing **is configured**, rather than a design-only feature. Function settings
include Application Insights, and `enable_tracing.ps1` configures the Foundry project
connection. A recorded client-span trace does not establish that every hosted hop is
captured or that the portal Traces view, retention, redaction and alerts were verified.
Check the deployed resource and a new correlated request before claiming end-to-end
observability.

`scripts/export_foundry_eval.py` exports JSONL for evaluation; it does not itself run
a Foundry evaluator or upload an evaluation. The experiment's score, provenance and
block-rate metrics remain the recorded results.

## Reading Gold with Entra auth

`pinned_scoring.fabric.sql_endpoint.fetcher(server, database)` and `GoldReader` are
hash-checking SQL-endpoint adapters. Their mocked-connection tests do not prove live
SQL permissions. The deployed receipt MCP host reads Blob, not the Gold SQL endpoint.
Fabric readers require explicit access to the chosen lakehouse; gold-only SQL grants
must be verified separately rather than inferred from Blob RBAC.

## Azure Well-Architected Framework: AI workload pillars

| Pillar | Evidence / implemented mechanism | Not established |
|---|---|---|
| Reliability | Live 150-parcel receipt parity, Function verification/fallback and missing-receipt rejection; regression coverage for invalid receipts | Availability target, failover and live load/fault testing of the whole service |
| Security | Entra, resource-scoped RBAC, receipt hashing, read-only MCP tools and separate human approval | Prompt-injection/leakage guarantees, compliance, private endpoints, tamper-proof logs or locked retention |
| Cost optimization | Experiment token counts and assumed-price estimates; code arithmetic uses no LLM tokens | Actual bills, analyst time saved, hard Azure/Fabric spend limits or production savings |
| Operational excellence | Bicep, deployment previews, automated tests, [runbook](testing.md), tracing configuration | Fresh-tenant reproducibility, production alerts, on-call readiness and complete trace delivery |
| Performance efficiency | Recorded tool counts and synthetic-volume checks | Tool counts are not latency measurements; no general latency/throughput benchmark |

The [Azure Well-Architected Framework](https://learn.microsoft.com/azure/well-architected/)
is an assessment lens, not a certification earned by this reference implementation.
