import subprocess, json, pathlib
HOOKS = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "ownership-hooks.ps1"
SUB = "11111111-1111-1111-1111-111111111111"
TEN = "22222222-2222-2222-2222-222222222222"


def ps(script):
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", f". '{HOOKS}'; {script}"],
                          capture_output=True, text=True)


def test_init_and_record_write_ledger_and_fail_loudly(tmp_path):
    led = tmp_path / "ownership.json"
    r = ps(f"Initialize-OwnershipLedger -LedgerPath '{led}' -TenantId {TEN} -SubscriptionId {SUB} -DeploymentId d1; "
           f"Initialize-OwnershipLedger -LedgerPath '{led}' -TenantId {TEN} -SubscriptionId {SUB} -DeploymentId d1; "
           f"Record-OwnedResource -LedgerPath '{led}' -Kind fabric_workspace -Id 33333333-3333-3333-3333-333333333333 "
           f"-Origin created -Evidence 'POST /workspaces'")
    assert r.returncode == 0, r.stderr + r.stdout
    data = json.loads(led.read_text())
    assert any(e["origin"] == "created" and e["kind"] == "fabric_workspace" for e in data["entries"])
    bad = ps(f"Record-OwnedResource -LedgerPath '{led}' -Kind fabric_workspace -Id not-a-guid -Origin created -Evidence x")
    assert bad.returncode != 0
