import json
from decimal import Decimal

import pytest

from pinned_scoring import graph_tools as gt
from pinned_scoring import scenarios as sc
from pinned_scoring.experiment import scenarios_eval as ev
from pinned_scoring.experiment.runner import POLICY_PATH
from pinned_scoring.graph import DEFAULT_GRAPH_PATH, build_graph, read_graph
from pinned_scoring.policy import Policy
from pinned_scoring.synth import read_dataset


@pytest.fixture(scope="module")
def ds():
    return read_dataset()


@pytest.fixture(scope="module")
def ctx(ds):
    return sc.Context(read_graph(), ds.observations, Policy.load(POLICY_PATH), ds.as_of)


def test_graph_deterministic_and_committed_file_matches(ds):
    parcels = sorted(ds.observations)
    a, b = build_graph(parcels), build_graph(parcels)
    assert a.to_doc() == b.to_doc()
    assert build_graph(parcels, seed=1).to_doc() != a.to_doc()
    assert read_graph(DEFAULT_GRAPH_PATH).to_doc() == a.to_doc()


def test_graph_traversals_consistent(ctx):
    g = ctx.graph
    for pid in g.parcels:
        for f in g.feeders_of(pid):
            assert pid in g.parcels_on(f)
    assert all(a["feeder"] in g.feeders for a in g.assets.values())


def test_exposure_no_double_counting_and_monotone(ctx):
    lo, hi = sc.exposure_if_ignite(ctx, "40"), sc.exposure_if_ignite(ctx, "60")
    assert hi["customers"] <= lo["customers"]
    assert set(hi["parcels"]) <= set(lo["parcels"])
    assert sc.exposure_if_ignite(ctx, "99")["customers"] == 0
    assert lo["policy_sha256"] == ctx.policy.sha256 and lo["counterfactual"] is False


def test_best_mitigation_budget_and_none(ctx):
    assert sc.best_mitigation(ctx, 5000)["best"] is None
    r = sc.best_mitigation(ctx, 9000)
    assert r["counterfactual"] is True
    assert r["best"] is None or Decimal(str(r["best"]["cost_usd"])) <= 9000


def test_deenergise_deterministic(ctx):
    assert sc.deenergise_first(ctx, 60) == sc.deenergise_first(ctx, 60)
    assert sc.deenergise_first(ctx, 60)["counterfactual"] is True


def test_sensitivity_reports_change_flag(ctx):
    r = sc.decision_sensitivity(ctx, "fuel_load:up", 60)
    assert isinstance(r["feeder_decision_changes"], bool)
    assert r["policy_sha256"]


def test_graph_tools_read_only_and_unknown(ctx):
    t = gt.GraphTools(ctx)
    before = ctx.baseline()
    out = t.call("exposure_if_ignite", {"min_score": "50"})
    assert out["source"] == "scorer.py"
    assert "error" in t.call("drop_tables", {})
    assert {k: v.receipt_sha256 for k, v in ctx.baseline().items()} == \
        {k: v.receipt_sha256 for k, v in before.items()}


def test_verify_pinned_numbers():
    res = [{"customers": 1234, "parcels": ["P001"]}]
    assert gt.verify_pinned_numbers("1234 customers", res, "q")["ok"]
    assert not gt.verify_pinned_numbers("5678 customers", res, "q")["ok"]


def test_agent_definition_graph_tools_opt_in():
    from pinned_scoring import foundry_agent as fa
    base = fa.agent_definition("m")
    ext = fa.agent_definition("m", graph_tools=True)
    assert len(json.dumps(ext, default=str)) > len(json.dumps(base, default=str))


def test_harness_oracle_graph_arm_correct_flat_arm_fails(ctx):
    res = ev.run_scenarios(ctx, {"oracle": ev.OracleClient(ctx)}, repeats=1, workers=2)
    s = res["summary"]["oracle"]
    assert s["graph"]["overall"]["correct"]["mean"] == 1.0
    assert s["graph"]["overall"]["numbers_pinned"]["mean"] == 1.0
    assert s["flat"]["overall"]["correct"]["mean"] == 0.0
    assert len(res["questions"]) == 20


def test_grade_rejects_wrong_and_unparseable(ctx):
    q = ev.default_questions(ctx.policy)[0]
    truth = ev.truth_for(ctx, q)
    assert ev.grade(q, truth, None)["correct"] == 0.0
    wrong = dict(truth, customers=truth["customers"] + 1)
    assert ev.grade(q, truth, wrong)["correct"] == 0.0
    assert ev.grade(q, truth, dict(truth))["provenance_ok"] == 1.0
