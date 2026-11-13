"""Model clients. Both return an OpenAI-style assistant message:
{"content": str|None, "tool_calls": [{"id","type":"function","function":{"name","arguments"}}]}
"""

import hashlib
import json
import threading
import time
from decimal import ROUND_HALF_UP, Decimal

from ..contracts import Observation
from ..policy import Policy
from ..scorer import score
from .config import ExperimentConfig

TASK_OPEN, TASK_CLOSE = "<<TASK>>", "<<END>>"
_RETRYABLE = (408, 409, 429, 500, 502, 503, 504)


def _is_reasoning(deployment: str) -> bool:
    return deployment.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))


def _default_credential():
    """DefaultAzureCredential, or the az CLI credential pinned to PINNED_AZURE_TENANT when set."""
    import os

    tenant = os.environ.get("PINNED_AZURE_TENANT")
    if tenant:
        from azure.identity import AzureCliCredential

        return AzureCliCredential(tenant_id=tenant)
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()

class AzureOpenAIModelClient:
    """Azure OpenAI chat completions with tool calling; Entra ID auth only (no keys)."""

    def __init__(self, deployment: str, config: ExperimentConfig, credential=None):
        self.name = deployment
        # "<deployment>@none" runs a reasoning model with reasoning_effort="none" on every arm
        self._deployment, _, effort = deployment.partition("@")
        self._effort = effort or None
        self._config = config
        self._credential = credential
        self._client = None
        self._lock = threading.Lock()
        self.prompt_tokens = self.completion_tokens = 0

    def _get(self):
        with self._lock:
            if self._client is None:
                from azure.identity import DefaultAzureCredential, get_bearer_token_provider
                from openai import AzureOpenAI
                cred = self._credential or _default_credential()
                provider = get_bearer_token_provider(
                    cred, "https://cognitiveservices.azure.com/.default")
                self._client = AzureOpenAI(
                    azure_endpoint=self._config.endpoint, api_version=self._config.api_version,
                    azure_ad_token_provider=provider, max_retries=0)
            return self._client

    def chat(self, messages: list, tools: list | None = None) -> dict:
        kwargs = {"model": self._deployment, "messages": messages}
        if _is_reasoning(self._deployment):  # no temperature control; completion-token cap
            kwargs["max_completion_tokens"] = 16000
            if self._effort:
                kwargs["reasoning_effort"] = self._effort
            if tools:  # chat completions rejects function tools with reasoning; use Responses API
                return self._with_retry(lambda: self._responses(kwargs, messages, tools))
        else:
            kwargs["temperature"] = self._config.temperature
        if tools:
            kwargs["tools"] = tools
        def call():
            resp = self._get().chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            if resp.usage:
                with self._lock:
                    self.prompt_tokens += resp.usage.prompt_tokens
                    self.completion_tokens += resp.usage.completion_tokens
            calls = [{"id": tc.id, "type": "function",
                      "function": {"name": tc.function.name,
                                   "arguments": tc.function.arguments}}
                     for tc in (msg.tool_calls or [])]
            return {"content": msg.content, "tool_calls": calls}
        return self._with_retry(call)

    def _responses(self, kwargs: dict, messages: list, tools: list) -> dict:
        """Responses API path (stateless) for reasoning models with function tools."""
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        from openai import OpenAI
        if getattr(self, "_rclient", None) is None:
            provider = get_bearer_token_provider(
                self._credential or _default_credential(),
                "https://cognitiveservices.azure.com/.default")
            base = self._config.endpoint.rstrip("/") + "/openai/v1/"
            self._rclient = OpenAI(base_url=base, api_key=provider(), max_retries=0)
            self._rprovider, self._rtime = provider, time.time()
        if time.time() - self._rtime > 1800:  # refresh bearer token
            self._rclient.api_key, self._rtime = self._rprovider(), time.time()
        items = []
        for m in messages:
            if m["role"] == "tool":
                items.append({"type": "function_call_output", "call_id": m["tool_call_id"],
                              "output": m["content"]})
            elif m["role"] == "assistant" and m.get("tool_calls"):
                if m.get("content"):
                    items.append({"role": "assistant", "content": m["content"]})
                for tc in m["tool_calls"]:
                    items.append({"type": "function_call", "call_id": tc["id"],
                                  "name": tc["function"]["name"],
                                  "arguments": tc["function"]["arguments"]})
            else:
                items.append({"role": m["role"], "content": m["content"]})
        rt = [{"type": "function", **t["function"]} for t in tools]
        rk = {"model": self._deployment, "input": items, "tools": rt,
              "max_output_tokens": kwargs["max_completion_tokens"]}
        if "reasoning_effort" in kwargs:
            rk["reasoning"] = {"effort": kwargs["reasoning_effort"]}
        resp = self._rclient.responses.create(**rk)
        if resp.usage:
            with self._lock:
                self.prompt_tokens += resp.usage.input_tokens
                self.completion_tokens += resp.usage.output_tokens
        calls = [{"id": o.call_id, "type": "function",
                  "function": {"name": o.name, "arguments": o.arguments}}
                 for o in resp.output if o.type == "function_call"]
        return {"content": resp.output_text or None, "tool_calls": calls}

    def _with_retry(self, call):
        last = None
        for attempt in range(self._config.max_retries):
            try:
                return call()
            except Exception as exc:  # noqa: BLE001
                last = exc
                status = getattr(exc, "status_code", None)
                if status == 429:
                    with self._lock:
                        self.throttled_429 = getattr(self, "throttled_429", 0) + 1
                transient = status in _RETRYABLE or type(exc).__name__ in (
                    "APIConnectionError", "APITimeoutError")
                if not transient or attempt == self._config.max_retries - 1:
                    raise
                time.sleep(min(30, 2 ** attempt))
        raise last  # pragma: no cover


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest(), 16)


