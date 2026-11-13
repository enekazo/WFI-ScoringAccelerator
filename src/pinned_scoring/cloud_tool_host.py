"""Azure Blob Storage receipt reader and streamable HTTP MCP handler for the cloud tool host.

Wraps the existing core (`FoundryToolbox`, `ReadOnlyReceiptStore`, `Receipt.from_wire`) without
forking scoring, verification, or receipt logic.

- `BlobReceiptReader`: reads `<parcel>.json` receipts from an Azure Blob Storage container using
  `DefaultAzureCredential` (or a user-assigned managed identity client ID). Every receipt is
  hash-verified on load with `Receipt.from_wire(wire)` before returning.
- `McpToolHost`: implements MCP streamable HTTP (`initialize`, `notifications/initialized`,
  `ping`, `tools/list`, `tools/call`) exposing the four read-only receipt tools (`get_receipt`,
  `list_receipts`, `list_reasons`, `compare`).

Why `propose_action` is excluded from the cloud MCP tool host:
`propose_action` appends a `'proposed'` row to the `authority_decisions` Delta table in Fabric
under `agent_id`. Over a shared Function host identity, an unauthenticated or shared-scope tool
endpoint could allow callers to forge proposals or exhaust the log without per-caller separation
of duties. Therefore the cloud MCP host is strictly read-only (`get_receipt`, `list_receipts`,
`list_reasons`, `compare`); authority proposals remain gated through the CLI / dedicated
authority workflow (`scripts/authority.py`).
"""

from __future__ import annotations

from copy import deepcopy as _deepcopy
import copy
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import agent as agent_tools
from . import foundry_agent as fa
from .contracts import ContractError, Observation
from .controls import KillSwitchEngaged, kill_switch_engaged
from .fabric.loader import load_raw
from .fabric.transforms import raw_to_silver, silver_to_observations
from .policy import Policy
from .receipt import Receipt
from .receipts_store import DEFAULT_DIR, ReadOnlyReceiptStore
from .scorer import score
from .verifier import Violation

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

STORAGE_ACCOUNT_ENV = "RECEIPTS_STORAGE_ACCOUNT"
STORAGE_CONTAINER_ENV = "RECEIPTS_CONTAINER"
STORAGE_PREFIX_ENV = "RECEIPTS_PREFIX"
PINNED_POLICY_SHA256_ENV = "PINNED_POLICY_SHA256"
PINNED_POLICY_PATH_ENV = "PINNED_POLICY_PATH"
PINNED_DATASET_PATH_ENV = "PINNED_DATASET_PATH"
FUNCTION_ENDPOINT_ENV = "PINNED_FUNCTION_ENDPOINT"
DEFAULT_CONTAINER = "receipts"
MANAGED_IDENTITY_CLIENT_ID_ENV = "AZURE_CLIENT_ID"
ALLOWED_TENANT_ENV = "MCP_ALLOWED_TENANT_ID"
ALLOWED_AUDIENCES_ENV = "MCP_ALLOWED_AUDIENCES"
ALLOWED_CALLER_OIDS_ENV = "MCP_ALLOWED_CALLER_OIDS"
REQUIRE_AUTH_ENV = "MCP_REQUIRE_AUTH"
DEFAULT_ALLOWED_AUDIENCES = (
    "https://management.azure.com/",
    "https://management.azure.com",
    "https://ai.azure.com/",
    "https://ai.azure.com",
    "https://cognitiveservices.azure.com/",
    "https://cognitiveservices.azure.com",
)
MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_SERVER_NAME = "pinnedscore-receipts-mcp"
MCP_SERVER_VERSION = "0.2.0"

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = _REPO_ROOT / "examples" / "wildfire" / "policy.json"
DEFAULT_DATASET_PATH = _REPO_ROOT / "data" / "dataset.json"

_JWKS_CLIENTS: dict[str, Any] = {}


def _normalise_observations(observations: list[Observation], policy: Policy, as_of: datetime) -> list[Observation]:
    # Match the receipt-preparation/Fabric path, including unit conversion and quality flags.
    raw = load_raw(
        [{**row.wire(), "parcel_id": row.entity_id} for row in observations],
        "function-score", as_of,
    )
    return silver_to_observations(raw_to_silver(raw, policy, as_of))


def is_auth_required() -> bool:
    """Return True when Entra Bearer JWT authentication is enforced on `/api/mcp`."""
    flag = os.environ.get(REQUIRE_AUTH_ENV, "").strip().lower()
    if flag in ("0", "false", "no"):
        return False
    if flag in ("1", "true", "yes"):
        return True
    return bool(os.environ.get(ALLOWED_TENANT_ENV, "").strip())


def parse_allowed_audiences(raw: str | None = None) -> list[str]:
    val = (raw if raw is not None else os.environ.get(ALLOWED_AUDIENCES_ENV, "")).strip()
    items = [x.strip() for x in val.split(",") if x.strip()] if val else []
    expanded: list[str] = []

    def _add(candidate: str) -> None:
        if candidate and candidate not in expanded:
            expanded.append(candidate)

    for item in [*items, *DEFAULT_ALLOWED_AUDIENCES]:
        _add(item)
        base = item.rstrip("/")
        _add(base)
        _add(f"{base}/")
        if base.startswith("api://"):
            bare = base[len("api://") :].strip("/")
            _add(bare)
        elif not base.startswith(("http://", "https://")):
            _add(f"api://{base}")
            _add(f"api://{base}/")
    return expanded


