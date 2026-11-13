"""Graph agent vs flat-table agent on the four decision scenarios.

Both arms get the same pinned receipts and the same real scorer. They differ in tools:
  graph: scenario tools (graph_tools.py) that return scorer-derived decision figures.
  flat : raw tables (parcel receipts, assets, customer groups, mitigations) plus a per-parcel
         what_if over the real scorer. The model must join, loop and aggregate itself.
Outcomes are scored against ground truth computed by scenarios.py (never by a model).
Resampling unit = question (repeats averaged first), as in metrics.aggregate.
"""

import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from statistics import fmean

from .. import graph_tools as gt
from .. import scenarios as sc
from ..synth import weight_sensitivity
from .metrics import bootstrap_ci

MAX_ROUNDS = 12
METRICS = ("correct", "provenance_ok", "numbers_pinned", "parse_failure")

SYSTEM = """You are a wildfire-risk decision analyst. Use the tools; do not answer from memory.
Scores come only from the scorer through tools. Never compute, adjust or estimate a score.
Answer with ONE JSON object and nothing else. Always include "policy_sha256" (the policy hash
your figures were computed under, copied from the tool output) and "counterfactual" (true if the
figures come from a re-scored what-if, false if they are baseline pinned scores). Schemas:
 exposure:    {"parcels":[ids],"customers":int,"critical_facilities":int,"policy_sha256":str,"counterfactual":bool}
 mitigation:  {"parcel":id|null,"mitigation":id|null,"risk_reduction_per_1000usd":str|null,"policy_sha256":str,"counterfactual":bool}
 deenergise:  {"feeder":id|null,"customers":int,"critical_facilities":int,"policy_sha256":str,"counterfactual":bool}
 sensitivity: {"feeder_decision_changes":bool,"feeder_variant":id|null,"policy_sha256":str,"counterfactual":bool}
Definitions: exposure counts distinct feeders behind the parcels (no double counting). Risk
reduction = (score_before - score_after) * exposure_weight * 1000 / cost_usd, where exposure_weight
= customers + 250 * critical_facilities on the parcel's distinct feeders. De-energise ranking =
sum of red-flag scores of parcels scoring >= 50 on the feeder, divided by its customers; red-flag
day = weather_danger 90 and the given wind_kmh. Sensitivity variant = policy weights moved 20%."""


@dataclass(frozen=True)
class Question:
    qid: str
    scenario: str
    text: str
    params: dict


def default_questions(policy) -> list:
    qs = []
    for t in (40, 50, 55, 60, 70):
        qs.append(Question(f"exposure-{t}", "exposure",
                           f"If every parcel scoring {t} or more ignites, how many customers and "
                           f"critical facilities are exposed? List the parcels.", {"min_score": str(t)}))
    for b in (5000, 6000, 9000, 18000, None):
        cap = "with no budget cap" if b is None else f"where one action may cost at most ${b}"
        qs.append(Question(f"mitigation-{b}", "mitigation",
                           f"Which single parcel mitigation gives the biggest risk reduction per "
                           f"dollar, {cap}?", {"budget_usd": b}))
    for w in (40, 50, 60, 70, 80):
        qs.append(Question(f"deenergise-{w}", "deenergise",
                           f"Red-flag day, wind {w} km/h: which feeder should we de-energise first?",
                           {"wind_kmh": w}))
    for v in ("fuel_load:up", "dead_fuel:up", "moisture:down", "wind:up", "weather_danger:down"):
        qs.append(Question(f"sensitivity-{v}", "sensitivity",
                           f"On a red-flag day with wind 60 km/h, does the feeder to de-energise "
                           f"first change if the policy weight changes as '{v}' (20%)?",
                           {"variant": v, "wind_kmh": 60}))
    return qs


def truth_for(ctx, q: Question) -> dict:
    p = q.params
    if q.scenario == "exposure":
        r = sc.exposure_if_ignite(ctx, p["min_score"])
        return {"parcels": r["parcels"], "customers": r["customers"],
                "critical_facilities": r["critical_facilities"],
                "policy_sha256": r["policy_sha256"], "counterfactual": False}
    if q.scenario == "mitigation":
        r = sc.best_mitigation(ctx, p["budget_usd"])
        b = r["best"]
        return {"parcel": b and b["parcel"], "mitigation": b and b["mitigation"],
                "risk_reduction_per_1000usd": b and b["risk_reduction_per_1000usd"],
                "policy_sha256": r["policy_sha256"], "counterfactual": True}
    if q.scenario == "deenergise":
        r = sc.deenergise_first(ctx, p["wind_kmh"])
        f = r["first"]
        return {"feeder": f and f["feeder"], "customers": f and f["customers"],
                "critical_facilities": f and f["critical_facilities"],
                "policy_sha256": r["policy_sha256"], "counterfactual": True}
    r = sc.decision_sensitivity(ctx, p["variant"], p["wind_kmh"])
    return {"feeder_decision_changes": r["feeder_decision_changes"],
            "feeder_variant": r["feeder_variant"], "policy_sha256": r["policy_sha256"],
            "counterfactual": True}


