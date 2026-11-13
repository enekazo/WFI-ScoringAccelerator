"""Re-run only the infrastructure-error rows of extra-100/per-run (429, timeouts, connection/auth glitches).

Originals are never touched. Replacement rows go to docs/evidence/scenario-1-locking-the-score/error-reruns/.
Same prompt/arms/verifier/models/dataset/pinned policy as run_extra_batches.py; receipts are built locally
from the pinned policy (Fabric Gold reproduces them byte-for-byte, see scenario-2 fabric_hash_check).
Errors that are genuine model behaviour (e.g. tool-round limit) are classified and kept, not re-run.

  python scripts/rerun_error_rows.py --classify-only
  python scripts/rerun_error_rows.py --workers 2 --budget 3 --hard-stop 6
"""

import argparse
import json
import logging
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from pinned_scoring.experiment.arms import run_arm  # noqa: E402
from pinned_scoring.experiment.config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from pinned_scoring.experiment.metrics import score_answer, signature  # noqa: E402
from pinned_scoring.experiment.runner import _jsonable, build_cases, make_clients  # noqa: E402
from pinned_scoring.policy import Policy  # noqa: E402
from pinned_scoring.receipts_store import ReadOnlyReceiptStore  # noqa: E402
from pinned_scoring.synth.generator import read_dataset  # noqa: E402
from run_extra_batches import PRICES, cost_usd  # noqa: E402

log = logging.getLogger("rerun")
EV = ROOT / "docs" / "evidence" / "scenario-1-locking-the-score"
SRC = EV / "extra-100" / "per-run"
OUT = EV / "error-reruns"
INFRA = re.compile(r"429|RateLimit|Timeout|timed out|Connection error|APIConnectionError|ClientAuthentication|"
                   r"\b(500|502|503|504)\b", re.I)


def classify(err: str) -> str:
    return "infrastructure" if INFRA.search(err) else "genuine"


def short(err: str) -> str:
    e = str(err).split("\n")[0]
    return "RateLimitError 429" if "429" in e else e[:80]


def load_errored():
    rows = []
    for f in sorted(SRC.glob("batch_*/runs.json")):
        b = int(f.parent.name.split("_")[1])
        for r in json.loads(f.read_text(encoding="utf-8")):
            if r.get("error"):
                rows.append({**r, "batch": b, "class": classify(r["error"])})
    return rows


def write_classification(rows):
    grp = Counter((r["model"], r["arm"], short(r["error"]), r["class"]) for r in rows)
    lines = ["| model | arm | error | class | rows |", "|---|---|---|---|---|"]
    lines += [f"| {m} | {a} | {e} | {c} | {n} |" for (m, a, e, c), n in sorted(grp.items())]
    by_b = Counter(r["batch"] for r in rows)
    lines += ["", "Per batch: " + ", ".join(f"batch_{b:02d}={n}" for b, n in sorted(by_b.items())),
              f"Total errored: {len(rows)}; infrastructure: {sum(r['class'] == 'infrastructure' for r in rows)}; "
              f"genuine: {sum(r['class'] == 'genuine' for r in rows)}"]
    (OUT / "classification.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "errored_rows_index.json").write_text(json.dumps(
        [{k: r[k] for k in ("batch", "model", "arm", "parcel", "repeat", "class")} | {"error": str(r["error"])[:300]}
         for r in rows], indent=1), encoding="utf-8")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--budget", type=float, default=3.0)
    ap.add_argument("--hard-stop", type=float, default=6.0)
    ap.add_argument("--attempts", type=int, default=4)
    ap.add_argument("--classify-only", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(OUT / "rerun.log", encoding="utf-8")])
    errored = load_errored()
    write_classification(errored)
    todo = [r for r in errored if r["class"] == "infrastructure"]
    if a.classify_only:
        return 0

    cfg = load_config(DEFAULT_CONFIG_PATH)
    dataset = read_dataset(ROOT / "data", prefix="extra_")
    policy = Policy.load(ROOT / "examples" / "wildfire" / "policy.json")
    parcels = sorted({r["parcel"] for r in todo})
    receipts_dir = OUT / "_receipts"
    cases = build_cases(dataset, policy, parcels, receipts_dir)
    store = ReadOnlyReceiptStore(receipts_dir)
    clients = make_clients(sorted({r["model"] for r in todo}), cfg, False)
    spent = {"usd": 0.0, "stop": False}
    lock = threading.Lock()
    results = {}

    def spend():
        return cost_usd({m: [c.prompt_tokens, c.completion_tokens] for m, c in clients.items()})

    def one(r):
        key = f"{r['model']}|{r['arm']}|{r['parcel']}|{r['repeat']}"
        case, client = cases[r["parcel"]], clients[r["model"]]
        for attempt in range(1, a.attempts + 1):
            with lock:
                if spent["stop"] or spend() >= a.budget:
                    spent["stop"] = True
                    return key, None, attempt - 1
            out = run_arm(r["arm"], case, client, store)
            if not (out.error and classify(out.error) == "infrastructure"):
                break
            log.warning("%s attempt %d: %s", key, attempt, short(out.error))
            time.sleep(min(240, 30 * 2 ** (attempt - 1)))
        row = {"model": r["model"], "arm": r["arm"], "parcel": r["parcel"], "repeat": r["repeat"],
               "metrics": score_answer(case, out.delivered, out.blocked),
               "signature": list(signature(out.delivered)), "blocked": out.blocked,
               "violations": out.violations, "tool_calls": out.tool_calls, "error": out.error,
               "answer": _jsonable(out.delivered), "raw_answer": _jsonable(out.parsed)}
        if r["arm"] == "B":
            row["raw_metrics"] = score_answer(case, out.parsed, False)
        return key, row, attempt

    started = time.time()
    log.info("re-running %d infrastructure-error rows, workers=%d", len(todo), a.workers)
    attempts = {}
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for key, row, n in pool.map(one, todo):
            attempts[key] = n
            if row is not None:
                results[key] = row
            if len(results) % 10 == 0:
                log.info("done %d/%d, est $%.2f", len(results), len(todo), spend())

    clean = sum(1 for r in results.values() if not r["error"])
    tokens = {m: [c.prompt_tokens, c.completion_tokens] for m, c in clients.items()}
    (OUT / "rerun_rows.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip()
    meta = {"commit": commit, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "workers": a.workers,
            "seconds": round(time.time() - started), "errored_total": len(errored),
            "infrastructure_rows": len(todo), "genuine_rows_kept": len(errored) - len(todo),
            "rerun_attempted": len(results), "not_run_budget_stop": len(todo) - len(results),
            "now_clean": clean, "still_failing": len(results) - clean,
            "tokens_in_out": tokens, "est_cost_usd": spend(), "budget_usd": a.budget,
            "hard_stop_usd": a.hard_stop, "prices_usd_per_1M_in_out": PRICES,
            "receipts_source": "local build from pinned policy (byte-identical to Fabric Gold per fabric_hash_check)",
            "key_format": "model|arm|parcel|repeat"}
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(json.dumps(meta, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