def parse_allowed_caller_oids(raw: str | None = None) -> list[str]:
    val = (raw if raw is not None else os.environ.get(ALLOWED_CALLER_OIDS_ENV, "")).strip()
    if not val:
        return []
    return [x.strip().lower() for x in val.split(",") if x.strip()]


def verify_entra_bearer_token(
    auth_header: str | None,
    *,
    allowed_tenant_id: str | None = None,
    allowed_audiences: list[str] | None = None,
    allowed_caller_oids: list[str] | None = None,
    signing_key_resolver: Callable[[str, str], Any] | None = None,
) -> tuple[bool, int, str, dict[str, Any]]:
    """Verify a Microsoft Entra ID `Authorization: Bearer <jwt>` header using RS256 JWKS verification.

    Returns `(ok, http_status, error_message, verified_claims)`.
    """
    tid = (allowed_tenant_id if allowed_tenant_id is not None else os.environ.get(ALLOWED_TENANT_ENV, "")).strip()
    if not tid:
        return False, 401, f"Entra authentication is required but {ALLOWED_TENANT_ENV} is not configured", {}

    if not auth_header or not isinstance(auth_header, str):
        return False, 401, "Missing Authorization header (expected 'Bearer <Entra JWT>')", {}

    parts = auth_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return False, 401, "Invalid Authorization header format (expected 'Bearer <Entra JWT>')", {}

    token = parts[1].strip()
    audiences = allowed_audiences if allowed_audiences is not None else parse_allowed_audiences()
    caller_oids = (
        [x.lower() for x in allowed_caller_oids]
        if allowed_caller_oids is not None
        else parse_allowed_caller_oids()
    )
    issuers = [
        f"https://sts.windows.net/{tid}/",
        f"https://login.microsoftonline.com/{tid}/v2.0",
    ]

    try:
        import jwt  # PyJWT

        if signing_key_resolver is not None:
            key = signing_key_resolver(token, tid)
        else:
            jwks_url = f"https://login.microsoftonline.com/{tid}/discovery/v2.0/keys"
            jwk_client = _JWKS_CLIENTS.get(jwks_url)
            if jwk_client is None:
                jwk_client = jwt.PyJWKClient(jwks_url, cache_keys=True)
                _JWKS_CLIENTS[jwks_url] = jwk_client
            key = jwk_client.get_signing_key_from_jwt(token).key

        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=audiences,
            issuer=issuers,
            leeway=60,
            options={"require": ["exp", "iss", "aud"]},
        )
    except Exception as exc:  # noqa: BLE001
        unverified_info = ""
        try:
            import jwt as _jwt

            unverified = _jwt.decode(token, options={"verify_signature": False})
            unverified_info = (
                f" (unverified aud={unverified.get('aud')!r}, "
                f"iss={unverified.get('iss')!r}, "
                f"tid={unverified.get('tid')!r}, "
                f"oid={unverified.get('oid')!r}, "
                f"azp={unverified.get('azp')!r})"
            )
        except Exception:  # noqa: BLE001
            pass
        return False, 401, f"Entra Bearer token validation failed: {type(exc).__name__}: {exc}{unverified_info}", {}

    claim_tid = str(claims.get("tid") or "").strip().lower()
    if claim_tid != tid.lower():
        return False, 403, f"Token tenant '{claim_tid}' is not authorized for tenant '{tid}'", {}

    claim_oid = str(claims.get("oid") or "").strip().lower()
    if caller_oids and claim_oid not in caller_oids:
        return False, 403, f"Caller principal '{claim_oid}' is not in {ALLOWED_CALLER_OIDS_ENV}", {}

    return True, 200, "", claims


def _is_blob_not_found(exc: BaseException) -> bool:
    """Return True only when a storage exception represents normal blob absence (404 / BlobNotFound)."""
    if isinstance(exc, (KeyError, FileNotFoundError)):
        return True
    if type(exc).__name__ == "ResourceNotFoundError":
        return True
    if getattr(exc, "status_code", None) == 404:
        return True
    err_code = str(getattr(exc, "error_code", "") or "")
    if err_code in ("BlobNotFound", "ResourceNotFound"):
        return True
    return False