def _close(a, b) -> bool:
    try:
        return abs(Decimal(str(a)) - Decimal(str(b))) <= Decimal("0.01")
    except Exception:  # noqa: BLE001
        return False


def grade(q: Question, truth: dict, answer: dict | None) -> dict:
    m = {k: None for k in METRICS}
    m["parse_failure"] = float(answer is None)
    if answer is None:
        m.update(correct=0.0, provenance_ok=0.0)
        return m
    keys = [k for k in truth if k not in ("policy_sha256", "counterfactual")]
    ok = True
    for k in keys:
        got, want = answer.get(k), truth[k]
        if k == "parcels":
            ok &= isinstance(got, list) and sorted(map(str, got)) == sorted(want)
        elif k == "risk_reduction_per_1000usd":
            ok &= (got is None and want is None) or (got is not None and want is not None and _close(got, want))
        elif isinstance(want, bool):
            ok &= got is want
        else:
            ok &= got == want
    m["correct"] = float(ok)
    m["provenance_ok"] = float(answer.get("policy_sha256") == truth["policy_sha256"]
                               and answer.get("counterfactual") is truth["counterfactual"])
    return m


# ------------------------------------------------------------------ tools per arm
def _chat_tools(defs) -> list:
    return [{"type": "function", "function": {
        "name": d["name"], "description": d["description"], "parameters": d["parameters"]}}
        for d in defs]


def _obj(name, desc, props=None, required=()):
    return {"type": "function", "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": list(required)}}


FLAT_TOOL_DEFINITIONS = [
    _obj("get_table", "Return a raw table: parcels (pinned receipt summary per parcel), assets "
         "(asset -> parcel, feeder), customer_groups (group -> feeder, customers, facility), "
         "mitigations (catalogue with metric, factor, cost_usd), weight_variants.",
         {"name": {"type": "string", "enum": ["parcels", "assets", "customer_groups",
                                              "mitigations", "weight_variants"]}}, ["name"]),
    _obj("what_if", "Re-score ONE parcel with the real scorer. overrides sets metric values "
         "(e.g. weather_danger, wind_kmh); scale_metric/scale_factor multiplies one metric; "
         "weight_variant (e.g. 'moisture:up') re-scores under a +/-20% policy weight variant. "
         "Returns score, band, policy_sha256, counterfactual:true.",
         {"parcel": {"type": "string"}, "overrides": {"type": "object"},
          "scale_metric": {"type": "string"}, "scale_factor": {"type": "string"},
          "weight_variant": {"type": "string"}}, ["parcel"]),
]
GET_RECEIPT = _obj("get_receipt", "Verified score receipt for one parcel.",
                   {"parcel": {"type": "string"}}, ["parcel"])


class FlatTools:
    def __init__(self, ctx):
        self.ctx = ctx
        self.variants = dict(weight_sensitivity(ctx.policy))
        self.base = ctx.baseline()

    def call(self, name, args):
        ctx, g = self.ctx, self.ctx.graph
        try:
            if name == "get_receipt":
                return {"counterfactual": False, "receipt": self.base[args["parcel"]].wire()}
            if name == "get_table":
                t = args["name"]
                if t == "parcels":
                    return {"counterfactual": False, "policy_sha256": ctx.policy.sha256, "rows": [
                        {"parcel": p, "status": r.status,
                         "score": None if r.score is None else str(r.score), "band": r.band}
                        for p, r in sorted(self.base.items())]}
                if t == "assets":
                    return {"rows": [{"asset": a, **v} for a, v in sorted(g.assets.items())]}
                if t == "customer_groups":
                    return {"rows": [{"group": k, **v} for k, v in sorted(g.customer_groups.items())]}
                if t == "mitigations":
                    return {"rows": list(g.mitigations)}
                if t == "weight_variants":
                    return {"rows": sorted(self.variants)}
            if name == "what_if":
                policy = self.variants[args["weight_variant"]] if args.get("weight_variant") else None
                scale = None
                if args.get("scale_metric"):
                    scale = (args["scale_metric"], str(args.get("scale_factor", "1")))
                r = ctx.receipt(args["parcel"], policy, args.get("overrides") or None, scale)
                return {"counterfactual": True, "parcel": args["parcel"], "status": r.status,
                        "score": None if r.score is None else str(r.score), "band": r.band,
                        "policy_sha256": (policy or ctx.policy).sha256}
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            return {"error": str(exc)}
        return {"error": f"unknown tool {name!r}"}


