"""Usefulness experiment: deterministic template answers (Arm T) vs the agent's delivered answers (Arm D).

Arm D = raw LLM text if the verifier passed it, otherwise the terse receipt fallback.
Faithfulness is a hard check done by code (the verifier); the LLM judge only rates the
question-answering qualities and is blind to which arm produced which answer.
"""

import json
import math
import random
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..agent import ReceiptStore, guarded_answer
from ..scorer import score
from ..synth import weight_sensitivity

CRITERIA = ("answers_question", "plain_language", "actionable")
CATEGORIES = ("explain", "why_incomplete", "compare", "what_if", "next_action", "plain_summary")

ACTIONS = {
    "missing": "supply a real field reading for {m}",
    "expired": "refresh {m} with a new measurement",
    "invalid": "re-measure {m}, because the stored reading is flagged invalid",
    "unit": "re-submit {m} in the unit the policy expects",
    "provenance": "replace the {m} reading with a real field reading, because its source is not accepted",
}
KIND_TEXT = {
    "missing": "is missing",
    "expired": "is too old to use",
    "invalid": "is flagged invalid",
    "unit": "was given in the wrong unit",
    "provenance": "comes from a source the policy does not accept",
}


@dataclass(frozen=True)
class Question:
    qid: str
    category: str
    text: str
    parcels: tuple
    variant: str | None = None  # weight_sensitivity label, for what_if questions


def default_questions() -> list:
    q = []

    def add(category, text, *parcels, variant=None):
        q.append(Question(f"Q{len(q) + 1:02d}", category, text, tuple(parcels), variant))

    add("explain", "Why did parcel P003 get the score it did?", "P003")
    add("explain", "Explain the score for parcel P014. What is driving it?", "P014")
    add("explain", "How was P007's risk band decided?", "P007")
    add("why_incomplete", "Why is there no score for P017?", "P017")
    add("why_incomplete", "P001 has no score. What went wrong?", "P001")
    add("why_incomplete", "Why was P019 not scored?", "P019")
    add("why_incomplete", "What is wrong with the data for P025?", "P025")
    add("compare", "Which of P014 and P003 is riskier, and why?", "P014", "P003")
    add("compare", "Compare P007 and P018.", "P007", "P018")
    add("compare", "How does P005 compare with P017?", "P005", "P017")
    add("what_if", "What would P003's score be if the moisture weight were 20% higher?", "P003",
        variant="moisture:up")
    add("what_if", "If the wind weight were 20% lower, what happens to P014?", "P014",
        variant="wind:down")
    add("what_if", "What if fuel load counted 20% more for P018?", "P018", variant="fuel_load:up")
    add("next_action", "What should I do next for P017?", "P017")
    add("next_action", "What should I do to get P024 scored?", "P024")
    add("next_action", "What is the next step for P002?", "P002")
    add("next_action", "P005 is scored. Is there anything I should do next?", "P005")
    add("plain_summary", "Give a plain-language summary of P030 for a non-expert.", "P030")
    add("plain_summary", "Summarise P021 for a landowner with no technical background.", "P021")
    add("plain_summary", "In simple words, what is the situation for P029?", "P029")
    return q


# ---- Arm T: deterministic template ---------------------------------------------------------

def _fmt(value) -> str:
    return str(value)


def _reason_parts(reason: str) -> tuple:
    kind, _, metric = reason.partition(":")
    return kind, metric


def _scored_text(r: dict) -> str:
    comps = "; ".join(f"{c[0]} contributes {c[3]}" for c in sorted(r["components"], key=lambda c: -float(c[3])))
    return (f"{r['entity_id']} is scored {r['score']} and falls in the {r['band']} band. "
            f"Contributions to the score: {comps}")


def _incomplete_text(r: dict) -> str:
    parts = []
    for reason in r["reasons"]:
        kind, metric = _reason_parts(reason)
        parts.append(f"{metric} {KIND_TEXT.get(kind, 'has a problem')}")
    return (f"{r['entity_id']} is incomplete, so no score or band is given. Reasons: " + "; ".join(parts))


def _next_text(r: dict) -> str:
    if r["status"] == "scored":
        return f"{r['entity_id']} is already scored, so no data fix is needed"
    steps = []
    for reason in r["reasons"]:
        kind, metric = _reason_parts(reason)
        steps.append(ACTIONS.get(kind, "review {m}").format(m=metric))
    return f"To get {r['entity_id']} scored: " + "; ".join(steps)


