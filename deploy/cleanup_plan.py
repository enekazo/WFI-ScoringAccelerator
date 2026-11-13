"""Ownership ledger, offline deletion planner and guarded executor for deploy-all cleanup.

The ledger (default ``deploy/.out/ownership.json``) records exact IDs of everything the installer
CREATED or REUSED. Cleanup only ever deletes entries whose origin is ``created`` (or explicitly
operator-``adopted``). Names, tags and prefixes never authorise a deletion.

Sub-commands: init, record, record-azure, adopt (ledger maintenance); plan (offline, no cloud
calls); execute (live, guarded; called by deploy/uninstall.ps1 after ShouldProcess approval).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_LEDGER = Path(__file__).resolve().parent / ".out" / "ownership.json"
KINDS = {"resource_group", "arm_resource", "role_assignment", "foundry_connection",
         "foundry_agent", "fabric_workspace", "fabric_item", "fabric_capacity"}
ORIGINS = {"created", "reused", "adopted"}
OWNED_ORIGINS = {"created", "adopted"}
GUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
ARM_ID = re.compile(r"^/subscriptions/([^/]+)/", re.I)
# Parents whose deletion removes their children (documented Azure cascade).
CASCADE_TYPES = {"microsoft.storage/storageaccounts", "microsoft.cognitiveservices/accounts"}
API_VERSIONS = {
    "microsoft.resources/resourcegroups": "2024-03-01",
    "microsoft.storage/storageaccounts": "2023-05-01",
    "microsoft.cognitiveservices/accounts": "2025-06-01",
    "microsoft.managedidentity/userassignedidentities": "2023-01-31",
    "microsoft.network/networksecurityperimeters": "2023-08-01-preview",
    "microsoft.web/serverfarms": "2023-12-01",
    "microsoft.web/sites": "2023-12-01",
    "microsoft.operationalinsights/workspaces": "2023-09-01",
    "microsoft.insights/components": "2020-02-02",
    "microsoft.authorization/roleassignments": "2022-04-01",
    "microsoft.fabric/capacities": "2023-11-01",
    "microsoft.cognitiveservices/accounts/projects/connections": "2025-10-01-preview",
    # Azure refuses to delete a Foundry account that still has projects, and a perimeter that still has associations.
    "microsoft.cognitiveservices/accounts/projects": "2025-06-01",
    "microsoft.network/networksecurityperimeters/resourceassociations": "2023-08-01-preview",
}
# Children that must be deleted explicitly before their parent (no cascade).
EXPLICIT_CHILD_TYPES = {"microsoft.cognitiveservices/accounts/projects"}
# ARM resource types ignored by cleanup (tenant/perimeter governance and ARM deployment history outside deletion scope).
IGNORED_ARM_PREFIXES = (
    "microsoft.network/networksecurityperimeters",
    "microsoft.resources/deployments",
)
ARM_DELETE_ORDER = {
    "microsoft.cognitiveservices/accounts/projects": 0,
    "microsoft.web/sites": 1,
    "microsoft.alertsmanagement/smartdetectoralertrules": 1,
    "microsoft.eventgrid/systemtopics": 1,
    "microsoft.web/serverfarms": 2,
    "microsoft.insights/components": 2,
    "microsoft.cognitiveservices/accounts": 2,
    "microsoft.operationalinsights/workspaces": 3,
    "microsoft.storage/storageaccounts": 3,
    "microsoft.managedidentity/userassignedidentities": 4,
}
FABRIC_ITEM_ORDER = {"GraphModel": 0, "Ontology": 1, "KQLDatabase": 2, "Eventhouse": 3, "Lakehouse": 9}
FABRIC_AUTO_CHILDREN = {"SQLEndpoint"}


class CleanupError(Exception):
    """Any condition that must stop or fail cleanup visibly."""


class LedgerError(CleanupError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def arm_type(arm_id: str) -> str:
    parts = arm_id.strip("/").split("/")
    lower = [p.lower() for p in parts]
    if "providers" not in lower:
        return "microsoft.resources/resourcegroups"
    # Role assignments are scoped under their target; classify by the last provider segment.
    rest = parts[len(lower) - 1 - lower[::-1].index("providers") + 1:]
    types = [rest[0]] + [rest[i] for i in range(1, len(rest), 2)]
    return "/".join(types).lower()


def key_of(entry: dict) -> tuple[str, str]:
    return entry["kind"], str(entry["id"]).lower()


# ---------------------------------------------------------------- ledger
def new_ledger(tenant: str, subscription: str, deployment_id: str) -> dict:
    return {"schemaVersion": SCHEMA_VERSION, "tenantId": tenant, "subscriptionId": subscription,
            "deploymentId": deployment_id, "createdAt": _now(), "entries": [], "unresolved": []}


def validate_ledger(ledger: Any) -> dict:
    if not isinstance(ledger, dict) or ledger.get("schemaVersion") != SCHEMA_VERSION:
        raise LedgerError(f"Unsupported ownership ledger (expected schemaVersion {SCHEMA_VERSION}).")
    for f in ("tenantId", "subscriptionId", "deploymentId"):
        if not ledger.get(f) or "00000000-0000-0000-0000-000000000000" in str(ledger[f]):
            raise LedgerError(f"Ownership ledger field '{f}' is missing or a placeholder.")
    seen: dict[tuple, str] = {}
    for e in ledger.get("entries", []):
        if e.get("kind") not in KINDS or e.get("origin") not in ORIGINS or not e.get("id"):
            raise LedgerError(f"Invalid ledger entry: {e!r}")
        if not str(e.get("evidence") or "").strip():
            raise LedgerError(f"Ledger entry {e['kind']} {e['id']} has no creation evidence.")
        k = key_of(e)
        if k in seen and seen[k] != e["origin"]:
            raise LedgerError(f"Conflicting origins for {e['kind']} {e['id']}.")
        seen[k] = e["origin"]
        if e["kind"] in {"resource_group", "arm_resource", "role_assignment", "foundry_connection", "fabric_capacity"}:
            m = ARM_ID.match(e["id"])
            if not m or m.group(1).lower() != ledger["subscriptionId"].lower():
                raise LedgerError(f"{e['kind']} {e['id']} is not in ledger subscription {ledger['subscriptionId']}.")
        elif e["kind"] in {"fabric_workspace", "fabric_item"} and not GUID.match(str(e["id"])):
            raise LedgerError(f"{e['kind']} id must be a GUID, got {e['id']!r}.")
        if e["kind"] == "fabric_capacity" and not GUID.match(str((e.get("attrs") or {}).get("capacityId", ""))):
            raise LedgerError(f"fabric_capacity {e['id']} needs attrs.capacityId (the Fabric capacity GUID).")
        if e["kind"] == "fabric_item" and not GUID.match(str((e.get("attrs") or {}).get("workspaceId", ""))):
            raise LedgerError(f"fabric_item {e['id']} needs attrs.workspaceId.")
        if e["kind"] == "foundry_agent" and not (e.get("attrs") or {}).get("endpoint"):
            raise LedgerError(f"foundry_agent {e['id']} needs attrs.endpoint.")
    return ledger


def load_ledger(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise LedgerError(
            f"Ownership ledger not found: {path}. Cleanup never guesses ownership. For a deployment "
            "made before ownership tracking, attest exact IDs with 'cleanup_plan.py adopt' (see README).")
    try:
        return validate_ledger(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise LedgerError(f"Ownership ledger {path} is not valid JSON: {exc}") from exc


def save_ledger(ledger: dict, path: Path) -> None:
    validate_ledger(ledger)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def record(ledger: dict, kind: str, id_: str, origin: str, evidence: str,
           name: str = "", attrs: dict | None = None) -> dict:
    """Upsert. A resource once recorded as created/adopted is never downgraded to reused:
    a resumed installer run sees its own earlier creation as 'existing'."""
    entry = {"kind": kind, "id": id_, "name": name, "origin": origin, "evidence": evidence,
             "recordedAt": _now(), "attrs": attrs or {}}
    for i, cur in enumerate(ledger["entries"]):
        if key_of(cur) == key_of(entry):
            merged = dict(cur.get("attrs") or {})
            for k, v in entry["attrs"].items():
                if isinstance(v, list) and isinstance(merged.get(k), list):
                    merged[k] = sorted(set(merged[k]) | set(v))
                else:
                    merged[k] = v
            if cur["origin"] in OWNED_ORIGINS and origin == "reused":
                # keep origin and evidence; only enrich attributes (e.g. capacity GUID)
                cur["attrs"] = merged
                return cur
            entry["attrs"] = merged
            ledger["entries"][i] = entry
            return entry
    ledger["entries"].append(entry)
    return entry


def _top_level(arm_id: str) -> bool:
    """True for a resource-group-level resource (one type segment). `az resource list` reliably
    lists those; nested resources and role assignments are not guaranteed to appear."""
    t = arm_type(arm_id)
    return (t.count("/") == 1 and t not in ("microsoft.resources/resourcegroups",
                                            "microsoft.authorization/roleassignments"))


def _is_ignored_arm(arm_id: str) -> bool:
    if not arm_id or not str(arm_id).startswith("/subscriptions/"):
        return False
    t = arm_type(str(arm_id))
    return any(t == p or t.startswith(p + "/") for p in IGNORED_ARM_PREFIXES)


def _ignored_arm_reason(arm_id: str) -> str:
    t = arm_type(str(arm_id))
    if t == "microsoft.resources/deployments" or t.startswith("microsoft.resources/deployments/"):
        return "ignored: ARM deployment history is not deleted by cleanup"
    return "ignored: Network Security Perimeter is not deleted by cleanup"


def classify_azure_operations(pre_ids: list[str], operations: list[dict],
                              created_rg: str | None = None) -> tuple[list[str], list[dict]]:
    """Split Bicep 'Create' targets into (proven created, unresolved).

    A Create operation is also reported for PUTs on existing resources, so absence from the
    pre-deployment snapshot is only evidence for top-level resources. A nested resource or role
    assignment is proven created only when its resource group (created_rg) or a created ancestor
    was itself created by this run; otherwise it is unresolved, never guessed."""
    pre = {p.lower() for p in pre_ids}
    targets = []
    for op in operations:
        props = op.get("properties", op)
        target = (props.get("targetResource") or {}).get("id")
        if target and props.get("provisioningOperation") == "Create" and target.lower() not in pre:
            if not _is_ignored_arm(target):
                targets.append(target)
    targets = sorted(set(targets))
    created, unresolved = [], []
    for t in targets:
        if _top_level(t) or (created_rg and t.lower().startswith(created_rg.lower() + "/")):
            created.append(t)
            continue
        anc = [c for c in created if t.lower().startswith(c.lower() + "/")]
        if anc:
            created.append(t)
        else:
            unresolved.append({"kind": "arm_resource", "id": t,
                               "reason": "nested/RBAC resource with no pre-write existence evidence; "
                                         "cannot prove it was created by this installation"})
    return sorted(set(created)), unresolved


def classify_azure_created(pre_ids: list[str], operations: list[dict], created_rg: str | None = None) -> list[str]:
    return classify_azure_operations(pre_ids, operations, created_rg)[0]


_MISSING_CODES = {"resourcenotfound", "resourcegroupnotfound", "notfound", "itemnotfound",
                  "workspacenotfound", "parentresourcenotfound", "deletedaccountnotfound",
                  "notfounderror"}


def _error_dict_is_missing(err: dict) -> bool:
    code = err.get("code") or err.get("errorCode")
    if isinstance(code, str) and code.lower() in _MISSING_CODES:
        return True
    if isinstance(code, str) and code.lower() in {"usererror", "badrequest"}:
        inner = err.get("innerError") or err.get("innererror")
        while isinstance(inner, dict):
            icode = inner.get("code") or inner.get("errorCode")
            if isinstance(icode, str) and icode.lower() in _MISSING_CODES:
                return True
            inner = inner.get("innerError") or inner.get("innererror")
    return False


def is_missing_error(stderr: str) -> bool:
    """Positively recognise a top-level 'resource is absent' CLI/REST failure. Message text is never
    trusted: a 403 or other failure that merely mentions NotFound must not count as absence."""
    text = (stderr or "").strip()
    text = re.sub(r"^ERROR:\s*", "", text, flags=re.I).strip()
    if re.match(r"^not ?found(\s*$|\s*\()", text, re.I):  # az rest: "Not Found" status line
        return True
    json_candidates = []
    if text.startswith("{"):
        json_candidates.append(text)
    elif not text.startswith("("):
        idx = text.find("({")
        if idx != -1:
            json_candidates.append(text[idx + 1:])
    for candidate in json_candidates:
        try:
            body, _ = json.JSONDecoder().raw_decode(candidate)
        except ValueError:
            continue
        if isinstance(body, dict):
            err = body.get("error") if isinstance(body.get("error"), dict) else body
            return isinstance(err, dict) and _error_dict_is_missing(err)
        return False
    m = re.match(r"^\(([A-Za-z]+)\)", text)  # azure-cli: "(ResourceNotFound) The Resource ..."
    return bool(m and m.group(1).lower() in _MISSING_CODES)


# ---------------------------------------------------------------- planning
def _is_capacity(e: dict) -> bool:
    return e["kind"] == "fabric_capacity" or (
        e["kind"] == "arm_resource" and arm_type(e["id"]) == "microsoft.fabric/capacities")


def plan_cleanup(ledger: dict, *, remove_fabric: bool = False, purge: bool = False,
                 default_location: str | None = None,
                 expected: dict | None = None) -> dict:
    """Pure/offline: no cloud calls. Returns steps, preserved and unresolved lists."""
    validate_ledger(ledger)
    for field, value in (expected or {}).items():
        if value and str(ledger.get(field, "")).lower() != str(value).lower():
            raise LedgerError(f"{field} mismatch: ledger has '{ledger.get(field)}', target is '{value}'.")
    entries = ledger["entries"]
    owned = [e for e in entries if e["origin"] in OWNED_ORIGINS and not (
        e["kind"] == "arm_resource" and _is_ignored_arm(e["id"]))]
    preserved = [{"kind": e["kind"], "id": e["id"], "reason": "reused/pre-existing: not created by installer",
                  "evidence": e["evidence"]} for e in entries if e["origin"] == "reused"] + [
                 {"kind": e["kind"], "id": e["id"],
                  "reason": _ignored_arm_reason(e["id"]),
                  "evidence": e["evidence"]}
                 for e in entries if e["origin"] in OWNED_ORIGINS
                 and e["kind"] == "arm_resource" and _is_ignored_arm(e["id"])]
    unresolved = [dict(u) for u in ledger.get("unresolved", []) if not _is_ignored_arm(u.get("id", ""))]
    steps: list[dict] = []

    def add(phase, action, e, reason="", **extra):
        steps.append({"phase": phase, "action": action, "kind": e["kind"], "id": e["id"],
                      "name": e.get("name", ""), "evidence": e["evidence"], "reason": reason,
                      "attrs": e.get("attrs") or {}, **extra})

    cascade_parents = [e for e in owned if e["kind"] == "arm_resource" and arm_type(e["id"]) in CASCADE_TYPES]
    # A parent that holds a reused/unresolved descendant must not be deleted: cascade would remove it.
    held = [e["id"].lower() for e in entries if e["origin"] == "reused"] + \
           [str(u.get("id", "")).lower() for u in unresolved]
    blocked = [p for p in cascade_parents if any(h.startswith(p["id"].lower() + "/") for h in held)]
    for p in blocked:
        unresolved.append({"kind": p["kind"], "id": p["id"],
                           "reason": "owned parent contains reused/unresolved descendant(s); deleting it would "
                                     "cascade-delete them, so the parent is NOT deleted"})
    cascade_parents = [p for p in cascade_parents if p not in blocked]
    blocked_ids = {p["id"] for p in blocked}

    def cascaded_by(e):
        if e["kind"] == "arm_resource" and arm_type(e["id"]) in EXPLICIT_CHILD_TYPES:
            return None
        for p in cascade_parents:
            if e["id"].lower().startswith(p["id"].lower() + "/"):
                return p
        return None

    for e in owned:
        if e["kind"] == "foundry_agent":
            versions = (e.get("attrs") or {}).get("versions") or []
            if not versions and not (e.get("attrs") or {}).get("agentCreated"):
                unresolved.append({"kind": e["kind"], "id": e["id"], "reason": "no created versions recorded"})
            else:
                add(1, "delete_agent_versions", e)
    for e in sorted((x for x in owned if x["kind"] == "fabric_item"),
                    key=lambda x: FABRIC_ITEM_ORDER.get((x.get("attrs") or {}).get("itemType"), 5)):
        if not remove_fabric:
            add(2, "skip", e, "Fabric items kept: -RemoveFabricItems not specified")
        else:
            add(2, "delete_fabric_item", e)
    for e in owned:
        if e["kind"] == "fabric_workspace":
            if not remove_fabric:
                add(2, "skip", e, "Fabric workspace kept: -RemoveFabricItems not specified")
            else:
                add(3, "delete_fabric_workspace_if_only_owned", e,
                    "deleted only if every remaining item is installer-owned")
    for e in owned:
        if e["kind"] == "role_assignment":
            if cascaded_by(e):
                add(4, "cascade", e, f"removed with owned parent {cascaded_by(e)['id']}", parent=cascaded_by(e)["id"])
            else:
                add(4, "delete_arm", e, api=API_VERSIONS["microsoft.authorization/roleassignments"])
    for e in owned:
        if e["kind"] == "foundry_connection":
            if cascaded_by(e):
                add(5, "cascade", e, f"removed with owned Foundry account {cascaded_by(e)['id']}", parent=cascaded_by(e)["id"])
            else:
                add(5, "delete_arm", e, api=API_VERSIONS["microsoft.cognitiveservices/accounts/projects/connections"])
    arm = [e for e in owned if e["kind"] == "arm_resource" and not _is_capacity(e) and e["id"] not in blocked_ids]
    for e in owned:
        if not _is_capacity(e):
            continue
        cap_guid = (e.get("attrs") or {}).get("capacityId")
        if not cap_guid:
            unresolved.append({"kind": e["kind"], "id": e["id"],
                               "reason": "capacity GUID (attrs.capacityId) not recorded; cannot verify no other workspace uses it"})
        elif not remove_fabric:
            add(5, "skip", e, "Fabric capacity kept: -RemoveFabricItems not specified")
        else:
            add(6, "delete_fabric_capacity_if_unshared", e,
                "installer-created capacity; refused if any workspace outside this installation is assigned to it",
                api=API_VERSIONS["microsoft.fabric/capacities"])
    for e in sorted(arm, key=lambda x: (ARM_DELETE_ORDER.get(arm_type(x["id"]), 2), -len(x["id"]))):
        t = arm_type(e["id"])
        if cascaded_by(e):
            add(6, "cascade", e, f"removed with owned parent {cascaded_by(e)['id']}", parent=cascaded_by(e)["id"])
        elif t not in API_VERSIONS:
            unresolved.append({"kind": e["kind"], "id": e["id"], "reason": f"no known api-version for {t}"})
        else:
            add(6, "delete_arm", e, api=API_VERSIONS[t])
    for e in owned:
        if e["kind"] == "resource_group":
            add(7, "delete_resource_group_if_only_owned", e,
                "deleted only when empty after owned resources are removed", api=API_VERSIONS[arm_type(e["id"])])
    if purge:
        for e in cascade_parents:
            if arm_type(e["id"]) == "microsoft.cognitiveservices/accounts":
                loc = (e.get("attrs") or {}).get("location") or default_location
                if not loc:
                    unresolved.append({"kind": "purge", "id": e["id"], "reason": "account location not recorded"})
                else:
                    e_loc = dict(e, attrs={**(e.get("attrs") or {}), "location": loc})
                    add(8, "purge_soft_deleted_account", e_loc, "IRREVERSIBLE: purges only this exact account id")
    steps.sort(key=lambda s: s["phase"])
    return {"tenantId": ledger["tenantId"], "subscriptionId": ledger["subscriptionId"],
            "deploymentId": ledger["deploymentId"], "steps": steps, "preserved": preserved,
            "unresolved": unresolved, "purge": purge, "removeFabric": remove_fabric}


def render_plan(plan: dict) -> str:
    lines = [f"Tenant:       {plan['tenantId']}", f"Subscription: {plan['subscriptionId']}",
             f"Deployment:   {plan['deploymentId']}",
             f"Options:      RemoveFabricItems={plan['removeFabric']} Purge={plan['purge']}",
             "Planned actions (only resources proven created by this installation):"]
    for s in plan["steps"]:
        lines.append(f"  [{s['phase']}] {s['action']:<38} {s['kind']} {s['id']}")
        lines.append(f"        evidence: {s['evidence']}" + (f" | {s['reason']}" if s["reason"] else ""))
    lines.append("Preserved (reused / not owned):")
    lines += [f"  {p['kind']} {p['id']} - {p['reason']}" for p in plan["preserved"]] or ["  (none recorded)"]
    lines.append("Always preserved: subscription, tenant, reused capacity/resources and unrelated items. A capacity is deleted only if recorded as created.")
    lines.append("Unresolved (no provenance; NOT deleted):")
    lines += [f"  {u.get('kind')} {u.get('id', u.get('description', ''))} - {u['reason']}"
              for u in plan["unresolved"]] or ["  (none)"]
    return "\n".join(lines)


# ---------------------------------------------------------------- runner
class AzRunner:
    """Real cloud access via the az CLI. Every non-404 failure raises CleanupError."""

    def __init__(self, az: str = "az", *, clear_storage_delete_protection: bool = False):
        self.az = az
        self.clear_storage_delete_protection = clear_storage_delete_protection

    def _run(self, args: list[str]) -> tuple[int, str, str]:
        # shutil.which resolves az.cmd on Windows, which CreateProcess cannot find from a bare "az".
        exe = shutil.which(self.az)
        if exe is None:
            raise CleanupError(f"Azure CLI '{self.az}' was not found on PATH; nothing was changed.")
        p = subprocess.run([exe, *args], capture_output=True, text=True, check=False)
        return p.returncode, p.stdout, p.stderr

    def _rest(self, method: str, url: str, resource: str | None = None) -> tuple[int, Any]:
        args = ["rest", "--method", method, "--url", url, "-o", "json"]
        if resource:
            args += ["--resource", resource]
        rc, out, err = self._run(args)
        if rc != 0:
            if is_missing_error(err):
                return 404, None
            hint = " (retention/immutability or lock in effect; remove it deliberately, cleanup will not force)" \
                if re.search(r"immutab|locked|ScopeLocked|retention", err, re.I) else ""
            raise CleanupError(f"{method} {url} failed: {err.strip()}{hint}")
        return 200, (json.loads(out) if out.strip() else None)

    def account_show(self) -> dict:
        rc, out, err = self._run(["account", "show", "-o", "json"])
        if rc != 0:
            raise CleanupError(f"az account show failed (run az login): {err.strip()}")
        d = json.loads(out)
        return {"tenantId": d["tenantId"], "subscriptionId": d["id"]}

    def arm_get(self, id_: str, api: str):
        return self._rest("GET", f"https://management.azure.com{id_}?api-version={api}")

    def arm_delete(self, id_: str, api: str) -> None:
        url = f"https://management.azure.com{id_}?api-version={api}"
        try:
            self._rest("DELETE", url)
        except CleanupError as exc:
            if arm_type(id_) == "microsoft.storage/storageaccounts" and (
                "AccountProtectedFromDeletion" in str(exc) or "ContainerOperationFailure" in str(exc)
            ):
                diag = self._diagnose_storage_delete_protection(id_, api)
                if diag["locked_containers"]:
                    raise CleanupError(
                        f"{exc} — {diag['summary']}. Refusing: container(s) {diag['locked_containers']} have an "
                        "active immutability policy or legal hold."
                    ) from exc
                if not self.clear_storage_delete_protection:
                    raise CleanupError(
                        f"{exc} — {diag['summary']}. Failing closed by default (no retention, versioning, container, "
                        "network, or NSP settings were modified). Re-run with --clear-storage-delete-protection "
                        "(-ClearStorageDeleteProtection) only if you explicitly authorize disabling soft-delete/"
                        "versioning and deleting unlocked blob versions/containers on this owned storage account."
                    ) from exc
                self._clear_unlocked_storage_delete_protection(id_, api, diag)
                self._rest("DELETE", url)
            else:
                raise

    def _diagnose_storage_delete_protection(self, id_: str, api: str) -> dict:
        """Read-only inspection of blob service retention/versioning and container immutability state."""
        acct = id_.rstrip("/").rsplit("/", 1)[-1]
        versioning = blob_sd = container_sd = None
        try:
            st, bsvc = self._rest("GET", f"https://management.azure.com{id_}/blobServices/default?api-version={api}")
            if st == 200 and isinstance(bsvc, dict):
                bprops = bsvc.get("properties") or {}
                versioning = bool(bprops.get("isVersioningEnabled"))
                blob_sd = bool((bprops.get("deleteRetentionPolicy") or {}).get("enabled"))
                container_sd = bool((bprops.get("containerDeleteRetentionPolicy") or {}).get("enabled"))
        except CleanupError:
            pass
        containers: list[dict] = []
        immut_ver: list[str] = []
        locked: list[str] = []
        try:
            st, cbody = self._rest("GET", f"https://management.azure.com{id_}/blobServices/default/containers?api-version={api}")
            containers = (cbody or {}).get("value", []) if st == 200 and isinstance(cbody, dict) else []
            for c in containers:
                cname = c.get("name")
                props = c.get("properties") or {}
                if not cname:
                    continue
                if ((props.get("immutableStorageWithVersioning") or {}).get("enabled")):
                    immut_ver.append(str(cname))
                if props.get("hasImmutabilityPolicy") or props.get("hasLegalHold"):
                    locked.append(str(cname))
        except CleanupError:
            pass
        summary = (
            f"storage account '{acct}' deletion blocked by protection state "
            f"(isVersioningEnabled={versioning}, blobSoftDelete={blob_sd}, containerSoftDelete={container_sd}, "
            f"immutableVersioningContainers={immut_ver}, lockedOrLegalHoldContainers={locked})"
        )
        return {
            "account": acct,
            "containers": containers,
            "immutable_versioning_containers": immut_ver,
            "locked_containers": locked,
            "summary": summary,
        }

    def _clear_unlocked_storage_delete_protection(self, id_: str, api: str, diag: dict) -> None:
        """Opt-in only: clear unlocked version-level immutability containers & soft-delete on an owned storage account.
        Never modifies Network Security Perimeters, publicNetworkAccess, legal holds, or active immutability policies."""
        from concurrent.futures import ThreadPoolExecutor
        import urllib.request
        from urllib.parse import quote

        acct = diag["account"]
        if diag["locked_containers"]:
            raise CleanupError(
                f"Container(s) {diag['locked_containers']} on {acct} have an active immutability policy or legal hold."
            )
        # 1. Disable versioning, blob soft-delete, and container soft-delete on blobServices/default
        rc, _, err = self._run(["rest", "--method", "PUT", "--url",
                                f"https://management.azure.com{id_}/blobServices/default?api-version={api}",
                                "--body", json.dumps({"properties": {
                                    "isVersioningEnabled": False,
                                    "deleteRetentionPolicy": {"enabled": False},
                                    "containerDeleteRetentionPolicy": {"enabled": False},
                                }}), "-o", "json"])
        if rc != 0:
            raise CleanupError(f"Failed to disable blob soft-delete/versioning on {acct}: {err.strip()}")
        containers = diag["containers"]
        if not containers:
            return
        rc, tok_out, tok_err = self._run(["account", "get-access-token", "--resource", "https://storage.azure.com/", "-o", "json"])
        if rc != 0:
            raise CleanupError(f"Cannot acquire storage data-plane token for {acct}: {tok_err.strip()}")
        token = json.loads(tok_out)["accessToken"]

        def _del_blob(url: str) -> None:
            req = urllib.request.Request(url, method="DELETE", headers={
                "Authorization": f"Bearer {token}",
                "x-ms-version": "2023-11-03",
            })
            try:
                with urllib.request.urlopen(req, timeout=30):
                    pass
            except Exception as e:
                if "404" not in str(e) and "BlobNotFound" not in str(e):
                    raise CleanupError(f"DELETE {url} failed: {e}") from e

        for c in containers:
            cname = c.get("name")
            props = c.get("properties") or {}
            if not cname:
                continue
            if props.get("hasImmutabilityPolicy") or props.get("hasLegalHold"):
                raise CleanupError(f"Container {acct}/{cname} has an active immutability policy or legal hold.")
            rc, bout, berr = self._run(["storage", "blob", "list", "--account-name", acct, "--container-name", cname,
                                        "--include", "dv", "--auth-mode", "login", "-o", "json"])
            if rc != 0:
                try:
                    self._rest("DELETE", f"https://management.azure.com{id_}/blobServices/default/containers/{cname}?api-version={api}")
                    continue
                except CleanupError as arm_exc:
                    raise CleanupError(
                        f"Cannot list blobs in {acct}/{cname} (cleanup does not modify NSP associations or "
                        f"publicNetworkAccess): {berr.strip()} | control-plane container delete also failed: {arm_exc}"
                    ) from arm_exc
            blobs = json.loads(bout) if bout.strip() else []
            version_urls = []
            base_urls = []
            for b in blobs:
                bname = quote(str(b["name"]), safe="/")
                vid = b.get("versionId")
                if vid:
                    version_urls.append(f"https://{acct}.blob.core.windows.net/{cname}/{bname}?versionid={quote(str(vid), safe='')}")
                else:
                    base_urls.append(f"https://{acct}.blob.core.windows.net/{cname}/{bname}")
            with ThreadPoolExecutor(max_workers=8) as ex:
                list(ex.map(_del_blob, version_urls))
                list(ex.map(_del_blob, base_urls))
            self._rest("DELETE", f"https://management.azure.com{id_}/blobServices/default/containers/{cname}?api-version={api}")

    def arm_list_resources(self, rg_id: str) -> list[str]:
        url = f"https://management.azure.com{rg_id}/resources?api-version=2021-04-01"
        ids: list[str] = []
        seen = set()
        while url:
            if url in seen:
                raise CleanupError(f"Repeated nextLink listing {rg_id}; inventory incomplete.")
            seen.add(url)
            _, body = self._rest("GET", url)
            if body is None:
                if ids or len(seen) > 1:
                    raise CleanupError(f"Empty page while listing {rg_id}; inventory incomplete.")
                return ids
            if not isinstance(body, dict) or not isinstance(body.get("value"), list):
                raise CleanupError(f"Malformed resource listing for {rg_id} (no 'value' list); refusing to treat as empty.")
            for r in body["value"]:
                if not isinstance(r, dict) or not isinstance(r.get("id"), str):
                    raise CleanupError(f"Malformed resource entry listing {rg_id}: {r!r}")
                ids.append(r["id"])
            url = body.get("nextLink")
            if url is not None and not isinstance(url, str):
                raise CleanupError("Malformed nextLink in resource listing.")
        return ids

    def deleted_account(self, sub: str, loc: str, rg: str, name: str):
        return self._rest("GET", f"https://management.azure.com/subscriptions/{sub}/providers/Microsoft.CognitiveServices/"
                          f"locations/{loc}/resourceGroups/{rg}/deletedAccounts/{name}?api-version=2025-06-01")

    def purge_account(self, sub: str, loc: str, rg: str, name: str) -> None:
        self._rest("DELETE", f"https://management.azure.com/subscriptions/{sub}/providers/Microsoft.CognitiveServices/"
                   f"locations/{loc}/resourceGroups/{rg}/deletedAccounts/{name}?api-version=2025-06-01")

    def fabric(self, method: str, path: str):
        return self._rest(method, f"https://api.fabric.microsoft.com/v1{path}", "https://api.fabric.microsoft.com")

    def _fabric_pages(self, path: str, key: str, extra: str = "") -> list[dict]:
        """Strict paginated Fabric list: the response must carry `key` as a list of objects; repeated or
        dangling continuation tokens and empty follow-up pages raise instead of yielding a partial list."""
        from urllib.parse import quote
        out: list[dict] = []
        token, seen = None, set()
        while True:
            qs = extra + (f"{'&' if extra else '?'}continuationToken={quote(token, safe='')}" if token else "")
            _, body = self.fabric("GET", path + qs)
            if not isinstance(body, dict) or not isinstance(body.get(key), list):
                raise CleanupError(f"Malformed Fabric listing {path}: expected an object with a '{key}' list; "
                                   "refusing to treat as empty.")
            for it in body[key]:
                if not isinstance(it, dict):
                    raise CleanupError(f"Malformed entry in Fabric listing {path}: {it!r}")
            out += body[key]
            token = body.get("continuationToken")
            if not token:
                if body.get("continuationUri"):
                    raise CleanupError(f"Fabric listing {path} has continuationUri without continuationToken; incomplete.")
                return out
            if not isinstance(token, str) or token in seen:
                raise CleanupError(f"Fabric listing {path} returned an invalid or repeated continuationToken.")
            seen.add(token)

    def fabric_list_items(self, ws: str) -> list[dict]:
        items = self._fabric_pages(f"/workspaces/{ws}/items", "value")
        for it in items:
            if not isinstance(it.get("id"), str):
                raise CleanupError(f"Fabric item without id in workspace {ws}: {it!r}")
        return items

    def fabric_list_capacity_workspaces(self, capacity_guid: str) -> list[dict]:
        # Admin API: the only capacity-wide inventory (GET /workspaces lists only caller-visible workspaces).
        # Schema (Learn: Admin - Workspaces - List Workspaces): workspaces[], continuationToken, continuationUri.
        # Requires Fabric administrator; any failure (e.g. InsufficientPrivileges) propagates and blocks deletion.
        ws = self._fabric_pages("/admin/workspaces", "workspaces", f"?capacityId={capacity_guid}")
        for w in ws:
            if not isinstance(w.get("id"), str) or str(w.get("capacityId", "")).lower() != capacity_guid.lower():
                raise CleanupError(f"Admin workspace entry lacks id or matching capacityId {capacity_guid}: {w!r}")
        return ws

    def agent_versions(self, endpoint: str, name: str) -> list[str]:
        from azure.ai.projects import AIProjectClient
        from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
        from azure.identity import DefaultAzureCredential
        client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
        try:
            return [str(v.version) for v in client.agents.list_versions(agent_name=name)]
        except ResourceNotFoundError:
            return []
        except HttpResponseError as exc:
            if getattr(exc, "status_code", None) == 404 or is_missing_error(str(exc)):
                return []
            raise CleanupError(f"Failed to list agent versions for {name} at {endpoint}: {exc}") from exc

    def agent_delete_version(self, endpoint: str, name: str, version: str) -> None:
        from azure.ai.projects import AIProjectClient
        from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
        from azure.identity import DefaultAzureCredential
        try:
            AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential()).agents.delete_version(
                agent_name=name, agent_version=version)
        except ResourceNotFoundError:
            return
        except HttpResponseError as exc:
            if getattr(exc, "status_code", None) == 404 or is_missing_error(str(exc)):
                return
            raise CleanupError(f"Failed to delete agent {name}:{version} at {endpoint}: {exc}") from exc


# ---------------------------------------------------------------- execution
def _await_absent(r, id_: str, api: str, timeout: float, interval: float, sleep=time.sleep) -> None:
    deadline = time.monotonic() + timeout
    while True:
        status, _ = r.arm_get(id_, api)
        if status == 404:
            return
        if time.monotonic() > deadline:
            raise CleanupError(f"Timed out waiting for deletion of {id_} (still present).")
        sleep(interval)


def execute_plan(plan: dict, runner, *, timeout: float = 900, interval: float = 10, sleep=time.sleep) -> dict:
    """Live execution. Caller must already have obtained approval. Returns a result report."""
    ident = runner.account_show()
    if ident["tenantId"].lower() != plan["tenantId"].lower() or ident["subscriptionId"].lower() != plan["subscriptionId"].lower():
        raise CleanupError(f"Signed-in tenant/subscription ({ident['tenantId']}/{ident['subscriptionId']}) does not "
                           f"match ledger ({plan['tenantId']}/{plan['subscriptionId']}). Nothing was changed.")
    res = {"deleted": [], "absent": [], "cascaded": [], "preserved": [], "skipped": [], "failed": []}
    blocked_after = False
    fabric_ws_status: dict[str, str] = {}

    def fail(step, msg):
        res["failed"].append({"id": step["id"], "action": step["action"], "error": msg})

    total = len(plan["steps"])
    deferred: list = []

    def progress(msg):
        print(msg, file=sys.stderr, flush=True)

    def outcome(before):
        for k in ("deleted", "absent", "preserved", "skipped", "failed"):
            if len(res[k]) > before[k]:
                last = res[k][-1]
                extra = f": {last['error']}" if k == "failed" else ""
                return k.upper() + extra
        return "DONE"

    progress(f"Executing {total} planned steps (tenant {plan['tenantId']}, subscription {plan['subscriptionId']})")
    for n, step in enumerate(plan["steps"], 1):
        a, id_ = step["action"], step["id"]
        if a == "cascade":
            deferred.append((n, step))
            continue
        if a == "skip":
            res["skipped"].append({"id": id_, "reason": step["reason"]})
            progress(f"[{n}/{total}] SKIPPED {id_}: {step['reason']}")
            continue
        if a in {"delete_resource_group_if_only_owned",
                 "delete_fabric_capacity_if_unshared"} and (res["failed"] or blocked_after):
            res["skipped"].append({"id": id_, "reason": "blocked by earlier failure; fix and re-run"})
            progress(f"[{n}/{total}] SKIPPED {id_}: blocked by earlier failure")
            continue
        if a == "purge_soft_deleted_account" and any(
            f["id"].lower() == id_.lower() or f["id"].lower().startswith(id_.lower() + "/")
            for f in res["failed"]
        ):
            res["skipped"].append({"id": id_, "reason": "blocked by earlier failure; fix and re-run"})
            progress(f"[{n}/{total}] SKIPPED {id_}: blocked by earlier failure")
            continue
        before = {k: len(res[k]) for k in ("deleted", "absent", "preserved", "skipped", "failed")}
        progress(f"[{n}/{total}] {a} {id_} ...")
        try:
            if a == "delete_agent_versions":
                _delete_agent(runner, step, res)
            elif a == "delete_fabric_item":
                ws = step["attrs"]["workspaceId"]
                st, body = runner.fabric("GET", f"/workspaces/{ws}/items/{id_}")
                if st == 404:
                    res["absent"].append(id_)
                else:
                    if str(body.get("id", "")).lower() != id_.lower():
                        raise CleanupError("Returned item id does not match ledger id; refusing.")
                    runner.fabric("DELETE", f"/workspaces/{ws}/items/{id_}")
                    if runner.fabric("GET", f"/workspaces/{ws}/items/{id_}")[0] != 404:
                        raise CleanupError("Fabric item still present after delete.")
                    res["deleted"].append(id_)
            elif a == "delete_fabric_workspace_if_only_owned":
                _delete_workspace(runner, step, plan, res)
            elif a == "delete_arm":
                _delete_arm(runner, step, res, timeout, interval, sleep)
            elif a == "delete_fabric_capacity_if_unshared":
                _delete_capacity(runner, step, res, timeout, interval, sleep)
            elif a == "delete_resource_group_if_only_owned":
                _delete_rg(runner, step, res, timeout, interval, sleep)
            elif a == "purge_soft_deleted_account":
                _purge(runner, step, plan, res, timeout, interval, sleep)
            else:
                raise CleanupError(f"Unknown plan action {a}")
        except CleanupError as exc:
            fail(step, str(exc))
        progress(f"[{n}/{total}]   -> {outcome(before)}")
    # A cascaded child counts as removed only if its parent was actually deleted (or verified absent).
    gone = {str(x).lower() for x in res["deleted"] + res["absent"]}
    for n, step in deferred:
        parent = str(step.get("parent", "")).lower()
        if parent and parent in gone:
            res["cascaded"].append(step["id"])
            progress(f"[{n}/{total}] cascade (removed with {step['parent']}) {step['id']}")
        else:
            res["failed"].append({"id": step["id"], "action": "cascade",
                                  "error": f"parent {step.get('parent')} was not deleted; this child still exists"})
            progress(f"[{n}/{total}] NOT REMOVED (parent not deleted) {step['id']}")
    res["unresolved"] = plan["unresolved"]
    res["ok"] = not res["failed"] and not plan["unresolved"]
    return res


def _delete_arm(r, step, res, timeout, interval, sleep):
    id_, api = step["id"], step["api"]
    st, body = r.arm_get(id_, api)
    if st == 404:
        res["absent"].append(id_)
        return
    if str((body or {}).get("id", id_)).lower() != id_.lower():
        raise CleanupError(f"Returned id for {id_} differs from ledger; refusing.")
    if arm_type(id_) == "microsoft.cognitiveservices/accounts":
        blockers = _blocking_agents(res, id_)
        if blockers:
            raise CleanupError(f"not deleting Foundry account {id_}: deleting it would also remove preserved "
                               f"agent(s) {blockers} that this installation does not own.")
    r.arm_delete(id_, api)
    _await_absent(r, id_, api, timeout, interval, sleep)
    res["deleted"].append(id_)


def _delete_capacity(r, step, res, timeout, interval, sleep):
    id_, api = step["id"], step["api"]
    st, body = r.arm_get(id_, api)
    if st == 404:
        res["absent"].append(id_)
        return
    guid = str(step["attrs"]["capacityId"]).lower()
    try:
        assigned = []
        for w in r.fabric_list_capacity_workspaces(guid):
            if "capacityId" not in w:
                raise CleanupError(f"admin workspace entry without capacityId: {w!r}")
            if str(w["capacityId"]).lower() == guid and str(w.get("state", "Active")).lower() != "deleted":
                assigned.append(w)
    except CleanupError as e:
        raise CleanupError(f"cannot verify the capacity is unshared (Fabric administrator access to "
                           f"/admin/workspaces is required; no permissions are granted by cleanup): {e}") from e
    if assigned:
        res["preserved"].append({
            "id": id_, "reason": "capacity still has workspace(s) assigned that this installation did not remove; "
            "not deleting (would affect unrelated workspaces).",
            "contents": [f"{w.get('name') or w.get('displayName')}:{w['id']}" for w in assigned]})
        res["failed"].append({"id": id_, "action": step["action"],
                              "error": "created capacity could not be deleted safely; unrelated workspaces use it"})
        return
    r.arm_delete(id_, api)
    _await_absent(r, id_, api, timeout, interval, sleep)
    res["deleted"].append(id_)


def _delete_rg(r, step, res, timeout, interval, sleep):
    id_, api = step["id"], step["api"]
    st, _ = r.arm_get(id_, api)
    if st == 404:
        res["absent"].append(id_)
        return
    left = r.arm_list_resources(id_)
    if left:
        res["preserved"].append({"id": id_, "reason": f"resource group still contains {len(left)} resource(s) not owned by "
                                 "this installation (or whose deletion is pending)", "contents": left})
        return
    r.arm_delete(id_, api)
    _await_absent(r, id_, api, timeout, interval, sleep)
    res["deleted"].append(id_)


def _delete_agent(r, step, res):
    ep, name = step["attrs"]["endpoint"], step["id"]
    owned_versions = [str(v) for v in step["attrs"].get("versions", [])]
    existing = set(r.agent_versions(ep, name))
    for v in owned_versions:
        if v in existing:
            r.agent_delete_version(ep, name, v)
            res["deleted"].append(f"agent:{name}:{v}")
        else:
            res["absent"].append(f"agent:{name}:{v}")
    if existing - set(owned_versions):
        res["preserved"].append({"id": f"agent:{name}", "endpoint": ep, "blocksParentDeletion": True,
                                 "reason": "has versions not created by this installation"})


def _blocking_agents(res, account_id: str) -> list[str]:
    name = account_id.rstrip("/").rsplit("/", 1)[-1].lower()
    return [p["id"] for p in res["preserved"]
            if p.get("blocksParentDeletion") and f"//{name}." in str(p.get("endpoint", "")).lower() + "."]


def _delete_workspace(r, step, plan, res):
    ws = step["id"]
    st, body = r.fabric("GET", f"/workspaces/{ws}")
    if st == 404:
        res["absent"].append(ws)
        return
    owned_items = {s["id"].lower() for s in plan["steps"] if s["kind"] == "fabric_item" and s["attrs"].get("workspaceId", "").lower() == ws.lower()}
    foreign = [i for i in r.fabric_list_items(ws)
               if i["id"].lower() not in owned_items and i.get("type") not in FABRIC_AUTO_CHILDREN]
    if foreign:
        res["preserved"].append({"id": ws, "reason": "Fabric workspace contains items not owned by this installation",
                                 "contents": [f"{i.get('type')}:{i.get('displayName')}:{i['id']}" for i in foreign]})
        return
    r.fabric("DELETE", f"/workspaces/{ws}")
    if r.fabric("GET", f"/workspaces/{ws}")[0] != 404:
        raise CleanupError("Fabric workspace still present after delete.")
    res["deleted"].append(ws)


def _purge(r, step, plan, res, timeout, interval, sleep):
    id_ = step["id"]
    parts = id_.strip("/").split("/")
    sub, rg, name = parts[1], parts[3], parts[-1]
    loc = step["attrs"]["location"]
    if r.arm_get(id_, API_VERSIONS["microsoft.cognitiveservices/accounts"])[0] != 404:
        raise CleanupError("Account still exists; delete it before purge.")
    st, body = r.deleted_account(sub, loc, rg, name)
    if st == 404:
        res["absent"].append(f"soft-deleted:{id_}")
        return
    original = str(((body or {}).get("properties") or {}).get("accountId", "")).lower()
    if original != id_.lower():
        raise CleanupError(f"Soft-deleted account {name} belongs to a different deployment ({original or 'unknown id'}); not purging.")
    r.purge_account(sub, loc, rg, name)
    res["deleted"].append(f"purged:{id_}")


# ---------------------------------------------------------------- CLI
def _ledger_arg(a) -> Path:
    return Path(a.ledger or DEFAULT_LEDGER)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    for f in ("tenant", "subscription", "deployment-id"):
        p.add_argument(f"--{f}", required=True)
    p = sub.add_parser("record")
    p.add_argument("--kind", required=True, choices=sorted(KINDS))
    p.add_argument("--id", required=True)
    p.add_argument("--origin", required=True, choices=["created", "reused"])
    p.add_argument("--evidence", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--attrs", default="{}", help="JSON object")
    p = sub.add_parser("adopt", help="operator attests that this exact ID was created by the installer (legacy deployments)")
    p.add_argument("--kind", required=True, choices=sorted(KINDS))
    p.add_argument("--id", required=True)
    p.add_argument("--attestation", required=True)
    p.add_argument("--attrs", default="{}")
    p = sub.add_parser("record-azure", help="classify Bicep deployment operations against a pre-deployment snapshot")
    p.add_argument("--pre-snapshot", required=True, help="JSON list of resource ids that existed before deployment")
    p.add_argument("--operations", required=True, help="JSON from az deployment operation (sub/group) list")
    for name in ("plan", "execute"):
        p = sub.add_parser(name)
        p.add_argument("--remove-fabric", action="store_true")
        p.add_argument("--purge", action="store_true")
        p.add_argument("--clear-storage-delete-protection", action="store_true",
                       help="explicitly disable blob versioning/soft-delete and empty unlocked containers when deleting owned storage accounts")
        p.add_argument("--default-location")
        p.add_argument("--expect-tenant")
        p.add_argument("--expect-subscription")
        p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    path = _ledger_arg(a)
    try:
        if a.cmd == "init":
            if path.exists():
                raise LedgerError(f"{path} already exists; refusing to overwrite.")
            save_ledger(new_ledger(a.tenant, a.subscription, a.deployment_id), path)
        elif a.cmd in {"record", "adopt"}:
            ledger = load_ledger(path)
            origin = "adopted" if a.cmd == "adopt" else a.origin
            ev = f"operator attestation: {a.attestation}" if a.cmd == "adopt" else a.evidence
            record(ledger, a.kind, a.id, origin, ev, getattr(a, "name", ""), json.loads(a.attrs))
            save_ledger(ledger, path)
        elif a.cmd == "record-azure":
            ledger = load_ledger(path)
            pre = json.loads(Path(a.pre_snapshot).read_text(encoding="utf-8"))
            ops = json.loads(Path(a.operations).read_text(encoding="utf-8"))
            rg_ids = [e["id"] for e in ledger["entries"] if e["kind"] == "resource_group" and e["origin"] in OWNED_ORIGINS]
            created, unres = classify_azure_operations(pre, ops, rg_ids[0] if rg_ids else None)
            for rid in created:
                t = arm_type(rid)
                kind = ("resource_group" if t == "microsoft.resources/resourcegroups" else
                        "role_assignment" if t == "microsoft.authorization/roleassignments" else "arm_resource")
                record(ledger, kind, rid, "created", "Bicep deployment operation Create; absent in pre-deployment snapshot")
            known = {(u.get("id") or "").lower() for u in ledger["unresolved"]}
            owned_ids = {e["id"].lower() for e in ledger["entries"] if e["origin"] in OWNED_ORIGINS}
            for u in unres:
                if u["id"].lower() not in known and u["id"].lower() not in owned_ids:
                    ledger["unresolved"].append(u)
            save_ledger(ledger, path)
        else:
            plan = plan_cleanup(load_ledger(path), remove_fabric=a.remove_fabric, purge=a.purge,
                                default_location=a.default_location,
                                expected={"tenantId": a.expect_tenant, "subscriptionId": a.expect_subscription})
            if a.cmd == "plan":
                print(json.dumps(plan, indent=2) if a.json else render_plan(plan))
                print("\nNo cloud calls were made.", file=sys.stderr)
                return 0
            res = execute_plan(plan, AzRunner(clear_storage_delete_protection=a.clear_storage_delete_protection))
            print(json.dumps(res, indent=2))
            return 0 if res["ok"] else 2
    except CleanupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
