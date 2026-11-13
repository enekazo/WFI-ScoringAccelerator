"""Offline tests (no cloud): run controls, access chain, graph tables, secrets scan."""

import re
from pathlib import Path

import pytest

from pinned_scoring import access_chain as ac
from pinned_scoring import foundry_agent as fa
from pinned_scoring.controls import (BudgetExceeded, BudgetGuard, CostMeter, KILL_SWITCH_ENV,
                                     kill_switch_engaged)
from pinned_scoring.fabric.graph_store import graph_to_rows, rows_to_graph
from pinned_scoring.graph import read_graph

ROOT = Path(__file__).resolve().parents[1]


def parcels():
    return [{"parcel_id": f"P{i:03d}", "status": "scored", "band": "high" if i % 3 == 0 else "moderate",
             "score": "0.5"} for i in range(1, 51)]


def test_cost_meter_and_budget_guard():
    m = CostMeter("gpt-4.1")
    m.add({"input_tokens": 1_000_000, "output_tokens": 0})
    m.add({"prompt_tokens": 0, "completion_tokens": 1_000_000})
    m.add(None)
    assert m.calls == 2 and m.usd == pytest.approx(10.0)
    with pytest.raises(BudgetExceeded):
        BudgetGuard(m, 5.0).check()
    BudgetGuard(m, 50.0).check()


def test_kill_switch_blocks_chain_tools(monkeypatch):
    tools = ac.ChainTools(ac.Chain(ac.generate(parcels())), graph=True)
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    assert not kill_switch_engaged()
    assert "error" not in tools.call("chain_for_parcel", {"parcel_id": "P001"})
    monkeypatch.setenv(KILL_SWITCH_ENV, "1")
    assert kill_switch_engaged()
    assert "error" in tools.call("chain_for_parcel", {"parcel_id": "P001"})


def test_chain_generation_is_deterministic_and_labelled():
    a, b = ac.generate(parcels()), ac.generate(parcels())
    assert a == b and set(a) == set(ac.TABLES)
    assert len(a["parcel"]) == 50
    for name, rows in a.items():
        if name != "parcel":
            assert rows and all(r["provenance"] in {"measured", "assumed"} for r in rows)
    assert ac.generate(parcels(), seed=1) != a


def test_chain_truth_and_grading():
    chain = ac.Chain(ac.generate(parcels()))
    for q in ac.questions(chain):
        t = ac.truth(chain, q)
        assert t["parcels"] is not None and t["basis"] in {"measured", "assumed"}
        assert ac.grade(t, {"parcels": t["parcels"], "value": t["value"], "basis": t["basis"]})["correct"] == 1
        assert ac.grade(t, None)["correct"] == 0


def test_flat_arm_cannot_use_graph_tools_and_vice_versa():
    chain = ac.Chain(ac.generate(parcels()))
    flat, graph = ac.ChainTools(chain, graph=False), ac.ChainTools(chain, graph=True)
    assert "rows" in flat.call("read_table", {"table": "road_segment"})
    assert "error" in flat.call("chain_for_parcel", {"parcel_id": "P001"})
    assert "error" in graph.call("read_table", {"table": "road_segment"})
    assert "error" in flat.call("read_table", {"table": "nope"})


def test_graph_table_round_trip():
    g = read_graph()
    rows = graph_to_rows(g)
    nodes = [dict(zip(("node_id", "node_type", "props"), r)) for r in rows["graph_nodes"]]
    edges = [dict(zip(("edge_id", "src_id", "dst_id", "edge_type"), r)) for r in rows["graph_edges"]]
    mits = [dict(zip(("mitigation_id", "metric", "factor", "cost_usd"), r)) for r in rows["graph_mitigations"]]
    assert rows_to_graph(nodes, edges, mits, g.seed) == g


def test_make_reader_local_and_invalid():
    assert hasattr(fa.make_reader("local"), "get_receipt")
    with pytest.raises(ValueError):
        fa.make_reader("nonsense")


def test_no_secrets_in_tracked_sources():
    patterns = [re.compile(p) for p in (r"AccountKey=", r"api[_-]?key\s*=\s*['\"][A-Za-z0-9]{16,}",
                                         r"-----BEGIN [A-Z ]*PRIVATE KEY", r"sig=[A-Za-z0-9%]{20,}",
                                         r"client_secret\s*=\s*['\"][^'\"]{8,}")]
    for folder in ("src", "scripts", "tests"):
        for f in (ROOT / folder).rglob("*.py"):
            if f.name == Path(__file__).name:
                continue
            text = f.read_text(encoding="utf-8")
            for p in patterns:
                assert not p.search(text), f"possible secret in {f}"
    assert "parameters.json" in (ROOT / "fabric" / ".gitignore").read_text(encoding="utf-8")