def arm_setup(arm: str, ctx):
    if arm == "graph":
        tools = gt.GraphTools(ctx)
        flat = FlatTools(ctx)
        defs = [GET_RECEIPT] + gt.GRAPH_TOOL_DEFINITIONS

        def call(name, args):
            return flat.call(name, args) if name == "get_receipt" else tools.call(name, args)
        return _chat_tools(defs), call
    flat = FlatTools(ctx)
    return _chat_tools([GET_RECEIPT] + FLAT_TOOL_DEFINITIONS), flat.call


def parse_answer(text):
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        obj = json.loads(text)
    except ValueError:
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b <= a:
            return None
        try:
            obj = json.loads(text[a:b + 1])
        except ValueError:
            return None
    return obj if isinstance(obj, dict) else None


def _chat_retry(client, messages, tools, tries=7):
    import time
    for i in range(tries):
        try:
            return client.chat(messages, tools)
        except Exception as exc:  # noqa: BLE001
            if "429" not in str(exc) and "RateLimit" not in type(exc).__name__ or i == tries - 1:
                raise
            time.sleep(min(60, 4 * 2 ** i))


def run_one(client, arm, ctx, q, truth, repeat=0):
    tools, call = arm_setup(arm, ctx)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"[scenario:{q.scenario}] {q.text}"}]
    results, text, rounds = [], None, 0
    try:
        for rounds in range(MAX_ROUNDS + 1):
            msg = _chat_retry(client, messages, tools)
            if not msg["tool_calls"]:
                text = msg["content"]
                break
            messages.append({"role": "assistant", "content": msg["content"],
                             "tool_calls": msg["tool_calls"]})
            for tc in msg["tool_calls"]:
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except ValueError:
                    args = {}
                out = call(tc["function"]["name"], args)
                results.append(out)
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": json.dumps(out)})
        error = None
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"[:200]
    answer = parse_answer(text)
    m = grade(q, truth, answer)
    if answer is not None:
        pin = gt.verify_pinned_numbers(answer, results, q.text)
        m["numbers_pinned"] = float(pin["ok"])
    else:
        pin = {"unpinned": [], "numbers_used": 0}
        m["numbers_pinned"] = 0.0
    return {"qid": q.qid, "scenario": q.scenario, "arm": arm, "repeat": repeat, "metrics": m,
            "tool_calls": len(results), "unpinned": pin["unpinned"][:8], "error": error,
            "answer": answer}


def aggregate(records):
    """records for one (model, arm). Returns metric -> {mean, lo, hi, n}, plus per scenario."""
    def agg(rows):
        by_q = {}
        for r in rows:
            by_q.setdefault(r["qid"], []).append(r)
        out = {}
        for name in METRICS:
            per_q = [fmean(x["metrics"][name] for x in runs if x["metrics"][name] is not None)
                     for runs in by_q.values()
                     if any(x["metrics"][name] is not None for x in runs)]
            out[name] = bootstrap_ci(per_q)
        out["tool_calls"] = bootstrap_ci([fmean(x["tool_calls"] for x in runs) for runs in by_q.values()])
        return out
    scen = {s: agg([r for r in records if r["scenario"] == s])
            for s in sorted({r["scenario"] for r in records})}
    return {"overall": agg(records), "by_scenario": scen}


def paired_difference(graph_rows, flat_rows, metric):
    def per_q(rows):
        d = {}
        for r in rows:
            v = r["metrics"][metric]
            if v is not None:
                d.setdefault(r["qid"], []).append(v)
        return {k: fmean(v) for k, v in d.items()}
    a, b = per_q(graph_rows), per_q(flat_rows)
    return bootstrap_ci([a[k] - b[k] for k in a if k in b])