class BlobReceiptReader:
    """Read-only receipt reader backed by an Azure Blob Storage container (Entra ID / Managed Identity).

    Accepts an optional `container_client` for offline unit testing; in production it constructs a
    `ContainerClient` with `DefaultAzureCredential` (using `AZURE_CLIENT_ID` when set for a
    user-assigned managed identity) against `https://<account>.blob.core.windows.net/<container>`.
    No account keys or connection strings are accepted or used.
    """

    __slots__ = ("_container", "_prefix")

    def __init__(
        self,
        account_name: str | None = None,
        container_name: str | None = None,
        *,
        container_client: Any = None,
        prefix: str | None = None,
        credential: Any = None,
    ):
        if container_client is None:
            account = (account_name or os.environ.get(STORAGE_ACCOUNT_ENV, "")).strip()
            container = (container_name or os.environ.get(STORAGE_CONTAINER_ENV, DEFAULT_CONTAINER)).strip()
            if not account:
                raise ValueError(
                    f"storage account name is required (pass account_name or set {STORAGE_ACCOUNT_ENV})"
                )
            if credential is None:
                from azure.identity import DefaultAzureCredential

                client_id = os.environ.get(MANAGED_IDENTITY_CLIENT_ID_ENV, "").strip() or None
                credential = DefaultAzureCredential(managed_identity_client_id=client_id)
            from azure.storage.blob import ContainerClient

            account_url = f"https://{account}.blob.core.windows.net"
            container_client = ContainerClient(
                account_url=account_url,
                container_name=container,
                credential=credential,
            )
        object.__setattr__(self, "_container", container_client)
        selected_prefix = prefix if prefix is not None else os.environ.get(STORAGE_PREFIX_ENV, "")
        object.__setattr__(self, "_prefix", selected_prefix.strip("/"))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("BlobReceiptReader is read-only")

    def _blob_name(self, parcel: str) -> str:
        return f"{self._prefix}/{parcel}.json" if self._prefix else f"{parcel}.json"

    def _read_metadata_json(self, filename: str) -> dict:
        blob_name = f"{self._prefix}/{filename}" if self._prefix else filename
        try:
            blob_client = self._container.get_blob_client(blob_name)
            raw = blob_client.download_blob().readall()
        except Exception as exc:  # noqa: BLE001
            if _is_blob_not_found(exc):
                raise KeyError(f"no metadata blob {blob_name!r}") from exc
            raise
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        return json.loads(text)

    def get_policy_wire(self) -> dict:
        """Read `_policy.json` from the active blob prefix/container."""
        return self._read_metadata_json("_policy.json")

    def get_dataset_wire(self) -> dict:
        """Read `_dataset.json` from the active blob prefix/container."""
        return self._read_metadata_json("_dataset.json")

    def get_receipt(self, parcel: str) -> dict:
        if not isinstance(parcel, str) or not _SAFE_ID.match(parcel):
            raise KeyError(f"invalid parcel id {parcel!r}")
        blob_name = self._blob_name(parcel)
        try:
            blob_client = self._container.get_blob_client(blob_name)
            raw = blob_client.download_blob().readall()
        except Exception as exc:  # noqa: BLE001
            if _is_blob_not_found(exc):
                raise KeyError(f"no receipt for parcel {parcel!r}") from exc
            raise
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        wire = json.loads(text)
        Receipt.from_wire(wire)  # raises ValueError if tampered
        return copy.deepcopy(wire)

    def list_receipts(self, band: str | None = None, status: str | None = None) -> list[dict]:
        prefix_arg = f"{self._prefix}/" if self._prefix else None
        blobs = self._container.list_blobs(name_starts_with=prefix_arg)
        parcels: list[str] = []
        for b in blobs:
            name = getattr(b, "name", None) or (b.get("name") if isinstance(b, dict) else str(b))
            if not name or not name.endswith(".json"):
                continue
            rel = name[len(prefix_arg):] if prefix_arg and name.startswith(prefix_arg) else name
            # Skip nested subdirectories (e.g. smoke/ timestamp blobs) and internal metadata (e.g. _policy.json)
            if "/" in rel or rel.startswith("_"):
                continue
            stem = rel[:-5]
            if _SAFE_ID.match(stem):
                parcels.append(stem)
        out: list[dict] = []
        for parcel in sorted(set(parcels)):
            wire = self.get_receipt(parcel)
            if band is not None and (wire["band"] or "").lower() != str(band).lower():
                continue
            if status is not None and wire["status"].lower() != str(status).lower():
                continue
            out.append({
                "parcel": wire["entity_id"],
                "status": wire["status"],
                "score": wire["score"],
                "band": wire["band"],
            })
        return out


def make_cloud_toolbox(
    *,
    reader: Any = None,
    directory: Path | str = DEFAULT_DIR,
) -> fa.FoundryToolbox:
    """Build a `FoundryToolbox` reading from Blob Storage when `RECEIPTS_STORAGE_ACCOUNT` is set,
    from Fabric when `PINNED_DATA_SOURCE=fabric`, or from the local receipts directory otherwise."""
    if reader is not None:
        return fa.FoundryToolbox(reader=reader)
    if os.environ.get(STORAGE_ACCOUNT_ENV, "").strip():
        return fa.FoundryToolbox(reader=BlobReceiptReader())
    return fa.FoundryToolbox(reader=fa.make_reader(directory=directory))


def mcp_tool_schemas() -> list[dict]:
    """Convert `foundry_agent.TOOL_DEFINITIONS` (4 read-only receipt tools) to MCP tool descriptors."""
    tools = []
    for t in fa.TOOL_DEFINITIONS:
        tools.append({
            "name": t["name"],
            "description": t["description"],
            "inputSchema": copy.deepcopy(t["parameters"]),
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        })
    return tools


