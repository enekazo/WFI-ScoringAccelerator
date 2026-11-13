# Cloud MCP Tool Host, Deterministic Scorer & Narration Verifier (`cloud/tool_host/`)

Azure Functions (Python v2, Linux Consumption/Basic plan) app that hosts the accelerator's cloud execution plane:
1. **MCP receipt tools (`POST /api/mcp`)**: Exposes the four read-only receipt tools over **MCP (streamable HTTP)** so the Microsoft Foundry agent (`pinned-score-agent`) calls them directly in the cloud through a Foundry project connection — with no local Python caller loop (`run_agent_turn`) required.
2. **Deterministic scorer (`POST /api/score`)**: Executes `pinned_scoring.scorer.score` inside the Function App under the pinned policy (`_policy.json` / `PINNED_POLICY_SHA256`), validating observation provenance, freshness, unit, and policy pinning and returning a hash-verified `Receipt.wire()` payload without requiring Blob write permissions.
3. **Agent-answer narration verifier & deterministic receipt fallback (`POST /api/verify`)**: Executes `pinned_scoring.agent.guarded_answer` / `pinned_scoring.verifier.verify_narration` inside the Function App against the authoritative receipt store (`RECEIPTS_PREFIX`). When a model answer hallucinates a score, band, status, reason, or provenance, the Function App blocks the narration (`blocked: true`, `fallback_used: true`) and returns a deterministic receipt-based fallback in `text` and `delivered`.

## HTTP Endpoints

### 1. `POST /api/mcp` & `GET /api/mcp`
Streamable HTTP JSON-RPC 2.0 MCP endpoint wrapping `pinned_scoring.foundry_agent.FoundryToolbox` and `pinned_scoring.cloud_tool_host.McpToolHost`:
- `get_receipt(parcel)` — verified score receipt for one parcel (score, band, status, components with provenance, reasons, SHA-256 hashes).
- `list_receipts(band, status)` — receipt summaries optionally filtered by band and/or status.
- `list_reasons(parcel)` — status and incompleteness reasons for one parcel.
- `compare(parcel_a, parcel_b)` — stored receipt comparison and per-component contribution differences.

### 2. `POST /api/score`
Function-hosted deterministic scorer (`pinned_scoring.scorer.score`).
- **Request JSON**:
  - Stored dataset parcel(s): `{"parcel": "P003"}`, `{"parcels": ["P001", "P003"]}`, or all dataset parcels via `{"parcel": "ALL"}` / `{"parcels": []}` / `{"parcels": ["ALL"]}` (reads observations from `_dataset.json` in `RECEIPTS_PREFIX` or local `data/dataset.json`).
  - Explicit observation wires: `{"parcel": "P003", "as_of": "2026-07-15T12:00:00+00:00", "observations": [...], "submitted_policy_sha256": "<optional>"}`.
  - Optional counterfactual weights: `"weight_overrides": {"fuel_load": "0.20", "wind": "0.20"}` (must sum to `1.0`).
- **Policy-pinning enforcement**: Rejects mismatched `submitted_policy_sha256` or dataset `policy_sha256` with HTTP `409` (`status: "policy_mismatch"`).
- **Response JSON (`200 OK`)**:
  - Single parcel: `{"counterfactual": false, "policy_sha256": "<sha>", "receipt": <Receipt.wire()>}`.
  - Batch / `ALL`: `{"counterfactual": false, "policy_sha256": "<sha>", "count": N, "receipts": [...]}`.

### 3. `POST /api/verify`
Function-hosted narration verifier (`pinned_scoring.verifier.verify_narration`) with deterministic receipt fallback.
- **Request JSON**:
  - `{"question": "...", "answer": "...", "entity_id": "P003" (optional), "mcp_trace": [...] (optional)}`.
- **Verification & fallback behavior**:
  - Resolves referenced parcels from `question`, `answer`, `entity_id`, and `mcp_trace`, loads their authoritative receipts from `BlobReceiptReader` (`RECEIPTS_PREFIX`), verifies `policy_sha256`, and runs sentence-by-sentence narration verification.
  - When `blocked == false`: returns `blocked: false`, `fallback_used: false`, permitted `text` and `delivered.mode: "verified_narration"`. `delivered` includes the primary receipt's fields when one is referenced.
  - When `blocked == true`: replaces `text` on the server with deterministic receipt values and returns `fallback_used: true`, `delivered.mode: "receipt_fallback"` and structured parcel values in `delivered.parcels`.
  - Both outcomes include top-level `violations`, `referenced_parcels`, `receipt_hashes` and `policy_sha256`. `raw_text` is diagnostic, not delivery output.

