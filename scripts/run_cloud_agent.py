"""Cloud runner: send a prompt to the Foundry agent (`pinned-score-agent`), verify the narration
server-side on the Azure Function App (`POST /api/verify`), and print the verified answer or the
Function App's deterministic receipt fallback — with ZERO local tool execution, scoring, or verification.

In cloud deployments:
- The four read-only receipt tools (`get_receipt`, `list_receipts`, `list_reasons`, `compare`) run
  inside the Azure Functions MCP tool host (`cloud/tool_host/` -> `POST /api/mcp`) and are invoked
  directly by Foundry Agent Service via its Entra-backed MCP connection.
- Deterministic scoring (`POST /api/score`) and agent-answer narration verification with receipt
  fallback (`POST /api/verify`) also execute inside the Azure Function App.
- This caller script never executes `pinned_scoring.scorer` or `pinned_scoring.verifier`
  locally; before delivering any answer from the receipt agent (`pinned-score-agent`), it submits the
  raw narration to the Function App's `POST /api/verify` endpoint. Server-side verification on the
  receipt-agent delivery path is mandatory and cannot be bypassed; Fabric IQ ontology relationship
  queries use `scripts/create_foundry_fabric_agent.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pinned_scoring import foundry_agent as fa  # noqa: E402
from pinned_scoring.cloud_tool_host import (  # noqa: E402
    FUNCTION_ENDPOINT_ENV,
    derive_function_base_url,
)

DEFAULT_QUESTION = "Explain the wildfire risk score and component provenance for parcel P003."


def resolve_function_endpoint(
    function_endpoint: str | None = None,
    project_client=None,
    agent_name: str = fa.AGENT_NAME,
    expected_server_label: str | None = fa.RECEIPTS_MCP_SERVER_LABEL,
) -> str:
    """Resolve the Azure Function App base URL from argument, `PINNED_FUNCTION_ENDPOINT`, or the
    deployed Foundry agent's attached MCP tool `server_url`."""
    raw = (function_endpoint or os.environ.get(FUNCTION_ENDPOINT_ENV, "")).strip()
    if raw:
        return derive_function_base_url(raw)
    if project_client is not None:
        try:
            agent = project_client.agents.get(agent_name=agent_name)
            for tool in agent.versions.latest.definition.tools or []:
                server_url = getattr(tool, "server_url", None)
                if (
                    getattr(tool, "type", None) == "mcp"
                    and (not expected_server_label or getattr(tool, "server_label", None) == expected_server_label)
                    and server_url
                ):
                    derived = derive_function_base_url(server_url)
                    if derived:
                        return derived
        except (ValueError, RuntimeError, AttributeError) as exc:
            raise ValueError(
                "Could not resolve the Function endpoint from the agent MCP wiring; "
                "check the agent configuration or pass --function-endpoint."
            ) from exc
    raise ValueError(
        f"Azure Function endpoint URL is required for server-side verification/scoring. "
        f"Pass --function-endpoint or set ${FUNCTION_ENDPOINT_ENV}."
    )


