# Experiment summary

Mode: live  
Models: grok-4.7-cmp  
Parcels: 50, repeats: 3

Cells are mean [95% bootstrap CI over parcels]. Arm B shows the delivered answer (after the verifier) and B(raw), the model's answer before verification.

| Model | Arm | Score err (MAE) | Band mismatch | Provenance misstated | Fault detection | False alarm | Reproducible | Verifier-block rate | Parse fail |
|---|---|---|---|---|---|---|---|---|---|
| grok-4.7-cmp | A | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| grok-4.7-cmp | A+ | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| grok-4.7-cmp | B | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.420 [0.320, 0.527] | 0.000 [0.000, 0.000] |
| grok-4.7-cmp | B(raw) | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |

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
| grok-4.7-cmp | A | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| grok-4.7-cmp | A+ | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| grok-4.7-cmp | B | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.560 [0.460, 0.660] | 0.000 [0.000, 0.000] |
| grok-4.7-cmp | B(raw) | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
