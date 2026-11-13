from decimal import Decimal

import pytest

import rescore_rows as r
from pinned_scoring.experiment.runner import _jsonable


@pytest.fixture(scope="module")
def cases():
    return r.build_case_map(["P001", "P002", "P003"])


def make(case, explanation, arm="B", **kw):
    ans = {"status": case.receipt["status"],
           "score": None if case.receipt["score"] is None else str(case.receipt["score"]),
           "band": case.receipt["band"], "reasons": list(case.receipt["reasons"]),
           "provenance": {c[0]: c[2] for c in case.receipt["components"]},
           "explanation": explanation}
    return {"model": "m", "arm": arm, "parcel": case.parcel, "repeat": 0, "metrics": {},
            "signature": [], "blocked": True, "violations": [{"kind": "old"}],
            "tool_calls": [], "error": None, "answer": ans, "raw_answer": ans, **kw}


def test_clean_narration_unblocked(cases):
    case = cases["P001"]
    out = r.rescore_row(make(case, "Report matches the receipt."), case)
    assert out["rescored"] and out["blocked"] is False and out["original_blocked"] is True
    assert out["block_changed"] is True and out["violations"] == []


def test_wrong_score_blocked(cases):
    case = cases["P002"]
    row = make(case, "The score is 99.9.")
    row["blocked"] = False
    row["raw_answer"] = {**row["raw_answer"], "score": "12.3", "status": "scored"}
    out = r.rescore_row(row, case)
    assert out["blocked"] is True and out["answer"]["explanation"].startswith("Verifier blocked")


def test_a_arm_and_errors_untouched(cases):
    case = cases["P001"]
    a = make(case, "x", arm="A")
    assert r.rescore_row(a, case)["rescored"] is False
    e = make(case, "x", error="429")
    out = r.rescore_row(e, case)
    assert out["rescored"] is False and out["blocked"] is True


def test_original_not_mutated(cases):
    case = cases["P001"]
    row = make(case, "ok")
    snap = repr(row)
    r.rescore_row(row, case)
    assert repr(row) == snap
