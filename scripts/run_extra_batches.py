"""Resumable batch runner for the 200 extra synthetic parcels (P051-P250) through the Fabric path.

Per batch (default 8 x 25 parcels): check capacity -> reset+reload the medallion tables -> load the
batch graph and chain tables -> refresh the SQL endpoint -> RefreshGraph (API only, never the portal,
never a schedule) -> read Gold from Fabric -> run the experiment (Arm A/B, repeats) on those receipts
-> write results/extra/batch_NN/ and a DONE marker. Completed batches are skipped on restart.

  python scripts/run_extra_batches.py --dry-run --out results/extra_dry   # stubs, no cloud
  python scripts/run_extra_batches.py --only 1                            # live smoke on batch 1
  python scripts/run_extra_batches.py                                     # all batches
  python scripts/run_extra_batches.py --restore-capacity                  # scale back to original SKU

Configuration is by environment variables only (no IDs are stored in the repo):
  FABRIC_WORKSPACE_ID FABRIC_LAKEHOUSE_ID FABRIC_SQL_SERVER FABRIC_SQL_DATABASE FABRIC_SQL_ENDPOINT_ID
  FABRIC_GRAPH_MODEL_ID FABRIC_CAPACITY_NAME FABRIC_CAPACITY_RG [FABRIC_CAPACITY_EXPECT_SKU=F8]
Capacity: the runner never scales UP; it verifies Active/expected SKU before each batch (resuming if
paused) and scales back to the recorded original SKU when the run ends, fails or is stopped.
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from pinned_scoring.experiment.config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from pinned_scoring.experiment.runner import default_models, run_experiment  # noqa: E402
from pinned_scoring.synth.generator import read_dataset, write_dataset  # noqa: E402

log = logging.getLogger("extra")
BATCH_SIZE = 25
BUDGET_USD = 200.0
LAST_PARCEL = 150  # P051-P150 now; raise to 250 later to add P151-P250 as batches 5-8 without redoing 1-4
# Assumed list prices, USD per 1M tokens (input, output); estimates, not measured.
PRICES = {"gpt-4.1": (2.0, 8.0), "gpt-4.1-mini": (0.4, 1.6), "phi-4": (0.125, 0.5),
          "gpt-6.1-sol": (5.0, 20.0), "gpt-6.1-terra": (2.0, 8.0), "grok-4.7": (3.0, 15.0)}


def cost_usd(tokens) -> float:
    total = 0.0
    for m, (p, c) in tokens.items():
        pi, po = PRICES.get(m.split("@")[0], (5.0, 20.0))
        total += pi * p / 1e6 + po * c / 1e6
    return round(total, 2)
MAX_HARD_ERROR_SHARE = 0.05  # non-429 call errors above this share abort the batch instead of marking it DONE
RATE_LIMIT_SHARE = 0.20  # a batch with more than this share of calls retried on 429 is re-run slower


# ----------------------------------------------------------------------------- capacity
class Capacity:
    """Thin az CLI wrapper. Reads the subscription from the active az login at runtime."""

    def __init__(self, name, rg, expect_sku="F8", runner=None):
        self.name, self.rg, self.expect_sku = name, rg, expect_sku
        self._run = runner or self._az

    @staticmethod
    def _az(args):
        return subprocess.check_output(["az", *args], text=True, shell=(os.name == "nt")).strip()

    def state(self) -> dict:
        out = self._run(["fabric", "capacity", "show", "-g", self.rg, "-n", self.name,
                         "--query", "{sku:sku.name,state:state}", "-o", "json"])
        return json.loads(out)

    def ensure_ready(self) -> dict:
        """Resume if paused; verify Active and the expected SKU. Never scales up."""
        st = self.state()
        if st["state"] != "Active":
            log.warning("capacity %s is %s, resuming", self.name, st["state"])
            self._run(["fabric", "capacity", "resume", "-g", self.rg, "-n", self.name])
            for _ in range(40):
                time.sleep(15)
                st = self.state()
                if st["state"] == "Active":
                    break
        if st["state"] != "Active":
            raise RuntimeError(f"capacity not Active: {st}")
        if st["sku"] != self.expect_sku:
            raise RuntimeError(f"capacity SKU is {st['sku']}, expected {self.expect_sku}; not scaling")
        return st

    def restore(self, sku) -> dict:
        st = self.state()
        if st["sku"] != sku:
            log.info("scaling capacity %s -> %s", st["sku"], sku)
            self._run(["fabric", "capacity", "update", "-g", self.rg, "-n", self.name,
                       "--sku", f"{{name:{sku},tier:Fabric}}"])
        st = self.state()
        log.info("final capacity state: %s", st)
        return st


# ----------------------------------------------------------------------------- Fabric ops
class FabricOps:
    """Live Fabric operations; every id comes from the environment."""

    def __init__(self, env=os.environ):
        self.ws = env["FABRIC_WORKSPACE_ID"]
        self.lh = env["FABRIC_LAKEHOUSE_ID"]
        self.server = env["FABRIC_SQL_SERVER"]
        self.db = env.get("FABRIC_SQL_DATABASE", "pinned_scoring_lh")
        self.sql_endpoint = env["FABRIC_SQL_ENDPOINT_ID"]
        self.graph_model = env["FABRIC_GRAPH_MODEL_ID"]
        self.session = None

    def _session(self):
        if self.session is None:
            from pinned_scoring.fabric.livy import LivySession
            self.session = LivySession(self.ws, self.lh).__enter__()
        return self.session

    def close(self):
        if self.session is not None:
            self.session.__exit__(None, None, None)
            self.session = None

    def _token(self) -> str:
        return subprocess.check_output(
            "az account get-access-token --resource https://storage.azure.com --query accessToken -o tsv",
            shell=True, text=True).strip()

    def reset_medallion(self):
        """Spark-free: remove the append-only medallion table folders (the loader recreates them)."""
        import requests
        from pinned_scoring.fabric.delta_loader import MEDALLION
        self.close()  # make sure no Livy session is left open
        h = {"Authorization": "Bearer " + self._token()}
        for t in MEDALLION:
            url = f"https://onelake.dfs.fabric.microsoft.com/{self.ws}/{self.lh}/Tables/{t}?recursive=true"
            r = requests.delete(url, headers=h, timeout=120)
            if r.status_code not in (200, 202, 404):
                r.raise_for_status()

    def upload_dataset(self, path: Path):
        self._dataset = path  # the Delta loader reads it locally; nothing to upload

    def load_medallion(self) -> str:
        from pinned_scoring.fabric import delta_loader as dl
        tables = dl.build_medallion(self._dataset, ROOT / "examples" / "wildfire" / "policy.json")
        counts = dl.write_tables(self.ws, self.lh, dl.medallion_arrow(tables), self._token(),
                                 append_only=("raw_observations", "gold_policy", "gold_receipts"))
        return f"delta/OneLake {counts}"

    def load_graph_and_chain(self, parcel_ids, receipts_by_id):
        from pinned_scoring.access_chain import generate as gen_chain
        from pinned_scoring.fabric import delta_loader as dl
        from pinned_scoring.fabric.graph_store import graph_to_rows
        from pinned_scoring.graph import build_graph
        parcels = [{"parcel_id": p, "status": r["status"], "band": r["band"], "score": r["score"]}
                   for p, r in sorted(receipts_by_id.items())]
        arrow = {**dl.graph_arrow(graph_to_rows(build_graph(parcel_ids))),
                 **dl.chain_arrow(gen_chain(parcels))}
        return dl.write_tables(self.ws, self.lh, arrow, self._token())

    def refresh_sql_endpoint(self):
        from fabric_graph_model_fix import fabric_call, wait_lro
        status, headers, text = fabric_call(
            "POST", f"/workspaces/{self.ws}/sqlEndpoints/{self.sql_endpoint}/refreshMetadata?preview=true", {})
        if status == 202:
            wait_lro(headers)
        elif status >= 300:
            raise RuntimeError(f"refreshMetadata {status}: {text[:200]}")

    def read_gold(self, expected: int):
        from pinned_scoring.fabric import GoldReader, sql_endpoint
        for attempt in range(12):
            rows = sql_endpoint.fetcher(self.server, self.db, connect_fn=sql_endpoint.connect_auto)()
            receipts = GoldReader(lambda: rows).list_receipts()
            if len(receipts) == expected:
                return receipts
            log.info("gold has %d/%d receipts, waiting (%d)", len(receipts), expected, attempt)
            time.sleep(15)
        raise RuntimeError(f"gold receipts never reached {expected}")

    def refresh_graph(self):
        """RefreshGraph job via the REST API only. Never saves the graph in the portal; no schedule."""
        from fabric_graph_model_fix import fabric_call
        base = f"/workspaces/{self.ws}/items/{self.graph_model}/jobs/instances"
        def open_jobs():
            st, _, tx = fabric_call("GET", base)
            vals = (json.loads(tx).get("value") or []) if st == 200 else []
            return [j for j in vals if j.get("status") in ("NotStarted", "InProgress")]
        for _ in range(90):  # never stack a refresh on a queued or running one
            if not open_jobs():
                break
            time.sleep(20)
        status, _, text = fabric_call("POST", f"{base}?jobType=RefreshGraph", {})
        if status >= 300:
            raise RuntimeError(f"RefreshGraph start {status}: {text[:200]}")
        for _ in range(120):
            time.sleep(15)
            status, _, text = fabric_call("GET", base)
            inst = (json.loads(text).get("value") or [{}])[0] if status == 200 else {}
            if inst.get("status") == "Completed":
                return inst
            if inst.get("status") in ("Failed", "Cancelled"):
                raise RuntimeError(f"RefreshGraph {inst.get('status')}: {inst.get('failureReason')}")
        raise RuntimeError("RefreshGraph timed out")


class StubOps:
    """Dry-run: records the calls, touches no cloud, and lets the experiment score locally."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*a, **k):
            self.calls.append(name)
            return [] if name == "read_gold" else None
        return call

    def close(self):
        pass


