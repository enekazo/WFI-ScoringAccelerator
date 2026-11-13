import json

import pytest

import merge_150 as m


def row(model="m", arm="A", parcel="P001", rep=0, blocked=False, error=None, se=0.0, tools=0):
    met = {k: None for k in m.METRICS}
    met.update(score_error=se, verifier_block=float(blocked))
    return {"model": model, "arm": arm, "parcel": parcel, "repeat": rep, "metrics": met,
            "raw_metrics": met, "signature": ["scored", "1", "low"], "blocked": blocked,
            "error": error, "tool_calls": ["get_receipt"] * tools}


def write(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows), encoding="utf-8")
    return p


def test_norm_model_and_key():
    assert m.norm_model("grok-4.7-cmp") == "grok-4.7"
    assert m.key(row(model="phi-4-cmp")) == ("phi-4", "A", "P001", 0)


def test_block_kind():
    assert m.block_kind(row()) is None
    assert m.block_kind(row(blocked=True)) == "verifier"
    assert m.block_kind(row(blocked=True, error="HTTPError: 429 Too Many")) == "api_429"
    assert m.block_kind(row(blocked=True, error="Timeout")) == "api_error"


def test_duplicate_rows_rejected(tmp_path):
    f1, f2 = write(tmp_path / "a.json", [row()]), write(tmp_path / "b.json", [row()])
    with pytest.raises(ValueError):
        m.build_rows({"g": [f1, f2]}, [])


def test_replacement_keeps_original_and_marks(tmp_path):
    orig = write(tmp_path / "o.json", [row(arm="B", blocked=True, error="429")])
    rep = write(tmp_path / "r.json", [row(arm="B", blocked=False)])
    rows, replaced, _ = m.build_rows({"g": [orig]}, [rep])
    assert len(rows) == 1 and rows[0]["replacement"] is True
    assert replaced[0]["original"]["error"] == "429" and replaced[0]["key"][0] == "m"


def test_replacement_dict_shape(tmp_path):
    orig = write(tmp_path / "o.json", [row(arm="B", blocked=True, error="429")])
    new = row(arm="B", blocked=False)
    rep = write(tmp_path / "r.json", {"m|B|P001|0": new})
    rows, replaced, _ = m.build_rows({"g": [orig]}, [rep])
    assert rows[0]["replacement"] is True and len(replaced) == 1


def test_replacement_unknown_key(tmp_path):
    orig = write(tmp_path / "o.json", [row()])
    rep = write(tmp_path / "r.json", [row(parcel="P009")])
    with pytest.raises(ValueError):
        m.build_rows({"g": [orig]}, [rep])


def test_cell_stats_splits_blocks():
    recs = [row(arm="B", parcel="P001", blocked=True),
            row(arm="B", parcel="P002", blocked=True, error="429"),
            row(arm="B", parcel="P003", tools=2),
            row(arm="B", parcel="P004", tools=2)]
    c = m.cell_stats(recs)
    b = c["blocks"]
    assert (b["total"], b["verifier"], b["api_429"]) == (2, 1, 1)
    assert b["rate_verifier"]["mean"] == 0.25 and c["tool_calls_per_run"] == 1.0
    assert c["errors"] == 1 and c["errors_429"] == 1


def test_score_error_mae_over_parcels():
    recs = [row(parcel="P001", se=2.0), row(parcel="P002", se=4.0)]
    assert m.cell_stats(recs)["score_error_mae"]["mean"] == 3.0


def test_coverage_gaps():
    rows = [row(parcel=f"P{i:03d}") for i in range(1, 151) if i != 7]
    cov = m.coverage(rows)["m"]
    assert cov["missing_parcels"] == ["P007"] and cov["missing_arms"] == ["A+", "B"]
    assert m._ranges(["P007", "P008", "P010"]) == "P007-P008, P010"


def test_render_has_no_banned_wording(tmp_path):
    rows = [row(arm="B", parcel="P001"), row(arm="A")]
    stats = {"cells": m.compute(rows), "coverage": m.coverage(rows),
             "replacements": {"count": 0}}
    md = m.render_md(stats).lower()
    assert "extra" not in md and "50 + 100" not in md and "150 parcels" in md
