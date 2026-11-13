# gpt-6.1-terra: Arm A / A+ / B / B(raw) rerun

(Named NOTES.md because `SUMMARY.md` collides with the generated `summary.md` on case-insensitive filesystems.)

Same protocol as `gpt-6.1-sol` ([docs/model-comparison.md](../../../model-comparison.md)): 50 parcels,
same seeds, 3 repeats (450 runs), weight-sensitivity pass (10 variants x first 10 parcels x 1 repeat).
Full tables: [summary.md](summary.md); raw data: `runs.json`, `metrics.json`, `sensitivity.json`, `receipts/`.

- **Deployment:** `gpt-6.1-terra`, model version `2026-10-26`, GlobalStandard, capacity 200 (200K TPM),
  account `example-foundry-account` (centralus). Entra auth only. Deleted after the run.
- **Errors in committed run:** 0 of 450 (no 429s). One full run only (a 2-parcel pilot was
  discarded); workers 4, `max_retries` 8 (local gitignored `results/config.json`).
- **Settings:** reasoning model, so no `temperature`; default reasoning effort, `max_completion_tokens=16000`.
  Tool turns (B, B(raw)) use the Responses API, A/A+ chat completions, as for gpt-6.1-sol.
  `clients.py` was not changed.

## Headline (mean over 50 parcels x 3 repeats)

| Arm | Score err (MAE) | Provenance misstated | Fault detection | Reproducible | Verifier-block rate |
|---|---|---|---|---|---|
| A | 0.000 | 0.000 | 0.989 [0.967, 1.000] | 1.000 | 0.000 |
| A+ | 0.000 | 0.000 | 1.000 | 1.000 | 0.000 |
| B (delivered) | 0.000 | 0.000 | 1.000 | 1.000 | 0.160 [0.100, 0.233] |
| B(raw) | 0.000 | 0.000 | 1.000 | 1.000 | 0.000 |

Sensitivity pass: 0 score error and 1.0 fault detection in all arms; B verifier-block rate 0.290 [0.200, 0.380].

## Caveats

- Differs from gpt-6.1-sol in one cell: Arm A fault detection 0.989 (not 1.0); a few injected
  faults were missed in Arm A. All other cells are saturated at 0 error / 1.0.
- B's blocks do not correspond to score errors (B(raw) MAE is 0); block causes were not analysed.
- Arm B's zero error is by construction (delivered answer is the pinned receipt).
- One model = one data point; the benchmark is saturated for this model.
- Token usage was not recorded by the harness, so cost is not measured. Run took ~11 min at
  200K TPM plus a short pilot; expected cost is well below the 5 USD cap, but this is an estimate.
- I briefly deployed `grok-4.7` (200 capacity rejected: InsufficientQuota, 80 available; deployed at 80),
  started a run, stopped it on reassignment, and deleted that deployment. No grok data is committed.