class McpToolHost:
    """Stateless streamable HTTP MCP JSON-RPC 2.0 dispatcher + hosted deterministic scorer and
    agent-answer narration verifier over `FoundryToolbox` and `pinned_scoring` core.

    Exposes only the four read-only receipt tools (`get_receipt`, `list_receipts`, `list_reasons`,
    `compare`) on MCP `/api/mcp`, plus hosted deterministic scoring (`POST /api/score`) and
    server-side narration verification with deterministic receipt fallback (`POST /api/verify`).
    Never exposes `propose_action` or mutating operations.
    """

    def __init__(
        self,
        toolbox: fa.FoundryToolbox | None = None,
        toolbox_factory: Callable[[], fa.FoundryToolbox] | None = None,
        *,
        policy: Policy | None = None,
        dataset_doc: dict | None = None,
    ):
        self._toolbox = toolbox
        self._factory = toolbox_factory or make_cloud_toolbox
        self._policy = policy
        self._dataset_doc = dataset_doc

    @property
    def toolbox(self) -> fa.FoundryToolbox:
        if self._toolbox is None:
            self._toolbox = self._factory()
        return self._toolbox

    def get_pinned_policy(self) -> Policy:
        """Load the active Policy from `_policy.json` in Blob Storage (or local fallback) and enforce
        `PINNED_POLICY_SHA256` when configured."""
        if self._policy is None:
            reader = getattr(self.toolbox, "_reader", None)
            wire: dict | None = None
            if reader is not None and hasattr(reader, "get_policy_wire"):
                try:
                    wire = reader.get_policy_wire()
                except KeyError:
                    wire = None
            if wire is not None:
                self._policy = Policy.from_dict(wire)
            else:
                path_str = os.environ.get(PINNED_POLICY_PATH_ENV, "").strip()
                policy_path = Path(path_str) if path_str else DEFAULT_POLICY_PATH
                self._policy = Policy.load(policy_path)
        expected_sha = os.environ.get(PINNED_POLICY_SHA256_ENV, "").strip().lower()
        if expected_sha and self._policy.sha256.lower() != expected_sha:
            raise ValueError(
                f"pinned policy hash mismatch: loaded {self._policy.sha256} != expected {expected_sha}"
            )
        return self._policy

    def get_dataset_doc(self) -> dict | None:
        """Load `_dataset.json` from Blob Storage (under `RECEIPTS_PREFIX`) or local fallback."""
        if self._dataset_doc is not None:
            return self._dataset_doc
        reader = getattr(self.toolbox, "_reader", None)
        if reader is not None and hasattr(reader, "get_dataset_wire"):
            try:
                self._dataset_doc = reader.get_dataset_wire()
                return self._dataset_doc
            except KeyError:
                pass
        path_str = os.environ.get(PINNED_DATASET_PATH_ENV, "").strip()
        ds_path = Path(path_str) if path_str else DEFAULT_DATASET_PATH
        if ds_path.is_file():
            self._dataset_doc = json.loads(ds_path.read_text(encoding="utf-8"))
        return self._dataset_doc

    def score_request(self, payload: dict) -> tuple[int, dict]:
        """Run deterministic scoring inside the Function App using `pinned_scoring.scorer.score`.

        Supports:
        1. Explicit observations for one entity:
           `{"parcel": "P003", "as_of": "2026-07-15T12:00:00+00:00", "observations": [...]}`
        2. Stored dataset observations by parcel ID(s):
           `{"parcel": "P003"}` or `{"parcels": ["P001", "P003"]}`
        3. Optional `submitted_policy_sha256` to verify policy pinning before scoring.

        Never writes to Blob Storage; returns hash-verified `Receipt.wire()` objects.
        """
        if kill_switch_engaged():
            return 503, {"error": f"kill switch engaged ({os.environ.get('PINNED_KILL_SWITCH')})"}
        if not isinstance(payload, dict):
            return 400, {"error": "Request body must be a JSON object"}
        try:
            policy = self.get_pinned_policy()
        except (ContractError, ValueError) as exc:
            return 409, {"error": str(exc)}

        submitted_sha = str(payload.get("submitted_policy_sha256") or "").strip().lower()
        if submitted_sha and submitted_sha != policy.sha256.lower():
            return 409, {
                "status": "policy_mismatch",
                "error": f"submitted_policy_sha256 {submitted_sha} != pinned policy {policy.sha256}",
                "policy_sha256": policy.sha256,
                "receipt": {
                    "status": "policy_mismatch",
                    "score": None,
                    "band": None,
                    "policy_sha256": policy.sha256,
                },
            }

        weight_overrides = payload.get("weight_overrides") or payload.get("weightOverrides")
        is_counterfactual = bool(payload.get("counterfactual", False)) or bool(weight_overrides)
        if weight_overrides:
            if not isinstance(weight_overrides, dict):
                return 400, {"error": "'weight_overrides' must be a dict mapping factor name to weight"}
            try:
                raw_policy = policy.wire()
                for f in raw_policy.get("factors", []):
                    fname = f.get("name")
                    if fname in weight_overrides:
                        f["weight"] = str(weight_overrides[fname])
                policy = Policy.from_dict(raw_policy)
            except (ContractError, ValueError, TypeError) as exc:
                return 400, {"error": f"Invalid weight_overrides: {exc}"}

        raw_obs = payload.get("observations")
        if raw_obs is not None:
            if not isinstance(raw_obs, list):
                return 400, {"error": "'observations' must be a list of observation wire dicts"}
            entity_id = str(payload.get("parcel") or payload.get("entity_id") or "").strip()
            if not entity_id and raw_obs and isinstance(raw_obs[0], dict):
                entity_id = str(raw_obs[0].get("entity_id") or "").strip()
            if not entity_id or not _SAFE_ID.match(entity_id):
                return 400, {"error": "Valid 'parcel' (or 'entity_id') is required when scoring observations"}
            as_of_raw = payload.get("as_of")
            try:
                if as_of_raw:
                    as_of = datetime.fromisoformat(str(as_of_raw))
                else:
                    ds_doc = self.get_dataset_doc()
                    as_of = (
                        datetime.fromisoformat(ds_doc["as_of"])
                        if isinstance(ds_doc, dict) and "as_of" in ds_doc
                        else datetime.now(timezone.utc)
                    )
                obs_rows = [
                    Observation(
                        entity_id=str(item.get("entity_id") or entity_id),
                        metric=str(item["metric"]),
                        value=item["value"],
                        unit=str(item["unit"]),
                        observed_at=datetime.fromisoformat(str(item["observed_at"])),
                        valid_until=datetime.fromisoformat(str(item["valid_until"])),
                        provenance=item["provenance"],
                        quality=item.get("quality", "valid"),
                        source=str(item.get("source", "")),
                    )
                    for item in raw_obs
                ]
                receipt = score(entity_id, _normalise_observations(obs_rows, policy, as_of), policy, as_of)
                wire = receipt.wire()
                Receipt.from_wire(wire)
            except (KeyError, TypeError, ValueError, ContractError) as exc:
                return 400, {"error": f"Invalid observation input: {exc}"}
            return 200, {
                "counterfactual": is_counterfactual,
                "policy_sha256": policy.sha256,
                "receipt": wire,
            }

        parcels_req: list[str] = []
        score_all = False
        single_parcel = payload.get("parcel") or payload.get("entity_id")
        if isinstance(single_parcel, str) and single_parcel.strip():
            if single_parcel.strip().upper() == "ALL":
                score_all = True
            else:
                parcels_req.append(single_parcel.strip())
        if isinstance(payload.get("parcels"), list):
            if len(payload["parcels"]) == 0:
                score_all = True
            else:
                for p in payload["parcels"]:
                    if isinstance(p, str) and p.strip():
                        if p.strip().upper() == "ALL":
                            score_all = True
                        elif p.strip() not in parcels_req:
                            parcels_req.append(p.strip())
        if not parcels_req and not score_all:
            return 400, {"error": "Provide 'parcel', 'parcels', or 'observations' to score"}

        ds_doc = self.get_dataset_doc()
        if not isinstance(ds_doc, dict) or not isinstance(ds_doc.get("parcels"), dict):
            return 404, {"error": "No dataset observations available in Function host for parcel lookup"}
        ds_policy_sha = str(ds_doc.get("policy_sha256") or "").strip().lower()
        if ds_policy_sha and not is_counterfactual and ds_policy_sha != policy.sha256.lower():
            return 409, {
                "status": "policy_mismatch",
                "error": f"dataset policy_sha256 {ds_policy_sha} != pinned policy {policy.sha256}",
                "policy_sha256": policy.sha256,
            }
        as_of_str = str(payload.get("as_of") or ds_doc.get("as_of") or "")
        try:
            as_of = datetime.fromisoformat(as_of_str)
        except ValueError as exc:
            return 400, {"error": f"Invalid as_of timestamp {as_of_str!r}: {exc}"}

        parcels_map = ds_doc["parcels"]
        if score_all:
            parcels_req = sorted(parcels_map.keys())
            if not parcels_req:
                return 404, {"error": "Dataset contains no parcels to score"}
        scored_wires: list[dict] = []
        for pid in parcels_req:
            if not _SAFE_ID.match(pid):
                return 400, {"error": f"invalid parcel id {pid!r}"}
            if pid not in parcels_map:
                return 404, {"error": f"no observations for parcel {pid!r}"}
            try:
                obs_rows = [
                    Observation(
                        entity_id=str(item.get("entity_id") or pid),
                        metric=str(item["metric"]),
                        value=item["value"],
                        unit=str(item["unit"]),
                        observed_at=datetime.fromisoformat(str(item["observed_at"])),
                        valid_until=datetime.fromisoformat(str(item["valid_until"])),
                        provenance=item["provenance"],
                        quality=item.get("quality", "valid"),
                        source=str(item.get("source", "")),
                    )
                    for item in parcels_map[pid]
                ]
                receipt = score(pid, _normalise_observations(obs_rows, policy, as_of), policy, as_of)
                wire = receipt.wire()
                Receipt.from_wire(wire)
                scored_wires.append(wire)
            except (KeyError, TypeError, ValueError, ContractError) as exc:
                return 400, {"error": f"Failed scoring parcel {pid!r}: {exc}"}

        if len(scored_wires) == 1 and "parcels" not in payload and not score_all:
            return 200, {
                "counterfactual": is_counterfactual,
                "policy_sha256": policy.sha256,
                "receipt": scored_wires[0],
            }
        return 200, {
            "counterfactual": is_counterfactual,
            "policy_sha256": policy.sha256,
            "count": len(scored_wires),
            "receipts": scored_wires,
        }

    def verify_request(self, payload: dict) -> tuple[int, dict]:
        """Verify an agent answer against the Function App's authoritative receipt store using
        `pinned_scoring.verifier.verify_narration` (`agent.guarded_answer`), and return a
        deterministic receipt-based fallback whenever verification blocks the model's narration.

        This keeps narration verification and fallback outside the model's discretion and eliminates
        any need for the caller to run the scorer or verifier locally.
        """
        if kill_switch_engaged():
            return 503, {"error": f"kill switch engaged ({os.environ.get('PINNED_KILL_SWITCH')})"}
        if not isinstance(payload, dict):
            return 400, {"error": "Request body must be a JSON object"}
        question = str(payload.get("question") or "")
        raw_text = payload.get("answer") if "answer" in payload else payload.get("text")
        entity_id = payload.get("entity_id") or payload.get("parcel")
        if entity_id is not None:
            entity_id = str(entity_id).strip() or None
        mcp_trace = payload.get("mcp_trace") if isinstance(payload.get("mcp_trace"), list) else []

        try:
            policy = self.get_pinned_policy()
            policy_sha = policy.sha256
        except Exception as exc:  # noqa: BLE001
            return 409, {
                "blocked": True,
                "fallback_used": True,
                "raw_text": raw_text if isinstance(raw_text, str) else None,
                "text": "Policy hash mismatch; answer withheld.",
                "delivered": {
                    "status": "policy_mismatch",
                    "score": None,
                    "band": None,
                    "reasons": [],
                    "provenance": {},
                    "explanation": "Policy hash mismatch; answer withheld.",
                },
                "violations": [{"kind": "policy_mismatch", "detail": str(exc)}],
                "referenced_parcels": [],
                "receipt_hashes": {},
                "policy_sha256": None,
            }

        store = self.toolbox.store
        candidate_ids = extract_parcel_candidates(question, raw_text if isinstance(raw_text, str) else "", mcp_trace, entity_id)
        unknown_parcels: list[str] = []
        if entity_id and not _SAFE_ID.match(entity_id):
            unknown_parcels.append(entity_id)
        for pid in candidate_ids:
            try:
                store.get(pid)
            except KeyError:
                try:
                    wire = self.toolbox._reader.get_receipt(pid)
                    store.load(wire)
                except KeyError:
                    if pid not in unknown_parcels:
                        unknown_parcels.append(pid)

        verdict = agent_tools.guarded_answer(
            lambda _q, _ctx: raw_text,
            question,
            store,
            entity_id=entity_id if (entity_id and entity_id not in unknown_parcels) else None,
        )
        if unknown_parcels:
            extra_v = tuple(Violation("unknown_parcel", f"no receipt for {pid!r}") for pid in unknown_parcels)
            verdict = agent_tools.GuardedAnswer(True, None, (*verdict.violations, *extra_v))
        referenced: list[str] = []
        for pid in ([entity_id] if entity_id else []) + candidate_ids:
            if pid and pid not in referenced:
                try:
                    store.get(pid)
                    referenced.append(pid)
                except KeyError:
                    pass
        if not referenced and isinstance(raw_text, str):
            for pid in agent_tools._entities_in(raw_text, store):
                if pid not in referenced:
                    referenced.append(pid)

        receipts_used = [store.get(pid).wire() for pid in referenced]
        for r_wire in receipts_used:
            if r_wire.get("policy_sha256", "").lower() != policy_sha.lower():
                verdict = agent_tools.GuardedAnswer(
                    True,
                    None,
                    (*verdict.violations, Violation("policy_mismatch", f"receipt {r_wire['entity_id']} policy_sha256 != pinned {policy_sha}")),
                )
                break

        violations = [{"kind": v.kind, "detail": v.detail} for v in verdict.violations]
        receipt_hashes = {r["entity_id"]: r["receipt_sha256"] for r in receipts_used}
        if verdict.blocked:
            fallback_text, fallback_delivered = build_receipt_fallback(receipts_used, policy_sha=policy_sha)
            return 200, {
                "blocked": True,
                "fallback_used": True,
                "raw_text": raw_text if isinstance(raw_text, str) else None,
                "text": fallback_text,
                "delivered": fallback_delivered,
                "violations": violations,
                "referenced_parcels": [r["entity_id"] for r in receipts_used],
                "receipt_hashes": receipt_hashes,
                "policy_sha256": policy_sha,
            }

        primary = receipts_used[0] if receipts_used else None
        delivered = (
            {
                "mode": "verified_narration",
                "verified": True,
                "text": verdict.text,
                "entity_id": primary["entity_id"],
                "status": primary["status"],
                "score": primary["score"],
                "band": primary["band"],
                "reasons": list(primary["reasons"]),
                "provenance": {c[0]: c[2] for c in primary["components"]},
                "receipt_sha256": primary["receipt_sha256"],
                "policy_sha256": primary["policy_sha256"],
                "explanation": verdict.text,
            }
            if primary
            else {"mode": "verified_narration", "verified": True, "text": verdict.text, "explanation": verdict.text}
        )
        return 200, {
            "blocked": False,
            "fallback_used": False,
            "raw_text": raw_text,
            "text": verdict.text,
            "delivered": delivered,
            "violations": [],
            "referenced_parcels": [r["entity_id"] for r in receipts_used],
            "receipt_hashes": receipt_hashes,
            "policy_sha256": policy_sha,
        }

    def handle_rpc(self, payload: dict | list) -> tuple[int, dict | list | None]:
        """Process a JSON-RPC 2.0 request or batch. Returns `(http_status, response_body_or_None)`."""
        if isinstance(payload, list):
            if not payload:
                return 400, _rpc_error(None, -32600, "Invalid Request: empty batch")
            responses = []
            for item in payload:
                _, resp = self._handle_single(item)
                if resp is not None:
                    responses.append(resp)
            return (200, responses) if responses else (202, None)
        return self._handle_single(payload)

    def _handle_single(self, req: Any) -> tuple[int, dict | None]:
        if not isinstance(req, dict) or req.get("jsonrpc") != "2.0":
            return 400, _rpc_error(req.get("id") if isinstance(req, dict) else None, -32600, "Invalid Request")

        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params") or {}

        # JSON-RPC notifications have no "id" member
        if "id" not in req:
            return 202, None

        if not isinstance(method, str):
            return 400, _rpc_error(req_id, -32600, "Invalid Request: missing method")

        if method == "initialize":
            requested_version = params.get("protocolVersion") if isinstance(params, dict) else None
            version = requested_version if isinstance(requested_version, str) and requested_version else MCP_PROTOCOL_VERSION
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
                    "instructions": fa.INSTRUCTIONS,
                },
            }

        if method == "ping":
            return 200, {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "tools/list":
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": mcp_tool_schemas()},
            }

        if method == "tools/call":
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return 400, _rpc_error(req_id, -32602, "Invalid params: 'name' is required")
            name = params["name"]
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return 400, _rpc_error(req_id, -32602, "Invalid params: 'arguments' must be an object")
            allowed_names = {t["name"] for t in fa.TOOL_DEFINITIONS}
            if name not in allowed_names:
                return 200, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps({"error": f"unknown tool {name!r}"})}],
                        "isError": True,
                    },
                }
            result = self.toolbox.call(name, arguments)
            is_err = isinstance(result, dict) and "error" in result
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, default=str)}],
                    "structuredContent": result,
                    "isError": is_err,
                },
            }

        return 404, _rpc_error(req_id, -32601, f"Method not found: {method}")


