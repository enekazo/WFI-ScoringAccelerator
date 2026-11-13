"""Run the overnight Fabric batches for gpt-5.5 only (assumed list price 5/25 USD per 1M tokens)."""
import sys

import run_extra_batches as r

r.PRICES["gpt-5.5"] = (5.0, 25.0)

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "--out", r"results\extra_gpt55", "--models", "gpt-5.5",
                "--repeats", "3", "--workers", "4", "--budget", "60"] + sys.argv[1:]
    sys.exit(r.main())
