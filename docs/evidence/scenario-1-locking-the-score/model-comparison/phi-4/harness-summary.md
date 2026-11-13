# Experiment summary

Mode: live  
Models: phi-4-cmp  
Parcels: 50, repeats: 3

Cells are mean [95% bootstrap CI over parcels]. Arm B shows the delivered answer (after the verifier) and B(raw), the model's answer before verification.

| Model | Arm | Score err (MAE) | Band mismatch | Provenance misstated | Fault detection | False alarm | Reproducible | Verifier-block rate | Parse fail |
|---|---|---|---|---|---|---|---|---|---|
| phi-4-cmp | A | 9.630 [6.228, 13.740] | 0.340 [0.220, 0.473] | 0.020 [0.000, 0.060] | 0.556 [0.389, 0.722] | 0.000 [0.000, 0.000] | 0.620 [0.480, 0.740] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| phi-4-cmp | A+ | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.833 [0.700, 0.967] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |

## Weight sensitivity (+/-20%)

Deterministic rescoring of clean parcels per variant:

| Variant | Mean abs score shift | Band flip share |
|---|---|---|
| fuel_load:up | 1.050 | 0.150 |
| fuel_load:down | 1.165 | 0.050 |
| dead_fuel:up | 0.625 | 0.000 |
| dead_fuel:down | 0.700 | 0.100 |
| moisture:up | 1.260 | 0.100 |
| moisture:down | 1.375 | 0.200 |
| weather_danger:up | 0.635 | 0.000 |
| weather_danger:down | 0.690 | 0.100 |
| wind:up | 0.580 | 0.050 |
| wind:down | 0.590 | 0.050 |

LLM arms pooled over 10 variants, parcels P001, P002, P003, P004, P005, P006, P007, P008, P009, P010, 1 repeat:

| Model | Arm | Score err (MAE) | Band mismatch | Provenance misstated | Fault detection | False alarm | Reproducible | Verifier-block rate | Parse fail |
|---|---|---|---|---|---|---|---|---|---|
| phi-4-cmp | A | 12.763 [10.995, 14.542] | 0.150 [0.080, 0.220] | 0.000 [0.000, 0.000] | 0.300 [0.175, 0.450] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| phi-4-cmp | A+ | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