def _rpc_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


_PARCEL_TOKEN_RE = re.compile(r"\b(P\d{3,10})\b", re.IGNORECASE)


def extract_parcel_candidates(
    question: str,
    answer: str,
    mcp_trace: list[dict] | None = None,
    explicit_entity_id: str | None = None,
) -> list[str]:
    """Extract ordered, deduplicated parcel IDs referenced in explicit args, MCP tool calls,
    question text, or answer text."""
    seen: list[str] = []

    def _add(val: Any) -> None:
        if isinstance(val, str):
            norm = val.strip()
            if _SAFE_ID.match(norm) and norm not in seen:
                seen.append(norm)

    if explicit_entity_id:
        _add(explicit_entity_id)

    for item in mcp_trace or []:
        if not isinstance(item, dict):
            continue
        raw_args = item.get("arguments")
        try:
            args_obj = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except Exception:  # noqa: BLE001
            args_obj = None
        if isinstance(args_obj, dict):
            for key in ("parcel", "parcel_id", "entity_id", "parcel_a", "parcel_b"):
                if key in args_obj:
                    _add(args_obj[key])
        raw_out = item.get("output")
        if isinstance(raw_out, str) and raw_out.strip():
            first_line = raw_out.splitlines()[0].strip()
            try:
                parsed = json.loads(first_line)
                if isinstance(parsed, dict):
                    if isinstance(parsed.get("receipt"), dict):
                        _add(parsed["receipt"].get("entity_id"))
                    if isinstance(parsed.get("entity_id"), str):
                        _add(parsed["entity_id"])
                    if isinstance(parsed.get("entities"), list):
                        for ent in parsed["entities"]:
                            _add(ent)
            except Exception:  # noqa: BLE001
                pass

    for text in (question or "", answer or ""):
        for m in _PARCEL_TOKEN_RE.finditer(text):
            _add(m.group(1).upper())

    return seen


