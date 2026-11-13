"""Microsoft Foundry Agent Service agent that explains pinned-scoring receipts.

The supported cloud path uses four remote receipt MCP tools and mandatory Function-hosted
answer verification/fallback. Scores are never computed or changed by the model.
Local toolbox/turn helpers remain for offline tests and historical experiments, not as a
second supported cloud deployment architecture.
"""

import json
from pathlib import Path

from . import agent as agent_tools
from .receipts_store import DEFAULT_DIR, ReadOnlyReceiptStore

AGENT_NAME = "pinned-score-agent"
DEFAULT_MODEL = "gpt-4.1"
PROJECT_ENDPOINT_ENV = "FOUNDRY_PROJECT_ENDPOINT"
DEFAULT_PROJECT_ENDPOINT = "https://foundry.example.invalid/api/projects/example-project"
RECEIPTS_MCP_CONNECTION_ENV = "FOUNDRY_RECEIPTS_MCP_CONNECTION"
DEFAULT_RECEIPTS_MCP_CONNECTION = "pinnedscore-receipts-mcp-entraid"
RECEIPTS_MCP_SERVER_LABEL = "pinnedscore_receipts"
RECEIPTS_MCP_URL_ENV = "FOUNDRY_RECEIPTS_MCP_URL"
MAX_TOOL_ROUNDS = 8

INSTRUCTIONS = """You explain wildfire-risk score receipts for land parcels. Each parcel has a
verified, hash-pinned receipt produced by a deterministic scorer under a pinned policy.

Rules:
- Never compute, estimate, adjust or re-derive a score. Never offer to change one. Scores come
  only from receipts returned by your tools.
- Always call a tool before answering about a parcel; do not answer from memory.
- Quote scores, bands, statuses and component values exactly as the receipt states them.
- Cite provenance: say whether each input was measured, estimated or otherwise, and name the
  receipt (parcel id, and its receipt_sha256 when helpful).
- If a receipt is incomplete (no score), say it is not scored and explain the listed reasons
  using the exact reason prefix from the receipt (e.g., `unit:<metric>` means a unit mismatch,
  not a missing, expired, or invalid input; `missing:<metric>` means absent; `expired:<metric>`
  means expired; `invalid:<metric>` means invalid); do not guess a score.
- If a tool reports no receipt for a parcel, say so plainly.
- Keep answers short and factual."""

