"""Experiment orchestration: build cases, run arms x models x repeats, sensitivity, outputs."""

import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

from ..receipts_store import ReadOnlyReceiptStore, build_receipts, write_receipts
from ..policy import Policy
from ..scorer import score
from ..synth import read_dataset, tamper_policy, weight_sensitivity
from .arms import ARMS, Case, run_arm
from .clients import AzureOpenAIModelClient, StubModelClient
from .metrics import aggregate, score_answer, signature

ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = ROOT / "examples" / "wildfire" / "policy.json"


def make_clients(models, config, dry_run: bool) -> dict:
    if dry_run:
        return {n: StubModelClient("small" if "small" in n or "mini" in n else "strong")
                for n in models}
    return {n: AzureOpenAIModelClient(n, config) for n in models}


def default_models(config, dry_run: bool) -> list:
    return ["stub-strong", "stub-small"] if dry_run else [config.strong, config.small]


def build_cases(dataset, policy, parcels, receipts_dir, truth=None, receipts=None) -> dict:
    """Write receipts for the pinned policy and build one Case per parcel.

    `receipts` (optional) are pre-built Receipt objects, e.g. read and hash-verified from
    the Fabric Gold table; they are written instead of scoring locally.
    """
    obs = {p: dataset.observations[p] for p in parcels}
    if receipts is None:
        receipts = build_receipts(obs, policy, dataset.as_of)
    write_receipts([r for r in receipts if r.entity_id in obs], receipts_dir)
    store = ReadOnlyReceiptStore(receipts_dir)
    cases = {}
    for p in parcels:
        t = dict((truth or dataset.ground_truth)[p])
        submitted = policy
        if t.get("fault") == "policy_tamper":
            submitted = tamper_policy(policy)
        cases[p] = Case(p, dataset.as_of.isoformat(), [o.wire() for o in obs[p]], policy,
                        submitted, store.get_receipt(p), t)
    return cases


def _run_cell(model, client, arm, case, repeats, store):
    out = []
    for rep in range(repeats):
        r = run_arm(arm, case, client, store)
        row = {"model": model, "arm": arm, "parcel": case.parcel, "repeat": rep,
               "metrics": score_answer(case, r.delivered, r.blocked),
               "signature": list(signature(r.delivered)),
               "blocked": r.blocked, "violations": r.violations,
               "tool_calls": r.tool_calls, "error": r.error,
               "answer": _jsonable(r.delivered), "raw_answer": _jsonable(r.parsed)}
        out.append(row)
        if arm == "B":  # raw (pre-verifier) view of the same run
            out[-1]["raw_metrics"] = score_answer(case, r.parsed, False)
    return out


def _jsonable(answer):
    if answer is None:
        return None
    return {**answer, "score": None if answer["score"] is None else str(answer["score"])}


def run_cases(clients, cases, repeats, workers, store, arms=ARMS, parallel_models=False) -> list:
    # Phi-4's serverless endpoint rejects tool calls (HTTP 400), so it only runs the no-tool arms.
    jobs = [(m, c, a, case) for m, c in clients.items() for a in arms for case in cases.values()
            if not (a == "B" and m.lower().startswith("phi"))]

    def run(js, n):
        with ThreadPoolExecutor(max_workers=max(1, n)) as pool:
            res = pool.map(lambda j: _run_cell(j[0], j[1], j[2], j[3], repeats, store), js)
            return [row for rows in res for row in rows]

    if not parallel_models:
        return run(jobs, workers)
    # one pool per model: each model has its own endpoint quota, so 429s stay isolated
    per_model = [[j for j in jobs if j[0] == m] for m in clients]
    with ThreadPoolExecutor(max_workers=len(per_model)) as outer:
        parts = outer.map(lambda js: run(js, workers), per_model)
        return [row for part in parts for row in part]


def summarize(rows, models, arms=ARMS) -> dict:
    out = {}
    for m in models:
        for a in arms:
            recs = [r for r in rows if r["model"] == m and r["arm"] == a]
            if not recs:
                continue
            out[f"{m}|{a}"] = aggregate(recs)
            if a == "B":
                raw = [{**r, "metrics": r["raw_metrics"]} for r in recs]
                out[f"{m}|B(raw)"] = aggregate(raw)
    return out


