# gpt-5.5 on P051-P150 (Fabric path) vs the six overnight models

Same design as `docs/evidence/scenario-1-locking-the-score/extra-100/summary.md`: 4 Fabric batches of 25 parcels, arms A / A+ / B (B(raw) derived), 3 repeats, 4 workers, Gold loaded into Fabric per batch and read back through the SQL endpoint (`gold_source: fabric` in every `batch_0N_stats.json`), graph refreshed through the REST job only. Runner: `scripts/run_extra_batches.py` via the thin wrapper `scripts/run_extra_gpt55_fabric.py` (adds only the gpt-5.5 price and the arguments). Full tables: `summary.md`.

| model | A MAE [95% CI] | A prov. misstated | A fault detection | A reproducibility | A+ MAE | B MAE | B(raw) MAE | B block rate | 429 errors (A/A+/B) |
|---|---|---|---|---|---|---|---|---|---|
| **gpt-5.5** | **0.000 [0.000, 0.000]** | **0.0%** | **100.0%** | **100.0%** | 0.000 | 0.000 | 0.000 | 50.7% | 0 / 0 / 0 (0 retries) |
| gpt-4.1 | 25.304 [15.056, 36.087] | 0.0% | 100.0% | 76.0% | 0.000 | 0.000 | 44.591 | 77.3% | 0 / 1 / 5 |
| gpt-4.1-mini | 12.828 [8.839, 18.113] | 1.7% | 86.1% | 65.0% | 0.000 | 0.000 | 4.444 | 14.3% | 0 / 0 / 8 |
| phi-4 (A/A+ only) | 18.510 [14.241, 23.872] | 0.7% | 49.1% | 74.0% | 0.000 | n/a | n/a | n/a | 0 / 0 |
| gpt-6.1-sol | 4.444 [0.000, 10.370] | 5.0% | 94.5% | 95.0% | 0.000 | 0.000 | 5.185 | 12.7% | 0 / 0 / 0 |
| gpt-6.1-terra | 2.222 [0.000, 5.185] | 2.3% | 98.2% | 97.0% | 0.000 | 0.000 | 2.222 | 21.7% | 0 / 0 / 0 |
| grok-4.7 | 8.889 [2.222, 16.296] | 6.0% | 96.4% | 93.0% | 0.000 | 0.000 | 0.000 | 44.0% | 0 / 0 / 0 |

gpt-5.5 cost: $9.15 estimated from token counts at an **assumed** list price of 5/25 USD per 1M tokens in/out (not read from the portal), well under the $60 cap. The summary figure of $9.15 comes from `cost.json` (tokens in/out per batch in `batch_0N_stats.json`). Per-model cost for the other six is not broken out in the overnight summary, so it is not compared. 429: 0 retries and 0 errors across all 4 batches; all 152 B blocks are verifier blocks.

## Where this is weak or inconclusive

- **Ceiling effect, degenerate CIs.** Arm A MAE is exactly 0 for all 300 runs, so the bootstrap CI collapses to [0, 0]. That shows no score error on this synthetic task; it does not bound the error on harder data. gpt-5.5 cannot be separated from a perfect scorer here, and gpt-6.1-terra / grok-4.7 CIs overlap with near-zero.
- **No claim of a best model.** A+ and B MAE are 0 for every model, so only Arm A and B(raw) discriminate. gpt-5.5 has the lowest Arm A MAE, but with one deployment per model, one data seed and synthetic parcels, that is not evidence it is better in general.
- **Fault detection is not 100% under A+/B:** 96.4% pooled [90.9, 100.0], and 0.846 in batch_02 (band mismatch 8%). The same 96.4% is seen for gpt-6.1-sol/terra and grok-4.7 in the overnight run, which suggests a property of that batch's data/fault labels rather than of the model; this was not investigated.
- **B verifier block rate (50.7%) is not an accuracy measure.** It is the share of arm B runs the verifier rejected before returning an answer, and it moves with how the model phrases claims. It is higher than five of the six overnight models (B ran for five) and lower only than gpt-4.1 (77.3%). With 0 score error in B, the blocks did not translate to wrong scores.
- **Reproducibility and provenance** are 100% and 0% for gpt-5.5, but these are measured on 3 repeats at default settings, so rare nondeterminism is not excluded.
- **Different runs, different dates.** The six models were run overnight; gpt-5.5 later, same code, data, batches and Gold. The 429 pressure on gpt-4.1/gpt-4.1-mini in the overnight run came from their own endpoint capacity and does not apply to gpt-5.5 (400 TPM, 0 throttling at 4 workers).

## Run notes

- Capacity `wildfiresharedv3` was F2 Active at the start. I scaled it **up to F8** manually before the run (the Fabric graph refresh needed it as in the overnight run; the runner expects F8), so `capacity_original.json` records `seen_at_start: F8`. The runner restored it to **F2 Active** at the end (`capacity_final.json`, confirmed by `az fabric capacity show`).
- `PINNED_AZURE_TENANT` was pinned to the tenant that owns the AI resource (the signed-in `az` tenant).
- The Fabric tables hold batch 4 (P126-P150) after the run, which is the same state the overnight run left. The 50-parcel state was **not** restored (that needs a scale-up for the graph refresh); restore with the steps in `docs/e2e-fabric-foundry-ontology.md` if needed.
- The earlier local-scorer version of this run (superseded) was removed from the branch.
