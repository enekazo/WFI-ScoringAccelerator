"""Connections, agent setup and model acceptance must remain separate operations."""

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import create_foundry_agent as receipts
import create_foundry_fabric_agent as fabric

ENDPOINT = "https://test.services.ai.azure.com/api/projects/test-project"


@pytest.fixture(autouse=True)
def _isolate_ownership_ledger(monkeypatch):
    monkeypatch.delenv("PINNED_OWNERSHIP_LEDGER", raising=False)


def forbidden(*args, **kwargs):
    raise AssertionError("This stage must not create agents or run model questions")


def test_receipt_connection_only_does_not_create_agent(monkeypatch):
    calls = []
    monkeypatch.setattr(receipts, "ensure_receipts_mcp_connection", lambda **kw: calls.append(kw) or {"body": {"id": "connection"}})
    monkeypatch.setattr(receipts.fa, "make_project_client", forbidden)
    assert receipts.main(["--endpoint", ENDPOINT, "--mcp-url", "https://function/api/mcp", "--connection-only"]) == 0
    assert len(calls) == 1


def arguments(tmp_path):
    return ["--endpoint", ENDPOINT, "--subscription", "subscription", "--resource-group", "rg",
            "--workspace-id", "workspace", "--ontology-id", "ontology", "--out", str(tmp_path / "result.json")]


def test_fabric_connection_only_does_not_create_agent_or_query(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(fabric, "put_connection", lambda url, body: calls.append((url, body)) or {
        "status": 200, "body": {"id": "connection", **body},
    })
    monkeypatch.setattr(fabric.fa, "make_project_client", forbidden)
    assert fabric.main([*arguments(tmp_path), "--connection-only"]) == 0
    assert len(calls) == 1
    assert "agent_version" not in json.loads((tmp_path / "result.json").read_text())


def test_fabric_setup_reuses_connection_and_never_asks_questions(monkeypatch, tmp_path):
    requests = []
    created = []
    body = {"id": "connection", **fabric.build_iq_connection_body("workspace", "ontology")}
    response = io.BytesIO(json.dumps(body).encode())
    response.status = 200
    monkeypatch.setattr(fabric, "_arm_token", lambda: "offline")
    monkeypatch.setattr(fabric.urllib.request, "urlopen", lambda req, **kw: requests.append(req) or response)
    monkeypatch.setattr(fabric, "put_connection", forbidden)
    version = SimpleNamespace(name="agent", version="1", model_dump_json=lambda: '{"definition":{"tools":[]}}')
    project = SimpleNamespace(
        agents=SimpleNamespace(create_version=lambda **kw: created.append(kw) or version),
        get_openai_client=forbidden,
    )
    monkeypatch.setattr(fabric.fa, "make_project_client", lambda endpoint: project)
    monkeypatch.setattr(fabric.fa, "verify_agent_mcp_wiring", lambda *a, **kw: {"ok": True})
    model = lambda **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setitem(sys.modules, "azure.ai.projects.models", SimpleNamespace(
        FabricDataAgentToolParameters=model, MCPTool=model, MicrosoftFabricPreviewTool=model,
        PromptAgentDefinition=model, ToolProjectConnection=model,
    ))
    assert fabric.main([*arguments(tmp_path), "--skip-connection-put", "--setup-only"]) == 0
    assert len(requests) == len(created) == 1
    assert requests[0].get_method() == "GET"
    saved = json.loads((tmp_path / "result.json").read_text())
    assert saved["agent_version"]["version"] == "1"
    assert "questions" not in saved


def test_fabric_setup_rejects_wrong_existing_connection(monkeypatch, tmp_path):
    response = io.BytesIO(json.dumps({"id": "connection", **fabric.build_iq_connection_body("wrong", "ontology")}).encode())
    response.status = 200
    monkeypatch.setattr(fabric, "_arm_token", lambda: "offline")
    monkeypatch.setattr(fabric.urllib.request, "urlopen", lambda *a, **kw: response)
    monkeypatch.setattr(fabric.fa, "make_project_client", forbidden)
    with pytest.raises(ValueError, match="different workspace/ontology"):
        fabric.main([*arguments(tmp_path), "--skip-connection-put", "--setup-only"])


@pytest.mark.parametrize("flags", [
    ["--connection-only", "--skip-connection-put"],
    ["--setup-only", "--question", "test"],
])
def test_fabric_rejects_conflicting_stage_flags(flags):
    with pytest.raises(SystemExit) as error:
        fabric.main(flags)
    assert error.value.code == 2


def test_create_agent_version_retries_after_assigning_azure_ai_user_role(monkeypatch):
    ensured = []
    attempts = []
    version = SimpleNamespace(name="pinned-score-agent", version="1")

    def fake_create_version(**kwargs):
        attempts.append(kwargs)
        if len(attempts) < 3:
            raise RuntimeError(
                "(UserError) Identity(object id: 00000000-0000-4000-8000-000000000029) does not have permissions "
                "for Microsoft.CognitiveServices/accounts/AIServices/agents/write actions."
            )
        return version

    monkeypatch.setattr(
        receipts.fa,
        "_ensure_foundry_ai_user_role",
        lambda msg: ensured.append(msg) or "wfi-pin2-foundry",
    )
    project = SimpleNamespace(agents=SimpleNamespace(create_version=fake_create_version))
    result = receipts.fa.create_agent_version_with_rbac_retry(
        project,
        agent_name="pinned-score-agent",
        definition={"model": "gpt-4o"},
        description="test",
        max_attempts=4,
        sleep_seconds=0.0,
    )
    assert result is version
    assert len(attempts) == 3
    assert len(ensured) == 1