# ----------------------------------------------------------------------------- batches
def split_batches(ids, size):
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def batch_stats(result) -> dict:
    """Verifier blocks and model-endpoint 429s are kept strictly separate."""
    stats = {}
    for row in result["runs"]:
        s = stats.setdefault(row["model"], {}).setdefault(row["arm"], {
            "runs": 0, "verifier_blocks": 0, "errors": 0, "errors_429": 0})
        s["runs"] += 1
        s["verifier_blocks"] += bool(row["blocked"])
        err = row.get("error")
        s["errors"] += bool(err)
        s["errors_429"] += bool(err) and "429" in str(err)
    tokens = result.get("tokens", {})
    return {"per_model_arm": stats, "model_429_retries": result.get("throttled_429", {}),
            "tokens_in_out": tokens, "est_cost_usd": cost_usd(tokens)}


def calls_total(result) -> int:
    return max(1, sum(c for c in (len(result["runs"]),)) )


def run_batch(n, ids, args, ops, cfg, models, dry_run):
    out = Path(args.out) / f"batch_{n:02d}"
    if (out / "DONE").exists():
        log.info("batch %02d already DONE, skipping", n)
        return None
    out.mkdir(parents=True, exist_ok=True)
    full = read_dataset(ROOT / "data", prefix="extra_")
    sub = replace(full, observations={p: full.observations[p] for p in ids},
                  ground_truth={p: full.ground_truth[p] for p in ids})
    data_dir = out / "_data"
    write_dataset(sub, data_dir)

    ops.reset_medallion()
    ops.upload_dataset(data_dir / "dataset.json")
    log.info("batch %02d medallion load: %s", n, ops.load_medallion())
    ops.refresh_sql_endpoint()
    receipts = ops.read_gold(len(ids)) if not dry_run else []
    by_id = {r.entity_id: {"status": r.status, "band": r.band,
                           "score": None if r.score is None else str(r.score)} for r in receipts}
    if not dry_run:
        ops.load_graph_and_chain(ids, by_id)
        ops.refresh_graph()

    workers = args.workers
    for attempt in range(1, args.attempts + 1):
        started = time.time()
        try:
            result = run_experiment(out, models, cfg, dry_run, repeats=args.repeats, workers=workers,
                                    sensitivity=False, data_dir=data_dir, parallel_models=True,
                                    receipts_loader=(lambda: receipts) if receipts else None)
        except Exception as exc:
            log.error("batch %02d attempt %d failed with workers=%d: %s", n, attempt, workers, exc)
            result = None
        if result is not None:
            stats = batch_stats(result)
            retries = sum(stats["model_429_retries"].values())
            share = retries / max(1, len(result["runs"]))
            stats.update(workers=workers, attempt=attempt, parcels=ids, seconds=round(time.time() - started),
                         model_429_share=round(share, 4), gold_source="fabric" if receipts else "local-stub")
            (out / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
            hard = sum(a["errors"] - a["errors_429"] for m in stats["per_model_arm"].values() for a in m.values())
            if hard > MAX_HARD_ERROR_SHARE * max(1, len(result["runs"])) and not dry_run:
                raise RuntimeError(f"batch {n:02d}: {hard} non-429 call errors (auth/config?); not marking DONE")
            if share <= RATE_LIMIT_SHARE or attempt == args.attempts:
                break
            log.warning("batch %02d: 429 share %.0f%%, retrying with fewer workers", n, share * 100)
        workers = max(1, workers // 2)
        time.sleep(0 if dry_run else min(600, 60 * attempt))
    else:
        raise RuntimeError(f"batch {n:02d} failed after {args.attempts} attempts")
    if not dry_run:
        time.sleep(120)  # let the capacity cool down between batches
    (out / "DONE").write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "results" / "extra"))
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--attempts", type=int, default=3, help="per batch, each with fewer workers")
    ap.add_argument("--models", help="comma-separated deployments (default: config models)")
    ap.add_argument("--last-parcel", type=int, default=LAST_PARCEL, help="highest parcel number to include")
    ap.add_argument("--budget", type=float, default=BUDGET_USD, help="stop (resumable) before exceeding est. USD")
    ap.add_argument("--only", type=int, nargs="*", help="run only these batch numbers (1-based)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-capacity", action="store_true", help="smoke runs: do not scale down at exit")
    ap.add_argument("--restore-capacity", action="store_true", help="only scale capacity back, then exit")
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(out / "run.log", encoding="utf-8")])

    cap = None
    if not a.dry_run or a.restore_capacity:
        cap = Capacity(os.environ["FABRIC_CAPACITY_NAME"], os.environ["FABRIC_CAPACITY_RG"],
                       os.environ.get("FABRIC_CAPACITY_EXPECT_SKU", "F8"))
    orig_file = out / "capacity_original.json"
    if a.restore_capacity:
        orig = json.loads(orig_file.read_text())
        print(json.dumps(cap.restore(orig["sku"])))
        return 0
    if cap and not orig_file.exists():
        orig_file.write_text(json.dumps({"note": "recorded before the run; expected F2 Active originally",
                                         "sku": "F2", "state": "Active", "seen_at_start": cap.state()}))
    original_sku = json.loads(orig_file.read_text())["sku"] if cap else None

    ids = [p for p in sorted(read_dataset(ROOT / "data", prefix="extra_").observations)
           if int(p[1:]) <= a.last_parcel]
    batches = split_batches(ids, a.batch_size)
    cfg = load_config(DEFAULT_CONFIG_PATH)
    models = a.models.split(",") if a.models else default_models(cfg, a.dry_run)
    ops = StubOps() if a.dry_run else FabricOps()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    done, failed = [], []
    try:
        for n, batch_ids in enumerate(batches, 1):
            if a.only and n not in a.only:
                continue
            spent = [json.loads(f.read_text())["est_cost_usd"] for f in sorted(out.glob("batch_*/stats.json"))]
            total = round(sum(spent), 2)
            (out / "cost.json").write_text(json.dumps({"est_total_usd": total, "budget_usd": a.budget,
                                                       "prices_usd_per_1M_in_out": PRICES}, indent=1))
            if spent and total + max(spent) > a.budget:
                log.warning("budget: spent %.2f + next ~%.2f exceeds %.2f; stopping (resumable)",
                            total, max(spent), a.budget)
                break
            log.info("running est. cost before batch %02d: $%.2f of $%.0f", n, total, a.budget)
            if cap:
                log.info("capacity before batch %02d: %s", n, cap.ensure_ready())
            try:
                if run_batch(n, batch_ids, a, ops, cfg, models, a.dry_run) is not None:
                    done.append(n)
            except Exception as exc:
                log.exception("batch %02d FAILED: %s", n, exc)
                failed.append(n)
    except KeyboardInterrupt:
        log.warning("interrupted")
    finally:
        ops.close()
        if cap and not a.keep_capacity:
            try:
                (out / "capacity_final.json").write_text(json.dumps(cap.restore(original_sku)))
            except Exception as exc:
                log.error("CAPACITY RESTORE FAILED, run --restore-capacity manually: %s", exc)
    log.info("completed batches %s; failed %s", done, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
