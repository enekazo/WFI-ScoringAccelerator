import json
import os
import stat

import pytest

from pinned_scoring.experiment.arms import ARMS, parse_answer, run_arm
from pinned_scoring.experiment.clients import StubModelClient
from pinned_scoring.experiment.config import load_config
from pinned_scoring.experiment.metrics import bootstrap_ci, score_answer
from pinned_scoring.experiment.runner import (POLICY_PATH, build_cases, deterministic_sensitivity,
                                              run_experiment)
from pinned_scoring.policy import Policy
from pinned_scoring.receipts_store import ReadOnlyReceiptStore, get_receipt, list_receipts
from pinned_scoring.synth import read_dataset

DS = read_dataset()
POLICY = Policy.load(POLICY_PATH)


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    d = tmp_path_factory.mktemp("rc")
    return build_cases(DS, POLICY, sorted(DS.observations), d), d


def by_fault(cases, fault):
    return next(c for c in cases.values() if c.truth["fault"] == fault)


def test_store_roundtrip_and_filters(cases):
    _, d = cases
    store = ReadOnlyReceiptStore(d)
    assert len(store.list_receipts()) == 50
    assert all(r["status"] == "incomplete" for r in store.list_receipts(status="incomplete"))
    assert get_receipt("P001", d)["entity_id"] == "P001"
    assert list_receipts(band="nonexistent", directory=d) == []


def test_store_is_read_only_and_tamper_evident(cases, tmp_path):
    _, d = cases
    store = ReadOnlyReceiptStore(d)
    with pytest.raises(AttributeError):
        store.x = 1
    assert not hasattr(store, "write") and not hasattr(store, "delete")
    assert not os.stat(d / "P001.json").st_mode & stat.S_IWRITE
    got = store.get_receipt("P001")
    got["score"] = "99.9"
    assert store.get_receipt("P001")["score"] != "99.9"
    with pytest.raises(KeyError):
        store.get_receipt("../P001")
    bad = tmp_path / "P001.json"
    doc = store.get_receipt("P001")
    doc["score"] = "1.0"
    bad.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        ReadOnlyReceiptStore(tmp_path).get_receipt("P001")


def test_strong_stub_is_perfect_on_all_arms(cases):
    cs, d = cases
    store = ReadOnlyReceiptStore(d)
    client = StubModelClient("strong")
    for arm in ARMS:
        for c in cs.values():
            r = run_arm(arm, c, client, store)
            m = score_answer(c, r.delivered, r.blocked)
            assert not m["band_mismatch"] and not m["provenance_misstatement"]
            assert m["fault_detection"] in (None, 1.0) and m["false_alarm"] in (None, 0.0)


def test_policy_tamper_detected(cases):
    cs, d = cases
    c = by_fault(cs, "policy_tamper")
    r = run_arm("A", c, StubModelClient("strong"), ReadOnlyReceiptStore(d))
    assert r.delivered["status"] == "policy_mismatch"


def test_verifier_blocks_wrong_narration(cases):
    cs, d = cases
    store = ReadOnlyReceiptStore(d)
    c = next(x for x in cs.values() if x.truth["fault"] is None and x.truth["expected_band"] != "low")

    class Liar:
        def chat(self, messages, tools=None):
            return {"content": json.dumps({"status": "scored", "score": "12.3", "band": "low",
                                           "reasons": [], "provenance": {},
                                           "explanation": "fine"}), "tool_calls": []}

    r = run_arm("B", c, Liar(), store)
    assert r.blocked and r.violations
    assert str(r.delivered["score"]) == c.truth["expected_score"]


def test_b_tool_loop_and_errors(cases):
    cs, d = cases
    r = run_arm("B", cs["P002"], StubModelClient("strong"), ReadOnlyReceiptStore(d))
    assert r.tool_calls == ["get_receipt"]

    class Boom:
        def chat(self, messages, tools=None):
            raise RuntimeError("down")

    r = run_arm("B", cs["P002"], Boom(), ReadOnlyReceiptStore(d))
    assert r.error and r.blocked and r.delivered["status"] == cs["P002"].truth["expected_status"]


def test_parse_answer():
    assert parse_answer("nope") is None
    p = parse_answer('x {"status":"Scored","score":"1.5","band":"Low"} y')
    assert str(p["score"]) == "1.5" and p["band"] == "low"


def test_bootstrap_ci():
    ci = bootstrap_ci([0, 1, 1, 0, 1, 1, 1, 0])
    assert ci["lo"] <= ci["mean"] <= ci["hi"] and ci["n"] == 8
    assert bootstrap_ci([2.0] * 5) == {"mean": 2.0, "lo": 2.0, "hi": 2.0, "n": 5}
    assert bootstrap_ci([])["mean"] is None
    assert bootstrap_ci([0, 1, 0, 1]) == bootstrap_ci([0, 1, 0, 1])


def test_config_env_override(tmp_path):
    cfg = load_config(tmp_path / "missing.json", env={"PINNED_AOAI_STRONG": "x"})
    assert cfg.strong == "x" and cfg.small == "gpt-4.1-mini" and cfg.temperature == 0
    assert cfg.endpoint == "https://model.example.invalid/"


def test_deterministic_sensitivity_shape():
    rows = deterministic_sensitivity(DS, POLICY)
    assert len(rows) == 10 and all(0 <= r["band_flip_share"] <= 1 for r in rows)


def test_dry_run_end_to_end(tmp_path):
    cfg = load_config(tmp_path / "none.json", env={})
    res = run_experiment(tmp_path, ["stub-strong", "stub-small"], cfg, True, repeats=3,
                         parcels=[f"P{n:03d}" for n in range(1, 13)], workers=4,
                         sensitivity=True, sensitivity_parcels=3)
    for name in ("runs.json", "metrics.json", "sensitivity.json", "summary.md"):
        assert (tmp_path / name).is_file()
    assert len(res["runs"]) == 2 * 3 * 12 * 3
    m = res["metrics"]
    assert m["stub-strong|B"]["band_mismatch"]["mean"] == 0
    assert m["stub-small|B"]["score_error"]["mean"] <= m["stub-small|A"]["score_error"]["mean"]
    assert "stub-small|B(raw)" in m
    assert "| Model | Arm |" in (tmp_path / "summary.md").read_text()