def run_prompt(
    openai_client,
    question: str = DEFAULT_QUESTION,
    agent_name: str = fa.AGENT_NAME,
    *,
    function_endpoint: str | None = None,
    verify_on_server: bool = True,
    entity_id: str | None = None,
    require_mcp_call: bool = False,
    expected_server_label: str | None = fa.RECEIPTS_MCP_SERVER_LABEL,
    bearer_token: str | None = None,
    token_provider=None,
    http_post=None,
    function_headers: dict[str, str] | None = None,
) -> dict:
    """Execute one prompt against the cloud-hosted Foundry receipt agent with zero local tool
    execution and mandatory server-side verification on the Azure Function App (`POST /api/verify`).

    Raises `ValueError` before invoking the Foundry agent if `verify_on_server` is disabled or if no
    Function endpoint URL is available. Raises `RuntimeError` if `POST /api/verify` fails or returns
    a malformed response — never falls back to returning unverified `raw_text`.
    """
    if not verify_on_server:
        raise ValueError(
            f"Server-side narration verification via Function /api/verify is mandatory for receipt "
            f"agent {agent_name!r} and cannot be disabled on the cloud answer-delivery path. "
            f"For Fabric IQ ontology relationship queries, use scripts/create_foundry_fabric_agent.py."
        )
    raw_ep = (function_endpoint or os.environ.get(FUNCTION_ENDPOINT_ENV, "")).strip()
    if not raw_ep:
        raise ValueError(
            f"Azure Function endpoint URL is required before invoking {agent_name!r}. "
            f"Pass --function-endpoint or set ${FUNCTION_ENDPOINT_ENV}."
        )
    resolved_fn = derive_function_base_url(raw_ep)

    trace: list[dict] = []
    raw_text = fa.run_cloud_agent_turn(
        openai_client,
        question,
        agent_name=agent_name,
        trace=trace,
    )
    if require_mcp_call:
        fa.validate_mcp_trace(trace, expected_server_label=expected_server_label)

    ver = fa.verify_cloud_answer(
        question,
        raw_text,
        function_endpoint=resolved_fn,
        entity_id=entity_id,
        mcp_trace=trace,
        bearer_token=bearer_token,
        token_provider=token_provider,
        http_post=http_post,
        headers=function_headers,
    )
    return {
        "agent": agent_name,
        "question": question,
        "local_tool_execution": False,
        "local_scoring": False,
        "local_verification": False,
        "server_verified": True,
        "function_endpoint": resolved_fn,
        "mcp_trace": trace,
        "blocked": bool(ver["blocked"]),
        "fallback_used": bool(ver.get("fallback_used", False)),
        "raw_text": ver.get("raw_text", raw_text),
        "text": ver["text"],
        "delivered": ver["delivered"],
        "violations": ver.get("violations") or [],
        "referenced_parcels": ver.get("referenced_parcels") or [],
        "receipt_hashes": ver.get("receipt_hashes") or {},
        "policy_sha256": ver.get("policy_sha256"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("question", nargs="?", default=DEFAULT_QUESTION, help="Prompt to send to the Foundry receipt agent")
    ap.add_argument("--question", dest="question_flag", help="Prompt to send to the Foundry receipt agent (alias for positional question)")
    ap.add_argument("--endpoint", help=f"Foundry project endpoint (default ${fa.PROJECT_ENDPOINT_ENV})")
    ap.add_argument("--agent", default=fa.AGENT_NAME, help="Foundry receipt agent name (default pinned-score-agent)")
    ap.add_argument(
        "--function-endpoint",
        help=f"Azure Function App base URL or /api/mcp URL (default ${FUNCTION_ENDPOINT_ENV} or discovered from agent MCP tool)",
    )
    ap.add_argument("--entity-id", help="Optional parcel ID hint for server-side verification")
    ap.add_argument(
        "--verify",
        action="store_true",
        help="Require remote MCP tool call in addition to mandatory server-side narration verification via Function /api/verify",
    )
    ap.add_argument(
        "--score-parcel",
        help="Invoke Function App deterministic scorer (POST /api/score) for the given parcel ID (or 'ALL') and exit",
    )
    ap.add_argument("--require-mcp-call", action="store_true", help="Fail if no remote MCP tool call was executed")
    ap.add_argument("--expected-server-label", default=fa.RECEIPTS_MCP_SERVER_LABEL, help="Expected MCP server label")
    ap.add_argument("--json", action="store_true", help="Print structured JSON result")
    args = ap.parse_args(argv)
    if args.question_flag:
        args.question = args.question_flag
    if not args.endpoint and not (
        args.score_parcel and (args.function_endpoint or os.environ.get(FUNCTION_ENDPOINT_ENV))
    ):
        ap.error("Set --endpoint or FOUNDRY_PROJECT_ENDPOINT; the cloud runner does not use a historical project default.")

    if args.score_parcel:
        try:
            project = fa.make_project_client(args.endpoint) if not (args.function_endpoint or os.environ.get(FUNCTION_ENDPOINT_ENV)) else None
            fn_url = resolve_function_endpoint(
                args.function_endpoint,
                project_client=project,
                agent_name=args.agent,
                expected_server_label=args.expected_server_label,
            )
            target = args.score_parcel.strip()
            score_payload = {"parcels": []} if target.upper() == "ALL" else {"parcel": target}
            scored = fa.score_cloud_parcels(score_payload, function_endpoint=fn_url)
        except Exception as exc:  # noqa: BLE001
            print(f"Cloud scoring failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(scored, indent=2))
        return 0

    try:
        project = fa.make_project_client(args.endpoint)
        openai_client = project.get_openai_client()
        fn_url = resolve_function_endpoint(
            args.function_endpoint,
            project_client=project,
            agent_name=args.agent,
            expected_server_label=args.expected_server_label,
        )
        result = run_prompt(
            openai_client,
            question=args.question,
            agent_name=args.agent,
            function_endpoint=fn_url,
            verify_on_server=True,
            entity_id=args.entity_id,
            require_mcp_call=(args.require_mcp_call or args.verify),
            expected_server_label=args.expected_server_label,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Cloud agent run failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["blocked"]:
            print("BLOCKED by Function verifier — delivering deterministic receipt fallback:", file=sys.stderr)
            for v in result["violations"]:
                print(f"  - {v['kind']}: {v['detail']}", file=sys.stderr)
        print(result["text"])
    return 0 if not result["blocked"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
