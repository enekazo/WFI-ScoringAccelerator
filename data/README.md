# Synthetic wildfire dataset

This directory contains the **historical experiment inputs**, not the active deployment
dataset. The baseline below has 50 parcels; pooled score experiments also use the extra
inputs described in the [reproduction guide](../docs/experiment.md). The configurable
cloud installer prepares a separate dataset under ignored `deploy\.out\`, defaults to
150 parcels and records its paths and hashes in `deployment-data.json`. Follow the
[deployment guide](../deploy/README.md#configured-dataset-and-generated-files); do not
regenerate these historical inputs to change a deployment's size.

Seeded (default seed `20260715`) and regenerated with `python scripts/generate_dataset.py [seed]`.
Same seed => byte-identical files. Fixed clock `as_of` = `2026-07-15T12:00:00+00:00`.

## Files

- `dataset.json` — `seed`, `as_of`, `policy_sha256` (pinned example policy), and `parcels`:
  parcel id (`P001`..`P050`) -> list of observations (`entity_id`, `metric`, `value` as decimal
  string, `unit`, `observed_at`, `valid_until`, `provenance`, `quality`, `source`).
- `ground_truth.json` — per parcel:
  - `fault`: fault type or `null` (clean); `injected_metric`: affected metric or `null`.
  - `expected_status`: `scored`, `incomplete`, or `policy_mismatch`.
  - `expected_reasons`: scorer reason codes expected for the faulted data.
  - `clean_status`, `expected_score`, `expected_band`: real scorer output on the CLEAN version.
  - `tampered_policy_sha256`: set only for `policy_tamper`.

20 clean parcels, 30 faulted (5 per fault).

## Fault catalogue

| fault | injection | expected reason |
|---|---|---|
| `missing_input` | observation removed | `missing:<metric>` |
| `expired_input` | observed 60 days ago, valid_until 30 days ago | `expired:<metric>` |
| `fixture_provenance` | provenance `fixture`, source string looks like `sensor:...` | `provenance:<metric>` |
| `unit_mismatch` | wrong unit (e.g. `mph`, `g/kg`) | `unit:<metric>` |
| `invalid_quality` | quality `invalid` | `invalid:<metric>` |
| `policy_tamper` | observations clean; ground truth marks that scoring under an altered policy (weights shifted 0.05) must be detected via a different `policy_sha256` | none (`policy_mismatch`) |

`weight_sensitivity(policy, pct=20)` yields policies with each factor weight moved by +/-pct,
renormalised in Decimal to sum exactly 1.
