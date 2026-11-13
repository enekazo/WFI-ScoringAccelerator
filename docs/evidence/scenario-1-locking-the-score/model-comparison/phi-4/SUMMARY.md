# Phi-4 model comparison (arms A and A+ only; arm B not run)

Same harness, 50 synthetic parcels, same seeds, 3 repeats, temperature 0, and weight-sensitivity
pass (10 variants x first 10 parcels x 1 repeat) as `gpt-6.1-sol`. Full harness tables:
`harness-summary.md` (the harness's `summary.md`, renamed because Windows filesystems are
case-insensitive and it collides with this file; rename back when merging if desired).
Raw data: `runs.json`, `metrics.json`, `sensitivity.json`, `receipts/`.

## Model / deployment

- Catalogue model **Phi-4**, format Microsoft, **version 7** (latest of versions 2-7 listed in
  centralus), GlobalStandard, capacity 50 (50K TPM), account `example-foundry-account`.
- Deployment name `phi-4-cmp` (appears as `phi-4-cmp` in the data files). Entra auth only.
- Catalogue capabilities: `chatCompletion` only; no tool-calling capability is advertised.
- Deployment succeeded; deleted after the run. `gpt-4.1` and `gpt-4.1-mini` remain
  (`grok-4.7-cmp` belongs to another session and was not touched).

## Arm B: not run

Probe with a function tool on chat completions returned HTTP 400:
`"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set`
(BadRequestError, code 400). The model server has tool calling disabled, so Arm B (and
B(raw)) cannot run. I did not try other Phi-4 versions or other routes. Not simulated or
implied. Only arms A and A+ appear in the results.

## Headline results (mean [95% bootstrap CI over parcels], 50 parcels x 3 repeats)

| Arm | Score err (MAE) | Band mismatch | Provenance misstated | Fault detection | False alarm | Reproducible |
|---|---|---|---|---|---|---|
| A | 9.630 [6.228, 13.740] | 0.340 [0.220, 0.473] | 0.020 [0.000, 0.060] | 0.556 [0.389, 0.722] | 0 | 0.620 [0.480, 0.740] |
| A+ | 0.000 | 0.000 | 0.000 | 0.833 [0.700, 0.967] | 0 | 1.000 |
| B | not run | | | | | |

Weight sensitivity (LLM arms, 10 variants x 10 parcels): A MAE 12.763 [10.995, 14.542], band
mismatch 0.150, fault detection 0.300; A+ MAE 0, fault detection 1.000.
The deterministic rescoring table is identical to other models (it does not involve the LLM).

## Reading

- Phi-4 Arm A is clearly worse than gpt-6.1-sol (MAE 0 there) and better than gpt-4.1 Arm A
  (MAE 49.2) on score error, but still has 0.34 band mismatch, weak fault detection (0.56) and
  only 0.62 reproducibility at temperature 0.
- A+ (receipt supplied) fixes score/band/reproducibility completely, but fault detection is
  0.833: it misses some injected faults even when given the receipt.
- No claim about the pinned-data + verifier pattern (B) can be made for this model.

## Caveats

- 0 errors in both the main run (300/300 runs) and the sensitivity run (200/200), i.e. no HTTP
  429s; the committed run is the first and only full run. Settings: workers 3, default config
  `max_retries` 4. The original baseline used workers 4.
- Arm B was excluded by a scratch wrapper (outside the repo) that set the runner's default
  arm tuple to `("A", "A+")` and supplied an Entra token via the Azure CLI (the SDK's own CLI
  credential timed out on this machine). `clients.py` and all repo source files are unchanged.
- Phi-4 is not a reasoning model, so temperature 0 was applied (chat completions, Entra ID).
- **Token usage: not recorded.** The harness only keeps in-memory counters and Azure Monitor
  returned no token metrics for this deployment. Rough estimate for 500 single-turn calls
  at a few thousand prompt tokens each: about 1-2M prompt and under 0.3M completion tokens;
  cost well below the 5 USD cap (estimate, not measured; pricing not checked).
- One model, one run; the CIs are over parcels only.
