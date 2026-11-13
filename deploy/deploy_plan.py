"""Pure-logic validation, body builders, step ordering, and dry-run runner for deploy-all.ps1.

Used by:
- deploy/deploy-all.ps1 (for parameter validation, body generation, cost/capacity summary, and dry-run plan)
- tests/test_deploy_all.py (unit tests without cloud calls or Pester)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from create_foundry_fabric_agent import (  # noqa: E402
    INSTRUCTIONS,
    IQ_CONNECTION_NAME,
    MCP_SERVER_LABEL,
    build_iq_connection_body,
    build_mcp_tool,
    connection_url,
)
from pinned_scoring import foundry_agent as fa  # noqa: E402
from pinned_scoring.overrides import apply_overrides  # noqa: E402
from pinned_scoring.policy import Policy  # noqa: E402
from pinned_scoring.synth.generator import validate_parcel_count  # noqa: E402
from fabric_graph_model_fix import (  # noqa: E402
    COMPOSITE_KEY,
    MIN_CAPACITY_SKU_F,
    SKU_HOURLY_USD_ESTIMATE,
    parse_retry_after,
    patch_graph_definition_json,
    patch_graph_model_parts,
    patch_graph_type_json,
    validate_capacity_sku,
    verify_c1_c5_ground_truth,
    verify_composite_key_three_places,
)

GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
ZERO_GUID = "00000000-0000-0000-0000-000000000000"
PREFIX_RE = re.compile(r"^[a-z0-9]{3,10}$")

REQUIRED_PARAM_KEYS = (
    "subscriptionId",
    "tenantId",
    "location",
    "resourcePrefix",
    "foundryProjectName",
    "modelDeploymentName",
    "policyPath",
)

ORDERED_STEPS = (
    "preflight",
    "parameters",
    "azure_foundry_deploy",
    "fabric_workspace_lakehouse_load",
    "ontology_and_graph_model",
    "foundry_iq_connection",
    "foundry_iq_agent",
    "tests_and_verification",
    "cost_capacity_summary",
)

TEST_SUBSTEPS = (
    "pytest",
    "fabric_hash_check_configured_dataset",
    "gql_ground_truth_c1_to_c5",
    "agent_through_ontology_smoke",
    "function_runtime_acceptance",
    "cloud_mcp_agent_smoke",
)

EXPECTED_GROUND_TRUTH = {
    "C1": {"count": 14},
    "C2": {"count": 11},
    "C3": {"parcel": "P003", "unreachable_crews": ["dozer", "engine"]},
    "C4": {"parcel": "P018", "houses": 42},
    "C5": {"parcels": ["P020", "P031"]},
}


def validate_parameters(params: dict, allow_template_guids: bool = False, require_ontology: bool = True) -> dict:
    """Validate deployment parameters dict; returns {'ok': bool, 'errors': list[str], 'normalized': dict}."""
    errors: list[str] = []
    if not isinstance(params, dict):
        return {"ok": False, "errors": ["Parameters payload must be a JSON object."], "normalized": {}}

    for k in REQUIRED_PARAM_KEYS:
        if not params.get(k):
            errors.append(f"Parameter '{k}' is required.")

    for guid_key in ("subscriptionId", "tenantId"):
        val = str(params.get(guid_key, ""))
        if val:
            if not GUID_RE.match(val):
                errors.append(f"Parameter '{guid_key}' must be a valid GUID.")
            elif not allow_template_guids and val == ZERO_GUID:
                errors.append(f"Parameter '{guid_key}' must be your real GUID, not the template placeholder {ZERO_GUID}.")

    prefix = str(params.get("resourcePrefix", ""))
    if prefix and not PREFIX_RE.match(prefix):
        errors.append("Parameter 'resourcePrefix' must be 3-10 lowercase letters/digits.")

    try:
        validate_parcel_count(params.get("parcelCount", 150))
    except ValueError as exc:
        errors.append(str(exc))
    model_capacity = params.get("modelCapacity", 10)
    if isinstance(model_capacity, bool) or not isinstance(model_capacity, int) or model_capacity < 1:
        errors.append("Parameter 'modelCapacity' must be a positive integer.")

    sku = str(params.get("fabricCapacitySku") or "F4")
    sku_check = validate_capacity_sku(sku)
    if require_ontology and not sku_check["ok"]:
        errors.append(sku_check["reason"])

    ws_name = str(params.get("fabricWorkspaceName") or "PinnedScore").strip()
    lh_name = str(params.get("fabricLakehouseName") or "pinned_scoring_lh").strip()
    onto_name = str(params.get("fabricOntologyName") or "pinned_scoring_ontology").strip()
    cap_name = str(params.get("fabricCapacityName") or "").strip()
    if require_ontology and (not ws_name or ws_name.startswith("<")):
        errors.append("Parameter 'fabricWorkspaceName' must be set to a valid Fabric workspace name.")
    if require_ontology and not allow_template_guids and (not cap_name or cap_name.startswith("<")):
        errors.append("Parameter 'fabricCapacityName' is required.")

    policy_rel = str(params.get("policyPath") or "")
    policy_sha = None
    if policy_rel:
        policy_path = Path(policy_rel) if Path(policy_rel).is_absolute() else (ROOT / policy_rel)
        if not policy_path.exists():
            errors.append(f"Policy file not found: {policy_path}")
        else:
            overrides = params.get("weightOverrides", {})
            if not isinstance(overrides, dict):
                errors.append("Parameter 'weightOverrides' must be a JSON object.")
            else:
                try:
                    policy_sha = apply_overrides(Policy.load(policy_path), overrides).sha256
                except (ValueError, TypeError, OSError) as exc:
                    errors.append(f"Policy/weightOverrides validation failed: {exc}")

    explicit_rg = str(params.get("resourceGroupName") or "").strip()
    normalized = {
        **params,
        "parcelCount": params.get("parcelCount", 150),
        "modelCapacity": model_capacity,
        "policySha256": policy_sha,
        "fabricCapacityName": cap_name,
        "fabricCapacitySku": sku_check["sku"],
        "fabricWorkspaceName": ws_name,
        "fabricLakehouseName": lh_name,
        "fabricOntologyName": onto_name,
        "resourceGroupName": explicit_rg or (f"{prefix}-rg" if prefix else ""),
        "existingFoundryAccountName": str(params.get("existingFoundryAccountName") or "").strip(),
        "existingLogAnalyticsName": str(params.get("existingLogAnalyticsName") or "").strip(),
        "existingAppInsightsName": str(params.get("existingAppInsightsName") or "").strip(),
    }
    return {"ok": len(errors) == 0, "errors": errors, "normalized": normalized}


def build_foundry_iq_connection_spec(
    subscription_id: str,
    resource_group: str,
    foundry_account: str,
    foundry_project: str,
    workspace_id: str,
    ontology_id: str,
    connection_name: str = IQ_CONNECTION_NAME,
) -> dict:
    """Build the ARM PUT URL and body for the Foundry Fabric IQ connection.
    Enforces: RemoteTool, UserEntraToken, audience https://api.fabric.microsoft.com,
    group GenericProtocol, metadata.type fabric_iq_preview, isSharedToAll False.
    Never uses the deprecated Fabric data agent CustomKeys route."""
    body = build_iq_connection_body(workspace_id, ontology_id)
    props = body["properties"]
    assert props["category"] == "RemoteTool"
    assert props["authType"] == "UserEntraToken"
    assert props["audience"] == "https://api.fabric.microsoft.com"
    assert props["group"] == "GenericProtocol"
    assert props["metadata"] == {"type": "fabric_iq_preview"}
    assert props["isSharedToAll"] is False
    url = connection_url(
        subscription_id,
        resource_group,
        foundry_account,
        foundry_project,
        name=connection_name,
        api_version="2025-10-01-preview",
    )
    mcp_tool_spec = build_mcp_tool(dict, connection_name, props["target"])
    mcp_tool_spec["type"] = "mcp"
    return {
        "url": url,
        "body": body,
        "connection_name": connection_name,
        "agent_route": "fabric-iq",
        "mcp_tool": mcp_tool_spec,
        "agent_instructions": INSTRUCTIONS,
    }


def build_receipts_mcp_connection_spec(
    subscription_id: str,
    resource_group: str,
    foundry_account: str,
    foundry_project: str,
    mcp_endpoint_url: str,
    connection_name: str = fa.DEFAULT_RECEIPTS_MCP_CONNECTION,
) -> dict:
    """Build the ARM PUT URL and body for the Foundry project connection to the Azure Functions
    MCP receipt tool host (`RemoteTool`, Entra auth, `GenericProtocol`, read-only)."""
    body = fa.build_receipts_mcp_connection_body(mcp_endpoint_url)
    props = body["properties"]
    assert props["category"] == "RemoteTool"
    assert props["authType"] in ("ProjectManagedIdentity", "UserEntraToken")
    assert props["group"] == "GenericProtocol"
    assert props["isSharedToAll"] is False
    url = connection_url(
        subscription_id,
        resource_group,
        foundry_account,
        foundry_project,
        name=connection_name,
        api_version="2025-10-01-preview",
    )
    mcp_tool_spec = fa.build_receipts_mcp_tool(dict, mcp_endpoint_url, connection_name)
    mcp_tool_spec["type"] = "mcp"
    return {
        "url": url,
        "body": body,
        "connection_name": connection_name,
        "agent_name": fa.AGENT_NAME,
        "mcp_tool": mcp_tool_spec,
        "agent_instructions": fa.INSTRUCTIONS,
    }


def build_cost_and_capacity_summary(sku: str = "F4", model_name: str = "gpt-4o", model_capacity_tpm_k: int = 10) -> dict:
    """Build cost/capacity summary reminding the operator to scale down or pause capacity after testing."""
    sku_info = validate_capacity_sku(sku)
    hourly = sku_info["hourly_usd_estimate"] or 0.72
    return {
        "fabric_capacity_sku": sku_info["sku"],
        "fabric_hourly_usd_estimate": hourly,
        "fabric_daily_usd_estimate": round(hourly * 24, 2),
        "foundry_model": model_name,
        "foundry_capacity_k_tpm": model_capacity_tpm_k,
        "graph_refresh_mode": "manual_only (no schedule)",
        "portal_warning": "Do NOT re-save the graph from the ontology portal; doing so regenerates edges and reverts the composite-key fix.",
        "scale_down_reminder": (
            f"IMPORTANT: Fabric capacity ({sku_info['sku']}, ~${hourly:.2f}/hr) continues billing while Active. "
            "Scale capacity down (e.g. to F2) or pause it when tests complete, or run deploy\\uninstall.ps1."
        ),
    }


def build_execution_plan(params: dict, with_ontology: bool = True, skip_tests: bool = False, dry_run: bool = True) -> dict:
    """Return the deterministic step-by-step execution plan for deploy-all.ps1."""
    val = validate_parameters(params, allow_template_guids=dry_run, require_ontology=with_ontology)
    norm = val["normalized"]
    ws_id = norm.get("fabricWorkspaceId") or "<workspace-id>"
    onto_id = norm.get("fabricOntologyId") or "<ontology-id>"
    sub_id = norm.get("subscriptionId") or ZERO_GUID
    rg = norm.get("resourceGroupName") or f"{norm.get('resourcePrefix', 'wfipin')}-rg"
    acct = norm.get("existingFoundryAccountName") or f"{norm.get('resourcePrefix', 'wfipin')}-ai-<suffix>"
    proj = norm.get("foundryProjectName") or "pinned-scoring"

    conn_spec = build_foundry_iq_connection_spec(sub_id, rg, acct, proj, ws_id, onto_id)
    func_name = norm.get("functionAppName") or f"{norm.get('resourcePrefix', 'wfipin')}-mcp-<suffix>"
    func_mcp_url = f"https://{func_name}.azurewebsites.net/api/mcp"
    receipts_mcp_spec = build_receipts_mcp_connection_spec(sub_id, rg, acct, proj, func_mcp_url)
    cost_summary = build_cost_and_capacity_summary(
        sku=norm.get("fabricCapacitySku", "F4"),
        model_name=norm.get("modelName", "gpt-4o"),
    )

    steps = [
        {
            "id": "preflight",
            "order": 1,
            "description": f"Verify az login, Python SDKs, target workspace/capacity and installer minimum F{MIN_CAPACITY_SKU_F}; model region/version/quota require live confirmation",
            "min_capacity_sku": f"F{MIN_CAPACITY_SKU_F}",
        },
        {
            "id": "parameters",
            "order": 2,
            "description": "Validate private parameters, pin the resolved policy and prepare the matching dataset, receipts and Fabric inputs",
            "valid": val["ok"],
            "errors": val["errors"],
        },
        {
            "id": "azure_foundry_deploy",
            "order": 3,
            "description": (
                f"Deploy resource group '{rg}', receipts storage, Azure Functions MCP tool host '{func_name}', "
                f"Foundry account/project '{proj}', and model deployment '{norm.get('modelDeploymentName')}' via deploy\\deploy.ps1, "
                f"publish cloud\\tool_host with scoring/verification routes, and upload {norm.get('parcelCount')} receipts + _dataset.json + pinned policy "
                "to a dataset/policy-scoped Blob Storage prefix"
            ),
            "entry_point": "deploy/deploy.ps1",
            "function_app_name": func_name,
            "function_mcp_url": func_mcp_url,
            "tool_host_source": "cloud/tool_host",
        },
        {
            "id": "fabric_workspace_lakehouse_load",
            "order": 4,
            "description": (
                f"Resolve existing Fabric workspace '{norm.get('fabricWorkspaceName')}' or create it on capacity "
                f"'{norm.get('fabricCapacityName') or '<capacity-name>'}' ({norm.get('fabricCapacitySku')}) if absent, "
                f"ensure lakehouse '{norm.get('fabricLakehouseName')}', load medallion tables "
                "(raw -> silver -> gold, plus authority_decisions) and 5 synthetic chain tables"
            ),
            "entry_point": "fabric/provision.ps1",
            "scripts": ["scripts/fabric_load_graph.py", "scripts/fabric_load_chain.py"],
        },
        {
            "id": "ontology_and_graph_model",
            "order": 5,
            "enabled": bool(with_ontology),
            "description": (
                "Create/update Fabric IQ ontology (5 entities, 4 relationships) and GraphModel; "
                "set CrewAccess composite key ['parcel_id', 'crew_type'] in all 3 places "
                "(ontology entity, graphType.json node, graphDefinition.json edge); "
                "run MANUAL RefreshGraph only (never add a schedule; never re-save graph from portal)"
            ),
            "composite_key": COMPOSITE_KEY,
            "composite_key_places": [
                "ontology:entities/CrewAccess.tmdl",
                "graphModel:graphType.json(CrewAccess.primaryKeyProperties)",
                "graphModel:graphDefinition.json(crew_access.sourceNodeKeyColumns)",
            ],
            "refresh_mode": "manual_only",
            "schedule_created": False,
            "scripts": ["scripts/fabric_ontology_define_chain.py", "scripts/fabric_graph_model_fix.py"],
        },
        {
            "id": "foundry_iq_connection",
            "order": 6,
            "enabled": bool(with_ontology),
            "description": (
                "ARM PUT Foundry project connections: (a) Azure Functions MCP receipt tool host "
                f"('{receipts_mcp_spec['connection_name']}' -> {func_mcp_url}) and (b) Fabric IQ ontology "
                "(RemoteTool, UserEntraToken, audience https://api.fabric.microsoft.com, group GenericProtocol, "
                "metadata.type fabric_iq_preview, isSharedToAll false)"
            ),
            "connection": conn_spec,
            "receipts_mcp_connection": receipts_mcp_spec,
        },
        {
            "id": "foundry_iq_agent",
            "order": 7,
            "enabled": bool(with_ontology),
            "description": (
                "Create/update independent cloud Foundry agents: "
                f"(a) '{fa.AGENT_NAME}' wired to the Azure Functions MCP receipt tool host "
                f"({{'type': 'mcp', 'server_label': '{fa.RECEIPTS_MCP_SERVER_LABEL}', 'server_url': '{func_mcp_url}', "
                f"'require_approval': 'never', 'project_connection_id': '{receipts_mcp_spec['connection_name']}'}}) "
                "and (b) 'pinned-score-agent-fabric' with connection-based ontology MCP tool "
                f"({{'type': 'mcp', 'server_label': '{MCP_SERVER_LABEL}', 'server_url': '.../ontologyEndpoint', "
                f"'require_approval': 'never', 'project_connection_id': '{conn_spec['connection_name']}'}}); "
                "instructions answer only from ask_ontology and never mention or use the Fabric data agent"
            ),
            "agent_route": "fabric-iq",
            "mcp_tool": conn_spec["mcp_tool"],
            "receipts_mcp_tool": receipts_mcp_spec["mcp_tool"],
            "agent_instructions": INSTRUCTIONS,
            "uses_fabric_data_agent": False,
            "scripts": [
                "scripts/create_foundry_agent.py --mcp-url <functionAppEndpoint>",
                "scripts/create_foundry_fabric_agent.py --mode fabric-iq",
            ],
        },
        {
            "id": "tests_and_verification",
            "order": 8,
            "enabled": not skip_tests,
            "description": f"Run pytest, verify {norm.get('parcelCount')} Fabric Gold receipts, live GQL C1-C5, Function authentication/scoring/verifier acceptance and both cloud agents",
            "substeps": list(TEST_SUBSTEPS),
            "expected_ground_truth": "computed from the configured deployment dataset",
        },
        {
            "id": "cost_capacity_summary",
            "order": 9,
            "description": "Print deployment outputs, verification status, and cost/capacity reminder to scale down or pause Fabric capacity",
            "summary": cost_summary,
        },
    ]

    return {
        "dry_run": dry_run,
        "with_ontology": with_ontology,
        "skip_tests": skip_tests,
        "validation": val,
        "step_ids": [s["id"] for s in steps],
        "steps": steps,
        "cost_capacity_summary": cost_summary,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parameters-file", default=str(ROOT / "deploy" / "parameters.template.json"))
    ap.add_argument("--allow-template-guids", action="store_true")
    ap.add_argument("--without-ontology", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--mode", choices=["plan", "validate", "cost"], default="plan")
    args = ap.parse_args(argv)

    p_path = Path(args.parameters_file)
    if not p_path.exists():
        print(json.dumps({"ok": False, "errors": [f"Parameters file not found: {p_path}"]}), file=sys.stderr)
        return 1

    params = json.loads(p_path.read_text(encoding="utf-8"))
    with_ontology = not args.without_ontology

    if args.mode == "validate":
        res = validate_parameters(params, allow_template_guids=args.allow_template_guids, require_ontology=with_ontology)
        print(json.dumps(res, indent=2))
        return 0 if res["ok"] else 1

    if args.mode == "cost":
        print(json.dumps(build_cost_and_capacity_summary(
            sku=params.get("fabricCapacitySku", "F4"),
            model_name=params.get("modelName", "gpt-4o"),
        ), indent=2))
        return 0

    plan = build_execution_plan(
        params,
        with_ontology=with_ontology,
        skip_tests=args.skip_tests,
        dry_run=args.allow_template_guids,
    )
    print(json.dumps(plan, indent=2))
    return 0 if plan["validation"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
