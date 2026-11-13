<h1 align="center">WFI Pinned Scoring Accelerator</h1>

<p align="center">
  <strong>Risk scores calculated in code, explained by AI and checked before human approval.</strong><br/>
  A reference implementation on Microsoft Foundry and Microsoft Fabric, with fixed scoring rules and labelled input sources.
</p>

<p align="center">
  <a href="https://github.com/enekazo/WFI-ScoringAccelerator/actions/workflows/tests.yml"><img alt="Offline tests" src="https://github.com/enekazo/WFI-ScoringAccelerator/actions/workflows/tests.yml/badge.svg?branch=main"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="Microsoft Foundry" src="https://img.shields.io/badge/Microsoft-Foundry-0078D4">
  <img alt="Microsoft Fabric" src="https://img.shields.io/badge/Microsoft-Fabric%20IQ-117865">
  <img alt="Entra ID, no keys" src="https://img.shields.io/badge/auth-Entra%20ID%2C%20no%20keys-243A5E">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-5C6B7A">
  <img alt="Status: reference implementation" src="https://img.shields.io/badge/status-reference%20implementation-C25E00">
</p>

<p align="center">
  <a href="#architecture">Architecture</a> ·
  <a href="#results">Results</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#how-the-score-is-calculated">How scoring works</a> ·
  <a href="#limitations">Limitations</a>
</p>

## Accelerator use case: forestry planning

The accelerator supports a wildfire-planning example. A planner at a utility, fire agency, insurer or forestry team needs to decide which land parcels should get a work crew first, for work such as vegetation clearing. Scores use evidence about fuel load, dead fuel, moisture, weather and wind. Fabric IQ holds related information about roads, nearby homes, water sources and crew access.

The planner needs a repeatable score and an explanation of where its inputs came from. AI alone cannot guarantee either, and the decision remains with the planner:

- The same evidence must produce the same score, so the basis for a crew list can be defended and repeated.
- The explanation must distinguish measured, estimated and missing inputs.
- The planner chooses the crew list; the AI never does.

Python code calculates each score under a fixed policy and writes a receipt, the stored record of the result and its supporting evidence. The agent explains that result, and a verifier checks its answer against the receipt before the planner decides. The example uses synthetic parcels. You can adapt the policy and evidence to another domain, but must test the approach there first.

## What is this accelerator?

The WFI Pinned Scoring Accelerator combines scoring code, business context from Microsoft Fabric IQ and explanations from a large language model (LLM). Users can inspect a risk score, understand its significance in context and check the evidence behind it.

The architecture gives each component a distinct responsibility:

- The scoring model applies approved rules to the evidence. It is deterministic: the same inputs and policy produce the same result, without relying on the LLM's judgement.
- Fabric IQ connects each scored item to related information. Nearby homes, road access, water sources and crew reachability help a planner interpret a high wildfire risk score.
- The LLM lets users ask questions, compare results and get explanations based on stored scores or connected data.
- The verifier checks receipt-based explanations against the authoritative scoring record. If the narration contradicts that record, receipt-based text replaces it.

Users can explore why an item is high risk and use the supporting information to make and explain their own decision.

