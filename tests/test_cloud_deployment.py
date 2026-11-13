import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
sys.path.insert(0, str(ROOT / "scripts"))

import pin_policy
import run_cloud_agent
import verify_cloud_deployment as checks
from pinned_scoring import cloud_tool_host as cth, foundry_agent as fa
from pinned_scoring.policy import Policy


class Response(io.BytesIO):
    status = 200


def install_transport(tmp_path, monkeypatch, count=4, alter=None):
    params = tmp_path / "parameters.json"
    params.write_text(json.dumps({"parcelCount": count}))
    manifest = pin_policy.prepare_data(
        ROOT / "examples/wildfire/policy.json", params, tmp_path / "output"
    )
    host = cth.McpToolHost(
        fa.FoundryToolbox(reader=fa.make_reader(directory=manifest["receiptsPath"])),
        policy=Policy.load(manifest["policyPath"]),
        dataset_doc=json.loads(Path(manifest["datasetPath"]).read_text()),
    )
    health = {
        "source": "blob", "authRequired": True, "receiptsLoaded": count,
        "receiptsPrefix": manifest["receiptsPrefix"], "policySha256": manifest["policySha256"],
    }

    def request(req, timeout):
        if isinstance(req, str):
            route, status, result = "health", 200, dict(health)
        else:
            route = req.full_url.rsplit("/", 1)[-1]
            if not req.get_header("Authorization"):
                raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO())
            payload = json.loads(req.data)
            status, result = (
                host.score_request(payload) if route == "score" else host.verify_request(payload)
            )
        if alter:
            result = alter(route, result)
        if status != 200:
            raise urllib.error.HTTPError(
                "https://function.example", status, "Rejected", {},
                io.BytesIO(json.dumps(result).encode()),
            )
        return Response(json.dumps(result).encode())

    monkeypatch.setattr(urllib.request, "urlopen", request)
    monkeypatch.setattr(cth, "acquire_function_bearer_token", lambda: "offline-test-token")
    return manifest


@pytest.mark.parametrize("count", [1, 4, 150])
def test_current_dataset_and_both_verifier_outcomes(tmp_path, monkeypatch, count):
    manifest = install_transport(tmp_path, monkeypatch, count)
    result = checks.check_deployment("https://function.example/api/mcp", manifest)
    assert result["ok"] is True
    assert result["parcelCount"] == count
    assert result["verification"]["normal"]["blocked"] is False
    assert result["verification"]["contradiction"]["blocked"] is True


@pytest.mark.parametrize(
    ("route", "field", "value", "message"),
    [
        ("health", "source", "local", "Blob"),
        ("health", "authRequired", False, "authentication"),
        ("health", "receiptsLoaded", 50, "count"),
        ("health", "receiptsPrefix", "old/receipts", "namespace"),
        ("health", "policySha256", "0" * 64, "policy"),
        ("score", "count", 50, "coverage"),
        ("score", "receipts", [], "coverage"),
        ("verify", "blocked", True, "blocked"),
        ("verify", "receipt_hashes", {}, "provenance"),
    ],
)
def test_release_check_rejects_mismatched_runtime(tmp_path, monkeypatch, route, field, value, message):
    def alter(name, result):
        return {**result, field: value} if name == route else result

    manifest = install_transport(tmp_path, monkeypatch, alter=alter)
    with pytest.raises(ValueError, match=message):
        checks.check_deployment("https://function.example", manifest)


def test_failed_check_replaces_previous_success_report(tmp_path, monkeypatch):
    manifest = install_transport(tmp_path, monkeypatch, alter=lambda route, body: {})
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    output = tmp_path / "acceptance.json"
    output.write_text('{"ok": true}')
    assert checks.main([
        "--function-endpoint", "https://function.example",
        "--manifest", str(manifest_path), "--output", str(output),
    ]) == 1
    assert json.loads(output.read_text())["ok"] is False


def test_token_failure_never_falls_through_to_anonymous_http(monkeypatch):
    monkeypatch.delenv("PINNED_FUNCTION_SKIP_AUTH", raising=False)

    def failure():
        raise RuntimeError("Credential unavailable")

    def unexpected_http(*args, **kwargs):
        pytest.fail("HTTP must not run after credential acquisition fails")

    monkeypatch.setattr(cth, "acquire_function_bearer_token", failure)
    monkeypatch.setattr(urllib.request, "urlopen", unexpected_http)
    with pytest.raises(RuntimeError, match="Credential unavailable"):
        cth.call_function_json("https://function.example", "/api/score", {"parcels": []})


def test_endpoint_discovery_uses_the_installed_sdk_shape(monkeypatch):
    monkeypatch.delenv("PINNED_FUNCTION_ENDPOINT", raising=False)
    calls = []

    def get(*, agent_name):
        calls.append(agent_name)
        tool = SimpleNamespace(type="mcp", server_label=fa.RECEIPTS_MCP_SERVER_LABEL,
                               server_url="https://function.example/api/mcp")
        return SimpleNamespace(versions=SimpleNamespace(
            latest=SimpleNamespace(definition=SimpleNamespace(tools=[tool]))
        ))

    project = SimpleNamespace(agents=SimpleNamespace(get=get))
    assert run_cloud_agent.resolve_function_endpoint(project_client=project) == "https://function.example"
    assert calls == [fa.AGENT_NAME]


def test_cloud_cli_rejects_missing_project_instead_of_using_historical_default(monkeypatch):
    monkeypatch.delenv(fa.PROJECT_ENDPOINT_ENV, raising=False)
    with pytest.raises(SystemExit) as error:
        run_cloud_agent.main(["--function-endpoint", "https://function.example", "Explain P001."])
    assert error.value.code == 2
