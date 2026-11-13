"""Experiment arms: A (raw evidence + rubric), A+ (receipts, no verifier), B (tools + verifier)."""

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from ..agent import ReceiptStore, guarded_answer
from ..receipts_store import ReadOnlyReceiptStore
from .clients import TASK_CLOSE, TASK_OPEN

ARMS = ("A", "A+", "B")
MAX_TOOL_TURNS = 6

SYSTEM = """You are a wildfire parcel risk analyst. Answer with ONE JSON object and nothing else:
{"status": "scored" | "incomplete" | "policy_mismatch",
 "score": "<one-decimal string>" | null,
 "band": "<band label>" | null,
 "reasons": ["missing:<metric>", "expired:<metric>", "invalid:<metric>", "unit:<metric>", "provenance:<metric>"],
 "provenance": {"<factor name>": "measured" | "generated" | "fixture"},
 "explanation": "<2-3 sentence plain-language explanation>"}
Rules: if submitted_policy_sha256 differs from pinned_policy_sha256 the status is "policy_mismatch"
with null score and band. If any policy factor lacks a usable input the status is "incomplete"
with null score and band, and reasons list each problem. "provenance" lists the provenance of
every factor's latest observation, whether or not it was usable. Never invent numbers."""

ARM_A_RULES = """Compute the result yourself from the evidence and rubric.
For each factor take the latest observation with observed_at <= as_of. It is unusable when:
quality != valid (invalid:), unit != factor unit (unit:), provenance not in allowed_provenance
(provenance:), valid_until <= as_of or age in hours > max_age_hours (expired:), or absent (missing:).
Otherwise ratio = clamp((value-low)/(high-low), 0, 1); if invert, ratio = 1-ratio;
contribution = weight * 100 * ratio. Score = sum of contributions rounded half-up to 1 decimal.
Band = first band whose upper bound is greater than the score."""
ARM_A_PLUS_RULES = ("The receipt is authoritative and was produced by a deterministic scorer. "
                    "Do not recompute it; report its status, score, band and reasons exactly and explain it.")
ARM_B_RULES = ("Use the get_receipt tool for the parcel (list_receipts is also available). The receipt "
               "is authoritative; report its status, score, band and reasons exactly and explain it. "
               "Your explanation will be machine-checked against the receipt.")

TOOLS = [
    {"type": "function", "function": {
        "name": "get_receipt", "description": "Read the stored score receipt for a parcel.",
        "parameters": {"type": "object", "properties": {"parcel_id": {"type": "string"}},
                       "required": ["parcel_id"]}}},
    {"type": "function", "function": {
        "name": "list_receipts",
        "description": "List receipt summaries, optionally filtered by band and/or status.",
        "parameters": {"type": "object", "properties": {
            "band": {"type": "string"}, "status": {"type": "string"}}}}},
]


@dataclass
class Case:
    parcel: str
    as_of: str
    observations: list          # wire dicts, as injected
    pinned_policy: object
    submitted_policy: object    # what the rubric given to the model says
    receipt: dict               # wire receipt from the real scorer under the pinned policy
    truth: dict                 # expected_status / _score / _band / _reasons / fault


@dataclass
class RunOutput:
    raw: str | None
    parsed: dict | None
    delivered: dict | None
    blocked: bool = False
    violations: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    error: str | None = None


def build_task(arm: str, case: Case) -> dict:
    task = {"arm": arm, "parcel": case.parcel, "as_of": case.as_of,
            "pinned_policy_sha256": case.pinned_policy.sha256,
            "submitted_policy_sha256": case.submitted_policy.sha256}
    if arm == "A":
        task["rubric"] = case.submitted_policy.wire()
        task["evidence"] = case.observations
    elif arm == "A+":
        task["receipt"] = case.receipt
    return task


