"""Run the pinned-scoring experiment. See docs/experiment.md."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pinned_scoring.experiment.config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from pinned_scoring.experiment.runner import default_models, run_experiment  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="deterministic stub models, no network")
    ap.add_argument("--models", help="comma-separated deployment names (default: config strong,small)")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--parcels", help="comma-separated parcel ids (default: all)")
    ap.add_argument("--out", help="output dir (default results/, or results/dry_run with --dry-run)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    ap.add_argument("--no-sensitivity", action="store_true")
    ap.add_argument("--sensitivity-parcels", type=int, default=10)
    a = ap.parse_args(argv)

    config = load_config(a.config)
    models = a.models.split(",") if a.models else default_models(config, a.dry_run)
    out = a.out or str(ROOT / "results" / ("dry_run" if a.dry_run else ""))
    result = run_experiment(
        out, models, config, a.dry_run, repeats=a.repeats,
        parcels=a.parcels.split(",") if a.parcels else None, workers=a.workers,
        sensitivity=not a.no_sensitivity, sensitivity_parcels=a.sensitivity_parcels)
    print(f"wrote {out}/summary.md ({len(result['runs'])} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
