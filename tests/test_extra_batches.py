import json

import run_extra_batches as reb
import merge_extra_results as mer


def test_cost_estimate_uses_assumed_prices():
    assert reb.cost_usd({"gpt-4.1": [1_000_000, 1_000_000]}) == 10.0
    assert reb.cost_usd({"gpt-6.1-terra@high": [1_000_000, 0]}) == 2.0


def test_batches_cover_p051_p150_in_four():
    ids = [f"P{n:03d}" for n in range(51, 151)]
    assert [len(b) for b in reb.split_batches(ids, 25)] == [25] * 4


def test_batch_stats_separates_blocks_and_429():
    res = {"runs": [{"model": "m", "arm": "B", "blocked": True, "error": None},
                    {"model": "m", "arm": "B", "blocked": False, "error": "HTTP 429"}],
           "throttled_429": {"m": 3}, "tokens": {"m": [10, 10]}}
    s = reb.batch_stats(res)
    cell = s["per_model_arm"]["m"]["B"]
    assert cell["verifier_blocks"] == 1 and cell["errors_429"] == 1
    assert s["model_429_retries"] == {"m": 3}


def test_merge_empty_dir(tmp_path):
    assert "Completed batches: 0" in mer.build(tmp_path)
    (tmp_path / "x").mkdir()
    json.dumps({})


def test_delta_loader_offline_counts():
    from pinned_scoring.fabric import delta_loader as dl
    from pathlib import Path
    ds = Path('data/extra_dataset.json')
    if not ds.exists():
        return
    t = dl.build_medallion(str(ds), 'examples/wildfire/policy.json')
    a = dl.medallion_arrow(t)
    assert a['gold_receipts'].num_rows == len({r['entity_id'] for r in t['gold_receipts']})
    assert a['raw_observations'].num_rows == a['silver_observations'].num_rows

