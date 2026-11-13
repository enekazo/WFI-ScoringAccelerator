"""Azure Functions Python v2 entrypoint exposing the read-only receipt tools over streamable HTTP MCP,
plus Function-hosted deterministic scoring (`POST /api/score`) and agent-answer narration verification
with deterministic receipt fallback (`POST /api/verify`).

Endpoints:
- `POST /api/mcp`: MCP streamable HTTP JSON-RPC 2.0 endpoint (`initialize`, `tools/list`, `tools/call`, `ping`).
- `GET /api/mcp`: returns server metadata and protocol status.
- `POST /api/score`: Function-hosted deterministic scorer (`pinned_scoring.scorer.score`) under pinned policy.
- `POST /api/verify`: Function-hosted narration verifier (`pinned_scoring.verifier.verify_narration`) with
  deterministic receipt fallback when verification blocks a mismatched model answer.
- `GET /api/health`: liveness/readiness check reporting loaded receipt count, data source, and capabilities.

All scoring, receipt verification, and tool logic is imported directly from `pinned_scoring` (no forked code).
Authentication is enforced via Microsoft Entra ID JWT verification (`verify_entra_bearer_token`) and Managed
Identity (`DefaultAzureCredential` with `AZURE_CLIENT_ID`) is used to read receipts from Azure Blob Storage.
No keys or connection strings are stored in code or configuration templates.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Support both packaged deployment (where `src/pinned_scoring` is bundled alongside `function_app.py`)
# and direct checkout execution.
_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE / "src", _HERE.parents[1] / "src"):
    if (_candidate / "pinned_scoring").is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from pinned_scoring.cloud_tool_host import (  # noqa: E402
    ALLOWED_TENANT_ENV,
    MCP_PROTOCOL_VERSION,
    MCP_SERVER_NAME,
    MCP_SERVER_VERSION,
    STORAGE_ACCOUNT_ENV,
    STORAGE_CONTAINER_ENV,
    STORAGE_PREFIX_ENV,
    McpToolHost,
    is_auth_required,
    mcp_tool_schemas,
    verify_entra_bearer_token,
)

_HOST: McpToolHost | None = None
_LAST_AUTH_ERROR: str | None = None


def get_host() -> McpToolHost:
    global _HOST
    if _HOST is None:
        _HOST = McpToolHost()
    return _HOST


def _lookup_header(req_headers: dict[str, str] | None, name: str) -> str | None:
    if not req_headers:
        return None
    target = name.lower()
    for k, v in req_headers.items():
        if k.lower() == target:
            return v
    return None


def handle_http_request(
    method: str,
    path: str,
    body_bytes: bytes | None = None,
    req_headers: dict[str, str] | None = None,
) -> tuple[int, dict, str]:
    """Framework-neutral HTTP handler used by both Azure Functions and offline unit tests.

    Returns `(status_code, headers_dict, json_body_str)`.
    Protected endpoints require authentication by default and reject a missing
    `MCP_ALLOWED_TENANT_ID`. Cryptographic RS256 verification checks the incoming
    Microsoft Entra `Authorization: Bearer <jwt>` token against the configured
    tenant's OIDC JWKS keys, unless authentication is explicitly disabled.
    """
    global _LAST_AUTH_ERROR
    headers = {"Content-Type": "application/json"}
    norm_method = (method or "GET").upper()
    norm_path = (path or "/api/mcp").rstrip("/") or "/"
    auth_enforced = is_auth_required()

    if norm_path.endswith("/health"):
        host = get_host()
        count = len(host.toolbox.store.entity_ids())
        source = "blob" if os.environ.get(STORAGE_ACCOUNT_ENV, "").strip() else os.environ.get("PINNED_DATA_SOURCE", "local")
        policy_sha = None
        try:
            policy_sha = host.get_pinned_policy().sha256
        except Exception:  # noqa: BLE001
            pass
        return 200, headers, json.dumps({
            "status": "ok",
            "server": MCP_SERVER_NAME,
            "version": MCP_SERVER_VERSION,
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "source": source,
            "storageAccount": os.environ.get(STORAGE_ACCOUNT_ENV) or None,
            "container": os.environ.get(STORAGE_CONTAINER_ENV, "receipts"),
            "receiptsPrefix": os.environ.get(STORAGE_PREFIX_ENV) or None,
            "policySha256": policy_sha,
            "receiptsLoaded": count,
            "authRequired": auth_enforced,
            "allowedTenantId": os.environ.get(ALLOWED_TENANT_ENV) or None,
            "lastAuthError": _LAST_AUTH_ERROR,
            "tools": [t["name"] for t in mcp_tool_schemas()],
            "endpoints": ["/api/mcp", "/api/score", "/api/verify", "/api/health"],
            "capabilities": {
                "mcpReceiptTools": True,
                "deterministicScorer": True,
                "narrationVerifier": True,
                "receiptFallback": True,
            },
        })

    if auth_enforced:
        auth_header = _lookup_header(req_headers, "Authorization")
        ok, auth_status, auth_err, _ = verify_entra_bearer_token(auth_header)
        if not ok:
            import logging

            header_keys = sorted((req_headers or {}).keys())
            _LAST_AUTH_ERROR = f"{auth_err} | headers={header_keys}"
            logging.warning("MCP auth rejected (%s): %s | headers=%s", auth_status, auth_err, header_keys)
            err_headers = {
                **headers,
                "WWW-Authenticate": 'Bearer realm="pinnedscore-receipts-mcp", error="invalid_token"',
            }
            return auth_status, err_headers, json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32001, "message": auth_err},
            }) if not (norm_path.endswith("/score") or norm_path.endswith("/verify")) else json.dumps({"status": auth_status, "error": auth_err})

    if norm_path.endswith("/score"):
        if norm_method != "POST":
            return 405, headers, json.dumps({"error": f"Method {norm_method} not allowed"})
        try:
            raw_text = (body_bytes or b"").decode("utf-8")
            payload = json.loads(raw_text) if raw_text.strip() else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return 400, headers, json.dumps({"error": f"Parse error: {exc}"})
        status, resp_obj = get_host().score_request(payload)
        return status, headers, json.dumps(resp_obj, default=str)

    if norm_path.endswith("/verify"):
        if norm_method != "POST":
            return 405, headers, json.dumps({"error": f"Method {norm_method} not allowed"})
        try:
            raw_text = (body_bytes or b"").decode("utf-8")
            payload = json.loads(raw_text) if raw_text.strip() else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return 400, headers, json.dumps({"error": f"Parse error: {exc}"})
        status, resp_obj = get_host().verify_request(payload)
        return status, headers, json.dumps(resp_obj, default=str)

    if norm_method == "GET":
        return 200, headers, json.dumps({
            "server": MCP_SERVER_NAME,
            "version": MCP_SERVER_VERSION,
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "transport": "streamable-http",
            "authRequired": auth_enforced,
            "tools": [t["name"] for t in mcp_tool_schemas()],
            "endpoints": ["/api/mcp", "/api/score", "/api/verify", "/api/health"],
            "proposeActionExposed": False,
        })

    if norm_method != "POST":
        return 405, headers, json.dumps({"error": f"Method {norm_method} not allowed"})

    try:
        raw_text = (body_bytes or b"").decode("utf-8")
        payload = json.loads(raw_text) if raw_text.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return 400, headers, json.dumps({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": f"Parse error: {exc}"},
        })

    status, resp_obj = get_host().handle_rpc(payload)
    if resp_obj is None:
        return status, headers, ""
    return status, headers, json.dumps(resp_obj, default=str)


try:
    import azure.functions as func  # type: ignore[import-untyped]

    app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

    @app.route(route="mcp", methods=["GET", "POST"])
    def mcp_endpoint(req: func.HttpRequest) -> func.HttpResponse:
        status, headers, body = handle_http_request(
            req.method,
            req.url,
            req.get_body(),
            dict(req.headers),
        )
        return func.HttpResponse(body=body, status_code=status, headers=headers)

    @app.route(route="score", methods=["POST"])
    def score_endpoint(req: func.HttpRequest) -> func.HttpResponse:
        status, headers, body = handle_http_request(
            req.method,
            "/api/score",
            req.get_body(),
            dict(req.headers),
        )
        return func.HttpResponse(body=body, status_code=status, headers=headers)

    @app.route(route="verify", methods=["POST"])
    def verify_endpoint(req: func.HttpRequest) -> func.HttpResponse:
        status, headers, body = handle_http_request(
            req.method,
            "/api/verify",
            req.get_body(),
            dict(req.headers),
        )
        return func.HttpResponse(body=body, status_code=status, headers=headers)

    @app.route(route="health", methods=["GET"])
    def health_endpoint(req: func.HttpRequest) -> func.HttpResponse:
        status, headers, body = handle_http_request("GET", "/api/health", None, dict(req.headers))
        return func.HttpResponse(body=body, status_code=status, headers=headers)

except ImportError:
    app = None