def deterministic_sensitivity(dataset, policy, pct=20) -> list:
    """Rescore the clean scored parcels under each +/-pct weight variant."""
    clean = [p for p, t in dataset.ground_truth.items() if t["expected_status"] == "scored"]
    base = {p: score(p, dataset.observations[p], policy, dataset.as_of) for p in clean}
    rows = []
    for label, variant in weight_sensitivity(policy, pct):
        shifts, flips = [], 0
        for p in clean:
            r = score(p, dataset.observations[p], variant, dataset.as_of)
            shifts.append(abs(r.score - base[p].score))
            flips += r.band != base[p].band
        rows.append({"variant": label, "policy_sha256": variant.sha256,
                     "mean_abs_score_shift": float(sum(shifts) / len(shifts)),
                     "band_flip_share": flips / len(clean)})
    return rows


def llm_sensitivity(dataset, policy, clients, parcels, workers, pct=20) -> dict:
    rows = []
    for label, variant in weight_sensitivity(policy, pct):
        truth = {}
        for p in parcels:
            t = dict(dataset.ground_truth[p])
            if t["fault"] == "policy_tamper":
                t.update(fault=None, expected_status="scored")
            if t["expected_status"] == "scored":
                r = score(p, dataset.observations[p], variant, dataset.as_of)
                t.update(expected_score=str(r.score), expected_band=r.band)
            truth[p] = t
        with tempfile.TemporaryDirectory() as tmp:
            # tamper parcels are treated as clean here, so no tampered submission is built
            cases = build_cases(dataset, variant, parcels, tmp, truth)
            for c in cases.values():
                c.submitted_policy = variant
            for row in run_cases(clients, cases, 1, workers, ReadOnlyReceiptStore(tmp)):
                row["variant"] = label
                row["parcel"] = f"{label}|{row['parcel']}"
                rows.append(row)
    return {"runs": rows, "metrics": summarize(rows, list(clients))}


def run_experiment(out_dir, models, config, dry_run, repeats=3, parcels=None, workers=4,
                   sensitivity=True, sensitivity_parcels=10, data_dir=None,
                   receipts_loader=None, parallel_models=False) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset = read_dataset(data_dir) if data_dir else read_dataset()
    policy = Policy.load(POLICY_PATH)
    ids = sorted(dataset.observations)
    if parcels:
        unknown = set(parcels) - set(ids)
        if unknown:
            raise ValueError(f"unknown parcels: {sorted(unknown)}")
        ids = [p for p in ids if p in parcels]
    clients = make_clients(models, config, dry_run)

    receipts_dir = out_dir / "receipts"
    cases = build_cases(dataset, policy, ids, receipts_dir,
                        receipts=receipts_loader() if receipts_loader else None)
    rows = run_cases(clients, cases, repeats, workers, ReadOnlyReceiptStore(receipts_dir),
                     parallel_models=parallel_models)
    result = {"dry_run": dry_run, "models": models, "repeats": repeats, "parcels": ids,
              "metrics": summarize(rows, models), "runs": rows,
              "throttled_429": {n: getattr(c, "throttled_429", 0) for n, c in clients.items()},
              "tokens": {n: [getattr(c, "prompt_tokens", 0), getattr(c, "completion_tokens", 0)]
                         for n, c in clients.items()}}

    if sensitivity:
        sens = {"deterministic": deterministic_sensitivity(dataset, policy)}
        subset = ids[:sensitivity_parcels]
        sens["llm"] = llm_sensitivity(dataset, policy, clients, subset, workers)
        sens["llm"]["parcels"] = subset
        result["sensitivity"] = sens

    (out_dir / "runs.json").write_text(json.dumps(result["runs"], indent=1), encoding="utf-8")
    (out_dir / "metrics.json").write_text(json.dumps(
        {k: result[k] for k in ("dry_run", "models", "repeats", "parcels", "metrics")}, indent=1),
        encoding="utf-8")
    if sensitivity:
        (out_dir / "sensitivity.json").write_text(json.dumps(result["sensitivity"], indent=1),
                                                  encoding="utf-8")
    from .report import render_summary
    (out_dir / "summary.md").write_text(render_summary(result), encoding="utf-8")
    return result
