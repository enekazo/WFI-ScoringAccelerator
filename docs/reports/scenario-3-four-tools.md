# Scenario 3 – Four tools (decision questions)

**Reproduction:** [Foundry and direct-model commands, sources and outputs](../experiment.md#experiment-3-decision-calculation-tools).
These are historical experimental calculation tools, not capabilities exposed by
the deployed receipt MCP endpoint. Each headline model run covers 20 questions.

**Question this report answers:** when a person asks a *decision* question about a wildfire ("which feeder do we switch off first?", "which mitigation gives the most protection per dollar?"), does an AI agent give a correct, traceable answer when it can call a few strict, read-only tools that do the calculation in code – compared with an agent that only has the raw tables, and with a model that has no tools at all?

Written for a non-technical reader first; every figure is traceable to a raw file listed at the end. All data are **synthetic**. "Locked" means the numbers in an answer were produced by code and the model only quoted them; the model cannot change them.

## 1. What was tested (plain language)

Four decision scenarios, five questions each (20 questions in total):

| Scenario | The question type |
|---|---|
| Exposure | If this parcel ignites, how many customers and critical facilities are exposed? |
| Mitigation | Which mitigation gives the most risk reduction per dollar? |
| De-energise first | Which power feeder should be switched off first? |
| Sensitivity | Does the decision change if one scoring weight moves by 20%? |

Exposure weight = customers + 250 × critical facilities; only the 25 scoring parcels are used (source: `docs/decision-scenarios.md`, kept as a short pointer; see §8).

Three arms:

| Arm | What the agent has |
|---|---|
| **Graph tools** ("four tools" arm) | Five strict, read-only tools on the data graph: `graph_node`, `exposure_if_ignite`, `best_mitigation`, `deenergise_first`, `decision_sensitivity`. The code does the join, filter and arithmetic and returns locked numbers. |
| **Flat tables** | The same data as plain tables, with row-lookup tools only (no SQL, no code interpreter). The model must join and calculate by itself. |
| **Bare** | No tools at all. The model answers from the question text only. |

Two sets of runs are reported:

* **Test 3A – Foundry agents, five models (headline).** The agents are Microsoft Foundry agents; the same 20 questions, 1 repeat per model (n = 20 per arm). gpt-4.1 ran against the data in Fabric; the other four ran against the same data read locally.
* **Test 3B – Direct-model run (earlier, gpt-4.1 only).** The same question set, but the model is called directly rather than through a Foundry agent, with bootstrap confidence intervals.

## 2. Setup at a glance

| Item | Value |
|---|---|
| Data | Synthetic decision graph: 50 parcels, 99 assets, 12 feeders, 34 customer groups, 3 mitigations (seed 20260716; customer counts and costs are invented). Read back from Fabric and equal to the local copy (`graph_readback.json`, `equal_to_local: true`). |
| Questions | 20 (4 scenarios × 5). Ground truth is computed in code. |
| Repeats | 1 (so n = 20 per arm per model; no confidence intervals in 3A) |
| Models (3A) | gpt-4.1, gpt-5.5, gpt-6.1-sol, gpt-6.1-terra, grok-4.7 |
| Where the data came from (3A) | gpt-4.1: **Fabric** (`meta.source = fabric`). gpt-5.5, gpt-6.1-sol, gpt-6.1-terra, grok-4.7: **local** (`meta.source = local`). In every file the answers computed from the source equal the local ground truth: parity 20/20. |
| Models (3B) | gpt-4.1 only (`example-foundry-account`); gpt-4.1-mini was not run (see Limits) |
| Metrics | **Correct** = the answer equals the ground truth computed in code. **Provenance ok** = the answer does not misstate where a figure came from. **Numbers locked** = every number in the answer appears in tool output or in the question (checked by `verify_pinned_numbers` in `src/pinned_scoring/graph_tools.py`). |

## 3. Test 3A – Foundry agents, per model

| Model | Data | Arm | n | Correct | Provenance ok | Numbers locked | Mean tool calls | API errors | Throttled retries | Est. USD | Est. USD / question |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gpt-4.1 | fabric | Graph tools | 20 | 100% | 100% | 100% | 1.0 | 0 | 0 | 0.13 | 0.0066 |
| gpt-4.1 | fabric | Flat tables | 20 | 0% | 40% | 25% | 11.0 | 2 | 3 | 1.03 | 0.0517 |
| gpt-4.1 | fabric | Bare (no tools) | 20 | 0% | 0% | 25% | 0.0 | 0 | 0 | 0.03 | 0.0014 |
| gpt-5.5 | local | Graph tools | 20 | 100% | 100% | 100% | 1.0 | 0 | 0 | 0.35 | 0.0176 |
| gpt-5.5 | local | Flat tables | 20 | 95% | 90% | 30% | 48.5 | 1 | 0 | 5.30 | 0.2651 |
| gpt-5.5 | local | Bare (no tools) | 20 | 5% | 0% | 15% | 0.0 | 0 | 0 | 0.40 | 0.0198 |
| gpt-6.1-sol | local | Graph tools | 20 | 100% | 100% | 100% | 1.0 | 0 | 0 | 0.34 | 0.0168 |
| gpt-6.1-sol | local | Flat tables | 20 | 100% | 95% | 30% | 43.8 | 0 | 0 | 3.63 | 0.1815 |
| gpt-6.1-sol | local | Bare (no tools) | 20 | 5% | 0% | 80% | 0.0 | 0 | 0 | 0.13 | 0.0065 |
| gpt-6.1-terra | local | Graph tools | 20 | 100% | 95% | 100% | 1.0 | 0 | 0 | 0.13 | 0.0067 |
| gpt-6.1-terra | local | Flat tables | 20 | 100% | 95% | 30% | 47.0 | 0 | 4 | 1.67 | 0.0833 |
| gpt-6.1-terra | local | Bare (no tools) | 20 | 5% | 0% | 100% | 0.0 | 0 | 0 | 0.07 | 0.0035 |
| grok-4.7 | local | Graph tools | 20 | 95% | 95% | 100% | 1.0 | 0 | 0 | 0.23 | 0.0113 |
| grok-4.7 | local | Flat tables | 20 | 75% | 95% | 30% | 45.5 | 0 | 0 | 2.03 | 0.1017 |
| grok-4.7 | local | Bare (no tools) | 20 | 0% | 0% | 20% | 0.0 | 0 | 0 | 0.09 | 0.0043 |

Reading the table: "Graph tools" is the four-tools arm. "Est. USD" is **an estimate** from token counts times assumed list prices, not a bill (see the price table below).

### Cost and token use per run (all three arms together)

| Model | Calls | Input tokens | Output tokens | Assumed price in/out per 1M tokens (USD)     | Est. total USD | Side check: receipts blocked / 20 | Side check errors |
|---|---|---|---|---|---|---|---|
| gpt-4.1 | 177 | 536,968 | 15,004 | 2.0 / 8.0 | 1.19 | 9 | 0 |
| gpt-5.5 | 150 | 777,260 | 86,546 | 5.0 / 25.0 | 6.05 | 3 | 0 |
| gpt-6.1-sol | 137 | 622,064 | 39,392 | 5.0 / 25.0 | 4.10 | 3 | 0 |
| gpt-6.1-terra | 142 | 656,871 | 55,642 | 2.0 / 10.0 | 1.87 | 0 | 0 |
| grok-4.7 | 133 | 592,064 | 37,864 | 3.0 / 15.0 | 2.34 | 4 | 0 |

**Prices are estimates.** They come from `src/pinned_scoring/controls.py` (`PRICES_USD_PER_M`), which documents them as assumptions. The prices for **gpt-5.5, gpt-6.1-sol, gpt-6.1-terra and grok-4.7 are estimates** that were not read from a billing statement; only gpt-4.1's price is a published list price. Do not use the dollar figures to compare models. (The 100-parcel overnight run in report 1 used different assumed prices for sol, terra and grok: 5/20, 2/8 and 3/15 per 1M tokens.)

### Plain-language reading of 3A

* **With the four tools, the answers are correct and locked.** Graph-tools correctness is 100% for gpt-4.1, gpt-5.5, gpt-6.1-sol and gpt-6.1-terra and 95% for grok-4.7 (19 of 20). The agent makes exactly one tool call per question (mean 1.0), and "numbers locked" is 100% in every run, because the numbers come out of the tool.
* **Without them, the picture depends on the model.** On gpt-4.1 the flat arm got 0 of 20 right and used 11 tool calls per question on average (2 agent failures and 3 throttled retries are recorded). On the other four models the flat arm is much better: gpt-5.5 95%, gpt-6.1-sol 100%, gpt-6.1-terra 100%, grok-4.7 75% – but only by making 43.8 to 48.5 tool calls per question (about 44 to 49 times more work than the 1.0 of the graph arm) and by computing the numbers itself: "numbers locked" is only 25–30% on the flat arm, which is by design (a model-computed number is not locked).
* **Bare models do not know the answers.** Correctness is 0–5% on every model.
* **Cost per question** (estimated) is lower for graph tools than flat tables for every model (for example gpt-5.5: $0.018 vs $0.265), mainly because the flat arm needs many more tool rounds.
* The data read from Fabric (gpt-4.1) and read locally gave the same ground truth for all 20 questions (parity 20/20 in all five files).

### The bare arm's "numbers locked" values are a measurement artefact – do not read them as a result

On the bare arm, "numbers locked" shows 80% (gpt-6.1-sol) and 100% (gpt-6.1-terra), but 15–25% for the other models. This is **not** evidence that those models are trustworthy. The check flags a number only if it appears in the answer but not in the tool output or the question. A bare model has no tools, so the tool output is empty; and most bare answers are refusals with empty fields ("Scoring tools are unavailable …") which contain no numbers at all. A check with nothing to flag passes by default. The 15–25% values on gpt-4.1, gpt-5.5 and grok-4.7 most likely come from answers whose numbers happen to appear in the question. The same bare arms have 0–5% correct and 0% provenance ok, which is the real finding. (This explanation comes from reading the check code and the arm outputs; it was not tested by a separate re-run.)

The flat arm's 25–30% is different and real: the model computes its own numbers, and those are flagged unlocked on purpose.

### Side check: can the verifier accept a receipt-based answer? (not a decision test)

Each run also passed 20 score receipts through the verifier as a side check (the last two columns of the cost table): blocked 9 of 20 for gpt-4.1, 3 for gpt-5.5, 3 for gpt-6.1-sol, 0 for gpt-6.1-terra, 4 for grok-4.7, with 0 errors in each. The blocks are mainly wording that contradicts the receipt (for example "claims weather_danger is measured; receipt says generated"). It is a check of the verifier, not of the four tools, and is covered in report 1.

## 4. Test 3B – Direct-model run (gpt-4.1, 1 repeat, 20 questions, 0 API errors)

Percent with 95% bootstrap confidence interval, resampling unit = question.

| Arm | Correct | Provenance ok | Numbers locked | Mean tool calls | Unreadable answers |
|---|---|---|---|---|---|
| Graph tools | 100% [100, 100] | 100% [100, 100] | 100% [100, 100] | 1.0 | 0% |
| Flat tables | 5% [0, 15] | 75% [55, 95] | 35% [15, 55] | 22.5 [15.15, 31.4] | 25% [5, 45] |

Paired difference (graph minus flat), percentage points: correct **+95 [+85, +100]**, provenance **+25 [+5, +45]**, numbers locked **+65 [+45, +85]**.

By scenario (n = 5 each; graph is 100% on every metric in every scenario):

| Scenario | Flat correct | Flat provenance ok | Flat numbers locked | Flat unreadable | Flat mean tool calls |
|---|---|---|---|---|---|
| Exposure | 0% | 100% | 0% | 0% | 4.4 |
| Mitigation | 20% | 100% | 40% | 0% | 36.8 |
| De-energise first | 0% | 0% | 0% | 100% | 21.6 |
| Sensitivity | 0% | 100% | 100% | 0% | 27.2 |

Reading: the direct run agrees with the Foundry run for gpt-4.1: the graph arm is fully correct and the flat arm is nearly always wrong. All five flat de-energise questions hit the 12-round tool limit without producing a parseable answer, which is why that row shows 0% provenance. This is the same finding as 3A for gpt-4.1 (flat 0% there vs 5% here – one question's difference at n = 20; the two runs used different harnesses, so they are separate samples, not repeats).

**Is 3B the same test as 3A?** No. It uses a different harness (direct model calls, not a Foundry agent) and an earlier run, with a different tool-round limit. It is kept as a sub-test. There is no newer equivalent of the direct-model run, so its raw file is kept (`scenarios.json`).

## 5. What the tests do and do not show

**What they show (on this synthetic data and these 20 questions):**

* Putting the join and the arithmetic in strict read-only tools makes the answer correct, locked and cheap for all five models tested (95–100% correct; one call per question).
* Without those tools the result is model-dependent: gpt-4.1 fails; the newer models mostly get the right answer but do about 44–49 times more tool calls and cannot keep their numbers locked.

**What they do not show (limits):**

* **Ceiling effect.** Graph-tools arms are at or near 100% for every model, so they cannot separate the models. The comparison tells us about the arms, not about which model is better. No model is ranked here.
* **Small samples.** 20 questions, 1 repeat, one data seed. 3A has no confidence intervals; one question is 5 percentage points. Differences such as grok-4.7 95% vs 100% are one question.
* **Synthetic data.** The graph, customer counts and costs are invented. Real data may behave differently.
* **The arms are not equal in kind.** The graph arm is correct partly by construction: the tools do the calculation. The result shows the value of putting the calculation in code, not that a model reasons better.
* **The flat arm had no SQL tool and no code interpreter.** A flat agent with a code interpreter was not tested and might do much better.
* **Estimated prices.** All dollar figures are estimates; gpt-5.5, gpt-6.1-sol, gpt-6.1-terra and grok-4.7 prices were assumed, not billed.
* **Throttling.** gpt-4.1 flat had 3 throttled retries and gpt-6.1-terra flat had 4; the effect on the result is not isolated.
* **Not every model is on Fabric.** Only gpt-4.1 was run against the data read from Fabric; the other four ran on the local copy of the same data (parity 20/20).
* **gpt-4.1-mini was not run** for the decision scenarios: two earlier multi-model attempts were abandoned after 30+ minutes of throttling back-off. There is no result for it.
* **The decision graph was not built as an ontology in Fabric.** Only the data (parcels 50, assets 99, feeders 12, customer groups 34, mitigations 3) were read back from Fabric.
* **A dry-run stub exists in the code and is not a result.**

## 6. Raw evidence (all paths relative to the repository root)

| What | File |
|---|---|
| gpt-4.1 Foundry run (Fabric data) | `docs/evidence/scenario-3-four-tools/foundry_arms.json` |
| Byte-identical copy of the above (SHA-256 `97FCEF90…`), kept so older links do not break | `docs/evidence/scenario-3-four-tools/foundry_arms_platform_graph.json` |
| gpt-5.5 | `docs/evidence/scenario-3-four-tools/foundry_arms_gpt-5.5.json` |
| gpt-6.1-sol | `docs/evidence/scenario-3-four-tools/foundry_arms_gpt-6.1-sol.json` |
| gpt-6.1-terra | `docs/evidence/scenario-3-four-tools/foundry_arms_gpt-6.1-terra.json` |
| grok-4.7 | `docs/evidence/scenario-3-four-tools/foundry_arms_grok-4.7.json` |
| Direct-model run (3B) | `docs/evidence/scenario-3-four-tools/scenarios.json` |
| Charts | `docs/evidence/scenario-3-four-tools/charts/scenarios_graph_vs_flat.png`, `docs/evidence/scenario-3-four-tools/charts/scenarios_foundry_agent.png` |
| Data readback (Fabric = local) | `docs/evidence/scenario-2-ontology/graph_readback.json` |
| Code | `src/pinned_scoring/graph_tools.py` (tools and the locked-number check), `src/pinned_scoring/controls.py` (assumed prices), `scripts/run_scenarios.py`, `scripts/run_e2e_foundry.py` |

The old `scenarios-summary.md` was deleted: every figure in it (the 3B table and the paired differences) is reproduced above and in `scenarios.json`. `scenarios.json` is kept because there is no newer equivalent of the direct-model run. The old `docs/decision-scenarios.md` is now a short pointer to this report.

## 7. Data issues found while writing this report

1. **Duplicate file.** `foundry_arms.json` and `foundry_arms_platform_graph.json` are byte-identical.
2. **Source label.** gpt-5.5's file says `source: local`; only gpt-4.1's says `source: fabric`. Earlier descriptions that placed all Foundry runs on Fabric are not supported by the files.
3. **"Throttled" vs "agent failure".** The gpt-4.1 flat arm has 2 `api_errors` in the raw JSON; they are agent failures or tool-round-limit cases, not throttling. Throttling is the separate `throttled_retries` column (3).
4. **Flat tool calls.** The task brief quoted "about 44–47"; the files give 43.8–48.5 for the four newer models (gpt-4.1: 11.0).
5. **Bare-arm "numbers locked"** is a measurement artefact (explained above), not a result.
6. **Two flat results for gpt-4.1** (0% in 3A, 5% in 3B) come from two different harnesses and are not repeats.
7. **Price assumptions differ between runs** for sol, terra and grok (5/25, 2/10, 3/15 here; 5/20, 2/8, 3/15 in the 100-parcel overnight run).
8. **`agent_versions`** are recorded for the four newer models only (gpt-4.1's file has none).