def template_answer(question: Question, receipts: dict, whatif: dict | None) -> str:
    rs = [receipts[p] for p in question.parcels]
    cat = question.category
    if cat == "what_if":
        r, h = rs[0], whatif
        return (f"{r['entity_id']} is scored {r['score']} today. With that weight change the score would be "
                f"{h['score']} instead, in the {h['band']} band. This is a what-if only; the stored score is unchanged")
    if cat == "compare":
        a, b = rs
        if a["status"] == "scored" and b["status"] == "scored":
            hi, lo = (a, b) if float(a["score"]) >= float(b["score"]) else (b, a)
            return (f"{hi['entity_id']} is higher at {hi['score']} ({hi['band']}) versus {lo['entity_id']} at "
                    f"{lo['score']} ({lo['band']})")
        parts = [(_scored_text(x) if x["status"] == "scored" else _incomplete_text(x)) for x in rs]
        return ". ".join(parts) + ". They cannot be ranked because one has no score"
    if cat == "next_action":
        return _next_text(rs[0])
    r = rs[0]
    return _scored_text(r) if r["status"] == "scored" else _incomplete_text(r)


def fallback_text(question: Question, receipts: dict) -> str:
    """What the agent delivers when the verifier blocks: receipt values only."""
    parts = ["Verifier blocked the narration; showing receipt values"]
    for p in question.parcels:
        r = receipts[p]
        if r["status"] == "scored":
            parts.append(f"{p} score {r['score']} band {r['band']}")
        else:
            parts.append(f"{p} incomplete; reasons {', '.join(r['reasons'])}")
    return ". ".join(parts)


# ---- Arm D: LLM answer, verified ------------------------------------------------------------

AGENT_SYSTEM = (
    "You answer questions about wildfire parcel risk for a non-expert. Use ONLY the receipt data given. "
    "Never invent or recompute numbers; quote scores, bands and reasons exactly. If a receipt is "
    "incomplete there is no score and no band: say so, say why (reasons look like 'missing:fuel_load' or "
    "'unit:fuel_moisture_pct') and what to do. Mention provenance (measured, generated, fixture) only as in "
    "the receipt. For what-if questions use the supplied what_if result and say it is hypothetical. "
    "Reply in plain prose, 2-5 sentences, no JSON."
)


def agent_messages(question: Question, receipts: dict, whatif: dict | None) -> list:
    ctx = {"receipts": {p: receipts[p] for p in question.parcels}}
    if whatif is not None:
        ctx["what_if"] = {"parcel": question.parcels[0], "change": question.variant,
                          "hypothetical_score": whatif["score"], "hypothetical_band": whatif["band"],
                          "counterfactual": True}
    return [{"role": "system", "content": AGENT_SYSTEM},
            {"role": "user", "content": f"Question: {question.text}\n\nData:\n{json.dumps(ctx, indent=1)}"}]


@dataclass
class Delivery:
    text: str
    blocked: bool
    infra_error: str | None = None
    violations: list = field(default_factory=list)
    raw: str | None = None


def deliver_agent(question, receipts, whatif, client, store) -> Delivery:
    raw, err = None, None
    try:
        raw = client.chat(agent_messages(question, receipts, whatif))["content"]
    except Exception as exc:  # recorded, never hidden
        err = f"{type(exc).__name__}: {exc}"[:300]
    if err or not raw:
        return Delivery(fallback_text(question, receipts), True, err or "empty answer", [], raw)
    text = raw.strip()
    guarded = guarded_answer(lambda q, c: text, question.text, store, question.parcels[0])
    if guarded.blocked:
        return Delivery(fallback_text(question, receipts), True, None,
                        [f"{v.kind}: {v.detail}" for v in guarded.violations], raw)
    return Delivery(text, False, None, [], raw)


def faithfulness_check(text: str, question: Question, store) -> list:
    g = guarded_answer(lambda q, c: text, question.text, store, question.parcels[0])
    return [f"{v.kind}: {v.detail}" for v in g.violations]


# ---- Judge --------------------------------------------------------------------------------------

JUDGE_SYSTEM = (
    "You are a strict, impartial grader of answers to questions from a non-expert about wildfire parcel "
    "risk. You see one question and two anonymous answers, A and B. Rate EACH answer from 1 (poor) to 5 "
    "(excellent) on: answers_question (directly and completely answers what was asked), plain_language "
    "(a non-expert would understand it; no jargon or raw identifiers left unexplained), actionable "
    "(tells the reader what they can do or conclude next, where relevant). Do NOT judge factual accuracy "
    "of numbers; that is checked elsewhere. Do not prefer an answer for its length or position. Reply "
    'with ONE JSON object only: {"A": {"answers_question": n, "plain_language": n, "actionable": n}, '
    '"B": {"answers_question": n, "plain_language": n, "actionable": n}}'
)


