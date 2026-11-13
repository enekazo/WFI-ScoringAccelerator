# Scenario reports

One report per scenario. Each holds all tests, results, limits and paths to the raw evidence.

Personal data has been redacted or pseudonymised to protect privacy, including
user sign-in names (UPNs) and workstation usernames.
Published evidence is sanitised: deployment identifiers and identity values use
consistent example replacements, and deployment hostnames use the reserved
`.invalid` domain. Workstation usernames are replaced with `example-user`.
These values are not deployment configuration. Scores, counts, policy/receipt
hashes and recorded outcomes are unchanged; original evidence is retained
privately. Authority-log hashes describe the original records and cannot be
recomputed from copies whose actor identities have been redacted. Source-file
fingerprints likewise describe the source tested at collection, not the later
sanitised source. Sanitisation of current files does not remove older Git copies.

[Reproduce the experiments](../experiment.md) using real CLI arguments and separate
outputs. [Test the cloud deployment](../testing.md) independently of those historical
harnesses; experiment source labels are not alternative supported architectures.

Scope: score evidence pools **150 parcels**; the latest live Fabric relationship
checks use **150 parcels and 450 CrewAccess records**, with five C1–C5 questions;
the separate usefulness comparison has 20 questions. Decision tests use **20 questions
per setup per model**. Parcel counts and question counts are different denominators.
The [live cloud evidence index](../evidence/cloud-acceptance/2026-11-11/summary.json)
also records Function scoring/verification and both Foundry MCP paths. These
runtime checks are not a new multi-model comparison or a clean-install certificate.
A separate [single installation evidence index](../evidence/installation-acceptance/summary.json)
records the accepted end-to-end Step 1-9 installer run and its current verification outputs.

1. [Locking the score](scenario-1-locking-the-score.md) - code computes and locks the score; the model explains but cannot change it. Experiment, sensitivity, model comparison, extra parcels, verifier.
2. [Ontology](scenario-2-ontology.md) - connection questions answered through the Fabric ontology graph. Build, GQL, MCP agent, chain arms, usefulness, routes that did not work, tracing.
3. [Four tools](scenario-3-four-tools.md) - decision questions answered with four locked tools vs a flat-table agent vs no tools, per model.

Raw evidence: `docs/evidence/scenario-1-locking-the-score/`, `scenario-2-ontology/`, `scenario-3-four-tools/`.
