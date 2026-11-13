# Experiment harness

The three [reports](reports/README.md) preserve recorded methods and results.
This guide explains how to rerun them without overwriting evidence. Experiments
use historical harnesses, sometimes with caller-executed tools; those methods are
not alternative supported product architectures or proof of the new cloud boundary.
For cloud installation and acceptance, use [deployment](../deploy/README.md) and
[testing](testing.md).

## Prerequisites and output safety

Run from the repository root in PowerShell. Use Python development dependencies for
offline checks; live runs additionally need the relevant OpenAI/Foundry SDK, Entra
login, model access/quota and, for Fabric sources, SQL access/ODBC and active capacity.
Live calls are billable even when tools only read. Agent creation changes project
state. Use an isolated experiment project/workspace, not the deployed product agents.

Never write new runs under `docs/evidence/` or overwrite recorded `results/`.
Choose a new ignored output directory:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = 'src;scripts'
$repro = Join-Path ([IO.Path]::GetTempPath()) ('pinned-repro-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $repro | Out-Null
$env:PINNED_REPRO_OUT = $repro
```

Set endpoints explicitly before live use; several historical scripts have personal
defaults that must not select your target:

```powershell
$env:PINNED_AOAI_ENDPOINT = '<your-Azure-OpenAI-resource-endpoint>'
$env:PINNED_AOAI_API_VERSION = '<API-version-supported-by-your-deployments>'
$env:PINNED_AOAI_STRONG = '<strong-model-deployment-name>'
$env:PINNED_AOAI_SMALL = '<judge-or-small-model-deployment-name>'
$env:FOUNDRY_PROJECT_ENDPOINT = '<your-isolated-Foundry-project-endpoint>'
$env:FABRIC_SQL_SERVER = '<your-experiment-lakehouse-SQL-endpoint>'
$env:FABRIC_SQL_DATABASE = '<your-experiment-lakehouse-database>'
```

The [configuration loader](../src/pinned_scoring/experiment/config.py) accepts
`--config` where exposed; JSON fields are `endpoint`, `api_version`, `strong`, `small`,
`temperature` and `max_retries`. Environment values override endpoint/version/model
fields. Live model calls require the appropriate Azure OpenAI role. Match recorded
model versions, temperature support and quota where possible; record any difference.
If a model is unavailable, record **not run**, without substitution. Phi-4's recorded
endpoint rejected tools; the harness omits its B arm. Claude notes are not model results.

## Running

### Experiment 1: locking the score

Offline stub preview (no model/cloud calls):

```powershell
python scripts\run_experiment.py --dry-run --repeats 3 --out "$repro\score-dry"
```

Live baseline, **billable** (deployment names are examples; only use models actually
available in your selected resource):

```powershell
python scripts\run_experiment.py --models gpt-4.1,gpt-4.1-mini --repeats 3 --workers 2 --out "$repro\score-live"
```

The runner reads committed `data/dataset.json` and ground truth through `read_dataset()`
and `examples/wildfire/policy.json`, not `deploy\.out\deployment-data.json`.
Its baseline is P001-P050; `--parcels P001,P002` selects a subset, not a new dataset.
There is **no** `--dataset` or `--policy` CLI argument.
Outputs: `runs.json`, `metrics.json`, `sensitivity.json`, `summary.md` and `receipts/`.
`--no-sensitivity` omits weight sensitivity; `--sensitivity-parcels N` controls its model
subset (default 10). Baseline repeats default to 3; sensitivity model repeats are separate.

Arms A, A+ and B compare model arithmetic, receipt-in-prompt and verified tools.
B(raw) is the same B response before checking, not an additional independent run.
Inspect score error, repeat agreement, fault detection, false alarms, provenance errors
and block rates. Zero delivered B error is enforced by receipt fallback; API failures
and rejected wording still matter. Stub results demonstrate plumbing only.

**The pooled 150-parcel result is not one baseline CLI run.** It combines P001-P050
with P051-P150 batches, model-specific reruns and final-verifier rescoring; use the
[full history](reports/scenario-1-locking-the-score.md) and its evidence index.
The [batch runner](../scripts/run_extra_batches.py) accepts `--out`, `--batch-size`,
`--repeats`, `--workers`, `--attempts`, `--models` (comma-separated), `--last-parcel`,
`--budget`, `--only` and `--dry-run`. Its extra inputs are `data/extra_dataset.json`
and matching ground truth. Safe offline batch preview:

```powershell
python scripts\run_extra_batches.py --dry-run --last-parcel 150 --only 1 --repeats 3 --out "$repro\extra-dry"
```

Do **not** casually remove `--dry-run`: the live batch path scales/restores capacity,
loads/replaces experiment tables and refreshes the graph. It needs an isolated workspace,
explicit capacity/resource-group settings and a reviewed mutation plan. Its historical
restore assumption is not proof that your capacity returns to its true starting state.
`--budget` is an assumed-price guard, not a billing cap.

The gpt-5.5 P001-P050 helper accepts `--model`, `--out`, `--repeats`, `--workers`,
`--budget` and `--only`; it uses two 25-parcel batches. For evidence-only recalculation,
these commands read recorded inputs and write fresh outputs without LLM calls:

```powershell
python scripts\rescore_rows.py --evidence docs\evidence\scenario-1-locking-the-score --out "$repro\rescored"
python scripts\merge_150.py --evidence docs\evidence\scenario-1-locking-the-score --out "$repro\combined-150"
```

The merger follows its expected evidence tree; a separately emitted rescoring folder
does not automatically replace that tree's rows. Reconstruct the tree in scratch
storage if testing a new combination, and preserve model coverage, replaced-row records
and original inputs. Do not claim exact 150-parcel reproduction from a single 50-parcel run.

### Experiment 2: Fabric IQ relationship questions and usefulness

Latest live relationship scope: **150 parcels, 450 CrewAccess records**, five questions
C1-C5; direct GQL and the Foundry ontology-connected agent each matched 5/5.
The [current evidence index](evidence/cloud-acceptance/2026-11-11/summary.json)
pins the dataset/policy hashes and links exact answers and tool calls.
Use [independent GQL checks](testing.md#independent-post-deployment-checks)
with explicit workspace/graph/receipt inputs for a new deployment. Ground truth
must come from that deployment; do not copy answers from another dataset.

Some historical harnesses have **no output CLI option**. The following in-process
launchers redirect their existing `OUT` variables, without modifying source or inventing
`--out` flags. The `--help` of each underlying parser remains authoritative.

Foundry ontology route: existing Microsoft Foundry relationship agent connected to
Fabric IQ, **live/billable**. The chain command below asks each of the five questions
once, matching the latest functional check. Usefulness is a separate 20-question,
three-judge-repeat comparison (60 judgements), not another 150-parcel parity test.

```powershell
python -c "import os; from pathlib import Path; import run_foundry_ontology_eval as m; m.OUT=Path(os.environ['PINNED_REPRO_OUT'])/'ontology'; raise SystemExit(m.main())" --step chain --c3-repeats 0
python -c "import os; from pathlib import Path; import run_foundry_ontology_eval as m; m.OUT=Path(os.environ['PINNED_REPRO_OUT'])/'ontology'; m.main()" --step usefulness --repeats 3
```

Outputs: `ontology\foundry_ontology_chain.json`,
`ontology\foundry_ontology_usefulness.json` and its summary. The agent's deployed model
is configured in Foundry, not a model flag on this parser. The judge uses config
`small` (set `PINNED_AOAI_SMALL` explicitly). Chain ground truth is generated from
receipt inputs via `fabric_load_chain.local_parcels()`; inspect parity with the chosen
graph before comparing to recorded results.

The harness process exit status alone is not an acceptance verdict. Inspect all five
question records: require exact ground-truth matches (including C4's numeric value),
no request/tool errors, and actual `ask_ontology` MCP calls. Agent-creation scripts
can create new versions; do not use them merely to query an existing agent.

Python traversal versus raw-table versus bare comparison, **mutating/billable**:
creates experiment agent versions, reads Fabric and runs three repeats:

```powershell
python -c "import os; from pathlib import Path; import run_e2e_chain as m; m.OUT=Path(os.environ['PINNED_REPRO_OUT'])/'chain_arms.json'; m.GRAPH_AGENT='repro-chain'; m.FLAT_AGENT='repro-chain-flat'; m.main()" --source fabric --model gpt-4.1 --repeats 3 --workers 2 --budget-usd 3
```

The `--source local` historical comparison reads generated local tables but still
makes live model/Foundry calls; it is **not offline**. Inspect `parity`, per-question
correctness, errors and throttled retries in `chain_arms.json`. These Python tools are
not `ask_ontology`, so their success is not a Fabric IQ advantage.

Offline usefulness plumbing, or the earlier direct-model comparison:

```powershell
python scripts\run_usefulness.py --dry-run --repeats 3 --out "$repro\usefulness-dry"
# LIVE / BILLABLE direct-model variant, not the ontology-agent result:
python scripts\run_usefulness.py --agent-model gpt-4.1 --judge-model gpt-4.1-mini --repeats 3 --workers 2 --out "$repro\usefulness-direct"
```

Outputs are `usefulness[-dryrun].json` and corresponding summaries. The recorded
ontology-agent/template result was 35 wins, 2 ties, 23 losses; mean difference +0.57/15,
95% CI -1.30 to +2.38: **inconclusive**, not proof of better advice or a controlled
with-versus-without-ontology experiment.

### Experiment 3: decision-calculation tools

The [decision report](reports/scenario-3-four-tools.md) covers 20 questions per model
(four scenarios, five each). Its Foundry headline runs used one repeat, not the
direct harness's default three. Inputs are the committed decision graph, baseline
observations and pinned policy. These experimental calculation tools are **not**
exposed by the cloud receipt MCP endpoint.

#### Offline calculation and harness check

Use Python 3.11+ and the unique `$repro` output directory from
[Prerequisites and output safety](#prerequisites-and-output-safety). No Azure
login, SDKs, Fabric capacity or cloud deployment is needed for this command:

```powershell
python scripts\run_scenarios.py --dry-run --repeats 1 --workers 2 --out "$repro\decisions-dry"
Get-Content "$repro\decisions-dry\scenarios-dryrun-summary.md"
```

Outputs are `scenarios-dryrun.json` and `scenarios-dryrun-summary.md`.
The runner computes the scenario answers and uses an **oracle stub instead of
an AI agent**. Stub comparison metrics only exercise the harness; they are not
evidence of Foundry-agent accuracy or an advantage over raw-table tools.

#### Run the Foundry comparison with local data

This is the actual Foundry-agent experiment with Python tools executed by your
local runner. It does **not** use Fabric IQ, the Fabric SQL endpoint or the
deployed Azure Function. It needs an existing, isolated Foundry project, a
tool-capable model deployment, permission to create agent versions and call the
model, and Entra authentication. Creating experiment agents changes project
state, and model calls are billable.

Use the Python environment prepared for installation, or install the same
dependencies into your experiment environment:

```powershell
python -m pip install -e ".[dev]" -r deploy\requirements.txt
```

Run the output-safety setup above, then replace the following placeholders with
your own target. Do not rely on the historical project defaults in the scripts:

```powershell
az login --tenant '<your-tenant-id>'
az account set --subscription '<your-subscription-id>'
az account show --query "{subscription:id,tenant:tenantId}" -o json

$env:FOUNDRY_PROJECT_ENDPOINT = '<your-isolated-Foundry-project-endpoint>'
$env:PYTHONPATH = 'src;scripts'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PINNED_REPRO_RUN_ID = [guid]::NewGuid().ToString('N')
$model = '<your-model-deployment-name>'
```

Confirm the selected account and project before running this **mutating,
billable** command. The launcher gives both experiment agents unique names and
writes to your new output directory, rather than modifying the deployed agents
or overwriting historical evidence:

```powershell
python -c "import os; from pathlib import Path; import run_e2e_foundry as m; run_id=os.environ['PINNED_REPRO_RUN_ID']; m.OUT=Path(os.environ['PINNED_REPRO_OUT'])/'decisions-foundry-local'; m.fa.AGENT_NAME='repro-decisions-'+run_id; m.FLAT_AGENT='repro-decisions-flat-'+run_id; raise SystemExit(m.main())" --source local --model $model --repeats 1 --workers 2 --receipt-parcels 0 --budget-usd 8
```

The three arms are the Foundry agent with calculation tools, the Foundry agent
with raw-table lookup and per-parcel what-if tools, and a model given no tools.
Each arm receives 20 questions. `--receipt-parcels 0` omits the separate
receipt-narration side check; it does not reduce the decision-question count.
The estimated USD guard is not an Azure billing cap and may be exceeded by
in-flight calls; inspect the saved completion state.

The runner reads committed `data\graph.json`, `data\dataset.json`, local receipts
and `examples\wildfire\policy.json`. It does not use your deployment-generated
dataset. Results are saved under `$repro\decisions-foundry-local` as
`foundry_arms.json` for `gpt-4.1`, otherwise `foundry_arms_<model>.json`.
Inspect the JSON even if the command exits successfully: `meta.source` must be
`local`, `meta.aborted` must be null, parity must be 20/20, and all three
summary arms must contain 20 results. Check API errors, parsed answers,
correctness, provenance and number traceability; incomplete or failed runs must
not be reported as successful reproductions. New model results need not equal
the historical results.

**Remaining gap:** Experiment 3 (Foundry Agent Tools) used locally executed Python
tools rather than running entirely on cloud services. Integrating these tools
into the provided cloud deployment remains to do.

#### Other historical variants

Direct-model historical variant, **live/billable**, produces `scenarios.json` and
`scenarios-summary.md` (not the Foundry-agent headline result):

```powershell
python scripts\run_scenarios.py --models gpt-4.1 --repeats 1 --workers 2 --out "$repro\decisions-direct"
```

Foundry-agent comparison, **mutating/billable**: creates isolated experiment agent
versions and reads the selected source. The launcher also replaces the script's
historical SQL constants with your explicit environment target:

```powershell
python -c "import os; from pathlib import Path; import run_e2e_foundry as m; m.OUT=Path(os.environ['PINNED_REPRO_OUT'])/'decisions-foundry'; m.fa.FABRIC_SERVER=os.environ['FABRIC_SQL_SERVER']; m.fa.FABRIC_DATABASE=os.environ['FABRIC_SQL_DATABASE']; m.fa.AGENT_NAME='repro-decisions'; m.FLAT_AGENT='repro-decisions-flat'; m.main()" --source fabric --model gpt-4.1 --repeats 1 --workers 2 --receipt-parcels 20 --budget-usd 8
```

Outputs: `decisions-foundry\foundry_arms.json` for the default model, otherwise
`foundry_arms_<model>.json`. `--arms foundry-graph foundry-flat bare-model` selects
arms; `--skip-agent-create` only works with suitable already-created experiment agents.
`--receipt-parcels` controls a separate receipt-narration side check, not decision n.
Newer-model recorded runs used `--source local`; preserve that source label and
parity 20/20 rather than claiming they all ran against Fabric.

Inspect correctness, provenance, pinned-number checks, tool-call counts, errors,
`meta.aborted`, budget state, per-arm n and source parity. Some scripts save partial
results despite returning 0: exit status alone is not completeness. A bare answer
with no numbers can pass number traceability vacuously; it is not an accurate decision.
Estimated USD uses assumed prices, not bills. Fewer tool calls are not measured
latency gains. gpt-4.1-mini's abandoned/throttled decision attempts are **not run**
results, not a substitute model's result.

## Reproducibility limits

Store new outputs with code revision, model deployment/version, input/policy hashes,
source, seeds, repeats, completeness/errors and any environment differences.
Read-only live data checks still consume capacity; experimental agent creation,
load/refresh and capacity operations mutate resources. This documentation task ran
only offline checks and previews: no new model result or fresh-tenant deployment is
claimed. Preserve raw evidence and frozen v9 assets; do not regenerate publication
figures as part of reproduction validation.
