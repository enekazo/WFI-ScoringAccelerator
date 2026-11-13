import random
import tempfile

from pinned_scoring.experiment.runner import POLICY_PATH
from pinned_scoring.experiment.usefulness import (
    default_questions, judge_pair, parse_judgement, run_usefulness, wilson)
from pinned_scoring.policy import Policy
from pinned_scoring.synth import read_dataset


class Fake:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, tools=None):
        r = self.reply(messages) if callable(self.reply) else self.reply
        if isinstance(r, Exception):
            raise r
        return {"content": r, "tool_calls": []}


GOOD = '{"A": {"answers_question": 4, "plain_language": 4, "actionable": 4}, "B": {"answers_question": 2, "plain_language": 2, "actionable": 2}}'


def test_questions_cover_categories_and_count():
    qs = default_questions()
    assert len(qs) == 20
    assert {q.category for q in qs} == {"explain", "why_incomplete", "compare", "what_if",
                                        "next_action", "plain_summary"}


def test_parse_judgement_rejects_bad():
    assert parse_judgement(GOOD)["A"]["actionable"] == 4
    assert parse_judgement("nope") is None
    assert parse_judgement(GOOD.replace("4", "9", 1)) is None


def test_judge_is_blind_and_order_randomised():
    seen = []

    def reply(messages):
        seen.append(messages[1]["content"])
        return GOOD

    outcomes = set()
    for i in range(20):
        res = judge_pair(Fake(reply), "q?", "TTT-text", "DDD-text", random.Random(i))
        outcomes.add(res["d_first"])
        assert "arm" not in seen[-1].lower() and "template" not in seen[-1].lower()
    assert outcomes == {True, False}


def test_judge_maps_back_to_arm():
    # judge always prefers position A; so D wins iff D was shown first
    res = judge_pair(Fake(GOOD), "q", "t", "d", random.Random(1))
    assert res["outcome"] == ("win" if res["d_first"] else "loss")


def test_judge_errors_are_recorded():
    res = judge_pair(Fake(RuntimeError("429")), "q", "t", "d", random.Random(1))
    assert res["error"] and "outcome" not in res


def test_wilson_bounds():
    lo, hi = wilson(30, 60)
    assert 0 < lo < 0.5 < hi < 1
    assert wilson(0, 0) == (0.0, 0.0)


def test_end_to_end_offline_blocks_unfaithful_agent():
    def agent(messages):
        return "P003 scored 99.9 and is extreme."  # wrong number, wrong band

    with tempfile.TemporaryDirectory() as tmp:
        res = run_usefulness(read_dataset(), Policy.load(POLICY_PATH), Fake(agent), Fake(GOOD), tmp,
                             repeats=1, workers=1)
    assert res["faithfulness"]["delivered_violations"] == 0
    assert res["faithfulness"]["template_violations"] == 0
    d = res["summary"]["agent_delivery"]
    assert d["fallback_delivered"] >= 1 and d["of_which_verifier_blocks"] >= 1
    assert res["summary"]["judgements"]["usable"] == 20
