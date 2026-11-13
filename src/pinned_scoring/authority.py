"""Authority plane: proposed -> reviewed -> approved, on Fabric, fed by the Foundry agent.

The Foundry agent (ontology + tools over Fabric Gold) can only *propose* an action, through its
`propose_action` function tool. People named in the roles file move it forward. Every step is a
row in the append-only Delta table `authority_decisions` in the Fabric lakehouse, hash-chained so
tampering is detectable. Entra identities act; the agent never holds a role.
"""

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .contracts import ContractError, sha256_json
from .receipt import Receipt
from .verifier import verify_narration

TABLE = "authority_decisions"
GENESIS = "0" * 64
PROPOSED, REVIEWED, APPROVED, REJECTED = "proposed", "reviewed", "approved", "rejected"
NEXT = {PROPOSED: (REVIEWED, "reviewer"), REVIEWED: (APPROVED, "approver")}
REQUIRED, SINGLE_PERSON = "required", "single_person"
MODES = {REQUIRED, SINGLE_PERSON}
COLUMNS = ["proposal_id", "state", "actor", "entity_id", "receipt_sha256", "policy_sha256",
           "narration", "note", "at", "prev", "hash", "mode"]
# 'mode' was added later: rows written before it (mode None) are hashed without it.

WS = os.environ.get("FABRIC_WORKSPACE_ID", "00000000-0000-4000-8000-000000000019")
LH = os.environ.get("FABRIC_LAKEHOUSE_ID", "00000000-0000-4000-8000-000000000015")


class AuthorityError(ContractError):
    pass


@dataclass(frozen=True)
class Roles:
    reviewers: frozenset
    approvers: frozenset
    mode: str = REQUIRED

    @classmethod
    def load(cls, path) -> "Roles":
        """Entries are {"upn", "display_name"} objects (plain UPN strings are accepted in
        single_person mode). In 'required' mode every entry needs a display name and the
        approvers must include someone who is not a reviewer."""
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        mode = doc.get("separation_of_duties", REQUIRED)
        if mode not in MODES:
            raise AuthorityError(f"separation_of_duties must be one of {sorted(MODES)}")
        groups = {}
        for key in ("reviewers", "approvers"):
            upns = set()
            for item in doc.get(key, []):
                upn = item.get("upn") if isinstance(item, dict) else item
                name = item.get("display_name") if isinstance(item, dict) else None
                if not upn or (mode == REQUIRED and not (name and name.strip())):
                    raise AuthorityError(f"{key}: in 'required' mode every person needs "
                                         "'upn' and 'display_name'")
                upns.add(upn.lower())
            groups[key] = frozenset(upns)
        if mode == REQUIRED and not (groups["approvers"] - groups["reviewers"]):
            raise AuthorityError("'required' mode needs an approver who is not also a reviewer "
                                 "(list the second person's UPN and display name)")
        return cls(groups["reviewers"], groups["approvers"], mode)

    def has(self, person: str, role: str) -> bool:
        return person.lower() in (self.reviewers if role == "reviewer" else self.approvers)


def az_token(resource: str = "https://storage.azure.com") -> str:
    return subprocess.check_output(
        f"az account get-access-token --resource {resource} --query accessToken -o tsv",
        shell=True, text=True).strip()


def signed_in_user() -> str:
    return subprocess.check_output("az account show --query user.name -o tsv",
                                   shell=True, text=True).strip()


class FabricDecisionStore:
    """Append-only Delta table in the Fabric lakehouse (OneLake, Entra token)."""

    def __init__(self, workspace_id=WS, lakehouse_id=LH, token_fn=az_token):
        self.workspace_id, self.lakehouse_id, self._token = workspace_id, lakehouse_id, token_fn

    def _uri(self):
        from .fabric.delta_loader import table_uri
        return table_uri(self.workspace_id, self.lakehouse_id, TABLE)

    def read(self) -> list:
        from deltalake import DeltaTable
        from .fabric.delta_loader import storage_options
        try:
            table = DeltaTable(self._uri(), storage_options=storage_options(self._token()))
        except Exception as exc:  # table not created yet
            text = str(exc).lower()
            if (type(exc).__name__ == "TableNotFoundError" or "not a delta table" in text
                    or "no files in log" in text):
                return []
            raise
        rows = table.to_pyarrow_table().to_pylist()
        rows.sort(key=lambda r: r["at"])
        return rows

    def append(self, entry: dict) -> None:
        from deltalake import write_deltalake
        from .fabric.delta_loader import APPEND_ONLY, _arrow, storage_options
        spec = [(c, "ts" if c == "at" else "str", c in ("note", "narration", "mode"))
                for c in COLUMNS]
        write_deltalake(self._uri(), _arrow([{**entry, "mode": entry.get("mode")}], spec),
                        mode="append", schema_mode="merge",
                        storage_options=storage_options(self._token()),
                        configuration=APPEND_ONLY)


