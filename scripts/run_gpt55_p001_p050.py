"""gpt-5.5 on P001-P050 (local data source), same arms/repeats/prompts/verifier as the P051-P150 run.

Two batches of 25 parcels; each writes runs.json, metrics.json, receipts/, stats.json, summary.md under
--out/batch_NN. Hard stop when the estimated cost would exceed --budget.
  python scripts/run_gpt55_p001_p050.py --out results/gpt55_p001_p050
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from pinned_scoring.experiment.config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from pinned_scoring.experiment.runner import run_experiment  # noqa: E402
from run_extra_batches import batch_stats, split_batches  # noqa: E402

log = logging.getLogger("gpt55")
PRICE = (5.0, 25.0)  # gpt-5.5 USD per 1M tokens (input, output), as in the P051-P150 run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "results" / "gpt55_p001_p050"))
    ap.add_argument("--model", default="gpt-5.5")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--budget", type=float, default=8.0)
    ap.add_argument("--only", type=int, nargs="*")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(out / "run.log", encoding="utf-8")])
    cfg = load_config(DEFAULT_CONFIG_PATH)
    ids = [f"P{i:03d}" for i in range(1, 51)]
    spent = 0.0
    for n, batch in enumerate(split_batches(ids, 25), 1):
        if a.only and n not in a.only:
            continue
        bdir = out / f"batch_{n:02d}"
        if (bdir / "DONE").exists():
            spent += json.loads((bdir / "stats.json").read_text())["est_cost_usd"]
            continue
        if spent >= a.budget:
            log.error("budget stop: est. $%.2f >= $%.2f", spent, a.budget)
            return 2
        started = time.time()
        res = run_experiment(bdir, [a.model], cfg, False, repeats=a.repeats, parcels=batch,
                             workers=a.workers, sensitivity=False)
        stats = batch_stats(res)
        tin, tout = res["tokens"].get(a.model, [0, 0])
        stats["est_cost_usd"] = round(PRICE[0] * tin / 1e6 + PRICE[1] * tout / 1e6, 2)
        stats.update(workers=a.workers, parcels=batch, seconds=round(time.time() - started),
                     gold_source="local")
        (bdir / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
        spent += stats["est_cost_usd"]
        errs = sum(x["errors"] for m in stats["per_model_arm"].values() for x in m.values())
        log.info("batch %02d: rows=%d errors=%d cost=$%.2f total=$%.2f",
                 n, len(res["runs"]), errs, stats["est_cost_usd"], spent)
        if errs > 0.05 * len(res["runs"]):
            log.error("batch %02d: too many errors; not marking DONE", n)
            return 1
        (bdir / "DONE").write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
