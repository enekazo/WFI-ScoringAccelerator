"""Offline tests for deploy/cleanup_plan.py and deploy/uninstall.ps1 (no cloud calls)."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import cleanup_plan as cp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SUB = "11111111-1111-1111-1111-111111111111"
TEN = "22222222-2222-2222-2222-222222222222"
WS = "33333333-3333-3333-3333-333333333333"
LH = "44444444-4444-4444-4444-444444444444"
ONT = "55555555-5555-5555-5555-555555555555"
RG = f"/subscriptions/{SUB}/resourceGroups/demo-rg"
P = f"{RG}/providers"
STORAGE = f"{P}/Microsoft.Storage/storageAccounts/demorcpt1"
ACCT = f"{P}/Microsoft.CognitiveServices/accounts/demo-ai"
CONN = f"{ACCT}/projects/proj/connections/pinnedscore-receipts-mcp-entraid"
FUNC = f"{P}/Microsoft.Web/sites/demo-mcp"
APPI = f"{P}/Microsoft.Insights/components/shared-appi"
RA_EXISTING = f"{APPI}/providers/Microsoft.Authorization/roleAssignments/aaaa"
RA_STORAGE = f"{STORAGE}/providers/Microsoft.Authorization/roleAssignments/bbbb"
EV = "test evidence"


def ledger(**over):
    lg = cp.new_ledger(TEN, SUB, "dep1")
    cp.record(lg, "resource_group", RG, "created", EV)
    cp.record(lg, "arm_resource", STORAGE, "created", EV)
    cp.record(lg, "arm_resource", FUNC, "created", EV)
    cp.record(lg, "arm_resource", ACCT, "created", EV, attrs={"location": "eastus"})
    cp.record(lg, "foundry_connection", CONN, "created", EV)
    cp.record(lg, "role_assignment", RA_STORAGE, "created", EV)
    cp.record(lg, "arm_resource", APPI, "reused", "pre-existing")
    cp.record(lg, "role_assignment", RA_EXISTING, "created", EV)
    cp.record(lg, "fabric_workspace", WS, "created", EV)
    cp.record(lg, "fabric_item", LH, "created", EV, attrs={"workspaceId": WS, "itemType": "Lakehouse"})
    cp.record(lg, "fabric_item", ONT, "created", EV, attrs={"workspaceId": WS, "itemType": "Ontology"})
    cp.record(lg, "foundry_agent", "pinned-score-agent", "created", EV,
              attrs={"endpoint": "https://x.services.ai.azure.com/api/projects/p", "versions": ["2"]})
    lg.update(over)
    return lg


class Fake:
    def __init__(self, present=None, ws_items=None, rg_left=None, identity=None, fail_delete=None, deleted_acct=None,
                 other_ws=None):
        self.other_ws = other_ws or []
        self.present = {p.lower() for p in (present or [])}
        self.ws_items = ws_items if ws_items is not None else []
        self.rg_left = rg_left or []
        self.identity = identity or {"tenantId": TEN, "subscriptionId": SUB}
        self.fail_delete = fail_delete or {}
        self.deleted_acct = deleted_acct
        self.calls = []

    def account_show(self):
        return self.identity

    def arm_get(self, id_, api):
        return (200, {"id": id_}) if id_.lower() in self.present else (404, None)

    def arm_delete(self, id_, api):
        self.calls.append(("arm_delete", id_))
        if id_ in self.fail_delete:
            raise cp.CleanupError(self.fail_delete[id_])
        self.present.discard(id_.lower())

    def arm_list_resources(self, rg):
        return [r for r in self.rg_left if r.lower() in self.present]

    def fabric(self, m, path):
        self.calls.append(("fabric", m, path))
        key = path.lower()
        if m == "DELETE":
            self.present.discard(key)
            return 200, None
        return (200, {"id": path.rsplit("/", 1)[-1]}) if key in self.present else (404, None)

    def fabric_list_items(self, ws):
        return self.ws_items

    def fabric_list_capacity_workspaces(self, guid):
        if getattr(self, "admin_denied", False):
            raise cp.CleanupError("403 InsufficientPrivileges")
        live = [{"id": WS, "capacityId": CAP_GUID}] if f"/workspaces/{WS}".lower() in self.present else []
        return live + self.other_ws

    def agent_versions(self, ep, name):
        return self.agents

    def agent_delete_version(self, ep, name, v):
        self.calls.append(("agent_delete", name, v))

    def deleted_account(self, *a):
        return (200, {"properties": {"accountId": self.deleted_acct}}) if self.deleted_acct else (404, None)

    def purge_account(self, *a):
        self.calls.append(("purge", *a))

    agents = ["1", "2"]


def run(lg, fake, **kw):
    plan = cp.plan_cleanup(lg, remove_fabric=kw.pop("remove_fabric", True), purge=kw.pop("purge", False))
    return cp.execute_plan(plan, fake, sleep=lambda s: None, **kw)


def fabric_paths():
    return [f"/workspaces/{WS}", f"/workspaces/{WS}/items/{LH}", f"/workspaces/{WS}/items/{ONT}"]


# --- planning -------------------------------------------------------------
def test_plan_deletes_created_preserves_reused_and_cascades_children():
    plan = cp.plan_cleanup(ledger(), remove_fabric=True)
    act = {s["id"]: s["action"] for s in plan["steps"]}
    assert act[FUNC] == "delete_arm" and act[STORAGE] == "delete_arm" and act[ACCT] == "delete_arm"
    assert act[CONN] == "cascade" and act[RA_STORAGE] == "cascade"
    assert act[RA_EXISTING] == "delete_arm"  # scoped on a reused resource: must be removed explicitly
    assert APPI not in act and any(p["id"] == APPI for p in plan["preserved"])
    assert act[RG] == "delete_resource_group_if_only_owned"
    assert not any(s["action"].startswith("purge") for s in plan["steps"])


def test_plan_orders_children_before_parents_and_never_names_capacity():
    plan = cp.plan_cleanup(ledger(), remove_fabric=True)
    phases = [s["phase"] for s in plan["steps"]]
    assert phases == sorted(phases)
    assert "capacity" not in json.dumps(plan["steps"]).lower()


CAP = f"{P}/Microsoft.Fabric/capacities/democap"
CAP_GUID = "77777777-7777-7777-7777-777777777777"


def cap_ledger(origin="created"):
    lg = ledger()
    cp.record(lg, "fabric_capacity", CAP, origin, EV if origin != "reused" else "pre-existing",
              attrs={"capacityId": CAP_GUID})
    return lg


def test_reused_capacity_preserved_created_capacity_planned():
    reused = cp.plan_cleanup(cap_ledger("reused"), remove_fabric=True)
    assert not any(s["id"] == CAP for s in reused["steps"])
    assert any(p["id"] == CAP for p in reused["preserved"])
    created = cp.plan_cleanup(cap_ledger(), remove_fabric=True)
    step = next(s for s in created["steps"] if s["id"] == CAP)
    assert step["action"] == "delete_fabric_capacity_if_unshared"
    assert step["phase"] > next(s for s in created["steps"] if s["id"] == WS)["phase"]
    kept = cp.plan_cleanup(cap_ledger(), remove_fabric=False)
    assert next(s for s in kept["steps"] if s["id"] == CAP)["action"] == "skip"


def test_created_capacity_without_guid_is_unresolved():
    lg = ledger()
    cp.record(lg, "arm_resource", CAP, "created", EV)
    plan = cp.plan_cleanup(lg, remove_fabric=True)
    assert not any(s["id"] == CAP for s in plan["steps"])
    assert any(u["id"] == CAP for u in plan["unresolved"])


def test_created_capacity_deleted_after_workspace_when_unshared():
    f = Fake(present=ALL_ARM + [CAP] + fabric_paths(), ws_items=[{"id": LH, "type": "Lakehouse"}])
    res = run(cap_ledger(), f)
    assert CAP.lower() not in f.present and CAP in res["deleted"] and res["ok"]


def test_created_capacity_refused_when_unrelated_workspace_assigned():
    f = Fake(present=ALL_ARM + [CAP] + fabric_paths(), ws_items=[{"id": LH, "type": "Lakehouse"}],
             other_ws=[{"id": "88888888-8888-8888-8888-888888888888", "displayName": "theirs", "capacityId": CAP_GUID}])
    res = run(cap_ledger(), f)
    assert CAP.lower() in f.present and not res["ok"]
    assert any(p["id"] == CAP and "theirs" in p["contents"][0] for p in res["preserved"])
    assert RG in [s["id"] for s in res["skipped"]]


def test_created_capacity_fails_closed_without_admin_visibility():
    f = Fake(present=ALL_ARM + [CAP] + fabric_paths(), ws_items=[{"id": LH, "type": "Lakehouse"}])
    f.admin_denied = True
    res = run(cap_ledger(), f)
    assert CAP.lower() in f.present and not res["ok"]
    assert any(CAP == x["id"] and "Fabric administrator" in x["error"] for x in res["failed"])
    assert RG in [s["id"] for s in res["skipped"]]


def test_capacity_not_deleted_if_its_workspace_was_preserved():
    foreign = {"id": "66666666-6666-6666-6666-666666666666", "type": "Notebook", "displayName": "mine"}
    f = Fake(present=ALL_ARM + [CAP] + fabric_paths(), ws_items=[foreign],
             other_ws=[{"id": WS, "displayName": "ws", "capacityId": CAP_GUID}])
    res = run(cap_ledger(), f)
    assert CAP.lower() in f.present and not res["ok"]


def test_fabric_items_skipped_without_flag_and_purge_is_explicit():
    plan = cp.plan_cleanup(ledger(), remove_fabric=False, purge=True)
    assert all(s["action"] == "skip" for s in plan["steps"] if s["kind"] in {"fabric_item", "fabric_workspace"})
    assert any(s["action"] == "purge_soft_deleted_account" for s in plan["steps"])


def test_missing_ledger_fails_closed_without_template_fallback(tmp_path):
    with pytest.raises(cp.LedgerError, match="never guesses"):
        cp.load_ledger(tmp_path / "ownership.json")


def test_mismatched_subscription_or_tenant_rejected():
    with pytest.raises(cp.LedgerError, match="subscriptionId mismatch"):
        cp.plan_cleanup(ledger(), expected={"subscriptionId": "99999999-9999-9999-9999-999999999999"})
    lg = ledger()
    lg["entries"][1]["id"] = STORAGE.replace(SUB, "99999999-9999-9999-9999-999999999999")
    with pytest.raises(cp.LedgerError, match="not in ledger subscription"):
        cp.validate_ledger(lg)


def test_placeholder_ids_and_missing_evidence_rejected():
    with pytest.raises(cp.LedgerError):
        cp.validate_ledger(cp.new_ledger("00000000-0000-0000-0000-000000000000", SUB, "d"))
    lg = ledger()
    lg["entries"][0]["evidence"] = ""
    with pytest.raises(cp.LedgerError, match="no creation evidence"):
        cp.validate_ledger(lg)


def test_unknown_type_and_agent_without_versions_are_unresolved_not_deleted():
    lg = ledger()
    cp.record(lg, "arm_resource", f"{P}/Microsoft.Foo/bars/x", "created", EV)
    cp.record(lg, "foundry_agent", "other", "created", EV, attrs={"endpoint": "https://e"})
    plan = cp.plan_cleanup(lg)
    reasons = " ".join(u["reason"] for u in plan["unresolved"])
    assert "no known api-version" in reasons and "no created versions" in reasons
    assert not any("bars" in s["id"] for s in plan["steps"])


def test_record_never_downgrades_created_to_reused_on_resume():
    lg = cp.new_ledger(TEN, SUB, "d")
    cp.record(lg, "arm_resource", STORAGE, "created", EV)
    cp.record(lg, "arm_resource", STORAGE, "reused", "seen existing on resume")
    assert lg["entries"][0]["origin"] == "created"


def test_classify_azure_created_excludes_preexisting():
    ops = [{"properties": {"provisioningOperation": "Create", "targetResource": {"id": i}}} for i in (RG, STORAGE, APPI)]
    ops.append({"properties": {"provisioningOperation": "Read", "targetResource": {"id": FUNC}}})
    assert cp.classify_azure_created([RG, APPI], ops) == [STORAGE]


def test_arm_type_parsing():
    assert cp.arm_type(CONN) == "microsoft.cognitiveservices/accounts/projects/connections"
    assert cp.arm_type(RA_STORAGE) == "microsoft.authorization/roleassignments"
    assert cp.arm_type(RG) == "microsoft.resources/resourcegroups"


# --- execution ------------------------------------------------------------
ALL_ARM = [RG, STORAGE, FUNC, ACCT, RA_EXISTING, APPI]


def test_execute_wrong_tenant_changes_nothing():
    f = Fake(present=ALL_ARM, identity={"tenantId": "other", "subscriptionId": SUB})
    with pytest.raises(cp.CleanupError, match="does not match ledger"):
        run(ledger(), f)
    assert not f.calls


def test_execute_deletes_only_owned_and_removes_rg_when_empty():
    f = Fake(present=ALL_ARM + fabric_paths(), rg_left=[APPI])
    # APPI (reused) remains in RG, so the RG must be preserved.
    res = run(ledger(), f)
    deleted = {c[1] for c in f.calls if c[0] == "arm_delete"}
    assert deleted == {STORAGE, FUNC, ACCT, RA_EXISTING}
    assert APPI.lower() in f.present and RG.lower() in f.present
    assert any(p["id"] == RG and "not owned" in p["reason"] for p in res["preserved"])
    assert ("agent_delete", "pinned-score-agent", "2") in f.calls
    assert ("agent_delete", "pinned-score-agent", "1") not in f.calls
    assert any(p["id"] == "agent:pinned-score-agent" for p in res["preserved"])
    assert res["ok"]


def test_rg_deleted_when_empty_and_idempotent_rerun():
    f = Fake(present=ALL_ARM + fabric_paths())
    f.present.discard(APPI.lower())
    res = run(ledger(), f)
    assert RG.lower() not in f.present and res["ok"]
    f.calls.clear()
    res2 = run(ledger(), f)
    assert not [c for c in f.calls if c[0] == "arm_delete" or c[:2] == ("fabric", "DELETE")]
    assert RG in res2["absent"] and res2["ok"]


def test_shared_fabric_workspace_preserved_when_foreign_items():
    foreign = {"id": "66666666-6666-6666-6666-666666666666", "type": "Notebook", "displayName": "mine"}
    f = Fake(present=ALL_ARM + fabric_paths(), ws_items=[foreign, {"id": "x", "type": "SQLEndpoint"}])
    res = run(ledger(), f)
    assert f"/workspaces/{WS}".lower() in f.present
    assert any(p["id"] == WS for p in res["preserved"])
    assert ("fabric", "DELETE", f"/workspaces/{WS}/items/{LH}") in f.calls


def test_created_workspace_deleted_when_only_owned_remains():
    f = Fake(present=ALL_ARM + fabric_paths(), ws_items=[{"id": LH, "type": "Lakehouse"}])
    run(ledger(), f)
    assert f"/workspaces/{WS}".lower() not in f.present


def test_reused_workspace_only_owned_items_removed():
    lg = ledger()
    for e in lg["entries"]:
        if e["kind"] == "fabric_workspace":
            e["origin"] = "reused"
    f = Fake(present=ALL_ARM + fabric_paths())
    run(lg, f)
    assert ("fabric", "DELETE", f"/workspaces/{WS}") not in f.calls
    assert ("fabric", "DELETE", f"/workspaces/{WS}/items/{ONT}") in f.calls


def test_errors_visible_and_block_rg_and_purge():
    f = Fake(present=ALL_ARM, fail_delete={STORAGE: "403 AuthorizationFailed; immutable retention"})
    res = run(ledger(), f, purge=False)
    assert not res["ok"] and res["failed"][0]["id"] == STORAGE
    assert RG.lower() in f.present
    assert any(s["id"] == RG for s in res["skipped"])


def test_purge_requires_exact_account_id_match():
    lg = ledger()
    f = Fake(present=[RG], deleted_acct=ACCT.replace("demo-ai", "other-ai"))
    res = run(lg, f, purge=True)
    assert not [c for c in f.calls if c[0] == "purge"]
    assert any("different deployment" in x["error"] for x in res["failed"])
    f2 = Fake(present=[RG], deleted_acct=ACCT)
    run(lg, f2, purge=True)
    assert [c for c in f2.calls if c[0] == "purge"]


def test_unresolved_makes_result_incomplete():
    lg = ledger()
    cp.record(lg, "arm_resource", f"{P}/Microsoft.Foo/bars/x", "created", EV)
    res = run(lg, Fake(present=ALL_ARM + fabric_paths()))
    assert not res["ok"] and res["unresolved"]


# --- PowerShell entry point ------------------------------------------------
def _ps(args, cwd=ROOT):
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(ROOT / "deploy" / "uninstall.ps1"), *args],
                          cwd=str(cwd), capture_output=True, text=True, timeout=120, check=False)


def test_uninstall_whatif_previews_offline_and_force_cannot_bypass_whatif(tmp_path):
    path = tmp_path / "ownership.json"
    path.write_text(json.dumps(ledger()), encoding="utf-8")
    proc = _ps(["-OwnershipFile", str(path), "-RemoveFabricItems", "-Purge", "-Force", "-WhatIf"])
    assert proc.returncode == 0, proc.stderr
    assert "offline preview" in proc.stdout and STORAGE in proc.stdout
    assert "purge_soft_deleted_account" in proc.stdout
    assert "No resources were changed" in proc.stdout or "What if" in proc.stdout + proc.stderr


def test_uninstall_without_ledger_fails_nonzero(tmp_path):
    proc = _ps(["-OwnershipFile", str(tmp_path / "missing.json"), "-WhatIf"])
    assert proc.returncode != 0 and "never guesses ownership" in proc.stdout + proc.stderr


def test_uninstall_rejects_parameters_for_other_subscription(tmp_path):
    path = tmp_path / "ownership.json"
    path.write_text(json.dumps(ledger()), encoding="utf-8")
    params = tmp_path / "p.json"
    params.write_text(json.dumps({"tenantId": TEN, "subscriptionId": "99999999-9999-9999-9999-999999999999"}), encoding="utf-8")
    proc = _ps(["-OwnershipFile", str(path), "-ParametersFile", str(params), "-WhatIf"])
    assert proc.returncode != 0 and "mismatch" in proc.stdout + proc.stderr


# --- strict inventory / error classification regressions -------------------
class Scripted(cp.AzRunner):
    def __init__(self, responses):
        super().__init__()
        self.responses, self.calls = list(responses), []

    def _run(self, args):
        self.calls.append(args)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def ok(body):
    return 0, json.dumps(body), ""


@pytest.mark.parametrize("body", [{"value": []}, {}, None, [], {"workspaces": None}])
def test_capacity_inventory_malformed_200_raises_and_blocks_delete(body):
    r = Scripted([ok(body)] if body is not None else [(0, "", "")])
    with pytest.raises(cp.CleanupError):
        r.fabric_list_capacity_workspaces(CAP_GUID)
    f = Fake(present=ALL_ARM + [CAP] + fabric_paths())
    f.fabric_list_capacity_workspaces = lambda g: r.fabric_list_capacity_workspaces(g)
    res = run(cap_ledger(), f)
    assert not res["ok"] and not any(c[0] == "arm_delete" and c[1] == CAP for c in f.calls)


def test_capacity_inventory_entries_and_pagination_fail_closed():
    for pages in ([ok({"workspaces": [{"id": WS}]})],
                  [ok({"workspaces": [{"id": WS, "capacityId": "other"}]})],
                  [ok({"workspaces": [], "continuationUri": "u"})],
                  [ok({"workspaces": [], "continuationToken": "t"}), ok({"workspaces": [], "continuationToken": "t"})]):
        with pytest.raises(cp.CleanupError):
            Scripted(pages).fabric_list_capacity_workspaces(CAP_GUID)


def test_capacity_inventory_valid_pagination_encodes_token():
    r = Scripted([ok({"workspaces": [{"id": WS, "capacityId": CAP_GUID}], "continuationToken": "a b+/="}),
                  ok({"workspaces": []})])
    assert len(r.fabric_list_capacity_workspaces(CAP_GUID)) == 1
    assert "continuationToken=a%20b%2B%2F%3D" in r.calls[1][r.calls[1].index("--url") + 1]


def test_arm_and_fabric_item_listings_reject_malformed_and_repeat():
    with pytest.raises(cp.CleanupError):
        Scripted([ok({"foo": 1})]).arm_list_resources(RG)
    with pytest.raises(cp.CleanupError):
        Scripted([ok({"value": [], "nextLink": "n"}), ok({"value": [], "nextLink": "n"})]).arm_list_resources(RG)
    with pytest.raises(cp.CleanupError):
        Scripted([ok({"items": []})]).fabric_list_items(WS)


@pytest.mark.parametrize("err", [
    "ERROR: Not Found",
    "ERROR: (ResourceNotFound) The Resource x was not found.",
    '{"error":{"code":"ResourceGroupNotFound"}}',
    (
        "ERROR: Connection pinnedscore-receipts-mcp-entraid can't be found in this workspace("
        '{"error":{"code":"UserError","severity":null,"message":"Connection pinnedscore-receipts-mcp-entraid '
        "can't be found in this workspace\",\"innerError\":{\"code\":\"NotFoundError\",\"innerError\":null}},"
        '"correlation":{"operation":"62a1","request":"81f3"},"environment":"centralus","location":"centralus"})'
    ),
])
def test_is_missing_error_positive(err):
    assert cp.is_missing_error(err)


@pytest.mark.parametrize("err", [
    "ERROR: (AuthorizationFailed) caller lacks access; resource NotFound maybe",
    "403 Forbidden while checking; 404 NotFound mentioned",
    "generic failure 404",
    "",
    'ERROR: Caller lacks access({"error":{"code":"AuthorizationFailed","message":"NotFound","innerError":{"code":"Forbidden"}}})',
    'ERROR: UserError({"error":{"code":"UserError","message":"QuotaExceeded","innerError":{"code":"QuotaExceeded"}}})',
])
def test_is_missing_error_rejects_misleading(err):
    assert not cp.is_missing_error(err)
    with pytest.raises(cp.CleanupError):
        Scripted([(1, "", err)])._rest("GET", "https://x")


def test_ownership_record_arm_exists_rejects_misleading_403(monkeypatch):
    import subprocess as sp
    sys.path.insert(0, str(ROOT / "scripts"))
    import ownership_record as orr
    mk = lambda e: sp.CompletedProcess([], 1, "", e)
    monkeypatch.setattr(sp, "run", lambda *a, **k: mk("ERROR: (AuthorizationFailed) NotFound 404"))
    with pytest.raises(RuntimeError):
        orr.arm_exists("https://x")
    monkeypatch.setattr(sp, "run", lambda *a, **k: mk("ERROR: (ResourceNotFound) gone"))
    assert orr.arm_exists("https://x") is False


# --- nested ownership evidence ----------------------------------------------
def test_classify_nested_without_evidence_is_unresolved():
    ra = f"{ACCT}/providers/Microsoft.Authorization/roleAssignments/zzz"
    proj = f"{ACCT}/projects/p"
    ops = [{"properties": {"provisioningOperation": "Create",
                           "targetResource": {"id": i, "resourceType": t}}}
           for i, t in ((ra, "Microsoft.Authorization/roleAssignments"),
                        (proj, "Microsoft.CognitiveServices/accounts/projects"))]
    created, unresolved = cp.classify_azure_operations(set(), ops, created_rg=None)
    assert not created and {u["id"] for u in unresolved} == {ra, proj}
    created, unresolved = cp.classify_azure_operations(set(), ops, created_rg=RG)
    assert set(created) >= {ra, proj} and not unresolved


# --- cascade vs preserved descendants -------------------------------------
def test_foreign_agent_blocks_deleting_owned_foundry_account():
    lg = ledger()
    lg["entries"] = [e for e in lg["entries"] if e["id"] != "pinned-score-agent"]
    cp.record(lg, "foundry_agent", "pinned-score-agent", "created", EV,
              attrs={"endpoint": "https://demo-ai.services.ai.azure.com/api/projects/p", "versions": ["2"]})
    f = Fake(present=ALL_ARM + fabric_paths(), ws_items=[])
    res = run(lg, f)
    assert any(p["id"] == "agent:pinned-score-agent" for p in res["preserved"])
    assert ACCT not in res["deleted"] and ("arm_delete", ACCT) not in f.calls
    assert not res["ok"] and any(x["id"] == ACCT for x in res["failed"])


def test_rg_with_foreign_unknown_types_is_preserved():
    ev = f"{P}/Microsoft.EventGrid/systemTopics/auto-1"
    f = Fake(present=ALL_ARM + [ev] + fabric_paths(), rg_left=[ev])
    res = run(ledger(), f)
    assert RG.lower() in f.present and RG not in res["deleted"]
    assert not any(c == ("arm_delete", RG) for c in f.calls)


def test_azrunner_resolves_az_via_path_and_fails_closed_when_missing(monkeypatch):
    calls = []

    class P:
        returncode, stdout, stderr = 0, "{}", ""

    monkeypatch.setattr(cp.shutil, "which", lambda name: r"C:\x\az.CMD")
    monkeypatch.setattr(cp.subprocess, "run", lambda argv, **kw: calls.append(argv) or P())
    cp.AzRunner()._run(["account", "show"])
    assert calls[0][0] == r"C:\x\az.CMD"

    monkeypatch.setattr(cp.shutil, "which", lambda name: None)
    with pytest.raises(cp.CleanupError, match="not found on PATH"):
        cp.AzRunner()._run(["account", "show"])


def test_foundry_project_deleted_explicitly_before_account():
    proj = f"{ACCT}/projects/proj2"
    lg = ledger()
    lg["entries"].append({"kind": "arm_resource", "id": proj, "origin": "created",
                          "evidence": "test", "attrs": {}})
    plan = cp.plan_cleanup(lg, remove_fabric=True)
    steps = [s for s in plan["steps"] if s["action"] == "delete_arm"]
    ids = [s["id"] for s in steps]
    assert proj in ids and ACCT in ids
    assert ids.index(proj) < ids.index(ACCT)


def test_network_security_perimeters_are_ignored_in_record_and_plan():
    nsp = f"{P}/Microsoft.Network/networkSecurityPerimeters/demo-nsp"
    prof = f"{nsp}/profiles/default"
    assoc = f"{nsp}/resourceAssociations/assoc-receipts-storage"
    dep = f"{P}/Microsoft.Resources/deployments/pinned-scoring-resources"
    ops = [
        {"properties": {"provisioningOperation": "Create", "targetResource": {"id": nsp}}},
        {"properties": {"provisioningOperation": "Create", "targetResource": {"id": prof}}},
        {"properties": {"provisioningOperation": "Create", "targetResource": {"id": assoc}}},
        {"properties": {"provisioningOperation": "Create", "targetResource": {"id": dep}}},
        {"properties": {"provisioningOperation": "Create", "targetResource": {"id": STORAGE}}},
    ]
    created, unresolved = cp.classify_azure_operations([], ops, RG)
    assert created == [STORAGE]
    assert unresolved == []

    lg = ledger()
    lg["entries"].append({"kind": "arm_resource", "id": nsp, "origin": "adopted", "evidence": "test", "attrs": {}})
    lg["entries"].append({"kind": "arm_resource", "id": assoc, "origin": "adopted", "evidence": "test", "attrs": {}})
    lg["entries"].append({"kind": "arm_resource", "id": dep, "origin": "created", "evidence": "test", "attrs": {}})
    lg["unresolved"].append({"kind": "arm_resource", "id": prof, "reason": "legacy"})
    plan = cp.plan_cleanup(lg, remove_fabric=True)
    step_ids = [s["id"] for s in plan["steps"]]
    assert nsp not in step_ids and assoc not in step_ids and dep not in step_ids
    assert plan["unresolved"] == []
    preserved_ids = {p["id"] for p in plan["preserved"]}
    assert nsp in preserved_ids and assoc in preserved_ids and dep in preserved_ids


def test_arm_delete_order_deletes_sites_before_serverfarms_and_appi_before_law():
    plan_id = f"{P}/Microsoft.Web/serverFarms/demo-mcp-plan"
    law_id = f"{P}/Microsoft.OperationalInsights/workspaces/demo-law"
    appi_id = f"{P}/Microsoft.Insights/components/demo-appi"
    lg = ledger()
    for rid in (plan_id, law_id, appi_id):
        cp.record(lg, "arm_resource", rid, "created", EV)
    plan = cp.plan_cleanup(lg, remove_fabric=True)
    ids = [s["id"] for s in plan["steps"] if s["action"] == "delete_arm"]
    assert ids.index(FUNC) < ids.index(plan_id)
    assert ids.index(appi_id) < ids.index(law_id)


def test_storage_delete_protection_fails_closed_by_default_and_clears_only_on_opt_in(monkeypatch):
    calls = []
    deleted_storage = {"count": 0}

    def fake_rest(self, method, url, resource=None):
        calls.append(("rest", method, url))
        if method == "DELETE" and url.startswith(f"https://management.azure.com{STORAGE}?"):
            deleted_storage["count"] += 1
            if deleted_storage["count"] == 1:
                raise cp.CleanupError("DELETE storage failed: (AccountProtectedFromDeletion) The account CannotBeDeleted.")
            return 200, None
        if method == "GET" and "/blobServices/default?" in url:
            return 200, {"properties": {
                "isVersioningEnabled": True,
                "deleteRetentionPolicy": {"enabled": True},
                "containerDeleteRetentionPolicy": {"enabled": True},
            }}
        if method == "GET" and "/blobServices/default/containers?" in url:
            return 200, {"value": [{
                "name": "receipts",
                "properties": {
                    "immutableStorageWithVersioning": {"enabled": True},
                    "hasImmutabilityPolicy": False,
                    "hasLegalHold": False,
                },
            }]}
        if method == "DELETE" and "/containers/receipts?" in url:
            return 200, None
        raise AssertionError(f"Unexpected _rest call: {method} {url}")

    def fake_run(self, args):
        calls.append(("run", args))
        if args[:2] == ["rest", "--method"] and args[2] == "PUT":
            return 0, "{}", ""
        if args[:2] == ["account", "get-access-token"]:
            return 0, json.dumps({"accessToken": "tok"}), ""
        if args[:3] == ["storage", "blob", "list"]:
            return 0, json.dumps([
                {"name": "a.json", "versionId": "v1"},
                {"name": "a.json", "versionId": None},
            ]), ""
        raise AssertionError(f"Unexpected _run call: {args}")

    monkeypatch.setattr(cp.AzRunner, "_rest", fake_rest)
    monkeypatch.setattr(cp.AzRunner, "_run", fake_run)

    # 1. Default runner MUST fail closed with diagnostics and zero mutations
    runner_default = cp.AzRunner()
    with pytest.raises(cp.CleanupError, match="Failing closed by default.*--clear-storage-delete-protection"):
        runner_default.arm_delete(STORAGE, "2023-05-01")
    assert not [c for c in calls if c[0] == "run"]
    assert not [c for c in calls if c[0] == "rest" and c[1] in ("PUT", "PATCH")]

    # 2. Opt-in runner disables soft-delete/versioning and empties unlocked container without touching NSP or publicNetworkAccess
    calls.clear()
    deleted_storage["count"] = 0
    import urllib.request

    class DummyResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    blob_deletes = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=30: blob_deletes.append(req.full_url) or DummyResp())
    runner_opt_in = cp.AzRunner(clear_storage_delete_protection=True)
    runner_opt_in.arm_delete(STORAGE, "2023-05-01")
    assert deleted_storage["count"] == 2
    assert len(blob_deletes) == 2
    assert any(c[0] == "rest" and c[1] == "DELETE" and "/containers/receipts?" in c[2] for c in calls)
    assert not any("networkSecurityPerimeters" in str(c) or "publicNetworkAccess" in str(c) for c in calls)

    # 3. When data-plane blob listing is blocked (e.g. SecuredByPerimeter on an empty container), control-plane container delete still succeeds
    calls.clear()
    deleted_storage["count"] = 0

    def fake_run_perimeter_blocked(self, args):
        if args[:2] == ["rest", "--method"] and args[2] == "PUT":
            return 0, "{}", ""
        if args[:2] == ["account", "get-access-token"]:
            return 0, json.dumps({"accessToken": "tok"}), ""
        if args[:3] == ["storage", "blob", "list"]:
            return 1, "", "PublicNetworkAccessNotEnabled"
        raise AssertionError(f"Unexpected _run call: {args}")

    monkeypatch.setattr(cp.AzRunner, "_run", fake_run_perimeter_blocked)
    runner_opt_in.arm_delete(STORAGE, "2023-05-01")
    assert deleted_storage["count"] == 2
    assert any(c[0] == "rest" and c[1] == "DELETE" and "/containers/receipts?" in c[2] for c in calls)


def test_storage_delete_protection_refuses_active_immutability_or_legal_hold(monkeypatch):
    def fake_rest(self, method, url, resource=None):
        if method == "DELETE" and url.startswith(f"https://management.azure.com{STORAGE}?"):
            raise cp.CleanupError("DELETE storage failed: (AccountProtectedFromDeletion) The account CannotBeDeleted.")
        if method == "GET" and "/blobServices/default?" in url:
            return 200, {"properties": {"isVersioningEnabled": True}}
        if method == "GET" and "/blobServices/default/containers?" in url:
            return 200, {"value": [{
                "name": "receipts",
                "properties": {"hasImmutabilityPolicy": True, "hasLegalHold": False},
            }]}
        raise AssertionError(f"Unexpected mutation call: {method} {url}")

    monkeypatch.setattr(cp.AzRunner, "_rest", fake_rest)
    runner_opt_in = cp.AzRunner(clear_storage_delete_protection=True)
    with pytest.raises(cp.CleanupError, match="active immutability policy or legal hold"):
        runner_opt_in.arm_delete(STORAGE, "2023-05-01")


def test_bicep_storage_accounts_disable_soft_delete_versioning_and_immutability():
    bicep = (ROOT / "deploy" / "resources.bicep").read_text(encoding="utf-8")
    assert "immutableStorageWithVersioning" not in bicep
    assert "isVersioningEnabled: true" not in bicep
    assert "deleteRetentionPolicy: { enabled: true" not in bicep
    assert "containerDeleteRetentionPolicy: { enabled: true" not in bicep
    assert bicep.count("isVersioningEnabled: false") >= 2
    assert bicep.count("deleteRetentionPolicy: { enabled: false }") >= 2
    assert bicep.count("containerDeleteRetentionPolicy: { enabled: false }") >= 2


def test_agent_versions_handles_deleted_foundry_subdomain_idempotently(monkeypatch):
    import types
    from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

    class FakeAgents:
        def __init__(self, exc):
            self.exc = exc

        def list_versions(self, agent_name):
            raise self.exc

        def delete_version(self, agent_name, agent_version):
            raise self.exc

    class FakeClient:
        def __init__(self, exc):
            self.agents = FakeAgents(exc)

    fake_exc = {"exc": ResourceNotFoundError("(ResourceNotFound) Subdomain does not map to a resource.")}
    monkeypatch.setitem(sys.modules, "azure.ai.projects", types.SimpleNamespace(
        AIProjectClient=lambda endpoint, credential: FakeClient(fake_exc["exc"])
    ))

    runner = cp.AzRunner()
    assert runner.agent_versions("https://deleted.services.ai.azure.com/api/projects/p", "pinned-score-agent") == []
    runner.agent_delete_version("https://deleted.services.ai.azure.com/api/projects/p", "pinned-score-agent", "1")

    err_403 = HttpResponseError(message="403 Forbidden")
    err_403.status_code = 403
    fake_exc["exc"] = err_403
    with pytest.raises(cp.CleanupError, match="Failed to list agent versions"):
        runner.agent_versions("https://deleted.services.ai.azure.com/api/projects/p", "pinned-score-agent")