TOOL_DEFINITIONS = [
    {
        "type": "function", "name": "get_receipt",
        "description": "Return the verified score receipt for one parcel (score, band, status, "
                       "components with provenance, reasons, hashes).",
        "parameters": {"type": "object", "properties": {
            "parcel": {"type": "string", "description": "Parcel id, e.g. P003"}},
            "required": ["parcel"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "list_receipts",
        "description": "List receipt summaries (parcel, status, score, band), optionally filtered.",
        "parameters": {"type": "object", "properties": {
            "band": {"type": ["string", "null"], "description": "Band filter, e.g. high; null for any"},
            "status": {"type": ["string", "null"], "description": "scored or incomplete; null for any"}},
            "required": ["band", "status"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "list_reasons",
        "description": "Return the status and reasons (why a parcel is incomplete) for one parcel.",
        "parameters": {"type": "object", "properties": {
            "parcel": {"type": "string", "description": "Parcel id"}},
            "required": ["parcel"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "compare",
        "description": "Compare two parcels' stored receipts: scores, bands, and per-component differences.",
        "parameters": {"type": "object", "properties": {
            "parcel_a": {"type": "string"}, "parcel_b": {"type": "string"}},
            "required": ["parcel_a", "parcel_b"], "additionalProperties": False},
        "strict": True,
    },
]


class FoundryToolbox:
    """Executes the agent's function tools against a receipts directory (read-only)."""

    def __init__(self, directory=DEFAULT_DIR, reader=None):
        """`reader` (get_receipt/list_receipts) overrides the local directory, e.g. a Fabric
        GoldReceiptReader. Local files remain the default fallback."""
        self._reader = reader if reader is not None else ReadOnlyReceiptStore(Path(directory))
        self.store = agent_tools.ReceiptStore()
        for summary in self._reader.list_receipts():
            self.store.load(self._reader.get_receipt(summary["parcel"]))

    def call(self, name: str, arguments: dict):
        from .controls import kill_switch_engaged
        if kill_switch_engaged():
            return {"error": "kill switch engaged: tools are disabled"}
        try:
            if name == "get_receipt":
                return agent_tools.get_receipt(self.store, arguments["parcel"])
            if name == "list_receipts":
                return {"counterfactual": False, "receipts": self._reader.list_receipts(
                    arguments.get("band"), arguments.get("status"))}
            if name == "list_reasons":
                return agent_tools.list_reasons(self.store, arguments["parcel"])
            if name == "compare":
                return agent_tools.compare(self.store, arguments["parcel_a"], arguments["parcel_b"])
        except (KeyError, ValueError) as exc:
            return {"error": str(exc)}
        return {"error": f"unknown tool {name!r}"}


GRAPH_INSTRUCTIONS = INSTRUCTIONS + """

Decision scenarios (graph tools):
- graph_node, exposure_if_ignite, best_mitigation, deenergise_first and decision_sensitivity
  return figures computed by the scorer and graph code. Report them exactly; never add,
  average, round or recompute any number yourself.
- State whether a figure is a pinned baseline score or a counterfactual (counterfactual: true)
  and quote the policy_sha256 the tool returned.
- A feeder with critical facilities needs human sign-off; say so when needs_human_signoff is true."""


class GraphToolbox(FoundryToolbox):
    """FoundryToolbox plus read-only graph tools over a scenarios.Context."""

    def __init__(self, ctx, directory=DEFAULT_DIR, reader=None):
        super().__init__(directory, reader)
        from .graph_tools import GraphTools
        self._graph = GraphTools(ctx)

    def call(self, name: str, arguments: dict):
        from .graph_tools import GRAPH_TOOL_DEFINITIONS
        from .controls import kill_switch_engaged
        if kill_switch_engaged():
            return {"error": "kill switch engaged: tools are disabled"}
        if name in {t["name"] for t in GRAPH_TOOL_DEFINITIONS}:
            return self._graph.call(name, arguments)
        return super().call(name, arguments)


class AuthorityToolbox(GraphToolbox):
    """GraphToolbox plus propose_action, which writes a 'proposed' row to the Fabric decision log.
    The agent has no tool to review, approve or execute."""

    def __init__(self, ctx, log, directory=DEFAULT_DIR, reader=None):
        super().__init__(ctx, directory, reader)
        self._log = log

    def call(self, name: str, arguments: dict):
        if name != "propose_action":
            return super().call(name, arguments)
        from .authority import AuthorityError
        from .controls import kill_switch_engaged
        if kill_switch_engaged():
            return {"error": "kill switch engaged: tools are disabled"}
        try:
            entry = self._log.propose(self._reader.get_receipt(arguments["parcel"]),
                                      arguments["narration"])
        except (AuthorityError, KeyError) as exc:
            return {"error": str(exc)}
        return {"proposal_id": entry["proposal_id"], "state": entry["state"],
                "awaiting": "reviewer"}


DATA_SOURCE_ENV = "PINNED_DATA_SOURCE"
FABRIC_SERVER = "sql.example.invalid"
FABRIC_DATABASE = "pinned_scoring_lh"


def make_reader(source: str | None = None, directory=DEFAULT_DIR):
    """PINNED_DATA_SOURCE=fabric|local (default local). Fabric reads the Gold table over the SQL
    analytics endpoint with Entra auth; local reads the receipts directory."""
    import os
    source = (source or os.environ.get(DATA_SOURCE_ENV, "local")).lower()
    if source == "local":
        return ReadOnlyReceiptStore(Path(directory))
    if source == "fabric":
        from .fabric.access import GoldReader, GoldReceiptReader
        from .fabric.sql_endpoint import connect_auto, fetcher
        return GoldReceiptReader(GoldReader(fetcher(
            os.environ.get("FABRIC_SQL_SERVER", FABRIC_SERVER),
            os.environ.get("FABRIC_SQL_DATABASE", FABRIC_DATABASE), connect_fn=connect_auto)))
    raise ValueError(f"unknown data source {source!r} (use 'local' or 'fabric')")


def build_receipts_mcp_connection_body(
    mcp_endpoint_url: str,
    audience: str = "https://management.azure.com/",
    auth_type: str = "ProjectManagedIdentity",
) -> dict:
    """Build the ARM PUT body for the Foundry project connection pointing to the Azure Functions
    MCP receipt tool host (`RemoteTool`, Project Managed Identity Entra auth, `GenericProtocol`). No keys are stored."""
    return {
        "properties": {
            "category": "RemoteTool",
            "authType": auth_type,
            "target": mcp_endpoint_url,
            "audience": audience,
            "group": "GenericProtocol",
            "metadata": {"type": "azure_functions_mcp", "read_only": "true"},
            "isSharedToAll": False,
        }
    }


def build_receipts_mcp_tool(
    mcp_tool_cls,
    mcp_endpoint_url: str,
    connection_name: str = DEFAULT_RECEIPTS_MCP_CONNECTION,
    server_label: str = RECEIPTS_MCP_SERVER_LABEL,
):
    """Build a connection-based MCPTool referencing the Azure Functions MCP receipt tool host."""
    return mcp_tool_cls(
        server_label=server_label,
        server_url=mcp_endpoint_url,
        project_connection_id=connection_name,
        require_approval="never",
    )


def _dump_model_or_dict(obj) -> dict:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump_json"):
        return json.loads(obj.model_dump_json())
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    out = {}
    for k in ("name", "version", "definition", "type", "server_label", "server_url",
              "project_connection_id", "require_approval", "tools"):
        if hasattr(obj, k):
            val = getattr(obj, k)
            if isinstance(val, list):
                out[k] = [_dump_model_or_dict(x) for x in val]
            elif hasattr(val, "__dict__") or hasattr(val, "model_dump_json"):
                out[k] = _dump_model_or_dict(val)
            else:
                out[k] = val
    return out


def verify_agent_mcp_wiring(
    agent_version_obj,
    expected_server_url: str,
    expected_connection_name: str = DEFAULT_RECEIPTS_MCP_CONNECTION,
    expected_server_label: str = RECEIPTS_MCP_SERVER_LABEL,
    connection_body: dict | None = None,
    expected_auth_type: str | tuple[str, ...] = ("ProjectManagedIdentity", "UserEntraToken"),
) -> dict:
    """Verify that a deployed Foundry agent version and its MCP connection have exact expected wiring.

    Raises ValueError if the connection resource or the agent version's attached MCP tool definition
    is missing or has mismatched connection ID, server URL, label, or authentication type.
    """
    allowed_auth = (expected_auth_type,) if isinstance(expected_auth_type, str) else tuple(expected_auth_type)
    if connection_body is not None:
        conn_id = connection_body.get("id") or ""
        props = connection_body.get("properties") or {}
        if not conn_id or not str(conn_id).rstrip("/").endswith(f"/connections/{expected_connection_name}"):
            raise ValueError(
                f"MCP connection ID {conn_id!r} does not end with '/connections/{expected_connection_name}'"
            )
        if props.get("category") != "RemoteTool":
            raise ValueError(f"MCP connection category must be 'RemoteTool', got {props.get('category')!r}")
        if props.get("authType") not in allowed_auth:
            raise ValueError(
                f"MCP connection authType must be in {allowed_auth!r}, got {props.get('authType')!r}"
            )
        if (props.get("target") or "").rstrip("/") != expected_server_url.rstrip("/"):
            raise ValueError(
                f"MCP connection target {props.get('target')!r} does not match expected {expected_server_url!r}"
            )

    dumped = _dump_model_or_dict(agent_version_obj)
    defn = dumped.get("definition") or {}
    tools = defn.get("tools") or []
    mcp_tools = [t for t in tools if isinstance(t, dict) and t.get("type") == "mcp"]
    if not mcp_tools:
        raise ValueError(f"Deployed agent version has no attached 'mcp' tool in definition.tools: {tools!r}")

    matched = None
    for t in mcp_tools:
        if t.get("server_label") == expected_server_label:
            matched = t
            break
    if matched is None:
        raise ValueError(
            f"No attached MCP tool with server_label={expected_server_label!r}; found: {mcp_tools!r}"
        )
    if (matched.get("server_url") or "").rstrip("/") != expected_server_url.rstrip("/"):
        raise ValueError(
            f"Attached MCP tool server_url {matched.get('server_url')!r} != expected {expected_server_url!r}"
        )
    if matched.get("project_connection_id") != expected_connection_name:
        raise ValueError(
            f"Attached MCP tool project_connection_id {matched.get('project_connection_id')!r} != "
            f"expected {expected_connection_name!r}"
        )
    if matched.get("require_approval") != "never":
        raise ValueError(
            f"Attached MCP tool require_approval {matched.get('require_approval')!r} != 'never'"
        )
    return {
        "agent_name": dumped.get("name"),
        "agent_version": dumped.get("version"),
        "server_label": matched.get("server_label"),
        "server_url": matched.get("server_url"),
        "project_connection_id": matched.get("project_connection_id"),
        "require_approval": matched.get("require_approval"),
        "connection_id": (connection_body or {}).get("id"),
        "auth_type": ((connection_body or {}).get("properties") or {}).get("authType"),
    }


def validate_mcp_trace(
    trace: list[dict],
    expected_server_label: str | None = RECEIPTS_MCP_SERVER_LABEL,
) -> None:
    """Validate that a cloud agent response invoked at least one remote MCP tool cleanly."""
    mcp_calls = [item for item in trace if item.get("type") == "mcp_call"]
    if not mcp_calls:
        raise RuntimeError(
            f"Expected at least one remote 'mcp_call' in agent response trace, found none (trace={trace!r})"
        )
    for item in trace:
        if expected_server_label and item.get("server_label") != expected_server_label:
            raise RuntimeError(
                f"MCP item server_label {item.get('server_label')!r} != expected {expected_server_label!r}"
            )
        if item.get("error"):
            raise RuntimeError(f"MCP item {item.get('type')}/{item.get('name')} failed: {item.get('error')}")
    for call in mcp_calls:
        out = call.get("output")
        if out is None or str(out).strip() == "":
            raise RuntimeError(f"MCP tool call {call.get('name')!r} returned empty output")
        try:
            parsed = json.loads(out) if isinstance(out, str) else out
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get("error"):
            raise RuntimeError(f"MCP tool call {call.get('name')!r} returned error payload: {parsed['error']}")


def agent_definition(
    model: str = DEFAULT_MODEL,
    graph_tools: bool = False,
    authority: bool = False,
    mcp_endpoint_url: str | None = None,
    mcp_connection_name: str = DEFAULT_RECEIPTS_MCP_CONNECTION,
):
    from azure.ai.projects.models import PromptAgentDefinition
    if mcp_endpoint_url:
        from azure.ai.projects.models import MCPTool
        mcp_tool = build_receipts_mcp_tool(MCPTool, mcp_endpoint_url, mcp_connection_name)
        return PromptAgentDefinition(model=model, instructions=INSTRUCTIONS, tools=[mcp_tool])
    if authority:
        from .authority import AUTHORITY_INSTRUCTIONS, PROPOSE_TOOL
        from .graph_tools import GRAPH_TOOL_DEFINITIONS
        return PromptAgentDefinition(model=model, instructions=GRAPH_INSTRUCTIONS + AUTHORITY_INSTRUCTIONS,
                                     tools=TOOL_DEFINITIONS + GRAPH_TOOL_DEFINITIONS + [PROPOSE_TOOL])
    if graph_tools:
        from .graph_tools import GRAPH_TOOL_DEFINITIONS
        return PromptAgentDefinition(model=model, instructions=GRAPH_INSTRUCTIONS,
                                     tools=TOOL_DEFINITIONS + GRAPH_TOOL_DEFINITIONS)
    return PromptAgentDefinition(model=model, instructions=INSTRUCTIONS, tools=TOOL_DEFINITIONS)


AZURE_AI_USER_ROLE_ID = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
OPENAI_USER_ROLE_ID = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"


def _ensure_foundry_ai_user_role(error_text: str) -> str | None:
    """Best-effort assign 'Azure AI User' on the Foundry account and project when agents/write RBAC is missing."""
    import os
    import re
    import subprocess

    m = re.search(r"object id:\s*([0-9a-fA-F-]{36})", error_text or "", re.IGNORECASE)
    principal_id = m.group(1) if m else ""
    if not principal_id:
        try:
            principal_id = subprocess.check_output(
                "az ad signed-in-user show --query id -o tsv",
                shell=True,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:  # noqa: BLE001
            principal_id = ""
    if not principal_id:
        try:
            import jwt

            tok = subprocess.check_output(
                "az account get-access-token --resource https://management.azure.com/ --query accessToken -o tsv",
                shell=True,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            if tok:
                principal_id = str(jwt.decode(tok, options={"verify_signature": False}).get("oid") or "").strip()
        except Exception:  # noqa: BLE001
            principal_id = ""
    sub = (os.environ.get("AZURE_SUBSCRIPTION_ID") or "").strip()
    rg = (os.environ.get("FOUNDRY_RESOURCE_GROUP") or os.environ.get("AZURE_RESOURCE_GROUP") or "").strip()
    account = (os.environ.get("FOUNDRY_ACCOUNT_NAME") or "").strip()
    project_name = (os.environ.get("FOUNDRY_PROJECT_NAME") or "").strip()
    ep = (os.environ.get(PROJECT_ENDPOINT_ENV) or "").strip()
    if not account and "//" in ep and "." in ep.split("//", 1)[1]:
        account = ep.split("//", 1)[1].split(".", 1)[0]
    if not project_name and "/api/projects/" in ep:
        project_name = ep.rstrip("/").rsplit("/", 1)[1]
    if not (principal_id and sub and rg and account):
        return account or None
    account_scope = (
        f"/subscriptions/{sub}/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account}"
    )
    assignments = [
        (AZURE_AI_USER_ROLE_ID, account_scope),
        (OPENAI_USER_ROLE_ID, account_scope),
    ]
    if project_name:
        assignments.append((AZURE_AI_USER_ROLE_ID, f"{account_scope}/projects/{project_name}"))
    print(
        f"  -> Ensuring 'Azure AI User' role on Foundry account '{account}'"
        f"{f' and project {project_name!r}' if project_name else ''} for identity {principal_id}...",
        flush=True,
    )
    for role_id, scope in assignments:
        _create_role_assignment_tracked(principal_id, role_id, scope)
    return account


def _create_role_assignment_tracked(principal_id: str, role_id: str, scope: str) -> None:
    """Create one role assignment; when an ownership ledger is active, record it only if it did not exist before."""
    import json
    import os
    import subprocess

    shell = os.name == "nt"
    if not os.environ.get("PINNED_OWNERSHIP_LEDGER"):
        subprocess.run(
            ["az", "role", "assignment", "create", "--assignee-object-id", principal_id,
             "--assignee-principal-type", "User", "--role", role_id, "--scope", scope, "-o", "none"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=shell,
        )
        return
    from ownership_record import record_owned  # type: ignore[import-not-found]  # scripts/ is on sys.path for installer callers

    listed = subprocess.run(
        ["az", "role", "assignment", "list", "--assignee", principal_id, "--role", role_id,
         "--scope", scope, "--query", "[?scope=='" + scope + "'].id", "-o", "json"],
        capture_output=True, text=True, shell=shell,
    )
    if listed.returncode != 0:
        raise RuntimeError(f"Cannot determine whether role {role_id} exists on {scope}: {listed.stderr.strip()}")
    existing = json.loads(listed.stdout or "[]")
    if existing:
        record_owned("role_assignment", existing[0], "reused", "role assignment already existed before this install step")
        return
    created = subprocess.run(
        ["az", "role", "assignment", "create", "--assignee-object-id", principal_id,
         "--assignee-principal-type", "User", "--role", role_id, "--scope", scope, "-o", "json"],
        capture_output=True, text=True, shell=shell,
    )
    if created.returncode != 0:
        raise RuntimeError(f"az role assignment create failed for {role_id} on {scope}: {created.stderr.strip()}")
    assignment_id = json.loads(created.stdout)["id"]
    record_owned("role_assignment", assignment_id, "created", "az role assignment create after list found none")


def create_agent_version_with_rbac_retry(
    project,
    *,
    agent_name: str,
    definition,
    description: str,
    max_attempts: int = 36,
    sleep_seconds: float = 10.0,
):
    """Call project.agents.create_version with auto-assignment and retry for RBAC propagation."""
    import time

    role_ensured = False
    account_label = None
    for attempt in range(1, max_attempts + 1):
        try:
            return project.agents.create_version(
                agent_name=agent_name,
                definition=definition,
                description=description,
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "Microsoft.CognitiveServices/accounts/AIServices/agents/write" not in msg or attempt >= max_attempts:
                raise
            if not role_ensured:
                account_label = _ensure_foundry_ai_user_role(msg)
                role_ensured = True
            print(
                f"  -> Waiting for Azure AI User RBAC propagation on Foundry account "
                f"'{account_label or 'foundry'}' (attempt {attempt}/{max_attempts})...",
                flush=True,
            )
            time.sleep(sleep_seconds)


def create_or_update_agent(
    project,
    model: str = DEFAULT_MODEL,
    name: str = AGENT_NAME,
    graph_tools: bool = False,
    authority: bool = False,
    mcp_endpoint_url: str | None = None,
    mcp_connection_name: str = DEFAULT_RECEIPTS_MCP_CONNECTION,
):
    """Create the agent, or add a new version if it exists. Returns the agent version.

    When `mcp_endpoint_url` is provided, the agent is configured with a connection-based MCP tool
    pointing at the Azure Functions MCP tool host so Foundry calls the tools directly in the cloud
    with no local caller loop required.
    """
    return create_agent_version_with_rbac_retry(
        project,
        agent_name=name,
        definition=agent_definition(
            model,
            graph_tools,
            authority,
            mcp_endpoint_url=mcp_endpoint_url,
            mcp_connection_name=mcp_connection_name,
        ),
        description="Explains pinned-scoring receipts; never computes or changes scores.",
    )


def make_project_client(endpoint: str | None = None):
    import os
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential
    return AIProjectClient(
        endpoint=endpoint or os.environ.get(PROJECT_ENDPOINT_ENV, DEFAULT_PROJECT_ENDPOINT),
        credential=DefaultAzureCredential(
            process_timeout=60,
            exclude_developer_cli_credential=True,
        ))


def enable_tracing(console: bool = False) -> bool:
    """Optionally instrument agent runs with the Foundry SDK's OpenTelemetry instrumentor.

    Returns True if instrumentation was enabled, False if the SDK/OpenTelemetry pieces are not
    installed (tracing is optional and never required). With console=True, spans are printed to
    stdout (needs opentelemetry-sdk). No exporter to any hosted service is configured here.
    """
    try:
        from azure.ai.projects.telemetry import AIProjectInstrumentor
        if console:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
            provider = TracerProvider()
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            trace.set_tracer_provider(provider)
        AIProjectInstrumentor().instrument()
    except ImportError:
        return False
    return True


def run_agent_turn(openai_client, toolbox: FoundryToolbox, question: str,
                   agent_name: str = AGENT_NAME, trace: list | None = None,
                   meter=None, guard=None) -> str:
    """Send a question to the Foundry agent, executing function calls locally until it answers.

    Optional `meter` (controls.CostMeter) records token usage per response; optional `guard`
    (controls.BudgetGuard) aborts before each call once the spend cap is reached."""
    extra = {"agent_reference": {"name": agent_name, "type": "agent_reference"}}
    if guard:
        guard.check()
    response = openai_client.responses.create(input=question, extra_body=extra)
    if meter:
        meter.add(getattr(response, "usage", None))
    for _ in range(MAX_TOOL_ROUNDS):
        calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
        if not calls:
            return response.output_text
        outputs = []
        for call in calls:
            result = toolbox.call(call.name, json.loads(call.arguments or "{}"))
            if trace is not None:
                trace.append({"tool": call.name, "arguments": call.arguments})
            outputs.append({"type": "function_call_output", "call_id": call.call_id,
                            "output": json.dumps(result, default=str)})
        if guard:
            guard.check()
        response = openai_client.responses.create(
            input=outputs, previous_response_id=response.id, extra_body=extra)
        if meter:
            meter.add(getattr(response, "usage", None))
    raise RuntimeError(f"agent did not finish within {MAX_TOOL_ROUNDS} tool rounds")


def run_cloud_agent_turn(
    openai_client,
    question: str,
    agent_name: str = AGENT_NAME,
    trace: list | None = None,
    meter=None,
    guard=None,
) -> str:
    """Send a question to a cloud-hosted Foundry agent whose tools run via a Foundry MCP connection.

    No local `FoundryToolbox` or local `function_call` loop is executed; Foundry invokes the
    Azure Functions MCP tool host directly in the cloud. Raises `RuntimeError` if the agent emits
    an unhandled local `function_call`.
    """
    extra = {"agent_reference": {"name": agent_name, "type": "agent_reference"}}
    if guard:
        guard.check()
    response = openai_client.responses.create(input=question, extra_body=extra)
    if meter:
        meter.add(getattr(response, "usage", None))
    output_items = getattr(response, "output", None) or []
    local_calls = [item for item in output_items if getattr(item, "type", None) == "function_call"]
    if local_calls:
        names = [getattr(c, "name", "?") for c in local_calls]
        raise RuntimeError(
            f"cloud agent turn received local function_call(s) {names}; "
            "configure the agent with the cloud MCP connection (--mcp-url) so Foundry executes tools remotely"
        )
    if trace is not None:
        for item in output_items:
            item_type = getattr(item, "type", None)
            if item_type and "mcp" in str(item_type).lower():
                err = getattr(item, "error", None)
                if err is not None and hasattr(err, "model_dump_json"):
                    err = json.loads(err.model_dump_json())
                trace.append({
                    "type": str(item_type),
                    "name": getattr(item, "name", None),
                    "server_label": getattr(item, "server_label", None),
                    "arguments": getattr(item, "arguments", None),
                    "output": getattr(item, "output", None),
                    "error": err,
                })
    return getattr(response, "output_text", "") or ""


def verify_cloud_answer(
    question: str,
    answer: str,
    *,
    function_endpoint: str | None = None,
    entity_id: str | None = None,
    mcp_trace: list | None = None,
    bearer_token: str | None = None,
    token_provider=None,
    http_post=None,
    headers: dict[str, str] | None = None,
) -> dict:
    """Invoke the Azure Function App's `POST /api/verify` endpoint to verify `answer` server-side.

    Returns the parsed response dict from `/api/verify`, including:
    `blocked`, `fallback_used`, `text` (delivered text — fallback when blocked), `raw_text`,
    `delivered`, `violations`, `referenced_parcels`, `receipt_hashes`, and `policy_sha256`.
    Raises `RuntimeError` if the Function App returns an HTTP error, unreachable status, or a
    malformed verification payload lacking a valid `blocked`/`text`/`delivered` contract.
    Never substitutes unverified `answer` text when the server response is missing or malformed.
    """
    from .cloud_tool_host import call_function_json, derive_function_base_url

    base_url = derive_function_base_url(function_endpoint)
    req_payload: dict = {"question": question, "answer": answer}
    if entity_id:
        req_payload["entity_id"] = entity_id
    if mcp_trace is not None:
        req_payload["mcp_trace"] = mcp_trace
    resp = call_function_json(
        base_url,
        "/api/verify",
        req_payload,
        bearer_token=bearer_token,
        token_provider=token_provider,
        http_post=http_post,
        headers=headers,
    )
    if not isinstance(resp, dict):
        raise RuntimeError("Function /api/verify returned non-object response")
    if "blocked" not in resp or not isinstance(resp.get("blocked"), bool):
        raise RuntimeError("Function /api/verify response missing boolean 'blocked' field")
    delivered_text = resp.get("text")
    if not isinstance(delivered_text, str) or not delivered_text.strip():
        raise RuntimeError("Function /api/verify response missing non-empty delivered 'text' field")
    delivered_obj = resp.get("delivered")
    if not isinstance(delivered_obj, dict) or not isinstance(delivered_obj.get("mode"), str):
        raise RuntimeError("Function /api/verify response missing valid 'delivered' object")
    return resp


def score_cloud_parcels(
    payload: dict,
    *,
    function_endpoint: str | None = None,
    bearer_token: str | None = None,
    token_provider=None,
    http_post=None,
    headers: dict[str, str] | None = None,
) -> dict:
    """Invoke the Azure Function App's `POST /api/score` endpoint for deterministic cloud scoring."""
    from .cloud_tool_host import call_function_json, derive_function_base_url

    base_url = derive_function_base_url(function_endpoint)
    resp = call_function_json(
        base_url,
        "/api/score",
        payload,
        bearer_token=bearer_token,
        token_provider=token_provider,
        http_post=http_post,
        headers=headers,
    )
    if not isinstance(resp, dict) or ("receipt" not in resp and "receipts" not in resp):
        raise RuntimeError("Function /api/score response missing 'receipt'/'receipts' payload")
    return resp


def chat_cloud(
    openai_client,
    question: str,
    store: agent_tools.ReceiptStore | None = None,
    agent_name: str = AGENT_NAME,
    trace: list | None = None,
    *,
    function_endpoint: str | None = None,
    entity_id: str | None = None,
    bearer_token: str | None = None,
    token_provider=None,
    http_post=None,
    headers: dict[str, str] | None = None,
):
    """Ask the cloud MCP Foundry agent with zero local tool execution and mandatory verification.

    Requires either `function_endpoint` (or `PINNED_FUNCTION_ENDPOINT` in the environment) for the
    canonical Function-hosted `POST /api/verify` path, or an explicit `ReceiptStore` for offline
    unit tests. Raises `ValueError` before invoking the agent if neither verification target is
    configured — never returns an unverified answer by default.
    """
    import os

    from .cloud_tool_host import FUNCTION_ENDPOINT_ENV, derive_function_base_url

    raw_ep = (function_endpoint or os.environ.get(FUNCTION_ENDPOINT_ENV, "")).strip()
    resolved_fn = derive_function_base_url(raw_ep) if raw_ep else ""
    if not resolved_fn and store is None:
        raise ValueError(
            "chat_cloud requires function_endpoint (or PINNED_FUNCTION_ENDPOINT) for mandatory "
            "server-side verification, or an explicit ReceiptStore for offline testing."
        )

    trace_buf = trace if trace is not None else []
    raw_text = run_cloud_agent_turn(
        openai_client, question, agent_name=agent_name, trace=trace_buf
    )
    if resolved_fn:
        from .verifier import Violation

        ver = verify_cloud_answer(
            question,
            raw_text,
            function_endpoint=resolved_fn,
            entity_id=entity_id,
            mcp_trace=trace_buf,
            bearer_token=bearer_token,
            token_provider=token_provider,
            http_post=http_post,
            headers=headers,
        )
        violations = tuple(
            Violation(kind=v.get("kind", "unknown"), detail=v.get("detail", ""))
            for v in (ver.get("violations") or [])
            if isinstance(v, dict)
        )
        return agent_tools.GuardedAnswer(
            blocked=bool(ver["blocked"]),
            text=ver["text"],
            violations=violations,
        )

    if trace_buf:
        for item in trace_buf:
            out_str = item.get("output")
            if not out_str or not isinstance(out_str, str):
                continue
            first_line = out_str.splitlines()[0].strip()
            try:
                parsed = json.loads(first_line)
                if isinstance(parsed, dict) and isinstance(parsed.get("receipt"), dict):
                    store.load(parsed["receipt"])
            except Exception:  # noqa: BLE001
                pass
    return agent_tools.guarded_answer(lambda _q, _ctx: raw_text, question, store)


def chat(openai_client, toolbox: FoundryToolbox, question: str,
         agent_name: str = AGENT_NAME, trace: list | None = None):
    """Ask the agent and verify its answer against stored receipts. Returns a GuardedAnswer."""
    def llm(q, _context):
        return run_agent_turn(openai_client, toolbox, q, agent_name, trace)
    return agent_tools.guarded_answer(llm, question, toolbox.store)
