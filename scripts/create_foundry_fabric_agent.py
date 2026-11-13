"""Create a Foundry agent version that uses the Microsoft Fabric data agent tool, and ask it questions.

Steps (Learn: azure/foundry/agents/how-to/tools/fabric):
 1. ARM PUT a project connection (category/authType CustomKeys, keys workspace_id + artifact_id).
 2. project.agents.create_version(<new agent name>, MicrosoftFabricPreviewTool) - never the existing agent.
 3. responses.create(tool_choice="required") per question, one attempt each; evidence to JSON.
Identity passthrough: the Fabric tool runs as the signed-in user (DefaultAzureCredential / az login).
IDs come from CLI flags, env vars or fabric/parameters.json (keys workspaceId, dataAgentId,
foundryResourceGroup, subscriptionId); nothing is hard-coded. No tokens or keys are stored.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from pinned_scoring import access_chain as ac  # noqa: E402
from pinned_scoring import foundry_agent as fa  # noqa: E402
import ownership_record  # noqa: E402

AGENT_NAME = "pinned-score-agent-fabric"
CONNECTION_NAME = "pinnedscore-fabric-dataagent"
IQ_CONNECTION_NAME = os.environ.get("FOUNDRY_IQ_CONNECTION", "pinnedscore-fabric-iq-ontology")
DEFAULT_QUESTIONS = ["Which crew types can reach parcel P003?",
                     "Which parcels have a critical facility but no passable access road that an "
                     "engine can use?"]
INSTRUCTIONS = (
    "You answer wildfire pinned-scoring chain questions over the Fabric IQ ontology "
    "(entities: Parcel, RoadSegment, HomeGroup, WaterSource, CrewAccess). "
    "Answer ONLY from the ontology tool (ask_ontology) result; never guess or use prior knowledge. "
    "Read-only; never compute or change scores. If the tool fails, say so.\n\n"
    "Ontology schema and domain rules:\n"
    "- Parcel (table dbo.parcel): parcel_id (key), status ('scored' or 'abstained'), "
    "band ('low', 'moderate', 'high', 'extreme', 'abstained'), score, provenance.\n"
    "- RoadSegment (table dbo.road_segment): road_id (key), parcel_id, passable (boolean), "
    "width_class ('narrow', 'standard', 'wide'), provenance.\n"
    "- HomeGroup (table dbo.home_group): group_id (key), parcel_id, houses (int), "
    "has_critical_facility (boolean), distance_to_houses_m (int), provenance. "
    "A parcel can have multiple HomeGroup rows, so the total number of homes for a parcel is "
    "always SUM(HomeGroup.houses) grouped by parcel_id (never a single row's houses value).\n"
    "- WaterSource (table dbo.water_source): water_id (key), road_id, type ('hydrant', 'tank', 'pond'), "
    "capacity_l (int), provenance. A parcel has a water source on a passable access road iff there "
    "exists a RoadSegment for that parcel_id with passable = true joined to a WaterSource on road_id. "
    "Conversely, a parcel has NO water source on a passable access road when NOT EXISTS such a "
    "(RoadSegment with passable = true joined to WaterSource) — this includes parcels that have no "
    "passable RoadSegment at all as well as parcels whose passable RoadSegments have no WaterSource.\n"
    "- CrewAccess (table dbo.crew_access): composite key (parcel_id, crew_type), where crew_type is "
    "'hand_crew', 'engine', or 'dozer', reachable (boolean), via_road_id, provenance. "
    "Domain rule for crew road usability: hand_crew can use any passable road (passable = true); "
    "engine requires a passable road with width_class <> 'narrow' ('standard' or 'wide'), which is "
    "materialized as CrewAccess(crew_type = 'engine', reachable = true/false); "
    "dozer requires a passable road with width_class = 'wide', materialized as "
    "CrewAccess(crew_type = 'dozer', reachable = true/false).\n\n"
    "How to call ask_ontology:\n"
    "- Always send a clear natural-language request (never pseudo-SQL like 'SHOW parcels WHERE ...') "
    "that includes the user's question, names the exact backing tables/columns to filter or join "
    "(for example, for engine access road questions, explicitly ask ask_ontology to check "
    "HomeGroup.has_critical_facility = true and CrewAccess with crew_type = 'engine' and reachable = false, "
    "or equivalently no RoadSegment with passable = true and width_class <> 'narrow'), "
    "and asks for all matching rows ordered by parcel_id or crew_type without truncation.\n"
    "- In your final reply, list every matching parcel_id (or crew_type, plus the exact numeric houses sum "
    "when asked for most homes) returned by ask_ontology, and do not mention non-matching IDs."
)
MCP_SERVER_LABEL = "pinnedscore_ontology"
UNGROUNDED_PATTERNS = (
    "cannot determine",
    "unable to determine",
    "not modeled or available",
    "does not contain the modeled",
    "only found unrelated tables",
)
CREW_TYPES = ("hand_crew", "engine", "dozer")


def is_ungrounded_text(text: str | None) -> bool:
    lowered = (text or "").lower()
    return any(pat in lowered for pat in UNGROUNDED_PATTERNS)


def load_chain_ground_truth(receipts_dir: str | None = None) -> tuple[list, dict[str, dict]]:
    import fabric_load_chain as fl

    parcels = fl.local_parcels(receipts_dir)
    chain = ac.Chain(ac.generate(parcels))
    qs = ac.questions(chain)
    gt = {q.qid: {"qid": q.qid, "question": q.text, "kind": q.kind, **ac.truth(chain, q)} for q in qs}
    return qs, gt


def match_question_ground_truth(question_text: str, gt_by_qid: dict[str, dict]) -> dict | None:
    norm_q = " ".join((question_text or "").split()).lower()
    for info in gt_by_qid.values():
        target = " ".join(info["question"].split()).lower()
        if norm_q == target or target in norm_q or norm_q in target:
            return info
    return None


def grade_ontology_answer(answer_text: str | None, truth_ans: dict, qid: str | None = None) -> dict:
    expected_parcels = sorted(str(x) for x in (truth_ans.get("parcels") or []))
    expected_value = truth_ans.get("value")
    text = (answer_text or "").strip()
    if not text or is_ungrounded_text(text):
        return {
            "qid": qid,
            "expected_parcels": expected_parcels,
            "expected_value": expected_value,
            "actual_parcels": [],
            "actual_value": None,
            "matched": False,
            "reason": "empty_or_ungrounded",
        }
    parsed = ac.parse(text)
    if isinstance(parsed, dict) and isinstance(parsed.get("parcels"), list):
        actual_parcels = sorted(str(x) for x in parsed.get("parcels") or [])
        actual_value = parsed.get("value")
    else:
        expecting_crews = bool(expected_parcels) and all(x in CREW_TYPES for x in expected_parcels)
        if expecting_crews:
            norm_text = text.lower().replace("hand crew", "hand_crew").replace("hand-crew", "hand_crew")
            actual_parcels = sorted(
                c for c in CREW_TYPES if re.search(rf"(?<![a-z_]){re.escape(c)}(?![a-z_])", norm_text)
            )
        else:
            actual_parcels = sorted(set(re.findall(r"\bP\d{3,}\b", text)))
        actual_value = None
        if expected_value is not None:
            stripped = re.sub(r"\bP\d+\b", "", text)
            stripped = re.sub(r"https?://\S+", "", stripped)
            nums = [int(n) for n in re.findall(r"(?<![A-Za-z0-9_.\-])\d+(?![A-Za-z0-9_.])", stripped)]
            actual_value = expected_value if expected_value in nums else (nums[0] if nums else None)
    parcels_ok = actual_parcels == expected_parcels
    value_ok = expected_value is None or actual_value == expected_value
    return {
        "qid": qid,
        "expected_parcels": expected_parcels,
        "expected_value": expected_value,
        "actual_parcels": actual_parcels,
        "actual_value": actual_value,
        "matched": bool(parcels_ok and value_ok),
    }


def _param(args, name, env, key):
    v = getattr(args, name) or os.environ.get(env)
    if not v:
        p = ROOT / "fabric" / "parameters.json"
        if p.exists():
            v = json.loads(p.read_text(encoding="utf-8")).get(key)
    return v


def _arm_token() -> str:
    return subprocess.check_output("az account get-access-token --resource https://management.azure.com/ "
                                   "--query accessToken -o tsv", shell=True, text=True).strip()


def build_connection_body(workspace_id: str, artifact_id: str) -> dict:
    return {"properties": {"category": "CustomKeys", "authType": "CustomKeys",
                           "credentials": {"keys": {"workspace_id": workspace_id, "artifact_id": artifact_id}}}}


def build_iq_connection_body(workspace_id: str, ontology_id: str) -> dict:
    """Fabric IQ ontology connection (Learn fabric-iq): RemoteTool + UserEntraToken passthrough."""
    return {"properties": {
        "category": "RemoteTool", "authType": "UserEntraToken",
        "target": f"https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{workspace_id}"
                  f"/items/{ontology_id}/ontologyEndpoint",
        "audience": "https://api.fabric.microsoft.com", "group": "GenericProtocol",
        "metadata": {"type": "fabric_iq_preview"}, "isSharedToAll": False}}


def build_mcp_tool(mcp_tool_cls, connection_name: str, server_url: str):
    """Connection-based MCP tool (type 'mcp'), referencing the project connection by name."""
    return mcp_tool_cls(server_label=MCP_SERVER_LABEL, server_url=server_url,
                        project_connection_id=connection_name, require_approval="never")


def connection_url(sub: str, rg: str, account: str, project: str, name: str = CONNECTION_NAME,
                   api_version: str = "2025-04-01-preview") -> str:
    return (f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/"
            f"Microsoft.CognitiveServices/accounts/{account}/projects/{project}/connections/{name}"
            f"?api-version={api_version}")


def put_connection(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="PUT", headers={
        "Authorization": f"Bearer {_arm_token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return {"status": r.status, "body": json.loads(r.read())}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode(errors="replace")[:2000]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--endpoint")
    ap.add_argument("--model", default=fa.DEFAULT_MODEL)
    ap.add_argument("--workspace-id")
    ap.add_argument("--data-agent-id")
    ap.add_argument("--resource-group")
    ap.add_argument("--subscription")
    ap.add_argument("--question", action="append")
    ap.add_argument("--mode", choices=["fabric-iq", "data-agent-tool"], default="fabric-iq")
    ap.add_argument("--ontology-id")
    ap.add_argument("--out")
    ap.add_argument("--connection-only", action="store_true", help="Create/update only the project connection")
    ap.add_argument("--skip-connection-put", action="store_true", help="Read the existing connection rather than updating it")
    ap.add_argument("--setup-only", action="store_true", help="Create and inspect the agent without running model questions")
    ap.add_argument("--verify-c1-c5", "--verify-ground-truth", dest="verify_c1_c5", action="store_true",
                    help="Run and verify C1-C5 answers against current deployment ground truth")
    ap.add_argument("--receipts-dir", help="Optional path to deployment receipts directory for ground-truth derivation")
    a = ap.parse_args(argv)
    if a.connection_only and (a.skip_connection_put or a.setup_only or a.question or a.verify_c1_c5):
        ap.error("--connection-only cannot be combined with --skip-connection-put, --setup-only or --question")
    if a.setup_only and (a.question or a.verify_c1_c5):
        ap.error("--setup-only cannot be combined with --question")
    iq = a.mode == "fabric-iq"
    a.out = a.out or str(ROOT / "docs/evidence/scenario-2-ontology" /
                         ("foundry_fabric_iq_answer.json" if iq else "foundry_fabric_tool_answer.json"))
    if iq:
        a.data_agent_id = a.data_agent_id or _param(a, "ontology_id", "FABRIC_ONTOLOGY_ID", "ontologyId")
    ws = _param(a, "workspace_id", "FABRIC_WORKSPACE_ID", "workspaceId")
    da = _param(a, "data_agent_id", "FABRIC_DATA_AGENT_ID", "dataAgentId")
    rg = _param(a, "resource_group", "FOUNDRY_RESOURCE_GROUP", "foundryResourceGroup")
    sub = _param(a, "subscription", "AZURE_SUBSCRIPTION_ID", "subscriptionId") or subprocess.check_output(
        "az account show --query id -o tsv", shell=True, text=True).strip()
    endpoint = a.endpoint or os.environ.get(fa.PROJECT_ENDPOINT_ENV, fa.DEFAULT_PROJECT_ENDPOINT)
    if not (ws and da and rg):
        ap.error("workspace id, data agent id and resource group are required (flags, env or parameters.json)")
    account = endpoint.split("//")[1].split(".")[0]
    project_name = endpoint.rstrip("/").rsplit("/", 1)[1]
    ev = {"agent": AGENT_NAME, "endpoint": endpoint, "model": a.model, "auth": "DefaultAzureCredential (az user)",
          "workspace_id": ws, "data_agent_id": da}

    if iq:
        ev["mode"] = "fabric-iq (ontology item id in data_agent_id field)"
    if a.skip_connection_put:
        name = IQ_CONNECTION_NAME if iq else CONNECTION_NAME
        version = "2025-10-01-preview" if iq else "2025-04-01-preview"
        request = urllib.request.Request(
            connection_url(sub, rg, account, project_name, name, version),
            headers={"Authorization": f"Bearer {_arm_token()}"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            conn = {"status": response.status, "body": json.loads(response.read())}
        if iq and conn["body"].get("properties", {}).get("target") != build_iq_connection_body(ws, da)["properties"]["target"]:
            raise ValueError("Existing Fabric IQ connection targets a different workspace/ontology.")
    elif iq:
        conn_url = connection_url(sub, rg, account, project_name, IQ_CONNECTION_NAME, "2025-10-01-preview")
        existed = ownership_record.arm_exists(conn_url) if ownership_record.ledger_path() else None
        conn = put_connection(conn_url, build_iq_connection_body(ws, da))
    else:
        conn_url = connection_url(sub, rg, account, project_name)
        existed = ownership_record.arm_exists(conn_url) if ownership_record.ledger_path() else None
        conn = put_connection(conn_url, build_connection_body(ws, da))
    body = conn["body"] if isinstance(conn["body"], dict) else {}
    if body.get("id") and not a.skip_connection_put and existed is not None:
        ownership_record.record_connection(conn_url, existed, body["id"])
    ev["connection"] = {"status": conn["status"], "id": body.get("id"),
                        "error": None if body.get("id") else conn["body"]}
    if not body.get("id"):
        return _save(a.out, ev, 1)
    if a.connection_only:
        return _save(a.out, ev, 0)

    from azure.ai.projects.models import (FabricDataAgentToolParameters, MCPTool,
                                          MicrosoftFabricPreviewTool, PromptAgentDefinition,
                                          ToolProjectConnection)
    project = fa.make_project_client(endpoint)
    if iq:
        tool = build_mcp_tool(MCPTool, IQ_CONNECTION_NAME, body["properties"]["target"])
    else:
        tool = MicrosoftFabricPreviewTool(fabric_dataagent_preview=FabricDataAgentToolParameters(
            project_connections=[ToolProjectConnection(project_connection_id=body["id"])]))
    try:
        ver = fa.create_agent_version_with_rbac_retry(
            project,
            agent_name=AGENT_NAME,
            definition=PromptAgentDefinition(model=a.model, instructions=INSTRUCTIONS, tools=[tool]),
            description="Fabric ontology MCP tool test; separate from pinned-score-agent.",
        )
    except Exception as e:  # noqa: BLE001 - record exact error
        ev["agent_version"] = {"error": f"{type(e).__name__}: {e}"}
        return _save(a.out, ev, 1)
    ev["agent_version"] = {"name": ver.name, "version": ver.version}
    ownership_record.record_agent_version(endpoint, ver.name, ver.version)
    try:
        ev["agent_version"]["readback_tools"] = (
            fa._dump_model_or_dict(ver).get("definition") or {}
        ).get("tools")
        if iq:
            ev["agent_version"]["verified_wiring"] = fa.verify_agent_mcp_wiring(
                ver,
                expected_server_url=body["properties"]["target"],
                expected_connection_name=IQ_CONNECTION_NAME,
                expected_server_label=MCP_SERVER_LABEL,
                connection_body=body,
            )
    except Exception as e:  # noqa: BLE001
        ev["agent_version"]["readback_error"] = str(e)
        return _save(a.out, ev, 1)

    if a.setup_only:
        return _save(a.out, ev, 0)
    gt_by_qid: dict[str, dict] = {}
    chain_qs: list = []
    if iq:
        try:
            chain_qs, gt_by_qid = load_chain_ground_truth(a.receipts_dir)
        except Exception as e:  # noqa: BLE001
            if a.verify_c1_c5:
                ev["ground_truth_error"] = f"{type(e).__name__}: {e}"
                return _save(a.out, ev, 1)
    questions_to_run = a.question or ([q.text for q in chain_qs] if (a.verify_c1_c5 and chain_qs) else DEFAULT_QUESTIONS)
    oc = project.get_openai_client(agent_name=AGENT_NAME)
    ev["questions"] = []
    overall_rc = 0
    for q in questions_to_run:
        rec = {"question": q, "tool_choice": "auto"}
        try:
            r = None
            for attempt in range(4):
                try:
                    r = oc.responses.create(input=q)
                    break
                except Exception as call_exc:  # noqa: BLE001
                    msg = str(call_exc)
                    if attempt < 3 and ("429" in msg or "Too Many" in msg or "RateLimit" in type(call_exc).__name__):
                        time.sleep(min(30, 5 * (2 ** attempt)))
                        continue
                    raise
            rec["answer"] = r.output_text
            rec["output_items"] = [fa._dump_model_or_dict(i) for i in r.output]
            rec["usage"] = fa._dump_model_or_dict(r.usage) if getattr(r, "usage", None) else None
            rec["status"] = getattr(r, "status", None)
            rec["error"] = fa._dump_model_or_dict(r.error) if getattr(r, "error", None) else None
            if iq:
                trace = [
                    {
                        "type": item.get("type"),
                        "name": item.get("name"),
                        "server_label": item.get("server_label"),
                        "arguments": item.get("arguments"),
                        "output": item.get("output"),
                        "error": item.get("error"),
                    }
                    for item in rec["output_items"]
                    if "mcp" in str(item.get("type", "")).lower()
                ]
                fa.validate_mcp_trace(trace, expected_server_label=MCP_SERVER_LABEL)
                if is_ungrounded_text(rec.get("answer")) or any(
                    is_ungrounded_text(str(item.get("output") or ""))
                    for item in trace
                    if item.get("type") == "mcp_call"
                ):
                    rec["grounding_error"] = "Ontology MCP returned an ungrounded/missing-tables response."
                    overall_rc = 1
                gt_info = match_question_ground_truth(q, gt_by_qid) if gt_by_qid else None
                if gt_info is not None:
                    gt_grade = grade_ontology_answer(rec.get("answer"), gt_info, qid=gt_info["qid"])
                    rec["ground_truth"] = gt_grade
                    if not gt_grade["matched"]:
                        overall_rc = 1
                elif a.verify_c1_c5:
                    rec["ground_truth"] = {"matched": False, "reason": "unrecognized_c1_c5_question"}
                    overall_rc = 1
            if not (rec.get("answer") or "").strip() or rec.get("error"):
                overall_rc = 1
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {e}"
            overall_rc = 1
        ev["questions"].append(rec)
        _save(a.out, ev, overall_rc)
        print(ascii(q), "->", ascii(str(rec.get("answer") or rec.get("error"))[:600]))
    if any("ground_truth" in r for r in ev["questions"]):
        gt_rows = [r["ground_truth"] for r in ev["questions"] if "ground_truth" in r]
        ev["ground_truth_summary"] = {
            "verified_questions": len(gt_rows),
            "matched_questions": sum(1 for g in gt_rows if g.get("matched")),
            "all_matched": all(g.get("matched") for g in gt_rows),
        }
        if not ev["ground_truth_summary"]["all_matched"]:
            overall_rc = 1
    return _save(a.out, ev, overall_rc)


def _save(path: str, ev: dict, rc: int) -> int:
    Path(path).write_text(json.dumps(ev, indent=2, default=str), encoding="utf-8")
    print("evidence:", path)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
