"""Create or update the 'pinned-score-agent' in the Foundry project. See docs/foundry-agent.md.

When `--mcp-url` (or `FOUNDRY_RECEIPTS_MCP_URL`) is provided, creates/updates the Foundry project
`RemoteTool` connection pointing at the Azure Functions MCP receipt tool host and configures
`pinned-score-agent` to use that MCP connection directly (no local Python caller loop needed).
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from pinned_scoring import foundry_agent as fa  # noqa: E402


def resolve_project_agent_blueprint_audience(
    subscription: str,
    resource_group: str,
    account: str,
    project_name: str,
) -> str:
    """Resolve the Foundry project's built-in `agentIdentityBlueprintId` (`api://<appId>`) via ARM.

    Foundry Agent Service blocks sending tokens scoped to Microsoft first-party audiences
    (`https://management.azure.com/`) to custom MCP endpoints (`*.azurewebsites.net`). Using the
    Foundry project's tenant-scoped `agentIdentityBlueprintId` allows `ProjectManagedIdentity`
    to mint and send a valid Entra Bearer token to the Azure Functions MCP tool host.
    """
    import json
    import urllib.request
    from create_foundry_fabric_agent import _arm_token

    url = (
        f"https://management.azure.com/subscriptions/{subscription}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account}"
        f"/projects/{project_name}?api-version=2025-04-01-preview"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_arm_token()}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        blueprint_id = (
            ((payload.get("properties") or {}).get("agentIdentity") or {})
            .get("agentIdentityBlueprintId", "")
            .strip()
        )
        if blueprint_id:
            return f"api://{blueprint_id}"
    except Exception:  # noqa: BLE001
        pass
    return "https://management.azure.com/"


def ensure_receipts_mcp_connection(
    endpoint: str,
    mcp_url: str,
    connection_name: str,
    subscription: str | None = None,
    resource_group: str | None = None,
    audience: str = "auto",
) -> dict:
    """Create or update the Foundry project RemoteTool connection for the Azure Functions MCP tool host."""
    from create_foundry_fabric_agent import connection_url, put_connection

    sub = (
        subscription
        or os.environ.get("AZURE_SUBSCRIPTION_ID")
        or subprocess.check_output("az account show --query id -o tsv", shell=True, text=True).strip()
    )
    rg = (
        resource_group
        or os.environ.get("FOUNDRY_RESOURCE_GROUP")
        or os.environ.get("AZURE_RESOURCE_GROUP")
        or "example-resource-group"
    )
    account = endpoint.split("//")[1].split(".")[0]
    project_name = endpoint.rstrip("/").rsplit("/", 1)[1]
    resolved_audience = (
        resolve_project_agent_blueprint_audience(sub, rg, account, project_name)
        if not audience or audience.lower() == "auto"
        else audience
    )
    url = connection_url(sub, rg, account, project_name, name=connection_name, api_version="2025-10-01-preview")
    body = fa.build_receipts_mcp_connection_body(mcp_url, audience=resolved_audience)
    import ownership_record

    existed = ownership_record.arm_exists(url) if ownership_record.ledger_path() else None
    result = put_connection(url, body)
    rbody = result.get("body") if isinstance(result.get("body"), dict) else {}
    if rbody.get("id") and existed is not None:
        ownership_record.record_connection(url, existed, rbody["id"])
    return result


def sync_post_agent_blueprint_audience(
    endpoint: str,
    mcp_url: str,
    connection_name: str,
    subscription: str | None = None,
    resource_group: str | None = None,
) -> dict | None:
    """Refresh connection and Function App audience after first agent creation provisions agentIdentityBlueprintId."""
    try:
        sub = subscription or os.environ.get("AZURE_SUBSCRIPTION_ID")
        rg = resource_group or os.environ.get("FOUNDRY_RESOURCE_GROUP") or os.environ.get("AZURE_RESOURCE_GROUP")
        if not sub or not rg:
            return None
        account = endpoint.split("//")[1].split(".")[0]
        project_name = endpoint.rstrip("/").rsplit("/", 1)[1]
        resolved_audience = resolve_project_agent_blueprint_audience(sub, rg, account, project_name)
        if not resolved_audience.startswith("api://"):
            return None
        blueprint_id = resolved_audience[len("api://"):].strip()
        if not blueprint_id:
            return None
        conn = ensure_receipts_mcp_connection(
            endpoint=endpoint,
            mcp_url=mcp_url,
            connection_name=connection_name,
            subscription=sub,
            resource_group=rg,
            audience=resolved_audience,
        )
        host = mcp_url.split("//", 1)[-1].split("/", 1)[0]
        if host.endswith(".azurewebsites.net"):
            func_name = host.split(".", 1)[0]
            allowed = f"api://{blueprint_id},{blueprint_id},https://management.azure.com/,https://management.azure.com"
            subprocess.run(
                [
                    "az",
                    "functionapp",
                    "config",
                    "appsettings",
                    "set",
                    "--resource-group",
                    rg,
                    "--name",
                    func_name,
                    "--settings",
                    f"MCP_ALLOWED_AUDIENCES={allowed}",
                    "-o",
                    "none",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=(os.name == "nt"),
            )
        body = conn.get("body") if isinstance(conn.get("body"), dict) else None
        return body if body and body.get("id") else None
    except Exception:  # noqa: BLE001
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--endpoint", help=f"project endpoint (default ${fa.PROJECT_ENDPOINT_ENV} or built-in)")
    ap.add_argument("--model", default=fa.DEFAULT_MODEL)
    ap.add_argument("--name", default=fa.AGENT_NAME, help="agent name (default pinned-score-agent)")
    ap.add_argument(
        "--mcp-url",
        default=os.environ.get(fa.RECEIPTS_MCP_URL_ENV),
        help="Azure Functions MCP tool host URL (e.g. https://<func>.azurewebsites.net/api/mcp)",
    )
    ap.add_argument(
        "--mcp-connection",
        default=os.environ.get(fa.RECEIPTS_MCP_CONNECTION_ENV, fa.DEFAULT_RECEIPTS_MCP_CONNECTION),
        help="Foundry project connection name for the MCP receipt tool host",
    )
    ap.add_argument("--subscription", help="Azure subscription ID (when creating MCP connection)")
    ap.add_argument("--resource-group", help="Foundry resource group (when creating MCP connection)")
    ap.add_argument("--audience", default="auto", help="Entra audience for MCP connection (default: auto from Foundry project agentIdentityBlueprintId)")
    ap.add_argument("--skip-connection-put", action="store_true", help="Assume the MCP connection already exists")
    ap.add_argument("--connection-only", action="store_true", help="Create/update the MCP connection without creating an agent")
    a = ap.parse_args(argv)
    if a.connection_only and (not a.mcp_url or a.skip_connection_put):
        ap.error("--connection-only requires --mcp-url and cannot use --skip-connection-put")

    endpoint = a.endpoint or os.environ.get(fa.PROJECT_ENDPOINT_ENV, fa.DEFAULT_PROJECT_ENDPOINT)
    conn_body: dict | None = None
    if a.mcp_url and not a.skip_connection_put:
        conn = ensure_receipts_mcp_connection(
            endpoint=endpoint,
            mcp_url=a.mcp_url,
            connection_name=a.mcp_connection,
            subscription=a.subscription,
            resource_group=a.resource_group,
            audience=a.audience,
        )
        body = conn.get("body") if isinstance(conn.get("body"), dict) else {}
        if not body.get("id"):
            print(f"MCP connection PUT failed ({conn.get('status')}): {conn.get('body')}", file=sys.stderr)
            return 1
        conn_body = body
        print(f"mcp connection: {body.get('id')}")

    if a.connection_only:
        return 0
    project = fa.make_project_client(endpoint)
    version = fa.create_or_update_agent(
        project,
        a.model,
        name=a.name,
        mcp_endpoint_url=a.mcp_url,
        mcp_connection_name=a.mcp_connection,
    )
    import ownership_record

    ownership_record.record_agent_version(endpoint, version.name, version.version)
    if a.mcp_url and (not a.audience or a.audience.lower() == "auto"):
        synced_body = sync_post_agent_blueprint_audience(
            endpoint=endpoint,
            mcp_url=a.mcp_url,
            connection_name=a.mcp_connection,
            subscription=a.subscription,
            resource_group=a.resource_group,
        )
        if synced_body:
            conn_body = synced_body
    if a.mcp_url:
        try:
            wiring = fa.verify_agent_mcp_wiring(
                version,
                expected_server_url=a.mcp_url,
                expected_connection_name=a.mcp_connection,
                expected_server_label=fa.RECEIPTS_MCP_SERVER_LABEL,
                connection_body=conn_body,
            )
            print(f"verified mcp wiring: {wiring}")
        except ValueError as exc:
            print(f"MCP wiring verification failed: {exc}", file=sys.stderr)
            return 1
    mode_label = f"cloud MCP ({a.mcp_connection} -> {a.mcp_url})" if a.mcp_url else "local function tools"
    print(f"agent {version.name} version {version.version} (model {a.model}, tools: {mode_label})")
    print("agents in project:", ", ".join(x.name for x in project.agents.list()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
