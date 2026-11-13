import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from pinned_scoring import Observation, Policy, score
from pinned_scoring import foundry_agent as fa
from pinned_scoring.receipts_store import write_receipts

ROOT = Path(__file__).resolve().parents[1]
POLICY = Policy.load(ROOT / "examples" / "wildfire" / "policy.json")
NOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
BASE = {
    "fuel_load": ("20", "t/ha"), "dead_fuel_pct": ("50", "%"),
    "fuel_moisture_pct": ("10", "%"), "weather_danger": ("40", "index"),
    "wind_kmh": ("30", "km/h"),
}


def rows(entity, drop=()):
    observed = NOW - timedelta(hours=1)
    return [Observation(entity, m, v, u, observed, observed + timedelta(days=60), "measured")
            for m, (v, u) in BASE.items() if m not in drop]


@pytest.fixture
def toolbox(tmp_path):
    receipts = [score("P1", rows("P1"), POLICY, NOW),
                score("P2", rows("P2", drop=("wind_kmh",)), POLICY, NOW)]
    write_receipts(receipts, tmp_path)
    return fa.FoundryToolbox(tmp_path), receipts


class FakeResponses:
    def __init__(self, scripted):
        self.scripted, self.calls = list(scripted), []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.scripted.pop(0)


def fake_client(*scripted):
    return SimpleNamespace(responses=FakeResponses(scripted))


def tool_call(name, args, call_id="c1"):
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)


def resp(output=(), text="", rid="r1"):
    return SimpleNamespace(output=list(output), output_text=text, id=rid)


def test_tool_definitions_match_toolbox(toolbox):
    box, _ = toolbox
    assert [t["name"] for t in fa.TOOL_DEFINITIONS] == ["get_receipt", "list_receipts", "list_reasons", "compare"]
    assert box.call("get_receipt", {"parcel": "P1"})["receipt"]["entity_id"] == "P1"
    assert len(box.call("list_receipts", {"band": None, "status": None})["receipts"]) == 2
    assert box.call("list_reasons", {"parcel": "P2"})["status"] == "incomplete"
    assert box.call("compare", {"parcel_a": "P1", "parcel_b": "P2"})["entities"] == ["P1", "P2"]
    assert "error" in box.call("get_receipt", {"parcel": "NOPE"})
    assert "error" in box.call("nope", {})


def test_run_agent_turn_executes_function_calls(toolbox):
    box, receipts = toolbox
    client = fake_client(resp([tool_call("get_receipt", {"parcel": "P1"})]),
                         resp(text="done", rid="r2"))
    trace = []
    assert fa.run_agent_turn(client, box, "explain P1", trace=trace) == "done"
    first, second = client.responses.calls
    assert first["extra_body"]["agent_reference"]["name"] == fa.AGENT_NAME
    assert second["previous_response_id"] == "r1"
    output = json.loads(second["input"][0]["output"])
    assert output["receipt"]["score"] == str(receipts[0].score)
    assert trace == [{"tool": "get_receipt", "arguments": '{"parcel": "P1"}'}]


def test_chat_passes_honest_answer(toolbox):
    box, receipts = toolbox
    text = f"P1 scored {receipts[0].score}."
    result = fa.chat(fake_client(resp(text=text)), box, "Explain P1")
    assert not result.blocked and result.text == text


def test_chat_blocks_wrong_score(toolbox):
    box, _ = toolbox
    result = fa.chat(fake_client(resp(text="P1 scored 99.9.")), box, "Explain P1")
    assert result.blocked and result.text is None and result.violations


def test_runaway_tool_loop_raises(toolbox):
    box, _ = toolbox
    looping = [resp([tool_call("list_receipts", {"band": None, "status": None})], rid=f"r{i}")
               for i in range(fa.MAX_TOOL_ROUNDS + 1)]
    with pytest.raises(RuntimeError):
        fa.run_agent_turn(fake_client(*looping), box, "x")


def test_create_or_update_agent_uses_prompt_definition():
    captured = {}

    class Agents:
        def create_version(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(name=kwargs["agent_name"], version="1")

    version = fa.create_or_update_agent(SimpleNamespace(agents=Agents()))
    assert version.name == "pinned-score-agent"
    assert captured["definition"].model == "gpt-4.1"
    assert len(captured["definition"].tools) == 4
    assert "never compute" in captured["definition"].instructions.lower()
