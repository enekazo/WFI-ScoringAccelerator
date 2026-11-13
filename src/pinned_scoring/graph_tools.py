"""Read-only graph query tools for the agent, plus a pinned-numbers check for its answers.

Tools return only ids, graph attributes and scorer-derived figures (scenarios.py). They never
write, and the agent cannot pass a score in. verify_pinned_numbers() flags any number in an
agent answer that no tool returned (and the question did not contain).
"""

import json
import re
from decimal import Decimal, InvalidOperation

from . import scenarios as sc

GRAPH_TOOL_DEFINITIONS = [
    {"type": "function", "name": "graph_node",
     "description": "Look up a graph node (parcel, asset, feeder or customer group) and its links.",
     "parameters": {"type": "object", "properties": {
         "node_id": {"type": "string", "description": "e.g. P007, A012, F03, C005"}},
         "required": ["node_id"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "exposure_if_ignite",
     "description": "Scenario A: parcels scoring at or above min_score (pinned scores) and the "
                    "distinct feeders, customers and critical facilities behind them.",
     "parameters": {"type": "object", "properties": {
         "min_score": {"type": "string", "description": "Score threshold, e.g. 50"}},
         "required": ["min_score"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "best_mitigation",
     "description": "Scenario B: mitigation with the biggest exposure-weighted risk reduction per "
                    "dollar (re-scored by the real scorer; counterfactual). Optional budget cap per action.",
     "parameters": {"type": "object", "properties": {
         "budget_usd": {"type": ["integer", "null"], "description": "Max cost per action; null for none"}},
         "required": ["budget_usd"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "deenergise_first",
     "description": "Scenario C: on a red-flag day with the given wind, which feeder to de-energise "
                    "first (re-scored by the real scorer; counterfactual).",
     "parameters": {"type": "object", "properties": {
         "wind_kmh": {"type": "integer"}}, "required": ["wind_kmh"],
         "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "decision_sensitivity",
     "description": "Scenario D: re-run the feeder and mitigation decisions under a +/-20% policy "
                    "weight variant and report whether either decision changes.",
     "parameters": {"type": "object", "properties": {
         "variant": {"type": "string", "description": "e.g. moisture:up, wind:down"},
         "wind_kmh": {"type": "integer"},
         "budget_usd": {"type": ["integer", "null"]}},
         "required": ["variant", "wind_kmh", "budget_usd"], "additionalProperties": False},
     "strict": True},
]


class GraphTools:
    """Executes graph tools against a scenarios.Context (read-only)."""

    def __init__(self, ctx: sc.Context):
        self.ctx = ctx

    def call(self, name: str, arguments: dict):
        ctx = self.ctx
        try:
            if name == "graph_node":
                node = ctx.graph.node(arguments["node_id"])
                return node or {"error": f"no node {arguments['node_id']!r}"}
            if name == "exposure_if_ignite":
                return sc.exposure_if_ignite(ctx, arguments["min_score"])
            if name == "best_mitigation":
                return sc.best_mitigation(ctx, arguments.get("budget_usd"))
            if name == "deenergise_first":
                return sc.deenergise_first(ctx, arguments["wind_kmh"])
            if name == "decision_sensitivity":
                return sc.decision_sensitivity(ctx, arguments["variant"],
                                               arguments.get("wind_kmh", 60),
                                               arguments.get("budget_usd"))
        except (KeyError, ValueError, TypeError) as exc:
            return {"error": str(exc)}
        return {"error": f"unknown tool {name!r}"}


_HEX = re.compile(r"\b[0-9a-f]{12,}\b")
_NUM = re.compile(r"(?<![A-Za-z0-9_.\-:])-?\d+(?:\.\d+)?")


def _norm(token: str):
    try:
        return Decimal(token).normalize()
    except InvalidOperation:
        return None


def numbers_in(text: str) -> set:
    cleaned = _HEX.sub(" ", text)
    return {n for n in (_norm(t) for t in _NUM.findall(cleaned)) if n is not None}


def verify_pinned_numbers(answer, tool_results, question: str = "") -> dict:
    """Every number in the answer must have been returned by a tool or appear in the question."""
    allowed = numbers_in(question)
    for result in tool_results:
        allowed |= numbers_in(json.dumps(result))
    used = numbers_in(answer if isinstance(answer, str) else json.dumps(answer))
    unpinned = sorted(str(n) for n in used - allowed)
    return {"numbers_used": len(used), "unpinned": unpinned, "ok": not unpinned}
