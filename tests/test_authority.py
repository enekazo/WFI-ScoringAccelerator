"""Authority plane tests. The in-memory FakeStore is used ONLY here; product code uses
FabricDecisionStore (Delta table on Fabric)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pinned_scoring import Observation, Policy, score
from pinned_scoring import foundry_agent as fa
from pinned_scoring.authority import (AuthorityError, AuthorityLog, FabricDecisionStore, Roles)

ROOT = Path(__file__).resolve().parents[1]
POLICY = Policy.load(ROOT / "examples" / "wildfire" / "policy.json")
NOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
BASE = {"fuel_load": ("20", "t/ha"), "dead_fuel_pct": ("50", "%"),
        "fuel_moisture_pct": ("10", "%"), "weather_danger": ("40", "index"),
        "wind_kmh": ("30", "km/h")}
ROLES = Roles(frozenset({"rev@x.com", "rev2@x.com"}), frozenset({"app@x.com"}))


def receipt(drop=(), when=NOW):
    obs = [Observation("P1", m, v, u, when - timedelta(hours=1), when + timedelta(days=60), "measured")
           for m, (v, u) in BASE.items() if m not in drop]
    return score("P1", obs, POLICY, when)


class FakeStore:
    def __init__(self):
        self.rows = []

    def read(self):
        return [dict(r) for r in self.rows]

    def append(self, entry):
        self.rows.append(dict(entry))


@pytest.fixture
def log():
    return AuthorityLog(FakeStore(), ROLES)


def good(log):
    r = receipt()
    return log.propose(r.wire(), f"P1 scored {r.score} in the {r.band} band.", now=NOW)


def test_happy_path(log):
    e = good(log)
    pid = e["proposal_id"]
    assert log.state(pid) == "proposed"
    log.advance(pid, "REV@x.com", "ok", now=NOW)
    log.advance(pid, "app@x.com", now=NOW)
    assert log.state(pid) == "approved"
    log.verify_chain()
    with pytest.raises(AuthorityError, match="nothing to advance"):
        log.advance(pid, "app@x.com")


def test_agent_cannot_advance(log):
    pid = good(log)["proposal_id"]
    with pytest.raises(AuthorityError, match="agent cannot"):
        log.advance(pid, log.agent_id)


def test_agent_cannot_hold_role():
    with pytest.raises(AuthorityError):
        AuthorityLog(FakeStore(), Roles(frozenset({"foundry:pinned-score-agent"}), frozenset()))


def test_same_person_cannot_review_and_approve():
    both = Roles(frozenset({"a@x.com"}), frozenset({"a@x.com"}))
    lg = AuthorityLog(FakeStore(), both)
    pid = good(lg)["proposal_id"]
    lg.advance(pid, "a@x.com", now=NOW)
    with pytest.raises(AuthorityError, match="separation of duties"):
        lg.advance(pid, "a@x.com")


def test_non_role_blocked_and_wrong_role_blocked(log):
    pid = good(log)["proposal_id"]
    with pytest.raises(AuthorityError, match="not a reviewer"):
        log.advance(pid, "stranger@x.com")
    with pytest.raises(AuthorityError, match="not a reviewer"):
        log.advance(pid, "app@x.com")
    log.advance(pid, "rev@x.com", now=NOW)
    with pytest.raises(AuthorityError, match="not a approver"):
        log.advance(pid, "rev2@x.com")


def test_stale_receipt(log):
    r = receipt(when=NOW - timedelta(days=30))
    with pytest.raises(AuthorityError, match="stale"):
        log.propose(r.wire(), f"P1 scored {r.score}.", now=NOW)


def test_incomplete_receipt(log):
    r = receipt(drop=("wind_kmh",))
    with pytest.raises(AuthorityError, match="not scored"):
        log.propose(r.wire(), "P1 is incomplete.", now=NOW)


def test_bad_narration(log):
    r = receipt()
    with pytest.raises(AuthorityError, match="narration"):
        log.propose(r.wire(), "P1 scored 99.9 and is low.", now=NOW)


def test_altered_receipt_rejected(log):
    wire = receipt().wire()
    wire["score"] = "1"
    with pytest.raises(AuthorityError, match="hash mismatch"):
        log.propose(wire, "P1 scored 1.", now=NOW)


def test_tampered_chain_detected(log):
    pid = good(log)["proposal_id"]
    log.advance(pid, "rev@x.com", now=NOW)
    log.store.rows[0]["narration"] = "edited"
    with pytest.raises(AuthorityError, match="hash-chain"):
        log.verify_chain()
    with pytest.raises(AuthorityError, match="hash-chain"):
        log.advance(pid, "app@x.com")


def test_deleted_row_detected(log):
    pid = good(log)["proposal_id"]
    log.advance(pid, "rev@x.com", now=NOW)
    del log.store.rows[0]
    with pytest.raises(AuthorityError):
        log.verify_chain()


def test_duplicate_proposal(log):
    good(log)
    with pytest.raises(AuthorityError, match="already exists"):
        good(log)


def test_reject_needs_reason(log):
    pid = good(log)["proposal_id"]
    with pytest.raises(AuthorityError, match="reason"):
        log.reject(pid, "rev@x.com", "  ")
    log.reject(pid, "rev@x.com", "not now", now=NOW)
    assert log.state(pid) == "rejected"
    with pytest.raises(AuthorityError, match="cannot reject"):
        log.reject(pid, "rev@x.com", "again")


def test_agent_toolbox_can_only_propose(tmp_path, log):
    from pinned_scoring.receipts_store import write_receipts
    r = receipt(when=datetime.now(timezone.utc))
    write_receipts([r], tmp_path)
    ctx = type("Ctx", (), {})()
    box = fa.AuthorityToolbox.__new__(fa.AuthorityToolbox)
    fa.FoundryToolbox.__init__(box, tmp_path)
    box._log = log
    out = box.call("propose_action", {"parcel": "P1", "narration": f"P1 scored {r.score}."})
    assert out["awaiting"] == "reviewer" and "error" not in out
    assert "error" in box.call("propose_action", {"parcel": "NOPE", "narration": "x"})
    names = {t["name"] for t in fa.agent_definition.__globals__["TOOL_DEFINITIONS"]}
    assert not names & {"review_action", "approve_action", "execute_action"}


def test_store_missing_table_returns_empty(monkeypatch):
    import deltalake
    from deltalake.exceptions import TableNotFoundError

    def boom(*a, **k):
        raise TableNotFoundError("no table")
    monkeypatch.setattr(deltalake, "DeltaTable", boom)
    assert FabricDecisionStore(token_fn=lambda: "t").read() == []


import json  # noqa: E402

AGENT = "foundry:pinned-score-agent"


def solo(mode):
    return AuthorityLog(FakeStore(), Roles(frozenset({"me@x.com"}), frozenset({"me@x.com"}), mode))


def test_single_person_mode_allows_same_person_and_marks_decisions():
    log = solo("single_person")
    pid = good(log)["proposal_id"]
    log.advance(pid, "me@x.com", now=NOW)
    log.advance(pid, "me@x.com", now=NOW)
    assert log.state(pid) == "approved"
    log.verify_chain()
    assert [e.get("mode") for e in log.history(pid)] == [None, "single_person", "single_person"]
    assert log.modes_used() == ["single_person"]


def test_required_mode_blocks_same_person_and_marks_decisions():
    log = solo("required")
    pid = good(log)["proposal_id"]
    log.advance(pid, "me@x.com", now=NOW)
    with pytest.raises(AuthorityError, match="separation of duties"):
        log.advance(pid, "me@x.com", now=NOW)
    assert log.history(pid)[1]["mode"] == "required"


@pytest.mark.parametrize("mode", ["required", "single_person"])
def test_agent_never_reviews_or_approves_in_either_mode(mode):
    log = solo(mode)
    pid = good(log)["proposal_id"]
    with pytest.raises(AuthorityError, match="agent cannot advance"):
        log.advance(pid, AGENT)
    log.advance(pid, "me@x.com", now=NOW)
    with pytest.raises(AuthorityError, match="agent cannot advance"):
        log.advance(pid, AGENT)
    with pytest.raises(AuthorityError, match="agent cannot advance"):
        log.reject(pid, AGENT, "no")
    with pytest.raises(AuthorityError, match="cannot hold"):
        AuthorityLog(FakeStore(), Roles(frozenset({AGENT}), frozenset({AGENT}), mode))


def test_mode_survives_tamper_check():
    log = solo("single_person")
    pid = good(log)["proposal_id"]
    log.advance(pid, "me@x.com", now=NOW)
    log.store.rows[1]["mode"] = "required"
    with pytest.raises(AuthorityError, match="hash-chain"):
        log.verify_chain()


def write_roles(tmp_path, doc):
    p = tmp_path / "roles.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_roles_file_required_is_default_and_needs_second_person_details(tmp_path):
    person = {"upn": "a@x.com", "display_name": "A"}
    with pytest.raises(AuthorityError, match="not also a reviewer"):
        Roles.load(write_roles(tmp_path, {"reviewers": [person], "approvers": [person]}))
    with pytest.raises(AuthorityError, match="display_name"):
        Roles.load(write_roles(tmp_path, {"reviewers": [person], "approvers": ["b@x.com"]}))
    roles = Roles.load(write_roles(tmp_path, {"reviewers": [person],
                                              "approvers": [{"upn": "B@x.com", "display_name": "B"}]}))
    assert roles.mode == "required" and roles.has("b@x.com", "approver")


def test_roles_file_single_person_and_bad_mode(tmp_path):
    roles = Roles.load(write_roles(tmp_path, {"separation_of_duties": "single_person",
                                              "reviewers": ["a@x.com"], "approvers": ["a@x.com"]}))
    assert roles.mode == "single_person" and roles.has("a@x.com", "approver")
    with pytest.raises(AuthorityError, match="separation_of_duties"):
        Roles.load(write_roles(tmp_path, {"separation_of_duties": "none"}))


def test_example_roles_template_loads():
    assert Roles.load(ROOT / "examples" / "wildfire" / "roles.example.json").mode == "required"
