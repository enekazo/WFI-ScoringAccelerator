# grok-4.7 model comparison (SUMMARY)

Same protocol as `docs/model-comparison.md` / `gpt-6.1-sol`: 50 synthetic parcels, same seeds, 3 repeats
(450 runs: A, A+, B x 150), weight sensitivity (10 variants x first 10 parcels x 1 repeat).
Full tables: `tables.md` (the harness's generated `summary.md`, renamed to avoid a case-insensitive
clash with this file), `metrics.json`, `runs.json`, `sensitivity.json`, `receipts/`.

- Model: `grok-4.7` (xAI, version 1), deployment `grok-4.7-cmp`, GlobalStandard, 80K TPM, in
  `example-foundry-account` (centralus). Entra auth only. In the data files the model is labelled
  `grok-4.7-cmp` (the deployment name).
- Deleted after the run; only `gpt-4.1` and `gpt-4.1-mini` remain.

## Headline (mean, 50 parcels x 3 repeats; all CIs degenerate [0,0] / [1,1])

| Arm | Score err (MAE) | Provenance misstated | Fault detection | Reproducible |
|---|---|---|---|---|
| A | 0.000 | 0.000 | 1.000 | 1.000 |
| A+ | 0.000 | 0.000 | 1.000 | 1.000 |
| B (delivered) | 0.000 | 0.000 | 1.000 | 1.000 |
| B(raw, unverified) | 0.000 | 0.000 | 1.000 | 1.000 |

- **Verifier block rate (Arm B): 0.420 [0.320, 0.527]** (gpt-6.1-sol: 0.127; gpt-4.1: 0.907), yet
  B(raw) has zero score/provenance error. Violations recorded in blocked runs: 44 "claims 'expired'
  but receipt is scored", 44 "claims 'invalid' but receipt is scored", 8 "claims incomplete but receipt
  is scored", 6 "claims complete but receipt is incomplete" (all `completeness_mismatch`), and 9
  `provenance_mismatch` (wind / weather_danger measured vs generated). These look like verifier
  strictness / wording disagreements, not score errors; per-run adjudication was not done.
- **Sensitivity:** A, A+, B, B(raw) all 0 error, fault detection 1.0; B block rate 0.560 [0.460, 0.660].
  Deterministic rescoring shifts (mean abs 0.58-1.38 points; band flip share 0-0.20) are model independent.
- Like gpt-6.1-sol, the benchmark is saturated for this model: the Arm A gap seen with gpt-4.1 does not
  appear. Arm B's 0 error is by construction.

## Caveats

- `temperature=0` was accepted by grok-4.7 via chat completions, and function tools worked on chat
  completions (no Responses API needed). `src/pinned_scoring/experiment/clients.py` was **not modified**
  (`_is_reasoning` does not match `grok-*`, so the temperature-0 path is used). Whether grok-4.7 reasons
  internally was not investigated.
- Quota: a 200K TPM deployment failed with `InsufficientQuota: ... requires 200 quota units, but only 80
  quota units are available in Tokens Per Minute (thousands) - grok-4.7`. Deployed at 80K instead. A
  transient `RequestConflict` (another operation on the parent account, from parallel sessions) was
  retried. Run settings: 3 workers, `max_retries=8` (baseline used 4).
- Single attempt: 0 errors of 450 (no HTTP 429, nothing discarded). Took roughly 70 minutes.
- Token usage was not recorded by the harness and Azure Monitor token metrics returned nothing for the
  deployment, so tokens and cost are unknown (not measured; the 5 USD cap was not verified).
- One model is one data point.