### 4. `GET /api/health`
Unauthenticated liveness/readiness probe returning `status`, `server` (`"pinnedscore-receipts-mcp"`), `version`, `protocolVersion`, `receiptsLoaded`, `source`, `storageAccount`, `container`, `receiptsPrefix`, `policySha256`, `authRequired`, `allowedTenantId`, `lastAuthError`, `tools`, `endpoints`, and `capabilities` (`{"mcpReceiptTools": true, "deterministicScorer": true, "narrationVerifier": true, "receiptFallback": true}`).

## Cloud Runner (`scripts/run_cloud_agent.py`)

`scripts/run_cloud_agent.py` is the supported cloud runner for the receipt agent (`pinned-score-agent`) and the Function-hosted scorer:
- **Mandatory server-side verification**: Every prompt run against `pinned-score-agent` requires a resolved Function endpoint (`--function-endpoint` or `PINNED_FUNCTION_ENDPOINT`, or auto-discovered from the agent's MCP tool definition) and routes the model's answer through `POST /api/verify` before returning output. There is no raw bypass flag (`--no-server-verify` is not supported); Fabric IQ ontology relationship queries run separately via `scripts/create_foundry_fabric_agent.py`.
- **Supported CLI flags**:
  - `question` (optional positional) or `--question <TEXT>`: Prompt to send to `pinned-score-agent`.
  - `--endpoint <URL>`: Foundry project endpoint (default `FOUNDRY_PROJECT_ENDPOINT`).
  - `--agent <NAME>`: Foundry receipt agent name (default `pinned-score-agent`).
  - `--function-endpoint <URL>`: Function App base URL or `/api/mcp` URL (default `PINNED_FUNCTION_ENDPOINT`).
  - `--entity-id <ID>`: Optional parcel ID hint for `POST /api/verify`.
  - `--verify` / `--require-mcp-call`: Also require at least one remote `mcp_call` in the Foundry response trace (narration verification via `/api/verify` is always enforced regardless of `--verify`).
  - `--score-parcel <ID|ALL>`: Invoke `POST /api/score` on the Function App for a single parcel (e.g. `P003`) or `ALL` dataset parcels and exit.
  - `--json`: Emit structured JSON output.

## Why `propose_action` and scenario graph tools are excluded

- `propose_action` appends a `'proposed'` row to the `authority_decisions` Delta table in Fabric under the agent's identity. Exposing it on a shared HTTP MCP tool host would allow any caller reaching the tool endpoint to append proposals under the host identity without per-human separation-of-duties attribution. Therefore the cloud MCP host is strictly read-only (`readOnlyHint: true`, `destructiveHint: false`); `propose_action` and human review/approval remain gated through `scripts/authority.py`.
- The four experimental planning-scenario graph tools are not this shared MCP surface.
  The separate Fabric IQ agent (`pinned-score-agent-fabric`) queries ontology through
  `ask_ontology`; no combined-agent orchestration or execution capability is implied.

## Authentication and security

- **Storage access:** The Function App uses the user-assigned managed identity (`<resourcePrefix>-agent-id`) with `Storage Blob Data Reader` on the receipts storage account (`RECEIPTS_STORAGE_ACCOUNT` / `RECEIPTS_CONTAINER` / `RECEIPTS_PREFIX`). Every blob is verified via `Receipt.from_wire()` (SHA-256 check) on load. Neither `/api/score` nor `/api/verify` writes to Blob Storage.
- **Host storage:** Uses identity-based Azure Functions host storage (`AzureWebJobsStorage__accountName` + `AzureWebJobsStorage__credential=managedidentity` + `AzureWebJobsStorage__clientId`), with `allowSharedKeyAccess: false` on the storage accounts.
- **Inbound auth:** `/api/mcp`, `/api/score`, and `/api/verify` enforce in-app RS256 JWT validation (`verify_entra_bearer_token`) against the configured tenant's Entra OIDC JWKS keys (`MCP_ALLOWED_TENANT_ID` and `MCP_ALLOWED_AUDIENCES`). No API keys or storage keys exist in the repository.

## Local testing

All Function endpoints (`/api/mcp`, `/api/score`, `/api/verify`, `/api/health`) can be tested locally in-process via `handle_http_request` with **zero additional installs**:

```powershell
python -m pytest tests/test_cloud_tool_host.py -q
```

To run the standalone local Azure Functions host (`func start`), install `python -m pip install -r cloud/tool_host/requirements.txt` and Azure Functions Core Tools v4.
