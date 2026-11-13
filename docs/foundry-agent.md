# Foundry agent: `pinned-score-agent`

Foundry hosts the receipt agent and calls the Azure Function App's MCP connection
directly. A **separate** agent, `pinned-score-agent-fabric`, queries the Fabric IQ
ontology through `ask_ontology`. They are not one combined agent, and neither shared
remote tool endpoint supplies approval or operational execution.

## Design

- **Narrates only.** The receipt agent reads `get_receipt`, `list_receipts`,
  `list_reasons` and `compare`; the model does not own the score.
- **Cloud computation and enforcement.** The approved architecture places deterministic
  scoring and mandatory receipt-answer verification/fallback in the Function App.
  Runtime revision `91fbe33` implements `/api/score` and `/api/verify`, with mandatory
  Function verification on the receipt runner's answer-delivery path. Corrected
  HTTP-helper integration and bypass rejection are offline-tested, not yet live
  deployment evidence. The corrected runtime is integrated; live cloud-runtime acceptance is pending;
  see [verified status](azure-mapping.md#verified-baseline-and-release-gates).
- **Entra authentication.** Hosted connections use the configured identity; operator
  scripts use `DefaultAzureCredential`. There are no API-key setup instructions.
- **Decision boundary.** Receipt inspection, relationship queries, experimental planning
  calculations and [human approval](authority.md) are separate workflows.

Code: [Foundry integration](../src/pinned_scoring/foundry_agent.py),
[receipt host](../src/pinned_scoring/cloud_tool_host.py),
[cloud runner](../scripts/run_cloud_agent.py).

## Create / update

Use the [canonical installer](../deploy/README.md), which configures the connections
and independent agents in the chosen project. Do not copy a personal project endpoint
from an old example or run an experimental agent-creation harness against production
agents.

## Chat

Use the project endpoint printed by your deployment and the matching
`deploy\.out\deployment-data.json`; see [query and verification guidance](testing.md#cloud-query-release-gate).
Do not treat an unverified direct portal/Responses answer as proof that the approved
mandatory cloud verifier is active. The runbook's revision-qualified commands require
the corrected implementation and matching Function package; `--verify` now adds an
MCP-trace requirement rather than enabling optional local verification.

The older `chat_foundry_agent.py` caller-executed function-tool loop remains historical
experiment/developer code, not a supported local product architecture.

## Tracing and evaluation (optional)

Deployment wires Application Insights for the Function App and a Foundry project
connection. This is distinct from SDK console instrumentation used in earlier tests.
Confirm a new correlated request reaches monitoring; configuration alone does not
prove complete trace coverage. See [tracing and evaluation](azure-mapping.md#foundry-tracing-and-evaluation-hooks).

`scripts/export_foundry_eval.py` produces a JSONL evaluation dataset; it does not run
a hosted evaluator. Do not overwrite recorded evidence to make a new evaluation look
like the original run.

## Portal

Open your deployed Foundry project, then **Agents**, and select the receipt or Fabric
IQ agent. The project endpoint and deployed model are environment-specific outputs.

## Observed runs

The recorded receipt-MCP baseline exercised all four receipt tools remotely with
`local_tool_execution: false` and caller-verifier checks. Historical blocked narration
(including reason/provenance mismatches) demonstrates that models can still contradict
receipts; it does not prove that the new server-side boundary has been deployed.

## Tests

Mocked-client tests run offline; [testing.md](testing.md) distinguishes them from
live MCP checks and billable agent queries.
