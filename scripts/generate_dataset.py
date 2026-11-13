"""Regenerate data/dataset.json and data/ground_truth.json. Usage: generate_dataset.py [seed]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pinned_scoring.synth import generate, generate_extra, write_dataset  # noqa: E402

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--extra"]
    if "--extra" in sys.argv:
        # 200 extra parcels P051-P250 -> data/extra_dataset.json, data/extra_ground_truth.json
        ds = generate_extra(int(args[0])) if args else generate_extra()
        write_dataset(ds, prefix="extra_")
        print(f"wrote data/extra_* for seed {ds.seed} (P051-P250)")
    else:
        seed = int(args[0]) if args else 20260715
        write_dataset(generate(seed))
        print(f"wrote data/ for seed {seed}")
