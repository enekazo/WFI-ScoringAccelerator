# Phi-4 full re-run (Arms A and A+; Arm B not possible)

Second full run of the harness for Phi-4 (catalogue version 7, GlobalStandard, deployment
`phi-4-cmp`, account `example-foundry-account`): 50 synthetic parcels x 3 repeats, temperature 0,
plus the weight-sensitivity pass. 300/300 main runs and the sensitivity runs finished with 0
errors. Only model calls were made (no Fabric calls). The deployment was recreated for this run
and deleted afterwards. Earlier run: `../phi-4/`.

Files: `runs.json`, `metrics.json`, `sensitivity.json`, `summary.md`, `receipts/`,
`arm_b_probe.json` (tool-call re-check), `token_usage.json`, `cost.json`.

## Results (from `summary.md`)

| Arm | Score err (MAE) | Band mismatch | Fault detection | Reproducible |
|---|---|---|---|---|
| A | 9.610 [6.097, 13.772] | 0.300 [0.180, 0.440] | 0.578 [0.400, 0.744] | 0.760 [0.640, 0.880] |
| A+ | 0.000 | 0.000 | 0.833 [0.700, 0.967] | 1.000 |

The earlier run gave A MAE 9.630, A+ 0.000; the two runs agree on MAE, but reproducibility
(0.62 vs 0.76) and band mismatch (0.34 vs 0.30) differ, so temperature 0 is not fully
deterministic for this endpoint.

## Arm B: not run

One chat-completions call with a function tool (`arm_b_probe.json`) was rejected again with
HTTP 400: `"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be
set`. Arm B and B(raw) require tool calling, so they cannot run on this endpoint. Nothing is
simulated.

## Cost

Measured tokens (client counters, all 500 calls incl. sensitivity): 513,077 prompt and 71,260
completion. At list price of $0.125 / $0.50 per 1M input / output tokens (looked up on the web,
not an invoice) that is about $0.10 (`cost.json`).

## Caveats

- The run used a scratch wrapper outside the repo that limited arms to A and A+ and used an
  Azure CLI credential; repo source is unchanged.
- Price is a list price from a third-party page; verify against the Azure pricing page.
