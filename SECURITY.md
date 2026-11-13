# Security

This is a reference accelerator with synthetic data, not a production system.

- Report a vulnerability privately to the repository owner via GitHub's "Report a vulnerability" feature. Please do not open a public issue.
- Never commit credentials or private deployment configuration. `deploy/parameters.json`,
  `fabric/parameters.json` and the real approval roles file are gitignored.
- **Implemented controls:** deterministic scoring and receipt hash checks, narration-verifier
  tests, experiment cost meter/budget guard/kill switch and secrets-scan tests. These are
  scoped controls, not evidence of production security or calibrated wildfire risk.
- **Cloud identity and access:** `deploy/resources.bicep` assigns resource-scoped RBAC.
  The Function managed identity has Storage Blob Data Reader on receipt storage, with
  separate write roles on Function host-state storage. The deployer can upload receipts.
  Foundry-to-Function MCP uses an Entra-authenticated connection; the host checks token
  signature, tenant and accepted audience (`verify_entra_bearer_token`). Read-only receipt
  tools do not expose approval or operational execution.
- **Storage and networking:** deployment disables storage shared keys and configures a
  Network Security Perimeter. Blob versioning, soft delete, and WORM/immutable storage are
  disabled by default so the storage accounts can be cleanly deleted without retention locks.
  Private endpoints, production retention and independently
  validated network isolation are not demonstrated.
- **Observability:** the Function App has Application Insights settings and the installer
  wires an Application Insights connection to Foundry. Wiring is not proof of complete
  trace delivery, redaction, alert coverage or incident readiness.
- **Cloud release gate:** mandatory Function-hosted scoring and answer verification/fallback
  are the approved architecture. The documented receipt-MCP live baseline predates that
  change. Runtime revision `91fbe33` implements the Function routes and rejects
  receipt-runner verification opt-out before a model turn; corrected HTTP transport
  and rejection paths are integrated and offline-tested. Live server-side enforcement
  must still be verified before claiming deployment. Direct portal/Responses output
  is not verified delivery. See the [status matrix](docs/azure-mapping.md).
- **Not demonstrated:** prompt-injection resistance, data-leak prevention, compliance
  certification, content-safety shields, automatic budget alerts, service availability,
  performance guarantees or a two-person live approval. Experiment guards do not impose an
  Azure/Fabric billing cap. Approval records are tamper-evident, not tamper-proof.

See [deployment](deploy/README.md), [testing and operations](docs/testing.md), and
[human review and approval](docs/authority.md). Use synthetic data until controls and
permissions have been evaluated for your environment.