def judge_messages(question: str, first: str, second: str) -> list:
    return [{"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": f"Question: {question}\n\nAnswer A:\n{first}\n\nAnswer B:\n{second}"}]


def parse_judgement(text) -> dict | None:
    if not isinstance(text, str):
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        out = {}
        for side in ("A", "B"):
            out[side] = {c: int(data[side][c]) for c in CRITERIA}
            if any(not 1 <= v <= 5 for v in out[side].values()):
                return None
        return out
    except (ValueError, KeyError, TypeError):
        return None


def judge_pair(client, question: str, text_t: str, text_d: str, rng: random.Random) -> dict:
    d_first = rng.random() < 0.5
    first, second = (text_d, text_t) if d_first else (text_t, text_d)
    err, parsed = None, None
    for _ in range(2):
        try:
            parsed = parse_judgement(client.chat(judge_messages(question, first, second))["content"])
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"[:300]
            continue
        if parsed:
            err = None
            break
        err = "unparseable judge output"
    if not parsed:
        return {"error": err, "d_first": d_first}
    sd, st = (parsed["A"], parsed["B"]) if d_first else (parsed["B"], parsed["A"])
    total_d, total_t = sum(sd.values()), sum(st.values())
    return {"error": None, "d_first": d_first, "scores_D": sd, "scores_T": st,
            "total_D": total_d, "total_T": total_t,
            "outcome": "win" if total_d > total_t else "loss" if total_d < total_t else "tie"}


# ---- Statistics ---------------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> tuple:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_mean_ci(values: list, seed: int = 0, iters: int = 5000) -> tuple:
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(iters))
    return (means[int(0.025 * iters)], means[int(0.975 * iters) - 1])


def _share(rows: list) -> dict:
    n = len(rows)
    out = {"n": n}
    for k in ("win", "tie", "loss"):
        c = sum(r["outcome"] == k for r in rows)
        lo, hi = wilson(c, n)
        out[k] = {"count": c, "share": c / n if n else 0.0, "ci95": [lo, hi]}
    return out


def summarize(rows: list, deliveries: dict) -> dict:
    ok = [r for r in rows if r.get("outcome")]
    by_q = {}
    for r in ok:
        by_q.setdefault(r["qid"], []).append(r["total_D"] - r["total_T"])
    q_means = [sum(v) / len(v) for v in by_q.values()]
    lo, hi = bootstrap_mean_ci(q_means)
    by_cat = {}
    for cat in CATEGORIES:
        sub = [r for r in ok if r["category"] == cat]
        if sub:
            diffs = [r["total_D"] - r["total_T"] for r in sub]
            by_cat[cat] = {**_share(sub), "mean_total_diff": sum(diffs) / len(diffs)}
    crit = {c: sum(r["scores_D"][c] - r["scores_T"][c] for r in ok) / len(ok) if ok else 0.0 for c in CRITERIA}
    agent = list(deliveries.values())
    return {
        "judgements": {"attempted": len(rows), "usable": len(ok), "judge_errors": len(rows) - len(ok)},
        "overall": _share(ok),
        "mean_total_diff_D_minus_T": sum(q_means) / len(q_means) if q_means else 0.0,
        "mean_total_diff_ci95_bootstrap_over_questions": [lo, hi],
        "mean_criterion_diff_D_minus_T": crit,
        "by_category": by_cat,
        "agent_delivery": {
            "questions": len(agent),
            "raw_passed_verifier": sum(not d["blocked"] for d in agent),
            "fallback_delivered": sum(d["blocked"] for d in agent),
            "of_which_infra_errors": sum(bool(d["infra_error"]) for d in agent),
            "of_which_verifier_blocks": sum(bool(d["violations"]) for d in agent),
        },
    }


# ---- Orchestration ------------------------------------------------------------------------------

def _prepare(dataset, policy, questions, receipts_dir):
    from ..receipts_store import ReadOnlyReceiptStore, build_receipts, write_receipts
    needed = sorted({p for q in questions for p in q.parcels})
    write_receipts(build_receipts({p: dataset.observations[p] for p in needed}, policy, dataset.as_of),
                   receipts_dir)
    ro = ReadOnlyReceiptStore(receipts_dir)
    receipts = {p: ro.get_receipt(p) for p in needed}
    variants = dict(weight_sensitivity(policy, 20))
    whatifs = {}
    for q in questions:
        if q.variant:
            r = score(q.parcels[0], dataset.observations[q.parcels[0]], variants[q.variant], dataset.as_of)
            whatifs[q.qid] = {"score": None if r.score is None else str(r.score), "band": r.band}
    store = ReceiptStore()
    store.load_many(receipts.values())
    return receipts, whatifs, store