def format_single_receipt_fallback(receipt_wire: dict) -> tuple[str, dict]:
    """Build the deterministic fail-safe text and structured payload from one verified receipt."""
    pid = receipt_wire["entity_id"]
    status = receipt_wire["status"]
    score_val = receipt_wire["score"]
    band = receipt_wire["band"]
    reasons = list(receipt_wire.get("reasons") or [])
    components = list(receipt_wire.get("components") or [])
    r_sha = receipt_wire["receipt_sha256"]
    p_sha = receipt_wire["policy_sha256"]
    prov = {c[0]: c[2] for c in components}

    if status == "scored":
        comp_parts = [f"{c[0]}={c[3]} ({c[2]})" for c in components]
        comp_str = ", ".join(comp_parts) if comp_parts else "none"
        text = (
            f"Verifier blocked the narration; showing receipt values. "
            f"Parcel {pid}: status={status}, score={score_val}, band={band}, "
            f"components=[{comp_str}], receipt_sha256={r_sha}."
        )
    else:
        reasons_str = ", ".join(reasons) if reasons else "none"
        text = (
            f"Verifier blocked the narration; showing receipt values. "
            f"Parcel {pid}: status={status}, score=null, band=null, "
            f"reasons=[{reasons_str}], receipt_sha256={r_sha}."
        )
    delivered = {
        "mode": "receipt_fallback",
        "reason": "verifier_blocked_narration",
        "text": text,
        "receipts": [receipt_wire],
        "entity_id": pid,
        "status": status,
        "score": score_val,
        "band": band,
        "reasons": reasons,
        "provenance": prov,
        "receipt_sha256": r_sha,
        "policy_sha256": p_sha,
        "explanation": "Verifier blocked the narration; showing receipt values.",
    }
    return text, delivered


