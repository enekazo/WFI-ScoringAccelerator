import json
import struct
from pathlib import Path

from pinned_scoring import foundry_agent as fa
from pinned_scoring import foundry_eval
from pinned_scoring.fabric import GoldReader
from pinned_scoring.fabric import sql_endpoint as sqe

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence"


def test_no_api_keys_in_source():
    banned = ("api_key", "AZURE_OPENAI_API_KEY", "AzureKeyCredential")
    for folder in ("src", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if path.name == "clients.py":
                # The OpenAI SDK parameter is named api_key but carries an Entra bearer token.
                text = text.replace("api_key=provider()", "").replace("self._rclient.api_key", "")
            assert not any(b in text for b in banned), path


def test_project_client_uses_entra_credential(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, endpoint, credential):
            captured.update(endpoint=endpoint, credential=credential)

    import azure.ai.projects as projects
    monkeypatch.setattr(projects, "AIProjectClient", FakeClient)
    fa.make_project_client("https://example.invalid/api/projects/p")
    assert type(captured["credential"]).__name__ == "DefaultAzureCredential"


def test_enable_tracing_is_optional(monkeypatch):
    from azure.ai.projects.telemetry import AIProjectInstrumentor
    calls = []
    monkeypatch.setattr(AIProjectInstrumentor, "instrument", lambda self, *a, **k: calls.append(1))
    assert fa.enable_tracing() is True and calls == [1]


def test_foundry_eval_export(tmp_path):
    runs = json.loads((EVIDENCE / "scenario-1-locking-the-score" / "runs.json").read_text(encoding="utf-8"))
    rows = foundry_eval.export_rows(runs, EVIDENCE / "receipts", {"B"})
    assert rows and all(r["arm"] == "B" for r in rows)
    assert all(r["query"] and r["context"] and r["ground_truth"] for r in rows)
    out = tmp_path / "e.jsonl"
    assert foundry_eval.write_jsonl(rows, out) == len(rows)
    assert len(out.read_text(encoding="utf-8").splitlines()) == len(rows)


class FakeCursor:
    description = [(c,) for c in ("entity_id", "status", "score", "band", "components", "reasons",
                                   "as_of", "policy_sha256", "input_sha256", "receipt_sha256",
                                   "scored_at")]

    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql):
        assert sql.startswith("SELECT") and "gold_receipts" in sql

    def fetchall(self):
        return self.rows


class FakeConn:
    closed = False

    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return FakeCursor(self.rows)

    def close(self):
        self.closed = True


def test_gold_reader_over_sql_endpoint_fetcher():
    r = json.loads((EVIDENCE / "receipts" / "P003.json").read_text(encoding="utf-8"))
    row = (r["entity_id"], r["status"], r["score"], r["band"], json.dumps(r["components"]),
           json.dumps(r["reasons"]), r["as_of"], r["policy_sha256"], r["input_sha256"],
           r["receipt_sha256"], "2026-07-15T12:00:00")
    conn = FakeConn([row])
    fetch = sqe.fetcher("srv.datawarehouse.fabric.microsoft.com", "lh", lambda s, d: conn)
    receipt = GoldReader(fetch).get_receipt("P003")
    assert receipt.receipt_sha256 == r["receipt_sha256"] and conn.closed


def test_token_struct_layout():
    packed = sqe.token_struct("ab")
    assert struct.unpack("<I", packed[:4])[0] == 4 and packed[4:] == "ab".encode("utf-16-le")


def test_connect_mssql_passes_explicit_access_token_struct(monkeypatch):
    import sys
    import types

    captured = {}

    class FakeCred:
        def get_token(self, scope):
            assert scope == sqe.SQL_SCOPE
            return types.SimpleNamespace(token="tok-123")

    fake_mod = types.SimpleNamespace(
        connect=lambda conn_str, **kwargs: captured.update(conn_str=conn_str, kwargs=kwargs) or "conn"
    )
    monkeypatch.setitem(sys.modules, "mssql_python", fake_mod)
    res = sqe.connect_mssql("srv.datawarehouse.fabric.microsoft.com", "lh", credential=FakeCred())
    assert res == "conn"
    assert "Authentication=" not in captured["conn_str"]
    assert captured["kwargs"]["attrs_before"] == {
        sqe.SQL_COPT_SS_ACCESS_TOKEN: sqe.token_struct("tok-123")
    }


def test_acquire_sql_token_falls_back_to_az_cli_on_default_credential_timeout(monkeypatch):
    import azure.identity as ai
    import subprocess

    monkeypatch.delenv("FABRIC_SQL_ACCESS_TOKEN", raising=False)

    class FailingDefaultCred:
        def __init__(self, **kwargs):
            assert kwargs.get("process_timeout") == 60
            assert kwargs.get("exclude_developer_cli_credential") is True

        def get_token(self, scope):
            raise RuntimeError("AzureCliCredential: Failed to invoke the Azure CLI")

    monkeypatch.setattr(ai, "DefaultAzureCredential", FailingDefaultCred)
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda cmd, **kw: "cli-fallback-token\n" if "database.windows.net" in cmd else "",
    )
    assert sqe._acquire_sql_token() == "cli-fallback-token"

