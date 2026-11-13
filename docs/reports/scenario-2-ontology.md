# Scenario 2 – Ontology (connection questions)

**Reproduction:** [relationship queries, comparison arms and usefulness](../experiment.md#experiment-2-fabric-iq-relationship-questions-and-usefulness).
The latest live relationship checks cover **150 parcels and 450 CrewAccess records**
on 11 November 2026. Direct GQL and the Microsoft Foundry ontology-connected agent
each matched all five C1–C5 answers against the deployment's receipt-scoped ground truth.

**Question this report answers:** wildfire questions are often about *connections* ("which parcels have homes but no passable access road?", "which high-risk parcels have no water source on a passable road?"). Does an ontology – a graph of parcels, roads, water sources, homes and crew access built in Microsoft Fabric – let an AI agent answer such questions correctly, and does it make the answers more useful to a reader?

All data are **synthetic**. The latest acceptance evidence below is separate from
the comparison/usefulness studies retained later in this report.

## Latest live relationship verification

| Item | Recorded result |
|---|---|
| Dataset | 150 parcels, 154 road segments, 129 home groups, 75 water sources and 450 CrewAccess records |
| Direct graph queries | C1–C5: 5/5 matched; current dataset matched |
| Agent | Microsoft Foundry `pinned-score-agent-fabric`, version 3 |
| Model field | Deployment alias `scoring-agent-model2`; the alias is not an independently verified underlying model/version |
| Remote tool | Error-free `ask_ontology` MCP call recorded for each question |
| Ground truth | Computed from the current deployment dataset; exact parcel/crew sets and the C4 number checked |

| Question | Current expected answer | GQL | Foundry agent |
|---|---|---|---|
| C1: homes but no passable access road | 40 parcels; exact IDs in the evidence | Matched | Matched |
| C2: high-risk parcels without water on a passable access road | 25 parcels; exact IDs in the evidence | Matched | Matched |
| C3: crew types that cannot reach P003 | `dozer`, `engine` | Matched | Matched |
| C4: homes on P057 | 59 | Matched | Matched |
| C5: critical facility without engine-accessible road | 15 parcels | Matched | Matched |

C5 IDs: `P020, P031, P052, P056, P060, P070, P080, P087, P090, P091, P102, P113, P121, P139, P149`.
Engine reachability uses `CrewAccess`: a passable but narrow road is insufficient.

| Current evidence | Contents |
|---|---|
| [Deployment evidence index](../evidence/cloud-acceptance/2026-11-11/summary.json) | Dataset/policy hashes, source revisions, scope and installer-acceptance boundary |
| [SQL parity](../evidence/cloud-acceptance/2026-11-11/fabric_hash_check.json) | 150/150 matching receipt hashes |
| [Graph queries](../evidence/cloud-acceptance/2026-11-11/fabric_graph_chain_gql.json) | Counts, exact answers and computed ground truth |
| [Foundry relationship checks](../evidence/cloud-acceptance/2026-11-11/fabric-agent-smoke.json) | Five answers, remote tool calls, expected/actual sets and all-matched summary |

The exports omit private target context and retain original artifact SHA-256 values.
Provider moderation annotations are preserved: a correct answer is not proof that
moderation completed successfully. These five functional checks do not rerun or
supersede the separate 20-question usefulness comparison.

<details>
<summary><strong>Separate comparison and usefulness studies (not the current 150-parcel acceptance run)</strong></summary>

## 1. Setup at a glance

| Item | Value |
|---|---|
| Data | Synthetic (seed 20260715), stored in the Fabric lakehouse `pinned_scoring_lh`. The platform had no road, water or access data, so these tables are new synthetic ones. |
| Tables / ontology entities | Parcel 50, RoadSegment 52, HomeGroup 40, WaterSource 25, CrewAccess 150 |
| Ontology | `pinned_scoring_ontology`: 5 entities, 4 relationships (`parcel_has_road`, `road_has_water`, `parcel_has_homes`, `parcel_has_crew_access`). Applied (HTTP 200) and read back with 17 parts. |
| Questions | Five connection questions, C1–C5, with ground truth computed in code (below) |
| Models | gpt-4.1 in all runs |
| Where it runs | Tables and graph are in **Fabric**; agents run in a Microsoft Foundry project (`example-project`) with Entra (no keys) authentication |
| Hash check | The gold receipts in Fabric match the local ones: 50/50 (`fabric_hash_check.json`). Answers computed from Fabric equal the local ones for 20/20 questions in the Scenario 3 runs. The Fabric readback of the chain tables equals the local copy. |

The five questions and their correct answers (computed in code; the basis of each input is "assumed" except C4, which is "measured"):

| # | Question | Correct answer |
|---|---|---|
| C1 | Which parcels have homes but no passable access road? | 14 parcels: P001, P004, P016, P020, P024, P028, P031, P032, P033, P036, P037, P038, P040, P050 |
| C2 | Which high-risk parcels (band high or extreme) have no water source on a passable access road? | 11 parcels: P008, P010, P012, P014, P015, P016, P023, P030, P035, P036, P042 |
| C3 | Which crew types cannot reach parcel P003? | dozer, engine |
| C4 | How many homes are on parcel P018? | 42 (basis measured) |
| C5 | Which parcels have a critical facility but no passable access road that an engine can use? | P020, P031 |

`CrewAccess` has 150 rows because it is rule-derived (always basis "assumed") with a composite key of parcel and crew type.

## 2. Test 2A – Ontology build in Fabric

| Check | Result |
|---|---|
| Entities loaded (final row counts) | Parcel 50, RoadSegment 52, HomeGroup 40, WaterSource 25, CrewAccess 150 |
| Ontology definition applied | HTTP 200; read back with 17 parts |
| Key and edge-direction fixes needed | Two fixes recorded (`graph_model_key_fix.json`, `graph_model_edge_fix.json`); edges in the graph run from child to parent |
| Gold receipts in Fabric = local | 50/50 hashes match |

Reading: the ontology exists in Fabric, is populated with the expected numbers of rows and can be read back.

## 3. Test 2B – Graph query (GQL) check: 5/5

The Fabric graph was queried directly with GQL for C1–C5. Each result matched the ground truth (5 of 5). GQL returns the traversed rows; the set logic (for example "has homes but no road") is done client-side. Edge labels failed in GQL, so unlabelled directed patterns are used. First query 52.37 s (cold start); later queries 1.07–1.44 s.

Reading: the connections can be traversed correctly in the Fabric graph. This is a test of the graph, not of an AI model.

## 4. Test 2C – An agent using the ontology through MCP (`ask_ontology`): 9/9

A Foundry agent (`pinned-score-agent-fabric`) was given a connection-based MCP tool (`pinnedscore_ontology`, connection `pinnedscore-fabric-iq-ontology-v2`) that exposes an `ask_ontology` function over the Fabric ontology.

| Result | Value |
|---|---|
| Correct answers | **9 of 9** |
| Errors | 0 |
| Throttled calls | 0 |
| Questions covered | C1–C5; C3 was run 5 times, all returning ["dozer", "engine"] |

Caveat: the very first C3 attempt was phrased "which crew types **can** reach P003", while the stored truth is for "**cannot** reach"; it was not counted. The 5 C3 results use the "cannot reach" phrasing.

Reading: through the Fabric ontology tool an agent answered every connection question correctly in this small test. It does not show that the ontology improves reasoning – the tool does the traversal; see §5.

## 5. Test 2D – The Python chain arms (graph tools vs flat tables vs bare)

**Setup.** gpt-4.1 in a Foundry project, C1–C5 × 3 repeats = 15 runs per arm, Entra authentication. 84 calls, 89,615 input tokens, 3,880 output tokens; cost **$0.2103 (a list-price estimate, not billing)**.

| Arm | What the agent has |
|---|---|
| Graph tools | Strict read-only tools that do the join and filter in code |
| Flat tables | The same tables, lookup only; the model must join them itself |
| Bare | No tools |

| Arm | Correct | Provenance ok | Numbers locked | Mean tool calls | API errors | Agent failures | Est. USD / question |
|---|---|---|---|---|---|---|---|
| Graph tools | 100% (15/15) | 80% | 20% | 1.0 | 0 | 0 | 0.00377 |
| Flat tables | 20% (3/15) | 26.7% | 73.3% | 2.4 | 0 | 0 | 0.00957 |
| Bare | 0% | 20% | 0% | 0 | 0 | 0 | 0.00069 |

By question: the graph arm is right on all of C1–C5; the flat arm is right only on C3; bare is wrong on all.

Reading: with tools that do the join, the agent is correct on every connection question; with plain tables it is right on one in five; with nothing it is wrong every time. **"Numbers locked" is not meaningful in this test**: the answers are lists of IDs, not figures from a receipt, so the check has little to measure (the odd pattern – flat 73%, graph 20% – comes from that, and is not a finding). The graph arm wins partly by construction: the tools do the work. In these Python arms the Fabric ontology was not in the answer path; they show the value of putting the connection logic in tools, not that the ontology itself improves answers.

## 6. Test 2E – Usefulness test (blind judge)

**Purpose.** Correct is not the same as useful. Does the agent's verified answer read better to a person than a plain template answer built from the same receipt?

**Setup.** 20 questions over 14 parcels in 6 categories (explain, why incomplete, compare, what-if, next action, plain summary). Arm T = deterministic template answer; Arm D = the agent's answer after verification (it falls back to the template if blocked). The judge is gpt-4.1-mini, blind to which arm is which, order randomised, scoring 1–5 on three points (answers the question, plain language, actionable). 3 repeats = 60 judgements per run. Faithfulness (no changed numbers) is checked by code. **The judge is from the same model family as the agent**, so it may favour similar wording.

| Run | Agent | Win | Tie | Loss | Mean D − T (95% CI over questions) | Passed verifier / fell back | Faithfulness violations |
|---|---|---|---|---|---|---|---|
| Run 1 | gpt-4.1 working from receipts | 32 (53% [41, 65]) | 3 (5% [2, 14]) | 25 (42% [30, 54]) | +0.23 [−1.80, +2.25] | 13 / 7 (7 blocks, 0 infra errors) | 0 |
| Run 2 | Foundry agent with the ontology MCP tool | 35 (58% [46, 70]) | 2 (3% [1, 11]) | 23 (38% [27, 51]) | +0.57 [−1.30, +2.38] | 15 / 5 (3 blocks, 2 infra errors) | 0 |

(Win/tie/loss counts are of 60 judgements; the percentage intervals are Wilson intervals.)

Mean D − T by criterion: Run 1: answers the question +0.10, plain language −0.17, actionable +0.30. Run 2: +0.37, −0.13, +0.33.

By category (judgements; win / tie / loss %; mean D − T):

| Category | n | Run 1 win / tie / loss | Run 1 D − T | Run 2 win / tie / loss | Run 2 D − T |
|---|---|---|---|---|---|
| Explain | 9 | 56 / 0 / 44 | +1.78 | 67 / 0 / 33 | +2.56 |
| Why incomplete | 12 | 100 / 0 / 0 | +4.08 | 92 / 8 / 0 | +3.50 |
| Compare | 9 | 33 / 0 / 67 | −1.78 | 0 / 0 / 100 | −5.00 |
| What-if | 9 | 67 / 33 / 0 | +1.89 | 33 / 11 / 56 | −1.22 |
| Next action | 12 | 25 / 0 / 75 | −4.33 | 75 / 0 / 25 | +1.67 |
| Plain summary | 9 | 33 / 0 / 67 | +0.00 | 67 / 0 / 33 | +0.56 |

Blocked answers in Run 1: Q08 and Q09 were false blocks (a sentence about several parcels), Q14, Q16 and Q20 were mostly false blocks, Q15 was hedged, and Q18 was a real error. The verifier was deliberately not loosened further, to avoid tuning on the test set. The 2 infrastructure errors in Run 2 were Fabric-side MCP errors ("ontologyEndpoint failed with status code 500" and "Remote MCP server error … without a reply to request with ID: 2"); 18 tool calls, 0 throttled retries.

**Reading.** The agent does **not clearly beat** the template. Both overall differences are small and their confidence intervals include zero. The agent helps on "why is this incomplete" in both runs and on "what-if" in Run 1; it loses on "compare" and, in Run 1, on "next action". Because the two runs use different agents and inputs (Run 2 had no receipts), they are not like-for-like and should not be read as an improvement from one to the other.

**Limits.** Same-family judge (gpt-4.1-mini judging gpt-4.1 text); 20 questions on 14 parcels; per-category cells have 9–12 judgements; synthetic data.

## 7. Routes that did NOT work (stated honestly)

| Route | What happened | Evidence |
|---|---|---|
| Fabric data agent (native query) | Returned HTTP 404 `EntityNotFound`. | `data_agent_query_blocked.json`, `data_agent_published_probe.json` |
| Fabric data agent (direct MCP route) | Worked technically but gave a **wrong** answer for C3 ("hand_crew only"). | `data_agent_answer.json`, `data_agent_ontology.json` |
| Foundry agent with the Fabric data agent tool | Failed with 400 `tool_user_error`: "Workspace ID and Artifact ID are required". The tool is documented as deprecated. | `foundry_fabric_tool_answer.json` |
| Built-in `fabric_iq_preview` tool | Failed twice: first "Remote MCP server error while enumerating tools"; on the smaller capacity, MCP 500 (-32603). A stale connection target is possible but unconfirmed. An earlier tool type (OneLake Catalog) was the wrong shape. | `foundry_fabric_iq_answer.json` |

What worked is the connection-based MCP route in §4. The built-in preview route and the data-agent routes were not made to work, and no claim is made about them beyond these failures.

## 8. Trace evidence (Application Insights)

One C5 question was run with tracing on, and the trace was read back with a KQL query about 4 minutes later.

| Item | Value |
|---|---|
| Resources | Resource group `example-resource-group`; Log Analytics `law-pinnedscore`; Application Insights `appi-pinnedscore` |
| Agent / version | `pinned-score-agent-fabric`, version 6 |
| Answer | "P020 and P031" (correct); 1 tool call |
| Trace id | `b7f33acd02922db1baa44eced164be5d` |
| Spans seen | `pinned-score-C5`, `invoke_agent pinned-score-agent-fabric:6`, `responses`, `chat gpt-4.1-2025-04-14`, `execute_tool mcp_pinnedscore_ontology.ask_ontology`, `AIProjectClient.get_openai_client` |

Limits: the KQL output was truncated at 8 rows; the Foundry portal Traces tab was not opened; the tracing deployment script `deploy/enable_tracing.ps1` was only tested with `-WhatIf`. No secrets are stored in the trace file.

## 9. Controls, what was demonstrated, and how to reproduce

* **Controls:** Entra authentication (no keys), read-only tools, a kill switch, and a cost meter with a budget guard. The whole engagement cost about **$1.4 (a list-price estimate)**.
* **Reproduce / install:** `deploy/deploy-all.ps1` deploys and `deploy/uninstall.ps1` removes it; the scripts in `scripts/` named `fabric_*`, `run_e2e_chain.py`, `run_foundry_ontology_eval.py` and `create_foundry_fabric_agent.py` rebuild the data, ontology and agents.
* **Apply to your own data:** replace the synthetic tables with your own road, water, home and crew-access tables, keep the same entity keys, re-apply the ontology definition, and re-run the chain questions with your own ground truth.

## 10. Limits and what was NOT proved

* **Small samples.** 5 questions; the agent run is 9 questions; the Python arms are 15 runs per arm; usefulness is 60 judgements per run.
* **Ceiling effect.** The graph arms and the MCP agent are at 100%; they cannot show degrees of better.
* **Arms are not equal in kind.** The graph arm does the join in code, so its correctness shows the value of tools, not of the ontology.
* **One model.** All agents are gpt-4.1.
* **Same model family judges** the usefulness test.
* **Synthetic data**, one seed.
* **Estimated cost**, not billing.
* **Routes that failed** (§7) were not resolved.
* Usefulness runs are not like-for-like.

## 11. Raw evidence (paths relative to the repository root, folder `docs/evidence/scenario-2-ontology/`)

| What | File |
|---|---|
| Ontology build and definition | `ontology_attempt.json`, `ontology_definition.json`, `ontology_definition_chain.json`, `graph_model_key_fix.json`, `graph_model_edge_fix.json`, `graph_readback.json` |
| Chain tables, ground truth | `chain_tables.json`, `crewaccess_key_rest_check.json` |
| Hash check | `fabric_hash_check.json` |
| GQL chain check and graph counts | `fabric_graph_chain_gql.json`, `fabric_graph_counts.json`, `fabric_graph_query.json` |
| Python chain arms | `chain_arms.json` |
| Agent via MCP (9/9) | `foundry_ontology_chain.json`, `foundry_fabric_iq_mcp_readback.json`, `foundry_fabric_iq_mcp_answer.json` |
| Usefulness | `usefulness.json`, `foundry_ontology_usefulness.json`, `charts/usefulness_win_tie_loss.png` |
| Routes that did not work | `data_agent_query_blocked.json`, `data_agent_published_probe.json`, `data_agent_answer.json`, `data_agent_ontology.json`, `foundry_fabric_tool_answer.json`, `foundry_fabric_iq_answer.json` |
| Trace | `app_insights_trace.json` |
| Data-flow diagram | `data_flow.png`, `data_flow.mmd` |

Prefix every file above with `docs/evidence/scenario-2-ontology/`. The earlier summaries `usefulness-summary.md` and `foundry_ontology_usefulness_summary.md` were deleted; their content is in §6 and in the two JSON files.

## 12. Data issues found while writing this report

1. `fabric_graph_counts.json` is an earlier snapshot that shows CrewAccess with 50 nodes; the final state, 150, is in `fabric_graph_chain_gql.json` and `chain_tables.json`. This report gives the final state.
2. The C3 phrasing difference ("can reach" vs "cannot reach") in the first attempt (see §4).
3. The two usefulness runs are not like-for-like (different agent and inputs).
4. "Numbers locked" has no meaning for ID-list answers (§5).
5. The 2 errors in usefulness Run 2 are Fabric-side MCP errors, not model errors.
6. The cost figures ($0.2103 and about $1.4) are list-price estimates.

</details>