def run_scenarios(ctx, clients: dict, repeats=3, workers=6):
    """clients: {model_name: client}. Returns {"runs": [...], "summary": {...}}."""
    questions = default_questions(ctx.policy)
    truths = {q.qid: truth_for(ctx, q) for q in questions}
    jobs = [(m, arm, q, r) for m in clients for arm in ("graph", "flat")
            for q in questions for r in range(repeats)]
    lock = threading.Lock()
    runs = []

    def work(job):
        model, arm, q, r = job
        rec = run_one(clients[model], arm, ctx, q, truths[q.qid], r)
        rec["model"] = model
        with lock:
            runs.append(rec)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, jobs))
    runs.sort(key=lambda r: (r["model"], r["arm"], r["qid"], r["repeat"]))
    summary = {}
    for model in clients:
        g = [r for r in runs if r["model"] == model and r["arm"] == "graph"]
        f = [r for r in runs if r["model"] == model and r["arm"] == "flat"]
        summary[model] = {"graph": aggregate(g), "flat": aggregate(f),
                          "graph_minus_flat": {m: paired_difference(g, f, m) for m in METRICS[:3]},
                          "errors": sum(1 for r in g + f if r["error"])}
    return {"questions": [q.__dict__ for q in questions], "truths": truths,
            "runs": runs, "summary": summary}


class OracleClient:
    """Offline stand-in for --dry-run and tests: calls the graph scenario tool, then echoes it.

    It proves the harness and grading plumbing only; it says nothing about any real model.
    """

    def __init__(self, ctx):
        self.tools = gt.GraphTools(ctx)

    def chat(self, messages, tools=None):
        if messages[-1]["role"] == "tool":
            out = json.loads(messages[-1]["content"])
            return {"content": json.dumps(self._answer(messages[1]["content"], out)),
                    "tool_calls": []}
        import re
        text = messages[1]["content"]
        nums = [int(x) for x in re.findall(r"\d+", text.split("]", 1)[1])]
        if "[scenario:exposure]" in text:
            name, args = "exposure_if_ignite", {"min_score": str(nums[0])}
        elif "[scenario:mitigation]" in text:
            name, args = "best_mitigation", ({"budget_usd": nums[0]} if "at most" in text else {})
        elif "[scenario:deenergise]" in text:
            name, args = "deenergise_first", {"wind_kmh": nums[0]}
        else:
            v = re.search(r"'([^']+)'", text).group(1)
            name, args = "decision_sensitivity", {"variant": v, "wind_kmh": 60}
        return {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": name, "arguments": json.dumps(args)}}]}

    @staticmethod
    def _answer(text, out):
        base = {"policy_sha256": out.get("policy_sha256"),
                "counterfactual": out.get("counterfactual", False)}
        if "[scenario:exposure]" in text:
            return {**base, "parcels": out["parcels"], "customers": out["customers"],
                    "critical_facilities": out["critical_facilities"]}
        if "[scenario:mitigation]" in text:
            b = out["best"]
            return {**base, "parcel": b and b["parcel"], "mitigation": b and b["mitigation"],
                    "risk_reduction_per_1000usd": b and b["risk_reduction_per_1000usd"]}
        if "[scenario:deenergise]" in text:
            f = out["first"]
            return {**base, "feeder": f and f["feeder"], "customers": f and f["customers"],
                    "critical_facilities": f and f["critical_facilities"]}
        return {**base, "feeder_decision_changes": out["feeder_decision_changes"],
                "feeder_variant": out["feeder_variant"]}


def render_summary(result: dict, meta: dict) -> str:
    def cell(c):
        return "n/a" if c["mean"] is None else f"{c['mean']*100:.0f}% [{c['lo']*100:.0f}, {c['hi']*100:.0f}]"
    lines = ["# Decision-scenario evaluation: graph agent vs flat-table agent", "",
             f"Models: {', '.join(meta['models'])}. Repeats: {meta['repeats']}. "
             f"Questions: {len(result['questions'])}. Dry run: {meta['dry_run']}.",
             "Synthetic data; 95% percentile-bootstrap CIs, resampling unit = question.", "",
             "| Model | Arm | Correct | Provenance | Numbers pinned | Tool calls (mean) |",
             "|---|---|---|---|---|---|"]
    for model, s in result["summary"].items():
        for arm in ("graph", "flat"):
            o = s[arm]["overall"]
            lines.append(f"| {model} | {arm} | {cell(o['correct'])} | {cell(o['provenance_ok'])} | "
                         f"{cell(o['numbers_pinned'])} | {o['tool_calls']['mean']:.1f} |")
    lines += ["", "Paired difference (graph minus flat), percentage points [95% CI]:", ""]
    for model, s in result["summary"].items():
        d = s["graph_minus_flat"]
        lines.append(f"- {model}: " + "; ".join(
            f"{k} {d[k]['mean']*100:+.0f} [{d[k]['lo']*100:+.0f}, {d[k]['hi']*100:+.0f}]" for k in d))
    return "\n".join(lines) + "\n"