class AuthorityLog:
    def __init__(self, store, roles: Roles, agent_id: str = "foundry:pinned-score-agent",
                 max_age_days: int = 7):
        if agent_id.lower() in roles.reviewers | roles.approvers:
            raise AuthorityError("the agent cannot hold a reviewer or approver role")
        self.store, self.roles, self.agent_id, self.max_age = store, roles, agent_id, max_age_days

    def entries(self) -> list:
        return [{**e, "at": _iso(e["at"])} for e in self.store.read()]

    def verify_chain(self) -> None:
        prev = GENESIS
        for e in self.entries():
            body = {k: e[k] for k in COLUMNS if k != "hash" and not (k == "mode" and e.get(k) is None)}
            if e["prev"] != prev or sha256_json(body) != e["hash"]:
                raise AuthorityError("decision log failed hash-chain verification")
            prev = e["hash"]

    def history(self, proposal_id: str) -> list:
        return [e for e in self.entries() if e["proposal_id"] == proposal_id]

    def state(self, proposal_id: str) -> str:
        h = self.history(proposal_id)
        if not h:
            raise AuthorityError(f"unknown proposal {proposal_id!r}")
        return h[-1]["state"]

    def modes_used(self) -> list:
        return sorted({e["mode"] for e in self.entries() if e.get("mode")})

    def _append(self, proposal_id, state, actor, base, note, now, mode=None):
        entries = self.entries()
        body = {"proposal_id": proposal_id, "state": state, "actor": actor,
                "entity_id": base["entity_id"], "receipt_sha256": base["receipt_sha256"],
                "policy_sha256": base["policy_sha256"], "narration": base["narration"],
                "note": note, "at": _iso(now),
                "prev": entries[-1]["hash"] if entries else GENESIS}
        if mode:
            body["mode"] = mode
        entry = {**body, "hash": sha256_json(body)}
        self.store.append(entry)
        return entry

    def propose(self, receipt_wire: dict, narration: str, now=None) -> dict:
        """Called by the Foundry agent's propose_action tool. Gated on evidence."""
        now = now or datetime.now(timezone.utc)
        self.verify_chain()
        try:
            receipt = Receipt.from_wire(receipt_wire)
        except ValueError as exc:
            raise AuthorityError(str(exc)) from exc
        if receipt.status != "scored":
            raise AuthorityError(f"{receipt.entity_id} is not scored ({', '.join(receipt.reasons)})")
        as_of = datetime.fromisoformat(receipt.as_of)
        as_of = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of
        if as_of < now - timedelta(days=self.max_age):
            raise AuthorityError(f"receipt for {receipt.entity_id} is stale (as_of {receipt.as_of})")
        result = verify_narration(narration, receipt)
        if not result.ok:
            raise AuthorityError("narration failed verification: "
                                 + "; ".join(f"{v.kind}: {v.detail}" if hasattr(v, "detail")
                                             else v.kind for v in result.violations))
        pid = sha256_json({"receipt": receipt.receipt_sha256, "narration": narration})[:16]
        if self.history(pid):
            raise AuthorityError(f"proposal {pid} already exists")
        base = {"entity_id": receipt.entity_id, "receipt_sha256": receipt.receipt_sha256,
                "policy_sha256": receipt.policy_sha256, "narration": narration}
        return self._append(pid, PROPOSED, self.agent_id, base, "", now)

    def _check_person(self, person, role):
        if person.lower() == self.agent_id.lower():
            raise AuthorityError("the agent cannot advance a proposal")
        if not self.roles.has(person, role):
            raise AuthorityError(f"{person} is not a {role}")

    def advance(self, proposal_id: str, person: str, note: str = "", now=None) -> dict:
        self.verify_chain()
        h = self.history(proposal_id)
        if not h:
            raise AuthorityError(f"unknown proposal {proposal_id!r}")
        current = h[-1]["state"]
        if current not in NEXT:
            raise AuthorityError(f"proposal is {current}; nothing to advance")
        target, role = NEXT[current]
        self._check_person(person, role)
        if self.roles.mode == REQUIRED and any(e["actor"].lower() == person.lower() for e in h):
            raise AuthorityError(f"{person} already acted on this proposal (separation of duties)")
        return self._append(proposal_id, target, person, h[0], note,
                            now or datetime.now(timezone.utc), self.roles.mode)

    def reject(self, proposal_id: str, person: str, note: str, now=None) -> dict:
        self.verify_chain()
        h = self.history(proposal_id)
        if not h:
            raise AuthorityError(f"unknown proposal {proposal_id!r}")
        current = h[-1]["state"]
        if current not in NEXT:
            raise AuthorityError(f"proposal is {current}; cannot reject")
        self._check_person(person, NEXT[current][1])
        if not note.strip():
            raise AuthorityError("a rejection needs a reason")
        return self._append(proposal_id, REJECTED, person, h[0], note,
                            now or datetime.now(timezone.utc), self.roles.mode)


def _iso(value) -> str:
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


PROPOSE_TOOL = {
    "type": "function", "name": "propose_action",
    "description": "Propose a mitigation for one parcel for human review, attaching your own "
                   "explanation. You can only propose: a named reviewer and approver decide. "
                   "Rejected if the parcel is not scored, stale, or your text disagrees with the receipt.",
    "parameters": {"type": "object", "properties": {
        "parcel": {"type": "string", "description": "Parcel id, e.g. P003"},
        "narration": {"type": "string", "description": "Your explanation, quoting the receipt exactly"}},
        "required": ["parcel", "narration"], "additionalProperties": False},
    "strict": True,
}

AUTHORITY_INSTRUCTIONS = """

Authority:
- When asked to recommend or propose an action for a parcel, first read its receipt, then call
  propose_action with your explanation. You can only propose. You cannot review, approve or
  execute anything; say so if asked. Report the returned proposal_id and that it awaits a reviewer.
- Keep the narration short: state the parcel, its exact score and band from the receipt, and that
  it is proposed for human review. Do not name individual components unless you also quote their
  provenance word exactly as in the receipt.
- If propose_action returns an error, tell the user the reason; do not retry with changed numbers."""