def build_messages(arm: str, case: Case) -> list:
    rules = {"A": ARM_A_RULES, "A+": ARM_A_PLUS_RULES, "B": ARM_B_RULES}[arm]
    user = f"{rules}\n\nParcel {case.parcel}.\n{TASK_OPEN}{json.dumps(build_task(arm, case))}{TASK_CLOSE}"
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def parse_answer(text) -> dict | None:
    if not isinstance(text, str):
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        doc = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(doc, dict):
        return None
    score = doc.get("score")
    try:
        score = None if score in (None, "", "null") else Decimal(str(score))
    except InvalidOperation:
        return None
    band = doc.get("band")
    prov = doc.get("provenance") if isinstance(doc.get("provenance"), dict) else {}
    return {
        "status": str(doc.get("status", "")).lower() or None,
        "score": score,
        "band": str(band).lower() if band not in (None, "", "null") else None,
        "reasons": [str(r).lower() for r in doc.get("reasons") or []],
        "provenance": {str(k): str(v).lower() for k, v in prov.items()},
        "explanation": str(doc.get("explanation") or ""),
    }


def receipt_answer(case: Case) -> dict:
    """Deterministic fail-safe answer derived purely from the receipt and the policy pin check."""
    r = case.receipt
    if case.submitted_policy.sha256 != case.pinned_policy.sha256:
        return {"status": "policy_mismatch", "score": None, "band": None, "reasons": [],
                "provenance": {}, "explanation": "Policy hash mismatch; answer withheld."}
    return {"status": r["status"], "score": None if r["score"] is None else Decimal(r["score"]),
            "band": r["band"], "reasons": list(r["reasons"]),
            "provenance": {c[0]: c[2] for c in r["components"]},
            "explanation": "Verifier blocked the narration; showing receipt values."}


def _narration(parsed: dict) -> str:
    parts = []
    if parsed["score"] is not None:
        parts.append(f"Score: {parsed['score']}")
    if parsed["band"]:
        parts.append(f"Band: {parsed['band']}")
    parts.append(parsed["explanation"])
    return ". ".join(parts)


def _run_tool(store: ReadOnlyReceiptStore, call: dict) -> str:
    name = call["function"]["name"]
    try:
        args = json.loads(call["function"].get("arguments") or "{}")
        if name == "get_receipt":
            return json.dumps(store.get_receipt(args.get("parcel_id")))
        if name == "list_receipts":
            return json.dumps(store.list_receipts(args.get("band"), args.get("status")))
        return json.dumps({"error": f"unknown tool {name}"})
    except (KeyError, ValueError, TypeError) as exc:
        return json.dumps({"error": str(exc)})


def run_arm(arm: str, case: Case, client, store: ReadOnlyReceiptStore | None = None) -> RunOutput:
    messages = build_messages(arm, case)
    tools = TOOLS if arm == "B" else None
    calls = []
    try:
        reply = client.chat(messages, tools)
        for _ in range(MAX_TOOL_TURNS):
            if not reply.get("tool_calls"):
                break
            messages.append({"role": "assistant", "content": reply.get("content"),
                             "tool_calls": reply["tool_calls"]})
            for tc in reply["tool_calls"]:
                calls.append(tc["function"]["name"])
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": _run_tool(store, tc)})
            reply = client.chat(messages, tools)
    except Exception as exc:  # noqa: BLE001
        out = RunOutput(None, None, None, error=f"{type(exc).__name__}: {exc}", tool_calls=calls)
        if arm == "B":
            out.blocked, out.delivered = True, receipt_answer(case)
        return out

    raw = reply.get("content")
    parsed = parse_answer(raw)
    if arm != "B":
        return RunOutput(raw, parsed, parsed, tool_calls=calls)

    if parsed is None:
        return RunOutput(raw, None, receipt_answer(case), True,
                         [{"kind": "invalid_output", "detail": "unparseable answer"}], calls)
    rstore = ReceiptStore()
    rstore.load(case.receipt)
    verdict = guarded_answer(lambda q, ctx: _narration(parsed), "explain", rstore, case.parcel)
    violations = [{"kind": v.kind, "detail": v.detail} for v in verdict.violations]
    if verdict.blocked:
        return RunOutput(raw, parsed, receipt_answer(case), True, violations, calls)
    return RunOutput(raw, parsed, parsed, False, [], calls)