def build_receipt_fallback(
    receipt_wires: list[dict],
    *,
    policy_sha: str | None = None,
) -> tuple[str, dict]:
    """Construct the deterministic receipt-based fallback when `verify_narration` blocks an answer."""
    if not receipt_wires:
        msg = "Verifier blocked the narration; no verified receipt was referenced for this answer."
        return msg, {
            "mode": "receipt_fallback",
            "reason": "verifier_blocked_narration",
            "text": msg,
            "receipts": [],
            "entity_id": None,
            "status": "no_receipt",
            "score": None,
            "band": None,
            "reasons": [],
            "provenance": {},
            "policy_sha256": policy_sha,
            "explanation": msg,
        }
    if len(receipt_wires) == 1:
        return format_single_receipt_fallback(receipt_wires[0])

    lines = ["Verifier blocked the narration; showing receipt values."]
    items = []
    for r in receipt_wires:
        single_text, single_deliv = format_single_receipt_fallback(r)
        # Strip the repeated prefix on multi-parcel summaries
        suffix = single_text.replace("Verifier blocked the narration; showing receipt values. ", "")
        lines.append(suffix)
        items.append(single_deliv)
    full_text = " ".join(lines)
    return full_text, {
        "mode": "receipt_fallback",
        "reason": "verifier_blocked_narration",
        "text": full_text,
        "receipts": list(receipt_wires),
        "entity_id": receipt_wires[0]["entity_id"],
        "status": receipt_wires[0]["status"],
        "score": receipt_wires[0]["score"],
        "band": receipt_wires[0]["band"],
        "reasons": list(receipt_wires[0].get("reasons") or []),
        "provenance": {c[0]: c[2] for c in receipt_wires[0].get("components") or []},
        "receipt_sha256": receipt_wires[0]["receipt_sha256"],
        "policy_sha256": receipt_wires[0]["policy_sha256"],
        "explanation": "Verifier blocked the narration; showing receipt values.",
        "parcels": items,
    }