def run_usefulness(dataset, policy, agent_client, judge_client, receipts_dir, repeats=3, seed=20260715,
                   workers=2, questions=None) -> dict:
    questions = questions or default_questions()
    receipts, whatifs, store = _prepare(dataset, policy, questions, receipts_dir)

    def one(q):
        t = template_answer(q, receipts, whatifs.get(q.qid))
        d = deliver_agent(q, receipts, whatifs.get(q.qid), agent_client, store)
        return q, t, d

    with ThreadPoolExecutor(max_workers=workers) as ex:
        built = list(ex.map(one, questions))

    answers, deliveries = [], {}
    for q, t, d in built:
        answers.append({"qid": q.qid, "category": q.category, "question": q.text, "parcels": list(q.parcels),
                        "template": t, "template_faithfulness_violations": faithfulness_check(t, q, store),
                        "agent_delivered": d.text, "agent_blocked": d.blocked,
                        "agent_infra_error": d.infra_error, "agent_violations": d.violations,
                        "agent_raw": d.raw,
                        "agent_delivered_faithfulness_violations": faithfulness_check(d.text, q, store)})
        deliveries[q.qid] = {"blocked": d.blocked, "infra_error": d.infra_error, "violations": d.violations}
    by_id = {a["qid"]: a for a in answers}

    jobs = [(q.qid, r) for q in questions for r in range(repeats)]

    def judge(job):
        qid, rep = job
        a = by_id[qid]
        rng = random.Random(f"{seed}|{qid}|{rep}")
        res = judge_pair(judge_client, a["question"], a["template"], a["agent_delivered"], rng)
        return {"qid": qid, "category": a["category"], "repeat": rep, **res}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(judge, jobs))
    return {"seed": seed, "repeats": repeats, "answers": answers, "judgements": rows,
            "summary": summarize(rows, deliveries),
            "faithfulness": {
                "template_violations": sum(bool(a["template_faithfulness_violations"]) for a in answers),
                "delivered_violations": sum(bool(a["agent_delivered_faithfulness_violations"])
                                            for a in answers)}}


def render_summary(result: dict, meta: dict) -> str:
    s = result["summary"]
    o = s["overall"]
    pct = lambda x: f"{100 * x:.0f}%"
    ci = lambda x: f"[{pct(x['ci95'][0])}, {pct(x['ci95'][1])}]"
    lines = [
        "# Usefulness summary: agent delivered answer (D) vs deterministic template (T)", "",
        f"Agent model: `{meta['agent_model']}`; judge: `{meta['judge_model']}` (blind, A/B order randomised); "
        f"{len(result['answers'])} questions x {result['repeats']} repeats; dry_run={meta['dry_run']}.", "",
        f"Usable judgements: {s['judgements']['usable']}/{s['judgements']['attempted']}.", "",
        "| Outcome (D vs T) | Count | Share | 95% CI (Wilson) |", "|---|---|---|---|"]
    for k in ("win", "tie", "loss"):
        lines.append(f"| {k} | {o[k]['count']} | {pct(o[k]['share'])} | {ci(o[k])} |")
    lo, hi = s["mean_total_diff_ci95_bootstrap_over_questions"]
    lines += ["", f"Mean rubric-total difference D-T (max 15 points): {s['mean_total_diff_D_minus_T']:+.2f} "
              f"(95% bootstrap CI over questions [{lo:+.2f}, {hi:+.2f}]).", "",
              "Per criterion mean D-T: " + ", ".join(f"{k} {v:+.2f}" for k, v in
                                                     s["mean_criterion_diff_D_minus_T"].items()), "",
              "| Category | n | win | tie | loss | mean total D-T |", "|---|---|---|---|---|---|"]
    for cat, c in s["by_category"].items():
        lines.append(f"| {cat} | {c['n']} | {pct(c['win']['share'])} | {pct(c['tie']['share'])} | "
                     f"{pct(c['loss']['share'])} | {c['mean_total_diff']:+.2f} |")
    a = s["agent_delivery"]
    f = result["faithfulness"]
    lines += ["", f"Agent delivery: {a['raw_passed_verifier']}/{a['questions']} raw LLM answers passed the "
              f"verifier; {a['fallback_delivered']} fell back to the terse receipt text "
              f"({a['of_which_verifier_blocks']} verifier blocks, {a['of_which_infra_errors']} infrastructure errors).",
              f"Hard faithfulness check on delivered text: template violations {f['template_violations']}, "
              f"agent violations {f['delivered_violations']}.", ""]
    return "\n".join(lines)