def _observations(wires) -> list:
    from datetime import datetime
    return [Observation(
        entity_id=w["entity_id"], metric=w["metric"], value=Decimal(w["value"]), unit=w["unit"],
        observed_at=datetime.fromisoformat(w["observed_at"]),
        valid_until=datetime.fromisoformat(w["valid_until"]),
        provenance=w["provenance"], quality=w["quality"], source=w.get("source", ""))
        for w in wires]


class StubModelClient:
    """Deterministic offline model. 'strong' is nearly faithful; 'small' makes seeded mistakes."""

    def __init__(self, profile: str = "strong"):
        if profile not in ("strong", "small"):
            raise ValueError("profile must be 'strong' or 'small'")
        self.profile = profile
        self.name = f"stub-{profile}"
        self._seen: dict = {}
        self._lock = threading.Lock()

    @staticmethod
    def _task(messages) -> dict:
        text = next(m["content"] for m in messages if m["role"] == "user")
        return json.loads(text.split(TASK_OPEN, 1)[1].split(TASK_CLOSE, 1)[0])

    def chat(self, messages: list, tools: list | None = None) -> dict:
        task = self._task(messages)
        arm, parcel = task["arm"], task["parcel"]
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        key = (arm, parcel)
        if not tool_msgs:  # first turn of a conversation
            with self._lock:
                nth = self._seen.get(key, 0)
                self._seen[key] = nth + 1
            if arm == "B":
                return {"content": None, "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "get_receipt",
                                 "arguments": json.dumps({"parcel_id": parcel})}}]}
        else:
            with self._lock:
                nth = self._seen[key] - 1
        return {"content": json.dumps(self._answer(task, messages, nth)), "tool_calls": []}

    def _answer(self, task: dict, messages: list, nth: int) -> dict:
        arm, parcel = task["arm"], task["parcel"]
        small = self.profile == "small"
        h = _h(self.profile, arm, parcel)
        if task["submitted_policy_sha256"] != task["pinned_policy_sha256"]:
            return {"status": "policy_mismatch", "score": None, "band": None, "reasons": [],
                    "provenance": {}, "explanation": "The submitted rubric hash differs from the pinned policy."}
        if arm == "A":
            policy = Policy.from_dict(task["rubric"])
            from datetime import datetime
            r = score(parcel, _observations(task["evidence"]), policy,
                      datetime.fromisoformat(task["as_of"]))
            wire = r.wire()
        else:
            wire = task.get("receipt")
            if wire is None:
                wire = json.loads(next(m["content"] for m in messages
                                       if m["role"] == "tool"))
        prov = {c[0]: c[2] for c in wire["components"]}
        if arm == "A":
            by_metric = {f["metric"]: f["name"] for f in task["rubric"]["factors"]}
            for o in task["evidence"]:
                prov.setdefault(by_metric.get(o["metric"], o["metric"]), o["provenance"])
        out = {"status": wire["status"], "score": wire["score"], "band": wire["band"],
               "reasons": list(wire["reasons"]), "provenance": prov}
        if wire["status"] == "scored":
            out["explanation"] = (f"Parcel {parcel} scored {wire['score']} in the {wire['band']} band.")
        else:
            out["explanation"] = (f"Parcel {parcel} is incomplete ({', '.join(wire['reasons'])}); "
                                  "no score is stated.")
        # seeded mistakes
        if arm == "A" and wire["status"] == "scored" and (small and h % 5 == 0 or h % 23 == 0):
            bad = (Decimal(wire["score"]) + Decimal("7.3")).quantize(Decimal("0.1"), ROUND_HALF_UP)
            out["score"] = str(bad)
            out["explanation"] = f"Parcel {parcel} scored {bad} in the {wire['band']} band."
        if arm == "A" and small and wire["status"] == "incomplete" and h % 3 == 0:
            out.update(status="scored", score="50.0", band="moderate", reasons=[],
                       explanation=f"Parcel {parcel} scored 50.0 in the moderate band.")
        if arm == "A" and small and h % 7 == 0:
            out["provenance"] = {k: "measured" for k in prov}
            out["explanation"] += " All inputs are measured."
        if arm == "A+" and small and wire["status"] == "scored" and h % 11 == 0:
            out["explanation"] = (f"Parcel {parcel} scored {Decimal(wire['score']) + 1} "
                                  f"in the {wire['band']} band.")
        if arm == "B" and small and wire["status"] == "scored" and h % 9 == 0:
            other = "extreme" if wire["band"] != "extreme" else "low"
            out["explanation"] = f"Parcel {parcel} is in the {other} band."
        if small and h % 13 == 0 and nth == 2 and out["score"]:
            out["score"] = str((Decimal(out["score"]) + Decimal("0.1")))
        return out
