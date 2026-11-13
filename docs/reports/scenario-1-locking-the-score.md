# Scenario 1 – Locking the score

**Reproduction:** [inputs, models, repeat counts, outputs and safe commands](../experiment.md#experiment-1-locking-the-score).
The headline score evidence pools 150 parcels; the 50-parcel stages below retain
their original methods and denominators rather than being relabelled.

**Question this report answers:** if the risk score of a parcel is computed and locked by code, and the AI model is only allowed to explain it, can the model still change the score, invent where a figure came from, or miss a data fault? And how does that compare with letting the model do the scoring?

Written for a non-technical reader first; every figure comes from a raw file listed in §10. All data are **synthetic**. "Locked" means the score is produced by code from a receipt (a record of inputs, score, band and where each input came from); the model can read the receipt but cannot change it.

## 1. The four arms (what each one means)

| Arm | What the model gets | What is returned to the user |
|---|---|---|
| **A** | Raw evidence and the scoring rubric, no tools. The model scores the parcel itself. | The model's answer |
| **A+** | Arm A plus the receipt (the locked result) in the prompt. | The model's answer |
| **B** | Read-only tools (`get_receipt`, `list_receipts`) and a **verifier** that checks the model's explanation against the receipt. If the answer is blocked, unreadable or errors, the locked receipt is returned instead. | Verified answer, or the receipt |
| **B(raw)** | The same model answer as B, **before** verification. It shows what the model alone would have said. | The unverified answer |

**Important reading rule:** in Arm B the score error is zero *by construction* (the receipt is returned whenever the model fails). B's value is that it never ships a wrong score; the "Blocked by verifier" rate is how often the model's wording had to be replaced. It is not an accuracy figure.

## 2. Metrics (plain language)

| Metric | Meaning | Lower/higher is better |
|---|---|---|
| Score error | Mean absolute difference between the delivered score and the locked score, in points (n = 20 clean parcels) | lower |
| Wrong band | Share of parcels given the wrong risk band (n = 50) | lower |
| Provenance misstated | Share of answers that misstate where an input came from (measured vs generated, etc.) | lower |
| Fault detected | Share of faulted parcels that the answer flags as faulty (n = 30 faulted parcels) | higher |
| False alarm | Share of clean parcels wrongly flagged as faulty (n = 20) | lower |
| Same answer on repeat | Whether the 3 repeats agree | higher |
| Blocked by verifier | Arm B only: share of runs the verifier rejected | context only |
| Unreadable answer | The model's reply could not be parsed | lower |

Confidence intervals (CI) are 95% percentile bootstraps over parcels (2,000 resamples). `n/a` = not applicable (for example score error on a faulted parcel, which has no score).

## 3. Test 1A – The 50-parcel experiment (the main test)

**Setup.** 50 synthetic parcels (25 clean, 25 faulted). Fault types: unit mismatch, missing input, expired input, invalid quality, fixture provenance, policy tamper (five parcels: P011, P023, P031, P036, P041). Models: gpt-4.1 and gpt-4.1-mini. 3 repeats, temperature 0, live calls to the Azure OpenAI endpoint (not a dry run). Data are local files (not Fabric). The fault-injection results are the **fault-detection** and **false-alarm** columns below (see also §9).

| Model | Arm | Score error (points) | Wrong band | Provenance misstated | Fault detected | False alarm | Same answer on repeat | Blocked by verifier | Unreadable answer |
|---|---|---|---|---|---|---|---|---|---|
| gpt-4.1 | A | 49.165 [33.982, 63.535] | 0.253 [0.153, 0.360] | 0.213 [0.147, 0.280] | 0.778 [0.700, 0.856] | 0.450 [0.283, 0.600] | 0.340 [0.220, 0.480] | 0.000 [0.000, 0.000] | 0.213 [0.147, 0.280] |
| gpt-4.1 | A+ | 11.667 [5.000, 18.333] | 0.047 [0.020, 0.080] | 0.140 [0.093, 0.187] | 0.856 [0.789, 0.922] | 0.117 [0.050, 0.183] | 0.620 [0.500, 0.760] | 0.000 [0.000, 0.000] | 0.133 [0.087, 0.180] |
| gpt-4.1 | B | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.989 [0.967, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.907 [0.840, 0.960] | 0.000 [0.000, 0.000] |
| gpt-4.1 | B(raw) | 57.738 [51.773, 63.897] | 0.567 [0.473, 0.660] | 0.260 [0.200, 0.327] | 0.144 [0.044, 0.256] | 0.283 [0.167, 0.400] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.260 [0.200, 0.327] |
| gpt-4.1-mini | A | 25.843 [17.413, 35.785] | 0.233 [0.127, 0.347] | 0.253 [0.173, 0.327] | 0.644 [0.522, 0.767] | 0.183 [0.083, 0.300] | 0.360 [0.240, 0.500] | 0.000 [0.000, 0.000] | 0.253 [0.173, 0.327] |
| gpt-4.1-mini | A+ | 18.333 [6.667, 31.667] | 0.073 [0.027, 0.133] | 0.173 [0.100, 0.253] | 0.833 [0.733, 0.922] | 0.183 [0.067, 0.317] | 0.680 [0.540, 0.800] | 0.000 [0.000, 0.000] | 0.173 [0.100, 0.253] |
| gpt-4.1-mini | B | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.873 [0.800, 0.933] | 0.000 [0.000, 0.000] |
| gpt-4.1-mini | B(raw) | 55.000 [43.333, 68.333] | 0.220 [0.140, 0.320] | 0.460 [0.380, 0.547] | 0.600 [0.500, 0.711] | 0.550 [0.433, 0.683] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.460 [0.380, 0.547] |

**Reading it.**

* **A (no help):** gpt-4.1 is off by about 49 points on average and misstates provenance in 21% of answers. gpt-4.1-mini is off by about 26 points. Letting a model score by itself is not reliable here.
* **A+ (given the receipt):** score error drops a lot (11.7 and 18.3 points) but is not zero: the model can still alter numbers when it writes the answer.
* **B (locked + verifier):** score error 0, wrong band 0, provenance misstated 0, fault detection 0.989 (gpt-4.1) and 1.000 (mini), false alarms 0. This is by construction of the fallback, so the real information is the block rate: 0.907 (gpt-4.1) and 0.873 (mini), meaning most model answers were replaced by the receipt in this first version of the verifier. (See §5 for why many blocks were not the model's fault.)
* **B(raw):** what the model would have said unverified: 57.7 and 55.0 points of error; gpt-4.1 detects only 14% of faults; false alarms 0.283 and 0.550. This is the "what would have gone wrong" number.

**What this does not prove:** with two models, one data seed and synthetic parcels, it shows the *mechanism* works, not that the same rates would hold on real data.

## 4. Test 1B – Weight sensitivity (what if a scoring weight moves ±20%?)

**Purpose.** A locked score is only useful if the code is stable when the rules are slightly changed. Five factor weights were each moved up and down by 20% (10 variants), and the clean parcels were re-scored by code.

| Variant | Mean absolute score shift (points) | Share of parcels that change band |
|---|---|---|
| fuel_load up | 1.050 | 0.150 |
| fuel_load down | 1.165 | 0.050 |
| dead_fuel up | 0.625 | 0.000 |
| dead_fuel down | 0.700 | 0.100 |
| moisture up | 1.260 | 0.100 |
| moisture down | 1.375 | 0.200 |
| weather_danger up | 0.635 | 0.000 |
| weather_danger down | 0.690 | 0.100 |
| wind up | 0.580 | 0.050 |
| wind down | 0.590 | 0.050 |

Reading: a ±20% change in one weight moves the score by about 0.6–1.4 points, and flips the band for 0–20% of parcels. The code result is deterministic and independent of the model.

**The same 10 variants with the models** (parcels P001–P010, 1 repeat, pooled over the 10 variants):

| Model | Arm | Score error | Wrong band | Provenance misstated | Fault detected | False alarm | Blocked by verifier |
|---|---|---|---|---|---|---|---|
| gpt-4.1 | A | 15.152 [9.860, 21.450] | 0.230 [0.150, 0.310] | 0.000 | 1.000 | 0.067 [0.017, 0.133] | 0 |
| gpt-4.1 | A+ | 0.000 | 0.000 | 0.000 | 1.000 | 0.000 | 0 |
| gpt-4.1 | B | 0.000 | 0.000 | 0.000 | 1.000 | 0.000 | 0.810 [0.730, 0.880] |
| gpt-4.1 | B(raw) | 22.687 [16.272, 29.333] | 0.360 [0.270, 0.450] | 0.000 | 0.375 [0.225, 0.525] | 0.033 [0.000, 0.083] | 0 |
| gpt-4.1-mini | A | 8.870 [7.723, 10.057] | 0.400 [0.310, 0.500] | 0.000 | 0.900 [0.800, 0.975] | 0.000 | 0 |
| gpt-4.1-mini | A+ | 8.333 [1.667, 15.000] | 0.050 [0.010, 0.100] | 0.060 [0.020, 0.110] | 0.975 [0.925, 1.000] | 0.083 [0.017, 0.150] | 0 |
| gpt-4.1-mini | B | 0.000 | 0.000 | 0.000 | 1.000 | 0.000 | 0.840 [0.760, 0.910] |
| gpt-4.1-mini | B(raw) | 18.333 [8.333, 28.333] | 0.110 [0.050, 0.170] | 0.280 [0.200, 0.370] | 0.575 [0.425, 0.725] | 0.183 [0.083, 0.283] | 0 |

Reading: the pattern from 1A repeats under changed weights: unverified answers drift, locked answers do not.

## 5. Test 1C – Why so many answers were blocked, and the verifier fix

In Arm B the first verifier blocked 262 of 300 runs (150 per model). Of those blocks, 108 (41%) were not the model's fault but HTTP 429 "too many requests" infrastructure errors. A block-analysis script re-read every blocked run and classified the cause, then the verifier was improved (it now splits sentences into clauses and widens negation scope). **Safety was not weakened:** the verifier still blocks wrong scores and wrong provenance claims. The unit tests grew from 83 to 97 at that time.

| Model | Stage | Blocked (of 150) | Wrong structured answer | Infrastructure error (429) | Wording of reason | Wording of provenance |
|---|---|---|---|---|---|---|
| gpt-4.1 | before fix | 136 | 93 | 39 | 4 | – |
| gpt-4.1 | after fix | 132 | 93 | 39 | – | – |
| gpt-4.1-mini | before fix | 131 | 1 | 69 | 41 | 20 |
| gpt-4.1-mini | after fix | 78 | – | 69 | 2 | 7 |

Reading: excluding the 429s, real answers blocked fell from 154 to 102. For gpt-4.1 most blocks are real structured-answer errors (93) and did not change. For gpt-4.1-mini the block count fell from 131 to 78; 69 of the remaining are infrastructure errors and 9 are deliberate blocks. Not every block is a true model error; some come from wording the check dislikes. Details of blocked reasons are in the raw files below.

## 6. Test 1D – Model comparison (same 50 parcels, 3 repeats)

**Purpose.** Check whether the findings hold for other models. Setup: same 50 parcels, same seeds, 3 repeats, Entra authentication, temperature 0 where the model allowed it, deployed temporarily in the same Azure AI resource and deleted afterwards. Data are local files.

### 6.1 Summary

| Model | Arms run | Score error (A / A+ / B / B(raw)) | Fault detection (A) | B block rate | Notes |
|---|---|---|---|---|---|
| gpt-6.1-sol | A, A+, B (Responses API) | 0 in all arms | 1.000 | 0.127 [0.060, 0.193] | 0 errors in 450 runs after a rerun (see Data issues) |
| gpt-6.1-terra | A, A+, B | 0 in all arms | 0.989 [0.967, 1.000] | 0.160 [0.100, 0.233] | 0 errors in 450 runs; rejects temperature setting; cost not measured |
| grok-4.7 | A, A+, B | 0 in all arms | 1.000 | 0.420 [0.320, 0.527] | about 70 minutes; temperature 0 accepted; cost not recorded |
| phi-4 (first run) | A, A+ only | see 6.5 | 0.556 [0.389, 0.722] | n/a | Arm B not possible (see below) |
| phi-4 (rerun) | A, A+ only | see 6.5 | 0.578 [0.400, 0.744] | n/a | about $0.10 estimated |
| Claude Opus 4.5 | **Not run** | – | – | – | Blocked: model provider data error, then Marketplace purchase disabled for the sandbox subscription (also quota 40K tokens/min). **No data exists.** |
| Claude Sonnet 4.5 | **Not run** | – | – | – | Same block. **No data exists.** |

(The model directories are named `claude-opus-5-5` and `claude-sonnet-5-5`; they hold notes only.)

### 6.2 gpt-6.1-sol

| Model | Arm | Score error (points) | Wrong band | Provenance misstated | Fault detected | False alarm | Same answer on repeat | Blocked by verifier | Unreadable answer |
|---|---|---|---|---|---|---|---|---|---|
| gpt-6.1-sol | A | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| gpt-6.1-sol | A+ | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| gpt-6.1-sol | B | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.127 [0.060, 0.193] | 0.000 [0.000, 0.000] |
| gpt-6.1-sol | B(raw) | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |

Weight-sensitivity arms (P001–P010 × 10 variants):

| Model | Arm | Score error (points) | Wrong band | Provenance misstated | Fault detected | False alarm | Same answer on repeat | Blocked by verifier | Unreadable answer |
|---|---|---|---|---|---|---|---|---|---|
| gpt-6.1-sol | A | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| gpt-6.1-sol | A+ | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| gpt-6.1-sol | B | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.110 [0.050, 0.180] | 0.000 [0.000, 0.000] |
| gpt-6.1-sol | B(raw) | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |

### 6.3 gpt-6.1-terra

| Model | Arm | Score error (points) | Wrong band | Provenance misstated | Fault detected | False alarm | Same answer on repeat | Blocked by verifier | Unreadable answer |
|---|---|---|---|---|---|---|---|---|---|
| gpt-6.1-terra | A | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.989 [0.967, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| gpt-6.1-terra | A+ | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| gpt-6.1-terra | B | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.160 [0.100, 0.233] | 0.000 [0.000, 0.000] |
| gpt-6.1-terra | B(raw) | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |

Weight-sensitivity arms:

| Model | Arm | Score error (points) | Wrong band | Provenance misstated | Fault detected | False alarm | Same answer on repeat | Blocked by verifier | Unreadable answer |
|---|---|---|---|---|---|---|---|---|---|
| gpt-6.1-terra | A | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| gpt-6.1-terra | A+ | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| gpt-6.1-terra | B | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.290 [0.200, 0.380] | 0.000 [0.000, 0.000] |
| gpt-6.1-terra | B(raw) | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |

### 6.4 grok-4.7 (deployment name `grok-4.7-cmp`)

| Model | Arm | Score error (points) | Wrong band | Provenance misstated | Fault detected | False alarm | Same answer on repeat | Blocked by verifier | Unreadable answer |
|---|---|---|---|---|---|---|---|---|---|
| grok-4.7 | A | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| grok-4.7 | A+ | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| grok-4.7 | B | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.420 [0.320, 0.527] | 0.000 [0.000, 0.000] |
| grok-4.7 | B(raw) | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |

Blocked runs recorded these violations: 44 "claims expired but receipt is scored", 44 "claims invalid but receipt is scored", 8 "claims incomplete but receipt is scored", 6 "claims complete but receipt is incomplete", and 9 provenance mismatches (wind / weather_danger measured vs generated). These look like verifier wording disagreements, not score errors; each run was not adjudicated by hand.

Weight-sensitivity arms:

| Model | Arm | Score error (points) | Wrong band | Provenance misstated | Fault detected | False alarm | Same answer on repeat | Blocked by verifier | Unreadable answer |
|---|---|---|---|---|---|---|---|---|---|
| grok-4.7 | A | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| grok-4.7 | A+ | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| grok-4.7 | B | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.560 [0.460, 0.660] | 0.000 [0.000, 0.000] |
| grok-4.7 | B(raw) | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |

### 6.5 phi-4

Phi-4 could not run Arm B: the serving endpoint answered HTTP 400 "auto tool choice requires --enable-auto-tool-choice and --tool-call-parser", so only Arms A and A+ exist. Rerun estimated cost: 513,077 input and 71,260 output tokens over 500 calls, about $0.0998 at an assumed $0.125 / $0.50 per 1M tokens (web list price, not billing).

First run:

| Model | Arm | Score error (points) | Wrong band | Provenance misstated | Fault detected | False alarm | Same answer on repeat | Blocked by verifier | Unreadable answer |
|---|---|---|---|---|---|---|---|---|---|
| phi-4 | A | 9.630 [6.228, 13.740] | 0.340 [0.220, 0.473] | 0.020 [0.000, 0.060] | 0.556 [0.389, 0.722] | 0.000 [0.000, 0.000] | 0.620 [0.480, 0.740] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| phi-4 | A+ | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.833 [0.700, 0.967] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |

Rerun:

| Model | Arm | Score error (points) | Wrong band | Provenance misstated | Fault detected | False alarm | Same answer on repeat | Blocked by verifier | Unreadable answer |
|---|---|---|---|---|---|---|---|---|---|
| phi-4-rerun | A | 9.610 [6.097, 13.772] | 0.300 [0.180, 0.440] | 0.020 [0.000, 0.060] | 0.578 [0.400, 0.744] | 0.000 [0.000, 0.000] | 0.760 [0.640, 0.880] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| phi-4-rerun | A+ | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.833 [0.700, 0.967] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |

Weight-sensitivity arms (first run):

| Model | Arm | Score error (points) | Wrong band | Provenance misstated | Fault detected | False alarm | Same answer on repeat | Blocked by verifier | Unreadable answer |
|---|---|---|---|---|---|---|---|---|---|
| phi-4 | A | 12.763 [10.995, 14.542] | 0.150 [0.080, 0.220] | 0.000 [0.000, 0.000] | 0.300 [0.175, 0.450] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| phi-4 | A+ | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |

Weight-sensitivity arms (rerun):

| Model | Arm | Score error (points) | Wrong band | Provenance misstated | Fault detected | False alarm | Same answer on repeat | Blocked by verifier | Unreadable answer |
|---|---|---|---|---|---|---|---|---|---|
| phi-4-rerun | A | 12.373 [10.625, 14.147] | 0.150 [0.080, 0.220] | 0.000 [0.000, 0.000] | 0.275 [0.150, 0.425] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| phi-4-rerun | A+ | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |

### 6.6 Plain-language reading

* **gpt-6.1-sol, gpt-6.1-terra and grok-4.7 scored every parcel exactly in all arms on these 50 parcels.** The test is saturated for them: the confidence intervals collapse to a single point and the test cannot separate them from a perfect scorer, and cannot rank them. (On the 100 additional parcels in Test 1E they do show some Arm A error.)
* **phi-4** looks like gpt-4.1-mini: error without the receipt (about 9.6 points), none with the receipt (A+), and weaker fault detection (about 0.56–0.58).
* **The verifier block rate varies a lot by model** (0.127 to 0.907) while B(raw) is often perfect. So blocks are not an accuracy measure; they show how the model phrases claims relative to the verifier's wording rules.
* Two runs of phi-4 at temperature 0 gave different "same answer on repeat" values (0.62 then 0.76), so temperature 0 does not guarantee identical output.

## 7. Test 1E – 100 additional parcels (P051–P150), seven models

**Purpose.** See whether the result holds on unseen parcels and on newer models.

**Setup.** 100 new synthetic parcels in 4 batches of 25, 3 repeats, run through Microsoft Fabric: the gold data for each batch was loaded to Fabric and read back through the SQL endpoint, the graph was refreshed through the Fabric REST job. Models: gpt-4.1, gpt-4.1-mini, phi-4 (A and A+ only), gpt-6.1-sol, gpt-6.1-terra, grok-4.7 (overnight run), and gpt-5.5 (a later run, same code and data). Capacity `wildfiresharedv3` was raised for the run and returned to F2 Active afterwards (`capacity_original.json`, `capacity_final.json`).

**Estimated spend: $18.37 for the overnight run and $9.15 for gpt-5.5, against caps of $200 and $60. These are estimates from token counts times assumed list prices, not billing.** Assumed prices per 1M tokens in/out: gpt-4.1 2/8, gpt-4.1-mini 0.4/1.6, phi-4 0.125/0.5, gpt-6.1-sol 5/20, gpt-6.1-terra 2/8, grok-4.7 3/15 (overnight) and gpt-5.5 5/25.

### 7.1 Pooled results over the four batches (overnight run, six models)

Mean [95% CI] with the raw column names (`reproducibility` is the last column; `parse_failure` is the share of unreadable answers); B block rate is the `verifier_block` column of Arm B.

| model | arm | score_error | band_mismatch | provenance_misstatement | fault_detection | false_alarm | parse_failure | verifier_block | reproducibility |
|---|---|---|---|---|---|---|---|---|---|
| gpt-4.1 | A | 25.304 [15.056, 36.087] | 0.180 [0.113, 0.250] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.215 [0.111, 0.333] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.760 [0.670, 0.840] |
| gpt-4.1 | A+ | 0.000 [0.000, 0.000] | 0.020 [0.000, 0.050] | 0.033 [0.007, 0.067] | 0.958 [0.897, 1.000] | 0.000 [0.000, 0.000] | 0.003 [0.000, 0.010] | 0.000 [0.000, 0.000] | 0.990 [0.970, 1.000] |
| gpt-4.1 | B | 0.000 [0.000, 0.000] | 0.020 [0.000, 0.050] | 0.003 [0.000, 0.010] | 0.897 [0.836, 0.952] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.773 [0.713, 0.827] | 1.000 [1.000, 1.000] |
| gpt-4.1 | B(raw) | 44.591 [38.786, 50.752] | 0.607 [0.537, 0.677] | 0.020 [0.007, 0.037] | 0.255 [0.176, 0.345] | 0.163 [0.096, 0.237] | 0.017 [0.003, 0.033] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] |
| gpt-4.1-mini | A | 12.828 [8.839, 18.113] | 0.227 [0.153, 0.310] | 0.017 [0.000, 0.043] | 0.861 [0.770, 0.939] | 0.044 [0.007, 0.096] | 0.017 [0.000, 0.043] | 0.000 [0.000, 0.000] | 0.650 [0.560, 0.740] |
| gpt-4.1-mini | A+ | 0.000 [0.000, 0.000] | 0.020 [0.000, 0.050] | 0.003 [0.000, 0.010] | 0.933 [0.867, 0.988] | 0.000 [0.000, 0.000] | 0.003 [0.000, 0.010] | 0.000 [0.000, 0.000] | 0.990 [0.970, 1.000] |
| gpt-4.1-mini | B | 0.000 [0.000, 0.000] | 0.020 [0.000, 0.050] | 0.000 [0.000, 0.000] | 0.964 [0.909, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.143 [0.093, 0.197] | 1.000 [1.000, 1.000] |
| gpt-4.1-mini | B(raw) | 4.444 [0.741, 8.148] | 0.040 [0.013, 0.077] | 0.027 [0.010, 0.050] | 0.952 [0.891, 0.994] | 0.044 [0.007, 0.081] | 0.027 [0.010, 0.050] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] |
| phi-4 | A | 18.510 [14.241, 23.872] | 0.290 [0.200, 0.377] | 0.007 [0.000, 0.020] | 0.491 [0.364, 0.636] | 0.037 [0.000, 0.096] | 0.007 [0.000, 0.020] | 0.000 [0.000, 0.000] | 0.740 [0.660, 0.830] |
| phi-4 | A+ | 0.000 [0.000, 0.000] | 0.020 [0.000, 0.050] | 0.000 [0.000, 0.000] | 0.818 [0.709, 0.909] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] |
| gpt-6.1-sol | A | 4.444 [0.000, 10.370] | 0.020 [0.000, 0.047] | 0.050 [0.017, 0.090] | 0.945 [0.885, 0.988] | 0.044 [0.000, 0.104] | 0.050 [0.017, 0.090] | 0.000 [0.000, 0.000] | 0.950 [0.900, 0.990] |
| gpt-6.1-sol | A+ | 0.000 [0.000, 0.000] | 0.020 [0.000, 0.050] | 0.000 [0.000, 0.000] | 0.964 [0.909, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] |
| gpt-6.1-sol | B | 0.000 [0.000, 0.000] | 0.020 [0.000, 0.050] | 0.000 [0.000, 0.000] | 0.964 [0.909, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.127 [0.083, 0.173] | 1.000 [1.000, 1.000] |
| gpt-6.1-sol | B(raw) | 5.185 [1.481, 10.370] | 0.033 [0.010, 0.063] | 0.050 [0.020, 0.090] | 0.933 [0.867, 0.988] | 0.052 [0.015, 0.104] | 0.050 [0.020, 0.090] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] |
| gpt-6.1-terra | A | 2.222 [0.000, 5.185] | 0.010 [0.000, 0.023] | 0.023 [0.003, 0.050] | 0.982 [0.945, 1.000] | 0.022 [0.000, 0.052] | 0.020 [0.003, 0.047] | 0.000 [0.000, 0.000] | 0.970 [0.930, 1.000] |
| gpt-6.1-terra | A+ | 0.000 [0.000, 0.000] | 0.020 [0.000, 0.050] | 0.000 [0.000, 0.000] | 0.964 [0.909, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] |
| gpt-6.1-terra | B | 0.000 [0.000, 0.000] | 0.020 [0.000, 0.050] | 0.000 [0.000, 0.000] | 0.964 [0.909, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.217 [0.163, 0.273] | 1.000 [1.000, 1.000] |
| gpt-6.1-terra | B(raw) | 2.222 [0.000, 5.926] | 0.030 [0.003, 0.063] | 0.013 [0.000, 0.030] | 0.958 [0.903, 1.000] | 0.022 [0.000, 0.059] | 0.013 [0.000, 0.030] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] |
| grok-4.7 | A | 8.889 [2.222, 16.296] | 0.040 [0.010, 0.077] | 0.060 [0.023, 0.103] | 0.964 [0.921, 0.994] | 0.089 [0.022, 0.163] | 0.060 [0.023, 0.103] | 0.000 [0.000, 0.000] | 0.930 [0.880, 0.980] |
| grok-4.7 | A+ | 0.000 [0.000, 0.000] | 0.020 [0.000, 0.050] | 0.000 [0.000, 0.000] | 0.964 [0.909, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] |
| grok-4.7 | B | 0.000 [0.000, 0.000] | 0.020 [0.000, 0.050] | 0.000 [0.000, 0.000] | 0.964 [0.909, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.440 [0.360, 0.517] | 1.000 [1.000, 1.000] |
| grok-4.7 | B(raw) | 0.000 [0.000, 0.000] | 0.020 [0.000, 0.050] | 0.000 [0.000, 0.000] | 0.964 [0.909, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] |

### 7.2 gpt-5.5 (P051–P150, Fabric)

| Arm | Score error | Provenance misstated | Fault detected | Same answer on repeat | B block rate |
|---|---|---|---|---|---|
| A | 0.000 [0.000, 0.000] | 0.0% | 100.0% | 100.0% | – |
| A+ | 0.000 | – | 96.4% (pooled [90.9, 100.0]) | – | – |
| B | 0.000 | – | 96.4% | – | 50.7% (152 blocks) |
| B(raw) | 0.000 | – | – | – | – |

gpt-5.5 had 0 HTTP 429 errors across 4 batches; all 152 blocks were verifier blocks.

### 7.3 Side-by-side (from `extra-gpt55/comparison.md`)

| Model | A score error [95% CI] | A provenance misstated | A fault detected | A same-on-repeat | B block rate |
|---|---|---|---|---|---|
| gpt-5.5 | 0.000 [0.000, 0.000] | 0.0% | 100.0% | 100.0% | 50.7% |
| gpt-4.1 | 25.304 [15.056, 36.087] | 0.0% | 100.0% | 76.0% | 77.3% |
| gpt-4.1-mini | 12.828 [8.839, 18.113] | 1.7% | 86.1% | 65.0% | 14.3% |
| phi-4 (A/A+ only) | 18.510 [14.241, 23.872] | 0.7% | 49.1% | 74.0% | n/a |
| gpt-6.1-sol | 4.444 [0.000, 10.370] | 5.0% | 94.5% | 95.0% | 12.7% |
| gpt-6.1-terra | 2.222 [0.000, 5.185] | 2.3% | 98.2% | 97.0% | 21.7% |
| grok-4.7 | 8.889 [2.222, 16.296] | 6.0% | 96.4% | 93.0% | 44.0% |

### 7.4 Errors per arm (300 runs per arm per model; overnight run)

Verifier blocks equal block rate × 300 (gpt-4.1 232, gpt-4.1-mini 43, sol 38, terra 65, grok 132).

| model | arm | runs | verifier blocks | block rate | errors | of which HTTP 429 |
|---|---|---|---|---|---|---|
| gpt-4.1 | A | 300 | 0 | 0.0% | 0 | 0 |
| gpt-4.1 | A+ | 300 | 0 | 0.0% | 1 | 1 |
| gpt-4.1 | B | 300 | 232 | 77.3% | 5 | 5 |
| gpt-4.1-mini | A | 300 | 0 | 0.0% | 5 | 0 |
| gpt-4.1-mini | A+ | 300 | 0 | 0.0% | 1 | 0 |
| gpt-4.1-mini | B | 300 | 43 | 14.3% | 8 | 8 |
| gpt-6.1-sol | A | 300 | 0 | 0.0% | 15 | 0 |
| gpt-6.1-sol | A+ | 300 | 0 | 0.0% | 0 | 0 |
| gpt-6.1-sol | B | 300 | 38 | 12.7% | 15 | 0 |
| gpt-6.1-terra | A | 300 | 0 | 0.0% | 6 | 0 |
| gpt-6.1-terra | A+ | 300 | 0 | 0.0% | 0 | 0 |
| gpt-6.1-terra | B | 300 | 65 | 21.7% | 4 | 0 |
| grok-4.7 | A | 300 | 0 | 0.0% | 18 | 0 |
| grok-4.7 | A+ | 300 | 0 | 0.0% | 0 | 0 |
| grok-4.7 | B | 300 | 132 | 44.0% | 0 | 0 |
| phi-4 | A | 300 | 0 | 0.0% | 2 | 0 |
| phi-4 | A+ | 300 | 0 | 0.0% | 0 | 0 |

### 7.5 Reading

* **A+ and B score error is 0 for every model**; only Arm A and B(raw) separate models. The locked arms reproduce the result everywhere, which is the point of the scenario.
* Arm A error without help is large for gpt-4.1 (25.3 points) and mini (12.8), small for the newer models (2.2–8.9), and 0 for gpt-5.5.
* Fault detection under A+/B is 96.4% for several models (0.846 in batch 2 for gpt-5.5), which points to a property of that batch's fault labels rather than of the models; it was not investigated.
* The pooled numbers differ from the 50-parcel test (for example gpt-4.1 A error 49.2 vs 25.3) because the parcel sets differ.

**Limits.** Ceiling effect (many cells are 0, with degenerate CIs). gpt-5.5 was run on a later date. The 429 pressure on gpt-4.1 and mini came from their own endpoint capacity. One deployment per model, one data seed, synthetic data. No claim is made that any model is better than another. No 200-parcel data exists: the branch `enekazo-extra-200-parcels-overnight` contains only the same 100-parcel (P051–P150) evidence.

## 8. Verifier and fault detection

* **Verifier** (`src/pinned_scoring/verifier.py`): compares each statement in the model's answer with the receipt (score, band, status, reasons, provenance) and blocks mismatches. Results are in §3 (Blocked by verifier), §5 (causes) and §6/§7 (other models).
* **Fault detection** is the share of faulted parcels the answer flags as faulty. In 1A it is 0.778 (gpt-4.1 A), 0.856 (A+), 0.989 (B) and only 0.144 for B(raw); for mini 0.644, 0.833, 1.000 and 0.600.
* **A caution on B:** B's fault detection comes from the receipt, which is generated by code, so a high value shows the locked path flags faults, not that the model does.

## 9. Synthetic-faults results

There is no separate synthetic-faults results file. Fault injection is part of the 50-parcel experiment itself: 25 of the 50 parcels carry an injected fault (six types), and the **fault detected** and **false alarm** columns above are the results. The generator is tested by `tests/test_synth.py`.

## 10. Limits and what was NOT proved

* **Ceiling and saturation.** gpt-6.1-sol, terra and grok score 0 error on the 50 parcels; confidence intervals are degenerate.
* **Sample sizes.** 50 parcels (n = 20 for score error, 30 for fault detection, 3 repeats); 100 more parcels; sensitivity uses only 10 parcels and 1 repeat.
* **Arm B is zero-error by construction.** It is not independent evidence of model accuracy.
* **Synthetic data**, one data seed; real data may behave differently.
* **Prices are estimates**, not billing; see §7.
* **Claude models were never run.**
* **phi-4** has no Arm B result.
* **Verifier wording.** High block rates partly reflect wording rules, not model errors; blocked runs were not each reviewed by hand.
* **Temperature 0 is not fully deterministic** (phi-4 reproducibility 0.62 vs 0.76).
* No model is declared better than another.

## 11. Raw evidence (paths relative to the repository root)

| What | Path |
|---|---|
| 50-parcel metrics, runs, sensitivity | `docs/evidence/scenario-1-locking-the-score/metrics.json`, `runs.json`, `sensitivity.json` |
| Harness summary of the same run | `docs/evidence/scenario-1-locking-the-score/experiment-summary.md` |
| Block analysis before / after | `docs/evidence/scenario-1-locking-the-score/block-analysis-before.json`, `block-analysis-after.json` |
| Model comparison (per model: `metrics.json`, `runs.json`, `sensitivity.json`, `SUMMARY.md`, `tables.md`, `NOTES.md` where present) | `docs/evidence/scenario-1-locking-the-score/model-comparison/{gpt-6.1-sol,gpt-6.1-terra,grok-4.7,phi-4,phi-4-rerun,claude-opus-5-5,claude-sonnet-5-5}/` |
| 100 extra parcels, overnight run | `docs/evidence/scenario-1-locking-the-score/extra-100/` (`summary.md`, `batch_0N_stats.json`, `cost.json`, `capacity_*.json`) |
| gpt-5.5 on the 100 parcels | `docs/evidence/scenario-1-locking-the-score/extra-gpt55/` (`summary.md`, `comparison.md`, `cost.json`, `batch_0N_stats.json`), `results/extra_gpt55/` |
| Per-parcel receipts | `docs/evidence/receipts/` |
| Charts | `docs/evidence/scenario-1-locking-the-score/charts/` |
| Code | `src/pinned_scoring/verifier.py`, `scripts/analyze_blocks.py`, `scripts/run_extra_batches.py`, `tests/test_synth.py` |

## 12. Data issues found while writing this report

1. The old model-comparison write-up listed phi-4 Arm A fault detection as "n/a / see evidence"; the files give 0.556 (first run) and 0.578 (rerun).
2. The old write-up said terra and grok cost was "not measured"; the overnight `cost.json` has only a combined estimate, with no per-model breakdown for the 50-parcel run. No per-model cost for the 50-parcel comparison exists.
3. The branch name "200 parcels" does not match its content (100 extra parcels, P051–P150).
4. The terra notes say a grok-4.7 deployment produced no data, but grok data exists from a separate run.
5. gpt-6.1-sol, terra and grok have 0 Arm A error on the 50 parcels but non-zero on P051–P150 (4.4, 2.2, 8.9): different parcel sets.
6. phi-4 "same answer on repeat" changed from 0.62 to 0.76 at temperature 0.
7. The overnight run used assumed prices for sol (5/20), terra (2/8), grok (3/15) that differ from `src/pinned_scoring/controls.py` (5/25, 2/10, 3/15) used in Scenario 3.
8. The first gpt-6.1-sol run was invalid (127 of 450 calls hit HTTP 429), was deleted and rerun; only the rerun is in the evidence.
9. Models are labelled `grok-4.7-cmp` and `phi-4-cmp` (deployment names) in the data files.
10. The Claude directories exist but contain notes only.