def derive_function_base_url(
    function_endpoint: str | None = None,
    mcp_endpoint_url: str | None = None,
) -> str:
    """Resolve the Function App base URL (e.g. `https://<app>.azurewebsites.net`) from explicit args
    or `PINNED_FUNCTION_ENDPOINT`."""
    raw = (
        function_endpoint
        or mcp_endpoint_url
        or os.environ.get(FUNCTION_ENDPOINT_ENV, "")
    ).strip()
    if not raw:
        raise ValueError(
            f"Function endpoint is required (pass --function-endpoint or set {FUNCTION_ENDPOINT_ENV})"
        )
    norm = raw.rstrip("/")
    for suffix in ("/api/mcp", "/api/verify", "/api/score", "/api/health", "/api"):
        if norm.lower().endswith(suffix):
            norm = norm[: -len(suffix)].rstrip("/")
            break
    return norm


def acquire_function_bearer_token(
    scope: str | None = None,
    *,
    credential: Any = None,
) -> str:
    """Acquire an Entra ID bearer token for invoking the Function App HTTP endpoints (`/api/verify`,
    `/api/score`, `/api/mcp`). Defaults to `https://management.azure.com/.default`, which is in the
    Function App's default allowed Entra audiences."""
    resolved_scope = (
        scope
        or os.environ.get("PINNED_FUNCTION_TOKEN_SCOPE")
        or os.environ.get("PINNED_FUNCTION_SCOPE")
        or "https://management.azure.com/.default"
    ).strip()
    if resolved_scope and not resolved_scope.endswith("/.default"):
        resolved_scope = f"{resolved_scope.rstrip('/')}/.default"
    if credential is None:
        from azure.identity import DefaultAzureCredential

        client_id = os.environ.get(MANAGED_IDENTITY_CLIENT_ID_ENV, "").strip() or None
        default_cred = DefaultAzureCredential(
            managed_identity_client_id=client_id,
            process_timeout=60,
            exclude_developer_cli_credential=True,
        )
        try:
            return default_cred.get_token(resolved_scope).token
        except Exception:
            import subprocess

            resource = resolved_scope[:-len("/.default")] if resolved_scope.endswith("/.default") else resolved_scope
            token = subprocess.check_output(
                f"az account get-access-token --resource {resource} --query accessToken -o tsv",
                shell=True,
                text=True,
                timeout=60,
            ).strip()
            if token:
                return token
            raise
    tok = credential.get_token(resolved_scope)
    return tok.token


def call_function_json(
    base_url: str,
    route: str,
    payload: dict,
    *,
    bearer_token: str | None = None,
    token_provider: Callable[[], str] | None = None,
    http_post: Callable[[str, bytes, dict[str, str]], tuple[int, str]] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict:
    """Invoke a JSON POST endpoint (`/api/verify` or `/api/score`) on the Azure Function App."""
    clean_base = derive_function_base_url(base_url)
    clean_route = route if route.startswith("/") else f"/{route}"
    if not clean_route.startswith("/api/"):
        clean_route = f"/api{clean_route}"
    url = f"{clean_base}{clean_route}"
    body_bytes = json.dumps(payload, default=str).encode("utf-8")
    req_headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    has_auth = any(k.lower() == "authorization" for k in req_headers)
    token = bearer_token
    if token is None and token_provider is not None:
        token = token_provider()
    if (
        token is None
        and not has_auth
        and http_post is None
        and os.environ.get("PINNED_FUNCTION_SKIP_AUTH", "").strip().lower() not in ("1", "true", "yes")
    ):
        token = acquire_function_bearer_token()
    if token and not has_auth:
        req_headers["Authorization"] = f"Bearer {token}"

    if http_post is not None:
        status, resp_text = http_post(url, body_bytes, req_headers)
    else:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                resp_text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status = exc.code
            resp_text = exc.read().decode("utf-8", errors="replace")

    try:
        parsed = json.loads(resp_text) if resp_text.strip() else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Function endpoint {url} returned non-JSON (HTTP {status}): {resp_text!r}") from exc
    if status >= 400:
        err_msg = parsed.get("error") if isinstance(parsed, dict) and parsed.get("error") else resp_text
        raise RuntimeError(f"Function endpoint {url} failed with HTTP {status}: {err_msg}")
    return parsed