Follow the [Quick start](#quick-start) to deploy the solution, then ask its agents about scores and connected information. The synthetic wildfire example lets you explore the workflow before introducing your own business evidence and approved rules.

Other possible uses include supplier risk, maintenance priorities and compliance assessments. Each requires its own rules, data, relationships and domain-appropriate safeguards. The accelerator is a starting point for that work, not a ready-made application for those industries.

<p align="center">
  <img src="docs/dissemination/figures/v6/fig00_crew_allocation_flow.png" alt="Forestry crew allocation flow: evidence in Fabric Gold, a pinned scorer that writes a receipt, Fabric IQ for job context, a Foundry agent with read-only tools, a verifier, a planner who decides, and the crew list; mismatches and rejections go back to a human" width="820">
</p>

> [!WARNING]
> This reference implementation has not been validated for wildfire decisions in the field. Its evidence uses synthetic data and illustrative policy weights.

## Architecture

<p align="center">
<img src="docs/dissemination/figures/fig02_azure_architecture.png" alt="Approved cloud architecture shared with the v9 article: Microsoft Foundry agents and MCP connections, an Azure Function hosting the deterministic scorer, receipt tools and verifier, Blob Storage for policy and receipts, Fabric Lakehouse and Fabric IQ for evidence and relationships, human approval with a Fabric decision log, Application Insights and Microsoft Entra ID" width="1000">
</p>

| Component | Placement in the approved cloud architecture |
|---|---|
| Policy and deterministic scorer | Azure Function App; hash-pinned policy stored in Azure Blob Storage |
| Narration verifier | Azure Function App; mandatory answer verification and receipt-based fallback before delivery |
| Cloud MCP receipt tool host (4 read-only receipt tools) | Azure Functions Python v2 (`cloud/tool_host/`, `src/pinned_scoring/cloud_tool_host.py`) over streamable HTTP (`/api/mcp`), reading receipts from Azure Blob Storage via Managed Identity |
| Explanation agents (`pinned-score-agent` and `pinned-score-agent-fabric`) | [Foundry Agent Service](https://learn.microsoft.com/azure/ai-foundry/agents/overview), calling the cloud MCP tool host and Fabric IQ through Foundry `RemoteTool` MCP connections (no local caller loop required) |
| Evidence (Bronze, Silver, Gold, `authority_decisions`) | [Fabric Lakehouse](https://learn.microsoft.com/fabric/data-engineering/lakehouse-overview), [medallion pattern](https://learn.microsoft.com/fabric/onelake/onelake-medallion-lakehouse-architecture) |
| Connections (road, homes, water, crew access) | Fabric IQ ontology and graph, queried with GQL and through a connection-based MCP tool |
| Policy and receipts store | Azure Blob Storage with versioning, dataset/policy-scoped receipts and Entra-only auth |
| Tracing | Log Analytics and workspace-based Application Insights, wired to the Foundry project and Function App |
| Authentication | [Microsoft Entra ID](https://learn.microsoft.com/entra/identity/managed-identities-azure-resources/overview): `DefaultAzureCredential`, managed identity, no API keys |

The deployment has two independent agents. The shared receipt endpoint supports inspection only. Planning calculations and approval use separate workflows.

See [docs/azure-mapping.md](docs/azure-mapping.md) for the architecture details.

## Results

We tested score consistency, answers from Fabric IQ and calculations through Foundry Agent Tools. The [v9 article](docs/dissemination/techcommunity-post-v9.md#what-we-measured) discusses these three experiments.

| Experiment | Test scope | Models tested |
|---|---|---|
| [1. Locking the score](docs/reports/scenario-1-locking-the-score.md) | 150 parcels; three repeats per available test setup | gpt-4.1, gpt-4.1-mini, Phi-4, gpt-5.5, gpt-6.1-sol, gpt-6.1-terra, grok-4.7 |
| [2. Fabric IQ](docs/reports/scenario-2-ontology.md) | Five connection questions against 150 parcels; a separate 20-question usefulness comparison | Live checks: `scoring-agent-model2` deployment; usefulness: gpt-4.1, judged by gpt-4.1-mini |
| [3. Foundry Agent Tools](docs/reports/scenario-3-four-tools.md) | 20 questions covering four decision scenarios; one repeat per test setup per model | gpt-4.1, gpt-5.5, gpt-6.1-sol, gpt-6.1-terra, grok-4.7 |

<details>
<summary><strong>Experiment 1: Locking the score</strong></summary>

A model calculating a risk score can return the wrong number, change its answer between runs or describe an estimated input as measured. We compared the models' calculations with a code-calculated, verified path across 150 parcels.

The deterministic scorer applies a policy identified by its hash and writes receipts containing the score, band, input sources and reasons for incomplete data. Read-only tools expose those receipts. A verifier checks the model's explanation against them.

We evaluated seven models, repeating each available test setup three times. Each setup asks the same question with different data or tools. The full reports call these setups "arms"; the letters below match the evidence files.

| Test setup | What the AI is given | What we check |
|---|---|---|
| **A** (AI calculates the score) | The input data and the rules for calculating the score | The score and explanation written by the AI |
| **A+** (AI is shown the stored result) | The same data and rules, plus the stored result (the receipt) | Whether the AI correctly repeats and explains that result |
| **B** (AI looks up the result, then its answer is checked) | Read-only functions for looking up stored results | The answer after checking it against the receipt; if the check fails, text from the receipt is shown instead |
| **B(raw)** (Same AI answer, before the check) | The same test setup as the previous row | What the AI originally said before its answer was checked; this is not an extra test |

We measured score errors, agreement between repeated answers, detection of bad inputs, correct descriptions of input sources and how often verification rejected an answer. Phi-4 was not tested with tools.

The table gives the average difference between the AI's score and the policy-calculated score for parcels with a valid score, on a scale of 0 to 100. Lower is better; 0 means they matched. If the rules give 60 and the AI gives 70, for example, the difference is 10 points.

| Model | A | B |
|---|:---:|:---:|
| gpt-4.1 | 32.646 | 0.000 |
| gpt-4.1-mini | 14.508 | 0.000 |
| Phi-4 | 15.771 | Not tested |
| gpt-5.5 | 0.000 | 0.000 |
| gpt-6.1-sol | 0.000 | 0.000 |
| gpt-6.1-terra | 0.000 | 0.000 |
| grok-4.7 | 0.000 | 0.000 |

<p align="center">
  <img src="docs/dissemination/figures/v9/test-1-score-consistency.png" alt="150-parcel score experiment: model-calculated scores compared with verified answers and receipt fallback" width="780">
</p>

Four models calculated every score correctly from raw data. All six tool-enabled models delivered policy-matching scores through verification. For gpt-4.1, repeatability rose from 62% to 100%, while fault detection was approximately 92% versus 91%, so verification did not improve every metric. The verifier rejected 76% of its answers in setup B and replaced them with receipt-based text. The check and fallback enforce zero delivered score error; the model itself can still make mistakes.

**Full report:** [Locking the score: methods and experiment history](docs/reports/scenario-1-locking-the-score.md) · [Pooled 150-parcel results and confidence intervals](docs/evidence/scenario-1-locking-the-score/combined-150/combined_150.md)

| Supporting evidence | Direct link |
|---|---|
| Pooled model metrics, coverage and block rates | [combined_stats.json](docs/evidence/scenario-1-locking-the-score/combined-150/combined_stats.json) |
| Final-verifier re-scoring record for P001–P050 | [rescore_report.json](docs/evidence/scenario-1-locking-the-score/combined-150/rescored-p001-p050/rescore_report.json) |
| Recorded gpt-4.1 answers for P001–P050, re-scored with that verifier | [runs.json](docs/evidence/scenario-1-locking-the-score/combined-150/rescored-p001-p050/main/runs.json) |
| Original rows retained when re-runs replaced them | [replaced_rows.json](docs/evidence/scenario-1-locking-the-score/combined-150/replaced_rows.json) |

</details>

<details>
<summary><strong>Experiment 2: Fabric IQ</strong></summary>

A planner needs to know which roads reach a parcel, whether water is available along them and which crew types can get in. Answering those questions requires relationships across several tables.

Our Fabric IQ ontology, a model of entities and their relationships, connects parcels, roads, homes, water sources and crew access through five entity types and four relationships. A Foundry agent queries it using the connection-based `ask_ontology` tool through Model Context Protocol (MCP).

We checked five planning answers against results calculated in code. The latest live run used 150 parcels, 154 roads, 129 home groups, 75 water sources and 450 CrewAccess records. Code computed answers C1 to C5 from the deployment's receipt-scoped dataset. We checked direct Graph Query Language (GQL) queries and the Foundry agent's `ask_ontology` calls against those results, including exact parcel sets and the C4 numeric value. The recorded model field is the deployment alias `scoring-agent-model2`; its underlying model and version were not independently verified.

| Setup | Correct answers |
|---|:---:|
| Direct GQL queries on the 150-parcel Fabric graph | 5 of 5 |
| Microsoft Foundry agent using Fabric IQ ontology MCP | 5 of 5 |

C5 returned all 15 expected critical-facility parcels without engine access, including those with passable roads too narrow for an engine. C4 returned 59 homes on P057.

In a separate usefulness test, gpt-4.1-mini compared the Fabric IQ agent's answers with template answers across 20 questions and 60 judgements, without knowing which source produced each answer. The agent won 35, tied 2 and lost 23. Its mean advantage was +0.57 out of 15, with a 95% confidence interval of −1.30 to +2.38.

Direct GQL and the ontology-connected Foundry agent matched all five answers for the current dataset. That establishes correctness for those checks, not a model-comparison or usefulness result. The five-question check did not rerun the separate 20-question usefulness comparison, which remains inconclusive.

**Full report:** [Fabric IQ: setup, connection tests and usefulness evaluation](docs/reports/scenario-2-ontology.md)

| Supporting evidence | Direct link |
|---|---|
| Current dataset identity and live-check scope | [summary.json](docs/evidence/cloud-acceptance/2026-11-11/summary.json) |
| 150-parcel counts, GQL answers and computed ground truth | [fabric_graph_chain_gql.json](docs/evidence/cloud-acceptance/2026-11-11/fabric_graph_chain_gql.json) |
| Five Foundry answers, remote MCP calls and exact answer checks | [fabric-agent-smoke.json](docs/evidence/cloud-acceptance/2026-11-11/fabric-agent-smoke.json) |
| Agent/template answers, individual judgements and usefulness results | [foundry_ontology_usefulness.json](docs/evidence/scenario-2-ontology/foundry_ontology_usefulness.json) |

</details>

<details>
<summary><strong>Experiment 3: Foundry Agent Tools</strong></summary>

Planning questions can require joins and calculations across connected records: who is exposed, which treatment gives the best return or whether changing a scoring weight changes a recommendation.

We tested five models on 20 questions using calculation functions, raw-table lookup tools and no data. One question asked whether the recommended electricity feeder to switch off would change if wind received 20% more weight. We checked whether calculation functions gave correct, traceable answers with fewer requests.

These tools are read-only Python functions called by the Foundry agent. Code performs the joins and arithmetic, returning the scoring-policy hash and a what-if label where applicable. The four scenarios are:

| Scenario | Planner question |
|---|---|
| Exposure | Who would be affected if a parcel ignites? |
| Mitigation | Which treatment gives the most risk reduction per dollar? |
| Feeder shutdown | Which electricity feeder should be switched off first? |
| Weight sensitivity | Does the recommendation change if a scoring weight moves by 20%? |

We asked five questions per scenario, with one repeat per test setup for each model. Code calculated the correct answers in advance. With raw-table lookup tools, the model had to do the joins and arithmetic itself. We checked answer correctness, input sources and whether each number could be traced to returned data.

| Model | Correct with calculation functions | Correct with raw-table lookup | Correct with no data | Mean raw-table retrieval requests per question |
|---|:---:|:---:|:---:|:---:|
| gpt-4.1 | 100% | 0% | 0% | 11.0 |
| gpt-5.5 | 100% | 95% | 5% | 48.5 |
| gpt-6.1-sol | 100% | 100% | 5% | 43.8 |
| gpt-6.1-terra | 100% | 100% | 5% | 47.0 |
| grok-4.7 | 95% | 75% | 0% | 45.5 |

With calculation functions, every model made one calculation request per question, and every number passed the tool-output traceability check. Raw-table lookup needed roughly 46 retrieval requests per question on the four newer-model runs.

<p align="center">
  <img src="docs/dissemination/figures/v9/test-3-foundry-agent-tools.png" alt="Correctness across five models using calculation functions, raw-table lookup tools and no data; calculation-function results ranged from 95 to 100 per cent" width="780">
</p>

Some models matched the calculation-function accuracy using raw tables. The functions therefore did not always improve correctness, but they reduced data retrieval and returned traceable numbers calculated in code. This evaluated workflow is separate from the deployed receipt endpoint, which does not expose these functions.

**Full report:** [Foundry Agent Tools: methods, per-model results and decision questions](docs/reports/scenario-3-four-tools.md)

| Supporting evidence | Direct link |
|---|---|
| gpt-4.1: questions, ground truth, answers and metrics | [foundry_arms.json](docs/evidence/scenario-3-four-tools/foundry_arms.json) |
| gpt-5.5: questions, ground truth, answers and metrics | [foundry_arms_gpt-5.5.json](docs/evidence/scenario-3-four-tools/foundry_arms_gpt-5.5.json) |
| gpt-6.1-sol: questions, ground truth, answers and metrics | [foundry_arms_gpt-6.1-sol.json](docs/evidence/scenario-3-four-tools/foundry_arms_gpt-6.1-sol.json) |
| gpt-6.1-terra: questions, ground truth, answers and metrics | [foundry_arms_gpt-6.1-terra.json](docs/evidence/scenario-3-four-tools/foundry_arms_gpt-6.1-terra.json) |
| grok-4.7: questions, ground truth, answers and metrics | [foundry_arms_grok-4.7.json](docs/evidence/scenario-3-four-tools/foundry_arms_grok-4.7.json) |

</details>

<a id="live-cloud-verification"></a>

<details>
<summary><strong>Installation verification</strong></summary>

On 12 November 2026, the accelerator completed a full installation run from Step 1 through Step 9. It deployed and configured the Azure, Microsoft Fabric and Microsoft Foundry components and completed the installer's verification stage. All nine stages succeeded, with the installer recording `status: accepted` and `accepted: true`.

The run took 35 minutes 17 seconds, including 7 minutes 28 seconds for automated tests and live verification. Checks covered the configured 150-parcel dataset, receipt-hash consistency, Fabric relationships, Function scoring and answer verification, and both Foundry agent paths.

These checks cover one 150-parcel deployment, not every tenant, model or dataset configuration.

| Check | Recorded result | Evidence |
|---|---|---|
| Installation | All nine stages completed; accepted | [Checkpoint](docs/evidence/installation-acceptance/checkpoint.json) |
| Function scoring and receipt hashes | 150/150 matched | [Runtime checks](docs/evidence/installation-acceptance/cloud-runtime.json) |
| Fabric SQL receipt hashes | 150/150 matched | [SQL parity](docs/evidence/installation-acceptance/fabric_hash_check.json) |
| Direct graph and Foundry relationship answers | C1–C5: 5/5 matched on each path | [GQL](docs/evidence/installation-acceptance/fabric_graph_chain_gql.json) · [Foundry MCP calls](docs/evidence/installation-acceptance/fabric-agent-smoke.json) |
| Answer verification and authentication | Valid narration accepted; contradiction replaced with receipt fallback; unauthenticated request returned HTTP 401 | [Runtime checks](docs/evidence/installation-acceptance/cloud-runtime.json) |
| Offline suite and receipt-agent check | Completed within Step 8; no separate console transcript saved | [Checkpoint](docs/evidence/installation-acceptance/checkpoint.json) |

The [installation evidence index](docs/evidence/installation-acceptance/summary.json) links the checkpoint, source fingerprint and supporting outputs in one acceptance record. It omits private configuration, credentials, target URLs and personal filesystem paths. The raw evidence is preserved privately.

</details>

<a id="how-the-score-is-calculated"></a>

<details>
<summary><strong>How the score is calculated</strong></summary>

The policy file, such as `examples/wildfire/policy.json`, supplies the rules for the arithmetic in `src/pinned_scoring/scorer.py`. Scoring does not call a model.

For each factor in the policy, in order:

1. Take the latest observation for the factor's metric observed at or before the scoring time.
2. Reject it if it is missing, flagged invalid, in the wrong unit, from a source not in `allowed_provenance`, past its `valid_until`, or older than `max_age_hours`. Record the reason, such as `expired:fuel_moisture_pct`.
3. Scale it to a value between 0 and 100: `ratio = clamp((value - low) / (high - low), 0, 1)`; if `invert` is true, use `1 - ratio`; multiply by 100.
4. Apply the weight: `contribution = weight × scaled value`.

Then:

- If any factor was rejected, the result is incomplete, with no score or band and a list of reasons. The code does not estimate missing values.
- Otherwise `score = sum of contributions`, rounded to one decimal (half up). The band is the first entry in `bands` whose upper bound is above the score.
- The receipt records the policy's SHA-256 and a hash of the inputs, so any score can be recomputed and audited.

<details open>
<summary><strong>Worked example</strong> (wildfire example policy, all readings fresh and measured)</summary>

| Factor | Reading | Scaled (0–100) | Weight | Contribution |
|---|--:|--:|--:|--:|
| fuel_load | 20 t/ha | 50.0 | 0.30 | 15.00 |
| dead_fuel | 40 % | 40.0 | 0.15 | 6.00 |
| moisture (inverted) | 15 % | 71.4 | 0.25 | 17.86 |
| weather_danger | 51 | 60.0 | 0.20 | 12.00 |
| wind | 30 km/h | 50.0 | 0.10 | 5.00 |
| **Total** | | | **1.00** | **55.86 → 55.9 (high)** |

The band from 50 to 75 gives **high**.

</details>

A receipt identifies the policy and inputs behind a number. It cannot establish whether the policy is right; its owner remains accountable for the weights. To adapt the accelerator, edit the policy's factors, weights, low and high limits, freshness limits, allowed sources and bands. The weights must sum to exactly 1.

</details>

<a id="deploy-to-azure"></a>

## Quick start

The one-command installer deploys the Azure resources, Function-hosted receipt tools, scoring, mandatory answer verification and fallback, Fabric data and both independent Foundry agents. It then runs acceptance checks. The [live cloud verification record](#live-cloud-verification) documents the tested 150-parcel deployment and the separate clean-install acceptance boundary.

Install the [prerequisites](deploy/README.md#prerequisites), including the Python deployment dependencies, first. You need access to the target subscription and tenant, and permission to use an active Fabric capacity (`F4+`) or provision the configured capacity when both the workspace and capacity are absent. An existing workspace must already be assigned to the configured capacity, which you must be able to access.

```powershell
git clone https://github.com/enekazo/WFI-ScoringAccelerator
cd WFI-ScoringAccelerator

Copy-Item deploy\parameters.template.json deploy\parameters.json
# Edit subscription, tenant, region, resource names, Fabric workspace and parcelCount

$config = Get-Content deploy\parameters.json -Raw | ConvertFrom-Json
az login --tenant $config.tenantId

.\deploy\deploy-all.ps1 -WhatIf   # dry-run preview, no cloud calls
.\deploy\deploy-all.ps1           # full install and verification
```

Your `deploy\parameters.json` stays private and is gitignored. Set `parcelCount` to the number of parcels to prepare. The default is 150.

The [detailed deployment instructions](deploy/README.md) cover prerequisites, all parameters, deployment stages, verification, cloud-agent queries, troubleshooting and uninstall.

### Optional: run the planning-calculation experiment locally

The synthetic electricity-network experiment discussed in V9 is separate from the
cloud installation. It includes 20 questions about exposure, mitigation, feeder
ranking and scoring-weight changes.

With Python 3.11+ installed, run this from the repository root to check the
calculations and test harness offline. It needs no Azure login, cloud deployment
or model calls:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$out = Join-Path $env:TEMP ('wfi-scenarios-' + [guid]::NewGuid().ToString('N'))
python scripts\run_scenarios.py --dry-run --repeats 1 --workers 2 --out $out
Get-Content (Join-Path $out 'scenarios-dryrun-summary.md')
```

This check uses a simulated agent. Its metrics are not Foundry model results.
To reproduce the agent comparison, follow the
[local-data Foundry experiment instructions](docs/experiment.md#run-the-foundry-comparison-with-local-data).
The Python tools execute on your computer while Foundry hosts the agents.
This mode requires an isolated Foundry project and incurs model charges.
It does not require Fabric or the Azure Function deployment.

## Repository layout

```text
src/pinned_scoring/      Scoring, receipts, verification, agents, experiments, decision calculations and review controls
src/pinned_scoring/fabric/ Fabric data access, loading and transforms
cloud/tool_host/        Azure Function App: authenticated, read-only MCP receipt service
examples/wildfire/       Example pinned policy (policy.json)
data/                   Example datasets, relationship graph and reference receipts
scripts/                Dataset generation, experiments, agent setup and queries, verification, tracing and charts
fabric/                 Fabric provisioning, parameter template, notebooks, Livy scripts and SQL
deploy/                 One-click installer, private-parameter template, Bicep, tracing, uninstall and deployment guide
docs/                   Architecture, business case, evaluation, Foundry, Fabric and review/approval guides
docs/reports/           Full reports for the three experiments
docs/dissemination/      Current v9 article and shared publication assets
docs/archive/            Superseded publication drafts and historical assets
docs/evidence/           Metrics, runs, receipts, summaries, charts
results/                Additional recorded experiment runs
tests/                  Automated tests for scoring, tools, deployment, Fabric and review controls
```

<details>
<summary><strong>Developer checks and chart regeneration</strong></summary>

Run the automated checks and rebuild the current article's charts from committed evidence:

```powershell
pip install -e ".[dev]"
$env:PYTHONPATH="src;scripts"; python -m pytest -q

pip install -e ".[charts]"
python scripts/make_v9_test_charts.py
```

</details>

## Recommendations

1. Keep the pinned scoring policy and receipts independent of the model chosen to explain them.
2. Require Function-hosted verification and receipt fallback before release. An optional caller-side `--verify` check does not meet that requirement.
3. Set `parcelCount` once in `deploy\parameters.json`; do not edit counts in individual scripts.
4. Keep approval separate from proposals and explanations. Neither authorises crew allocation or field work.
5. Compare against the [150-parcel evidence](docs/evidence/scenario-1-locking-the-score/combined-150/combined_150.md) whenever you change a model, policy or dataset.

## Limitations

- We used synthetic data for the evaluation.
- Our Fabric IQ ontology has five entity types and four relationships. We have not tested larger or more complex ontologies.
- Experiment 3 (Foundry Agent Tools) ran its Python tools locally. These tools are not yet integrated into the provided cloud deployment.
- The solution does not allocate budgets or assign crews, check whether field work can begin or carry out that work.
- We have not measured performance or reliability at larger deployment sizes.

### When not to use it

- The result is a judgement or forecast with no stable rules to pin.
- Answers contain no numbers or labels the verifier can check.
- A wrong or unrepeatable answer costs nothing.
- The use case requires very high volumes or real-time responses, and extra tool calls and verification would be too slow or costly. We have not measured those limits here.

## More

| What you need | Start here |
|---|---|
| Install or configure the cloud solution | [Detailed deployment instructions](deploy/README.md) |
| Understand the design and what has actually been verified | [Architecture and release status](docs/azure-mapping.md#verified-baseline-and-release-gates) |
| Check an existing deployment without reinstalling | [Testing and operations](docs/testing.md) |
| Reproduce the three experiments or inspect their evidence | [Reproduction guide](docs/experiment.md) · [Evidence reports](docs/reports/README.md) |
| Read the companion article and its business context | [Forestry planning with AI (v9)](docs/dissemination/techcommunity-post-v9.md) · [Business case](docs/business-case.md) |
| Understand the separate human decision workflow | [Review and approval](docs/authority.md) · [Recorded approval evidence](docs/evidence/authority-plane/summary.md) |
| Find older publication drafts, not current instructions | [Archive](docs/archive/README.md) |
| Contribute or check security and attribution | [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Notice](NOTICE.md) |

## Built On + Thanks

- Architecture artwork uses the official [Azure service icons](https://learn.microsoft.com/azure/architecture/icons/) and [Microsoft Fabric icons](https://learn.microsoft.com/fabric/fundamentals/icons), copyright Microsoft. See [NOTICE.md](NOTICE.md) for sources and permitted use.

## License

[MIT](LICENSE)
