"""Offline unit tests for the Azure Functions MCP receipt tool host (`src/pinned_scoring/cloud_tool_host.py`,
`cloud/tool_host/function_app.py`), cloud Foundry MCP connection/agent wiring, and `scripts/run_cloud_agent.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "cloud" / "tool_host"))
sys.path.insert(0, str(ROOT / "deploy"))

import function_app as func_app  # noqa: E402
import pin_policy  # noqa: E402
import run_cloud_agent  # noqa: E402
from pinned_scoring import agent as agent_tools  # noqa: E402
from pinned_scoring import cloud_tool_host as cth  # noqa: E402
from pinned_scoring import foundry_agent as fa  # noqa: E402

RECEIPTS_DIR = ROOT / "docs" / "evidence" / "receipts"
POLICY_PATH = ROOT / "examples" / "wildfire" / "policy.json"


@pytest.fixture(autouse=True)
def _clean_cloud_tool_host_env(monkeypatch):
    for var in (
        cth.STORAGE_ACCOUNT_ENV,
        cth.STORAGE_CONTAINER_ENV,
        cth.STORAGE_PREFIX_ENV,
        cth.PINNED_POLICY_SHA256_ENV,
        cth.PINNED_POLICY_PATH_ENV,
        cth.PINNED_DATASET_PATH_ENV,
        cth.FUNCTION_ENDPOINT_ENV,
        cth.ALLOWED_TENANT_ENV,
        cth.ALLOWED_AUDIENCES_ENV,
        cth.ALLOWED_CALLER_OIDS_ENV,
        cth.REQUIRE_AUTH_ENV,
    ):
        monkeypatch.delenv(var, raising=False)



class _FakeDownloader:
    def __init__(self, data: bytes):
        self._data = data

    def readall(self) -> bytes:
        return self._data


class _FakeBlobClient:
    def __init__(self, blobs: dict[str, bytes], name: str):
        self._blobs = blobs
        self._name = name

    def download_blob(self) -> _FakeDownloader:
        if self._name not in self._blobs:
            raise KeyError(f"BlobNotFound: {self._name}")
        return _FakeDownloader(self._blobs[self._name])


class _FakeContainerClient:
    def __init__(self, blobs: dict[str, bytes]):
        self._blobs = dict(blobs)

    def get_blob_client(self, name: str) -> _FakeBlobClient:
        return _FakeBlobClient(self._blobs, name)

    def list_blobs(self, name_starts_with: str | None = None):
        prefix = name_starts_with or ""
        return [
            SimpleNamespace(name=k)
            for k in sorted(self._blobs)
            if k.startswith(prefix)
        ]


def _make_fake_blob_reader() -> tuple[cth.BlobReceiptReader, dict[str, bytes]]:
    from pinned_scoring.synth.generator import generate, write_dataset

    blobs: dict[str, bytes] = {}
    for p in sorted(RECEIPTS_DIR.glob("P*.json"))[:5]:
        blobs[p.name] = p.read_bytes()
    blobs["smoke/20260417T000000Z.json"] = (RECEIPTS_DIR / "P001.json").read_bytes()
    blobs["_policy.json"] = POLICY_PATH.read_bytes()
    ds = generate(20260715)
    blobs["_dataset.json"] = json.dumps(
        {
            "seed": ds.seed,
            "as_of": ds.as_of.isoformat().replace("+00:00", "Z"),
            "parcels": {pid: [o.wire() for o in obs] for pid, obs in ds.observations.items()},
        },
        indent=2,
    ).encode("utf-8")
    container = _FakeContainerClient(blobs)
    reader = cth.BlobReceiptReader(container_client=container)
    return reader, blobs


def test_blob_receipt_reader_get_and_list_and_tamper_rejection():
    reader, blobs = _make_fake_blob_reader()
    rows = reader.list_receipts()
    assert [r["parcel"] for r in rows] == ["P001", "P002", "P003", "P004", "P005"]

    r1 = reader.get_receipt("P001")
    assert r1["entity_id"] == "P001"
    assert r1["status"] == "incomplete"
    assert r1["score"] is None
    assert len(r1["receipt_sha256"]) == 64

    r3 = reader.get_receipt("P003")
    assert r3["entity_id"] == "P003"
    assert r3["status"] == "scored"
    assert r3["score"] == "33.3"
    assert r3["band"] == "moderate"

    with pytest.raises(KeyError, match="no receipt for parcel 'P999'"):
        reader.get_receipt("P999")

    with pytest.raises(KeyError, match="invalid parcel id"):
        reader.get_receipt("../secret")

    # Tamper a blob in storage -> BlobReceiptReader must reject it via Receipt.from_wire
    tampered = json.loads(blobs["P001.json"].decode("utf-8"))
    tampered["score"] = "0.123456"
    container_tampered = _FakeContainerClient({**blobs, "P001.json": json.dumps(tampered).encode("utf-8")})
    tampered_reader = cth.BlobReceiptReader(container_client=container_tampered)
    with pytest.raises(ValueError, match="receipt hash mismatch"):
        tampered_reader.get_receipt("P001")


def test_mcp_tool_host_exposes_only_four_read_only_tools():
    schemas = cth.mcp_tool_schemas()
    names = [s["name"] for s in schemas]
    assert names == ["get_receipt", "list_receipts", "list_reasons", "compare"]
    assert "propose_action" not in names
    for s in schemas:
        assert s["annotations"]["readOnlyHint"] is True
        assert s["annotations"]["destructiveHint"] is False


def test_mcp_tool_host_jsonrpc_lifecycle_and_all_four_tools():
    reader, _ = _make_fake_blob_reader()
    tb = fa.FoundryToolbox(reader=reader)
    host = cth.McpToolHost(tb)

    # 1. initialize
    status, init_resp = host.handle_rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}
    )
    assert status == 200
    assert init_resp["result"]["protocolVersion"] == "2025-03-26"
    assert init_resp["result"]["serverInfo"]["name"] == cth.MCP_SERVER_NAME

    # 2. notification returns (202, None)
    status_notif, notif_resp = host.handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert status_notif == 202
    assert notif_resp is None

    # 3. ping
    status_ping, ping_resp = host.handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert status_ping == 200
    assert ping_resp == {"jsonrpc": "2.0", "id": 2, "result": {}}

    # 4. tools/list
    status_list, list_resp = host.handle_rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert status_list == 200
    tools = list_resp["result"]["tools"]
    assert [t["name"] for t in tools] == ["get_receipt", "list_receipts", "list_reasons", "compare"]

    # 5. tools/call: get_receipt
    _, call_get = host.handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_receipt", "arguments": {"parcel": "P003"}},
        }
    )
    assert call_get["result"]["isError"] is False
    p003 = call_get["result"]["structuredContent"]["receipt"]
    assert p003["entity_id"] == "P003"
    assert p003["score"] == "33.3"
    assert p003["band"] == "moderate"
    assert p003["receipt_sha256"] == json.loads(call_get["result"]["content"][0]["text"])["receipt"]["receipt_sha256"]

    # 6. tools/call: list_receipts
    _, call_list = host.handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "list_receipts", "arguments": {"band": "moderate"}},
        }
    )
    assert call_list["result"]["isError"] is False
    mod_rows = call_list["result"]["structuredContent"]["receipts"]
    assert any(r["parcel"] == "P003" for r in mod_rows)

    # 7. tools/call: list_reasons
    _, call_reasons = host.handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "list_reasons", "arguments": {"parcel": "P001"}},
        }
    )
    assert call_reasons["result"]["isError"] is False
    assert call_reasons["result"]["structuredContent"]["entity_id"] == "P001"
    assert "unit:fuel_moisture_pct" in call_reasons["result"]["structuredContent"]["reasons"]

    # 8. tools/call: compare
    _, call_cmp = host.handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "compare", "arguments": {"parcel_a": "P003", "parcel_b": "P004"}},
        }
    )
    assert call_cmp["result"]["isError"] is False
    cmp_data = call_cmp["result"]["structuredContent"]
    assert cmp_data["entities"] == ["P003", "P004"]
    assert cmp_data["counterfactual"] is False


def test_mcp_tool_host_rejects_propose_action_and_honours_kill_switch(monkeypatch):
    reader, _ = _make_fake_blob_reader()
    tb = fa.FoundryToolbox(reader=reader)
    host = cth.McpToolHost(tb)

    # Attempting to call propose_action or an unknown tool returns isError=True
    _, bad_call = host.handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "propose_action", "arguments": {"parcel_id": "P001"}},
        }
    )
    assert bad_call["result"]["isError"] is True
    assert "unknown tool" in json.loads(bad_call["result"]["content"][0]["text"])["error"]

    # Kill switch blocks tool calls
    monkeypatch.setenv("PINNED_KILL_SWITCH", "1")
    _, disabled_call = host.handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "get_receipt", "arguments": {"parcel": "P001"}},
        }
    )
    assert disabled_call["result"]["isError"] is True
    assert "kill switch" in json.loads(disabled_call["result"]["content"][0]["text"])["error"]


def test_function_app_http_handler_get_and_post(monkeypatch):
    monkeypatch.setenv(cth.REQUIRE_AUTH_ENV, "false")
    reader, _ = _make_fake_blob_reader()
    tb = fa.FoundryToolbox(reader=reader)
    host = cth.McpToolHost(tb)
    monkeypatch.setattr(func_app, "_HOST", host)

    status, headers, body_str = func_app.handle_http_request("GET", "/api/health")
    assert status == 200
    payload = json.loads(body_str)
    assert payload["status"] == "ok"
    assert payload["receiptsLoaded"] == 5
    assert payload["tools"] == ["get_receipt", "list_receipts", "list_reasons", "compare"]
    assert headers["Content-Type"] == "application/json"

    status_meta, _, body_meta = func_app.handle_http_request("GET", "/api/mcp")
    assert status_meta == 200
    assert json.loads(body_meta)["proposeActionExposed"] is False

    req_bytes = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "tools/list"}).encode("utf-8")
    status_post, _, body_post = func_app.handle_http_request("POST", "/api/mcp", req_bytes)
    assert status_post == 200
    assert [t["name"] for t in json.loads(body_post)["result"]["tools"]] == [
        "get_receipt",
        "list_receipts",
        "list_reasons",
        "compare",
    ]

    status_bad, _, body_bad = func_app.handle_http_request("POST", "/api/mcp", b"{not-json")
    assert status_bad == 400
    assert json.loads(body_bad)["error"]["code"] == -32700


def test_cloud_agent_definition_and_cloud_runner_require_zero_local_tools(monkeypatch):
    mcp_url = "https://wfipin-mcp-abc123.azurewebsites.net/api/mcp"
    conn_body = fa.build_receipts_mcp_connection_body(mcp_url)
    assert conn_body["properties"]["category"] == "RemoteTool"
    assert conn_body["properties"]["authType"] == "ProjectManagedIdentity"
    assert conn_body["properties"]["target"] == mcp_url
    assert conn_body["properties"]["isSharedToAll"] is False

    fake_models = SimpleNamespace(
        PromptAgentDefinition=lambda **kw: kw,
        MCPTool=lambda **kw: {"type": "mcp", **kw},
    )
    monkeypatch.setitem(sys.modules, "azure.ai.projects.models", fake_models)

    defn = fa.agent_definition(
        "gpt-4o",
        mcp_endpoint_url=mcp_url,
        mcp_connection_name="pinnedscore-receipts-mcp",
    )
    assert len(defn["tools"]) == 1
    assert defn["tools"][0] == {
        "type": "mcp",
        "server_label": "pinnedscore_receipts",
        "server_url": mcp_url,
        "require_approval": "never",
        "project_connection_id": "pinnedscore-receipts-mcp",
    }

    # Verify run_cloud_agent_turn executes a single server-side responses.create call with no local tool loop
    p003_wire = json.loads((RECEIPTS_DIR / "P003.json").read_text(encoding="utf-8"))
    mcp_call_item = SimpleNamespace(
        type="mcp_call",
        name="get_receipt",
        server_label="pinnedscore_receipts",
        arguments=json.dumps({"parcel": "P003"}),
    )
    fake_response = SimpleNamespace(
        output=[mcp_call_item],
        output_text=(
            f"Parcel P003 has score {p003_wire['score']} ({p003_wire['band']}) "
            f"with receipt_sha256 {p003_wire['receipt_sha256']}."
        ),
    )
    calls = []

    class _FakeOpenAI:
        class responses:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                return fake_response

    reader, _ = _make_fake_blob_reader()
    monkeypatch.setattr(func_app, "_HOST", cth.McpToolHost(fa.FoundryToolbox(reader=reader)))
    monkeypatch.setenv(cth.REQUIRE_AUTH_ENV, "false")
    _install_fake_urllib_for_function_app(monkeypatch)

    res = run_cloud_agent.run_prompt(
        _FakeOpenAI(),
        question="Get P003",
        function_endpoint="https://function.example.invalid",
    )
    assert len(calls) == 1
    assert res["local_tool_execution"] is False
    assert res["local_scoring"] is False
    assert res["local_verification"] is False
    assert res["server_verified"] is True
    assert res["blocked"] is False
    assert res["fallback_used"] is False
    assert len(res["mcp_trace"]) == 1
    assert res["mcp_trace"][0]["name"] == "get_receipt"

    # If the agent ever emitted a local function_call, run_cloud_agent_turn must fail closed
    bad_response = SimpleNamespace(
        output=[SimpleNamespace(type="function_call", name="get_receipt", arguments="{}")],
        output_text="",
    )

    class _FakeOpenAIBad:
        class responses:
            @staticmethod
            def create(**kwargs):
                return bad_response

    with pytest.raises(RuntimeError, match="local function_call"):
        fa.run_cloud_agent_turn(_FakeOpenAIBad(), "Get P001")


@pytest.mark.parametrize("auth_flag", [None, "", "invalid"])
@pytest.mark.parametrize(
    "path, payload",
    [
        ("/api/mcp", {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_receipt", "arguments": {"parcel": "P003"}},
        }),
        ("/api/score", {"parcel": "P003"}),
        ("/api/verify", {"question": "P003?", "answer": "Parcel P003 has score 33.3."}),
    ],
)
def test_function_endpoints_reject_missing_tenant_by_default(monkeypatch, auth_flag, path, payload):
    if auth_flag is not None:
        monkeypatch.setenv(cth.REQUIRE_AUTH_ENV, auth_flag)
    reader, _ = _make_fake_blob_reader()
    monkeypatch.setattr(func_app, "_HOST", cth.McpToolHost(fa.FoundryToolbox(reader=reader)))

    status, headers, body = func_app.handle_http_request(
        "POST", path, json.dumps(payload).encode("utf-8"),
    )

    assert status == 401
    assert "WWW-Authenticate" in headers
    error = json.loads(body)["error"]
    message = error["message"] if path == "/api/mcp" else error
    assert "MCP_ALLOWED_TENANT_ID is not configured" in message


def test_entra_jwt_authentication_enforcement_on_mcp_endpoint(monkeypatch):
    import time
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    tenant_id = "00000000-0000-4000-8000-000000000006"
    caller_oid = "00000000-0000-4000-8000-000000000029"
    now = int(time.time())

    def _mint(tid=tenant_id, oid=caller_oid, aud="https://management.azure.com/", key=private_key):
        claims = {
            "iss": f"https://sts.windows.net/{tid}/",
            "aud": aud,
            "tid": tid,
            "oid": oid,
            "iat": now - 10,
            "nbf": now - 10,
            "exp": now + 3600,
        }
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-kid"})

    # Test verify_entra_bearer_token directly with injected RSA public key
    ok, status, err, claims = cth.verify_entra_bearer_token(
        f"Bearer {_mint()}",
        allowed_tenant_id=tenant_id,
        signing_key_resolver=lambda tok, tid: public_key,
    )
    assert ok is True
    assert status == 200
    assert err == ""
    assert claims["oid"] == caller_oid

    # Wrong signature -> 401
    ok_bad_sig, status_bad_sig, _, _ = cth.verify_entra_bearer_token(
        f"Bearer {_mint(key=other_private_key)}",
        allowed_tenant_id=tenant_id,
        signing_key_resolver=lambda tok, tid: public_key,
    )
    assert ok_bad_sig is False
    assert status_bad_sig == 401

    # Wrong caller OID when MCP_ALLOWED_CALLER_OIDS is configured -> 403
    ok_bad_oid, status_bad_oid, err_bad_oid, _ = cth.verify_entra_bearer_token(
        f"Bearer {_mint(oid='00000000-0000-0000-0000-000000000099')}",
        allowed_tenant_id=tenant_id,
        allowed_caller_oids=[caller_oid],
        signing_key_resolver=lambda tok, tid: public_key,
    )
    assert ok_bad_oid is False
    assert status_bad_oid == 403
    assert "not in MCP_ALLOWED_CALLER_OIDS" in err_bad_oid

    # Now test handle_http_request with MCP_ALLOWED_TENANT_ID set
    reader, _ = _make_fake_blob_reader()
    monkeypatch.setattr(func_app, "_HOST", cth.McpToolHost(fa.FoundryToolbox(reader=reader)))
    monkeypatch.setenv("MCP_ALLOWED_TENANT_ID", tenant_id)
    monkeypatch.setattr(
        func_app,
        "verify_entra_bearer_token",
        lambda hdr: cth.verify_entra_bearer_token(
            hdr,
            allowed_tenant_id=tenant_id,
            signing_key_resolver=lambda tok, tid: public_key,
        ),
    )

    # /api/health remains 200 and reports authRequired=True
    h_status, _, h_body = func_app.handle_http_request("GET", "/api/health")
    assert h_status == 200
    assert json.loads(h_body)["authRequired"] is True
    assert json.loads(h_body)["allowedTenantId"] == tenant_id

    # /api/mcp, /api/score, and /api/verify without Authorization header -> 401
    for path, body_obj in [
        ("/api/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        ("/api/score", {"parcel": "P003"}),
        ("/api/verify", {"question": "P003?", "answer": "Parcel P003 has score 33.3."}),
    ]:
        u_status, u_headers, u_body = func_app.handle_http_request(
            "POST",
            path,
            json.dumps(body_obj).encode("utf-8"),
        )
        assert u_status == 401
        assert "WWW-Authenticate" in u_headers
        err_payload = json.loads(u_body)
        assert "error" in err_payload
        if path == "/api/mcp":
            assert err_payload["error"]["code"] == -32001
        else:
            assert err_payload["status"] == 401

    # /api/mcp, /api/score, and /api/verify with valid Bearer token -> 200
    a_status, _, a_body = func_app.handle_http_request(
        "POST",
        "/api/mcp",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode("utf-8"),
        {"Authorization": f"Bearer {_mint()}"},
    )
    assert a_status == 200
    assert [t["name"] for t in json.loads(a_body)["result"]["tools"]] == [
        "get_receipt",
        "list_receipts",
        "list_reasons",
        "compare",
    ]

    s_status, _, s_body = func_app.handle_http_request(
        "POST",
        "/api/score",
        json.dumps({"parcel": "P003"}).encode("utf-8"),
        {"Authorization": f"Bearer {_mint()}"},
    )
    assert s_status == 200
    assert json.loads(s_body)["receipt"]["entity_id"] == "P003"

    v_status, _, v_body = func_app.handle_http_request(
        "POST",
        "/api/verify",
        json.dumps({"question": "P003?", "answer": "Parcel P003 has score 33.3 (moderate)."}).encode("utf-8"),
        {"Authorization": f"Bearer {_mint()}"},
    )
    assert v_status == 200
    assert json.loads(v_body)["blocked"] is False



def test_package_func_bundles_tool_host_and_pinned_scoring(tmp_path):
    import zipfile

    zip_out = tmp_path / "tool_host.zip"
    pin_policy.package_func(str(ROOT), str(zip_out))
    assert zip_out.is_file()
    with zipfile.ZipFile(zip_out, "r") as zf:
        names = set(zf.namelist())
    assert "function_app.py" in names
    assert "host.json" in names
    assert "requirements.txt" in names
    assert "pinned_scoring/cloud_tool_host.py" in names
    assert "pinned_scoring/foundry_agent.py" in names
    assert "local.settings.json" not in names


def test_parse_allowed_audiences_expands_api_uri_and_guid():
    audiences = cth.parse_allowed_audiences("api://00000000-0000-4000-8000-000000000001")
    assert "api://00000000-0000-4000-8000-000000000001" in audiences
    assert "00000000-0000-4000-8000-000000000001" in audiences
    assert "https://management.azure.com/" in audiences


def test_function_hosted_score_endpoint_executes_deterministic_scorer_and_enforces_policy_pinning(monkeypatch):
    reader, _ = _make_fake_blob_reader()
    monkeypatch.setattr(func_app, "_HOST", cth.McpToolHost(fa.FoundryToolbox(reader=reader)))
    monkeypatch.setenv(cth.REQUIRE_AUTH_ENV, "false")

    p003_expected = json.loads((RECEIPTS_DIR / "P003.json").read_text(encoding="utf-8"))

    # 1. Score stored parcel P003 via POST /api/score -> must reproduce exact receipt SHA-256
    st, _, body = func_app.handle_http_request(
        "POST",
        "/api/score",
        json.dumps({"parcel": "P003"}).encode("utf-8"),
    )
    assert st == 200
    payload = json.loads(body)
    assert payload["counterfactual"] is False
    assert payload["policy_sha256"] == p003_expected["policy_sha256"]
    assert payload["receipt"]["entity_id"] == "P003"
    assert payload["receipt"]["score"] == "33.3"
    assert payload["receipt"]["band"] == "moderate"
    assert payload["receipt"]["receipt_sha256"] == p003_expected["receipt_sha256"]

    # 2. Score incomplete parcel P001 -> deterministic incomplete status, null score, unit reason
    st_inc, _, body_inc = func_app.handle_http_request(
        "POST",
        "/api/score",
        json.dumps({"parcel": "P001"}).encode("utf-8"),
    )
    assert st_inc == 200
    rec_inc = json.loads(body_inc)["receipt"]
    assert rec_inc["status"] == "incomplete"
    assert rec_inc["score"] is None
    assert "unit:fuel_moisture_pct" in rec_inc["reasons"]

    # 3. Counterfactual weight overrides run in the Function without mutating pinned policy
    st_cf, _, body_cf = func_app.handle_http_request(
        "POST",
        "/api/score",
        json.dumps({"parcel": "P003", "weight_overrides": {"fuel_load": "0.20", "wind": "0.20"}}).encode("utf-8"),
    )
    assert st_cf == 200
    cf_payload = json.loads(body_cf)
    assert cf_payload["counterfactual"] is True
    assert cf_payload["policy_sha256"] != p003_expected["policy_sha256"]

    # 4. Policy mismatch -> HTTP 409 policy_mismatch fail-closed
    st_mis, _, body_mis = func_app.handle_http_request(
        "POST",
        "/api/score",
        json.dumps({"parcel": "P003", "submitted_policy_sha256": "0" * 64}).encode("utf-8"),
    )
    assert st_mis == 409
    mis_payload = json.loads(body_mis)
    assert mis_payload["receipt"]["status"] == "policy_mismatch"

    # 5. Score ALL parcels (`{"parcels": []}` or `{"parcel": "ALL"}`) -> scores every parcel in _dataset.json
    st_all, _, body_all = func_app.handle_http_request(
        "POST",
        "/api/score",
        json.dumps({"parcels": []}).encode("utf-8"),
    )
    assert st_all == 200
    all_payload = json.loads(body_all)
    assert all_payload["count"] == 50
    assert len(all_payload["receipts"]) == 50
    assert {r["entity_id"] for r in all_payload["receipts"]}.issuperset({"P001", "P002", "P003"})

    st_all_kw, _, body_all_kw = func_app.handle_http_request(
        "POST",
        "/api/score",
        json.dumps({"parcel": "ALL"}).encode("utf-8"),
    )
    assert st_all_kw == 200
    assert json.loads(body_all_kw)["count"] == 50


def test_function_hosted_verify_endpoint_blocks_altered_answer_and_returns_receipt_fallback(monkeypatch):
    reader, _ = _make_fake_blob_reader()
    monkeypatch.setattr(func_app, "_HOST", cth.McpToolHost(fa.FoundryToolbox(reader=reader)))
    monkeypatch.setenv(cth.REQUIRE_AUTH_ENV, "false")

    p003_wire = json.loads((RECEIPTS_DIR / "P003.json").read_text(encoding="utf-8"))

    # 1. Valid narration passes server-side verification unchanged
    good_text = (
        f"Parcel P003 is scored at {p003_wire['score']} ({p003_wire['band']}) "
        f"with receipt_sha256 {p003_wire['receipt_sha256']}."
    )
    st_ok, _, body_ok = func_app.handle_http_request(
        "POST",
        "/api/verify",
        json.dumps({"question": "Score for P003?", "answer": good_text, "entity_id": "P003"}).encode("utf-8"),
    )
    assert st_ok == 200
    ver_ok = json.loads(body_ok)
    assert ver_ok["blocked"] is False
    assert ver_ok["fallback_used"] is False
    assert ver_ok["text"] == good_text
    assert ver_ok["delivered"]["verified"] is True
    assert ver_ok["delivered"]["text"] == good_text
    assert ver_ok["receipt_hashes"]["P003"] == p003_wire["receipt_sha256"]

    # 2. Altered/hallucinated score and band -> blocked on the server and replaced by receipt fallback
    altered_text = "Parcel P003 is in the critical band with a score of 91.5."
    st_bad, _, body_bad = func_app.handle_http_request(
        "POST",
        "/api/verify",
        json.dumps({"question": "Score for P003?", "answer": altered_text, "entity_id": "P003"}).encode("utf-8"),
    )
    assert st_bad == 200
    ver_bad = json.loads(body_bad)
    assert ver_bad["blocked"] is True
    assert ver_bad["fallback_used"] is True
    assert ver_bad["raw_text"] == altered_text
    assert ver_bad["text"] != altered_text
    assert "Verifier blocked the narration; showing receipt values." in ver_bad["text"]
    assert "score=33.3" in ver_bad["text"]
    assert "band=moderate" in ver_bad["text"]
    assert ver_bad["delivered"]["mode"] == "receipt_fallback"
    assert ver_bad["delivered"]["receipts"][0]["receipt_sha256"] == p003_wire["receipt_sha256"]
    kinds = {v["kind"] for v in ver_bad["violations"]}
    assert "score_mismatch" in kinds
    assert "band_mismatch" in kinds

    # 3. Unknown parcel referenced (inferred without entity_id) -> fail-closed on the server
    st_unk, _, body_unk = func_app.handle_http_request(
        "POST",
        "/api/verify",
        json.dumps({"question": "What about P999?", "answer": "Parcel P999 has score 10.0."}).encode("utf-8"),
    )
    assert st_unk == 200
    ver_unk = json.loads(body_unk)
    assert ver_unk["blocked"] is True
    assert ver_unk["fallback_used"] is True
    assert any(v["kind"] == "unknown_parcel" for v in ver_unk["violations"])

    # 4. Explicit unknown entity_id (e.g. P999999) -> fail-closed HTTP 200 with structured no_receipt fallback (never HTTP 500 KeyError)
    st_exp_unk, _, body_exp_unk = func_app.handle_http_request(
        "POST",
        "/api/verify",
        json.dumps({
            "question": "Explain parcel P999999.",
            "answer": "Parcel P999999 has score 50.",
            "entity_id": "P999999",
        }).encode("utf-8"),
    )
    assert st_exp_unk == 200
    ver_exp_unk = json.loads(body_exp_unk)
    assert ver_exp_unk["blocked"] is True
    assert ver_exp_unk["fallback_used"] is True
    assert ver_exp_unk["delivered"]["mode"] == "receipt_fallback"
    assert ver_exp_unk["delivered"]["status"] == "no_receipt"
    assert ver_exp_unk["referenced_parcels"] == []
    assert ver_exp_unk["receipt_hashes"] == {}
    exp_kinds = {v["kind"] for v in ver_exp_unk["violations"]}
    assert "unknown_parcel" in exp_kinds
    assert "no_receipt" in exp_kinds
    assert any("P999999" in v["detail"] for v in ver_exp_unk["violations"])


def test_blob_receipt_reader_does_not_relabel_auth_or_outage_or_tamper_as_missing():
    reader, blobs = _make_fake_blob_reader()

    class _AuthFailContainer:
        def get_blob_client(self, name: str):
            class _Client:
                def download_blob(self):
                    err = PermissionError("403 AuthorizationFailure")
                    setattr(err, "status_code", 403)
                    raise err
            return _Client()

    auth_reader = cth.BlobReceiptReader(container_client=_AuthFailContainer())
    with pytest.raises(PermissionError, match="403 AuthorizationFailure"):
        auth_reader.get_receipt("P001")
    with pytest.raises(PermissionError, match="403 AuthorizationFailure"):
        auth_reader.get_policy_wire()

    # Verify that tampered receipt on lazy load in verify_request is NOT swallowed as unknown_parcel
    host = cth.McpToolHost(fa.FoundryToolbox(reader=reader))
    tampered_p010 = json.loads(blobs["P003.json"].decode("utf-8"))
    tampered_p010["entity_id"] = "P010"
    reader._container._blobs["P010.json"] = json.dumps(tampered_p010).encode("utf-8")
    with pytest.raises(ValueError, match="receipt hash mismatch"):
        host.verify_request({"question": "Explain P010.", "answer": "Parcel P010 has score 33.3.", "entity_id": "P010"})


def _install_fake_urllib_for_function_app(monkeypatch):
    """Route `urllib.request.urlopen` used inside the real `call_function_json` directly into
    `func_app.handle_http_request`, so the full `verify_cloud_answer` / `score_cloud_parcels` ->
    `call_function_json` HTTP serialization, header handling, and status checking are exercised."""
    import io
    import urllib.error
    import urllib.request

    class _FakeHttpResponse:
        def __init__(self, status: int, body: str):
            self.status = status
            self._bytes = body.encode("utf-8")

        def read(self) -> bytes:
            return self._bytes

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def _fake_urlopen(req, timeout=30.0):
        from urllib.parse import urlsplit

        parts = urlsplit(req.full_url)
        hdrs = dict(req.header_items())
        st, resp_hdrs, bd = func_app.handle_http_request(
            req.get_method(),
            parts.path,
            req.data,
            hdrs,
        )
        if st >= 400:
            raise urllib.error.HTTPError(
                req.full_url,
                st,
                f"HTTP {st}",
                resp_hdrs,
                io.BytesIO(bd.encode("utf-8")),
            )
        return _FakeHttpResponse(st, bd)

    monkeypatch.setenv("PINNED_FUNCTION_SKIP_AUTH", "true")
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)


def test_cloud_runner_executes_zero_local_scoring_or_verification_and_replaces_altered_answer(monkeypatch):
    reader, _ = _make_fake_blob_reader()
    host = cth.McpToolHost(fa.FoundryToolbox(reader=reader))
    monkeypatch.setattr(func_app, "_HOST", host)
    monkeypatch.setenv(cth.REQUIRE_AUTH_ENV, "false")
    _install_fake_urllib_for_function_app(monkeypatch)

    # Model hallucinates a wrong score (88.8 instead of 33.3)
    mcp_call_item = SimpleNamespace(
        type="mcp_call",
        name="get_receipt",
        server_label="pinnedscore_receipts",
        arguments=json.dumps({"parcel": "P003"}),
    )
    hallucinated_response = SimpleNamespace(
        output=[mcp_call_item],
        output_text="Parcel P003 has a high score of 88.8.",
    )

    class _FakeOpenAIHallucinated:
        class responses:
            @staticmethod
            def create(**kwargs):
                return hallucinated_response

    # Ensure the caller module has no reference to local scorer or verifier
    assert not hasattr(run_cloud_agent, "verify_narration")
    assert not hasattr(run_cloud_agent, "ReceiptStore")

    res = run_cloud_agent.run_prompt(
        _FakeOpenAIHallucinated(),
        question="What is the score for P003?",
        function_endpoint="https://function.example.invalid/api/mcp",
    )
    assert res["local_tool_execution"] is False
    assert res["local_scoring"] is False
    assert res["local_verification"] is False
    assert res["server_verified"] is True
    assert res["blocked"] is True
    assert res["fallback_used"] is True
    assert res["raw_text"] == "Parcel P003 has a high score of 88.8."
    assert "Verifier blocked the narration; showing receipt values." in res["text"]
    assert "score=33.3" in res["text"]


def test_mandatory_verification_rejects_bypass_and_missing_endpoint_before_agent_call(monkeypatch):
    called = {"count": 0}

    class _SpyOpenAI:
        class responses:
            @staticmethod
            def create(**kwargs):
                called["count"] += 1
                raise AssertionError("Foundry agent must NOT be invoked when verification is bypassed or unconfigured")

    # 1. Explicit verify_on_server=False is rejected before calling the agent
    with pytest.raises(ValueError, match="Server-side narration verification via Function /api/verify is mandatory"):
        run_cloud_agent.run_prompt(
            _SpyOpenAI(),
            question="What is P003?",
            function_endpoint="https://function.example.invalid",
            verify_on_server=False,
        )
    assert called["count"] == 0

    # 2. Missing function_endpoint in run_prompt is rejected before calling the agent
    monkeypatch.delenv("PINNED_FUNCTION_ENDPOINT", raising=False)
    monkeypatch.delenv("PINNED_MCP_URL", raising=False)
    with pytest.raises(ValueError, match="Azure Function endpoint URL is required"):
        run_cloud_agent.run_prompt(
            _SpyOpenAI(),
            question="What is P003?",
            function_endpoint=None,
        )
    assert called["count"] == 0

    # 3. Missing function_endpoint and store in chat_cloud is rejected before calling the agent
    with pytest.raises(ValueError, match="chat_cloud requires function_endpoint"):
        fa.chat_cloud(
            _SpyOpenAI(),
            "What is P003?",
            function_endpoint=None,
            store=None,
        )
    assert called["count"] == 0

    # 4. CLI --no-server-verify flag does not exist (SystemExit on parse_args)
    with pytest.raises(SystemExit):
        run_cloud_agent.main(["What is P003?", "--no-server-verify"])


def test_verification_endpoint_failures_and_malformed_payloads_never_substitute_raw_text(monkeypatch):
    mcp_call_item = SimpleNamespace(
        type="mcp_call",
        name="get_receipt",
        server_label="pinnedscore_receipts",
        arguments=json.dumps({"parcel": "P003"}),
    )
    raw_resp = SimpleNamespace(
        output=[mcp_call_item],
        output_text="UNVERIFIED RAW MODEL OUTPUT FOR P003",
    )

    class _FakeOpenAI:
        class responses:
            @staticmethod
            def create(**kwargs):
                return raw_resp

    # 1. HTTP 500 from /api/verify -> RuntimeError (through real call_function_json)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        fa.verify_cloud_answer(
            question="What is P003?",
            answer="Parcel P003 has score 33.3.",
            function_endpoint="https://function.example.invalid",
            http_post=lambda url, body, hdrs: (500, json.dumps({"error": "internal verifier fault"})),
        )

    # 2. HTTP 409 policy mismatch from /api/verify -> RuntimeError (not silently passed)
    with pytest.raises(RuntimeError, match="HTTP 409"):
        fa.verify_cloud_answer(
            question="What is P003?",
            answer="Parcel P003 has score 33.3.",
            function_endpoint="https://function.example.invalid",
            http_post=lambda url, body, hdrs: (
                409,
                json.dumps({"error": "Policy hash mismatch", "blocked": True}),
            ),
        )

    # 3. Malformed 200 payload missing `text` or `delivered` -> RuntimeError, never returns raw_text
    for bad_payload in [
        {"blocked": False},
        {"blocked": False, "text": "", "delivered": {"mode": "verified_narration"}},
        {"blocked": False, "text": "ok", "delivered": {}},
        {"text": "ok", "delivered": {"mode": "verified_narration"}},
    ]:
        with pytest.raises(RuntimeError, match="Function /api/verify response missing"):
            fa.verify_cloud_answer(
                question="What is P003?",
                answer="UNVERIFIED RAW MODEL OUTPUT",
                function_endpoint="https://function.example.invalid",
                http_post=lambda url, body, hdrs, bp=bad_payload: (200, json.dumps(bp)),
            )

    # 4. End-to-end run_prompt propagates RuntimeError when /api/verify fails
    monkeypatch.setattr(
        fa,
        "verify_cloud_answer",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("Function /api/verify HTTP 503")),
    )
    with pytest.raises(RuntimeError, match="HTTP 503"):
        run_cloud_agent.run_prompt(
            _FakeOpenAI(),
            question="What is P003?",
            function_endpoint="https://function.example.invalid",
        )


def test_cli_main_enforces_mandatory_fallback_without_verify_flag_and_supports_score_parcel_all(monkeypatch, capsys):
    reader, _ = _make_fake_blob_reader()
    host = cth.McpToolHost(fa.FoundryToolbox(reader=reader))
    monkeypatch.setattr(func_app, "_HOST", host)
    monkeypatch.setenv(cth.REQUIRE_AUTH_ENV, "false")
    _install_fake_urllib_for_function_app(monkeypatch)

    # 1. --score-parcel P003 via CLI main (no --verify flag, exercises real call_function_json)
    rc_single = run_cloud_agent.main(
        [
            "--score-parcel",
            "P003",
            "--function-endpoint",
            "https://function.example.invalid",
            "--json",
        ]
    )
    assert rc_single == 0
    out_single = json.loads(capsys.readouterr().out)
    assert out_single["receipt"]["entity_id"] == "P003"
    assert out_single["receipt"]["score"] == "33.3"

    # 2. --score-parcel ALL via CLI main -> scores all 50 dataset parcels on the Function App
    rc_all = run_cloud_agent.main(
        [
            "--score-parcel",
            "ALL",
            "--function-endpoint",
            "https://function.example.invalid",
            "--json",
        ]
    )
    assert rc_all == 0
    out_all = json.loads(capsys.readouterr().out)
    assert out_all["count"] == 50
    assert len(out_all["receipts"]) == 50

    # 3. Normal prompt without --verify still enforces server verification and replaces altered answer
    mcp_call_item = SimpleNamespace(
        type="mcp_call",
        name="get_receipt",
        server_label="pinnedscore_receipts",
        arguments=json.dumps({"parcel": "P003"}),
    )
    hallucinated_response = SimpleNamespace(
        output=[mcp_call_item],
        output_text="Parcel P003 has a critical score of 95.0.",
    )

    class _FakeOpenAI:
        class responses:
            @staticmethod
            def create(**kwargs):
                return hallucinated_response

    monkeypatch.setattr(
        fa,
        "make_project_client",
        lambda ep=None: SimpleNamespace(get_openai_client=lambda: _FakeOpenAI()),
    )
    rc_prompt = run_cloud_agent.main(
        [
            "--question",
            "What is the score for P003?",
            "--endpoint",
            "https://fake-foundry.services.ai.azure.com/api/projects/proj-default",
            "--function-endpoint",
            "https://function.example.invalid",
            "--json",
        ]
    )
    # Exit code 2 signals that the Function verifier blocked the altered narration and delivered receipt fallback
    assert rc_prompt == 2
    out_prompt = json.loads(capsys.readouterr().out)
    assert out_prompt["server_verified"] is True
    assert out_prompt["blocked"] is True
    assert out_prompt["fallback_used"] is True
    assert "Verifier blocked the narration; showing receipt values." in out_prompt["text"]
    assert "95.0" not in out_prompt["text"]
    assert "score=33.3" in out_prompt["text"]

    # Also test cloud scoring via fa.score_cloud_parcels -> runs in Function App
    score_res = fa.score_cloud_parcels(
        {"parcel": "P003"},
        function_endpoint="https://function.example.invalid/api/mcp",
    )
    assert score_res["receipt"]["entity_id"] == "P003"
    assert score_res["receipt"]["score"] == "33.3"



def test_verify_agent_mcp_wiring_and_validate_mcp_trace():
    mcp_url = "https://function.example.invalid/api/mcp"
    agent_ver = SimpleNamespace(
        name="pinned-score-agent",
        version="13",
        definition={
            "tools": [
                {
                    "type": "mcp",
                    "server_label": "pinnedscore_receipts",
                    "server_url": mcp_url,
                    "project_connection_id": "pinnedscore-receipts-mcp-entraid",
                    "require_approval": "never",
                }
            ]
        },
    )
    conn_resp = {
        "id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acc/projects/proj/connections/pinnedscore-receipts-mcp-entraid",
        "properties": {
            "category": "RemoteTool",
            "target": mcp_url,
            "authType": "ProjectManagedIdentity",
        },
    }
    wiring = fa.verify_agent_mcp_wiring(
        agent_ver,
        expected_server_label="pinnedscore_receipts",
        expected_server_url=mcp_url,
        expected_connection_name="pinnedscore-receipts-mcp-entraid",
        connection_body=conn_resp,
    )
    assert wiring["agent_version"] == "13"
    assert wiring["auth_type"] == "ProjectManagedIdentity"

    with pytest.raises(ValueError, match="server_url"):
        fa.verify_agent_mcp_wiring(
            agent_ver,
            expected_server_label="pinnedscore_receipts",
            expected_server_url="https://wrong.example.com/api/mcp",
            expected_connection_name="pinnedscore-receipts-mcp-entraid",
        )

    valid_trace = [
        {"type": "mcp_list_tools", "server_label": "pinnedscore_receipts", "error": None},
        {
            "type": "mcp_call",
            "name": "get_receipt",
            "server_label": "pinnedscore_receipts",
            "arguments": '{"parcel":"P003"}',
            "output": '{"receipt":{"entity_id":"P003"}}',
            "error": None,
        },
    ]
    fa.validate_mcp_trace(valid_trace, expected_server_label="pinnedscore_receipts")

    with pytest.raises(RuntimeError, match="Expected at least one remote 'mcp_call'"):
        fa.validate_mcp_trace([])

    with pytest.raises(RuntimeError, match="failed: 401"):
        fa.validate_mcp_trace(
            [{"type": "mcp_call", "name": "get_receipt", "server_label": "pinnedscore_receipts", "output": "ok", "error": "401"}]
        )

