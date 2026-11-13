import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path

from pinned_scoring import Policy, score
from pinned_scoring.synth import (FAULTS, generate, read_dataset, tamper_policy,
                                  weight_sensitivity, write_dataset)
from pinned_scoring.synth.generator import DEFAULT_POLICY

SEED = 20260715
POLICY = Policy.load(DEFAULT_POLICY)
DATA = Path(__file__).resolve().parents[1] / "data"


def _bytes(ds, name):
    with tempfile.TemporaryDirectory() as d:
        write_dataset(ds, d)
        return (Path(d) / name).read_bytes()


def test_deterministic_bytes():
    for name in ("dataset.json", "ground_truth.json"):
        assert _bytes(generate(SEED), name) == _bytes(generate(SEED), name)
    assert _bytes(generate(SEED), "dataset.json") != _bytes(generate(SEED + 1), "dataset.json")


def test_roundtrip_and_committed_data():
    ds = generate(SEED)
    with tempfile.TemporaryDirectory() as d:
        write_dataset(ds, d)
        back = read_dataset(d)
    assert back.observations == ds.observations and back.ground_truth == ds.ground_truth
    for name in ("dataset.json", "ground_truth.json"):
        assert (DATA / name).read_bytes() == _bytes(ds, name)


def test_counts():
    ds = generate(SEED)
    assert sorted(ds.ground_truth) == [f"P{n:03d}" for n in range(1, 51)]
    counts = Counter(r["fault"] for r in ds.ground_truth.values())
    assert counts[None] == 20
    assert all(counts[f] == 5 for f in FAULTS)


def test_clean_parcels_score():
    ds = generate(SEED)
    for pid, truth in ds.ground_truth.items():
        assert truth["clean_status"] == "scored"
        if truth["fault"] is None:
            r = score(pid, ds.observations[pid], POLICY, ds.as_of)
            assert r.status == "scored"
            assert str(r.score) == truth["expected_score"] and r.band == truth["expected_band"]


def test_each_fault_yields_expected_reason():
    ds = generate(SEED)
    for pid, truth in ds.ground_truth.items():
        fault = truth["fault"]
        r = score(pid, ds.observations[pid], POLICY, ds.as_of)
        if fault in (None, "policy_tamper"):
            assert r.status == "scored"
        else:
            assert r.status == "incomplete" and r.score is None
            assert list(r.reasons) == truth["expected_reasons"]
            assert truth["injected_metric"] in truth["expected_reasons"][0]
        if fault == "policy_tamper":
            tampered = tamper_policy(POLICY)
            assert tampered.sha256 == truth["tampered_policy_sha256"] != POLICY.sha256
            t = score(pid, ds.observations[pid], tampered, ds.as_of)
            assert t.policy_sha256 != r.policy_sha256


def test_weight_sensitivity_valid():
    variants = weight_sensitivity(POLICY)
    assert len(variants) == 2 * len(POLICY.factors)
    for _, p in variants:
        assert sum(f.weight for f in p.factors) == Decimal(1)
        assert all(f.weight > 0 and isinstance(f.weight, Decimal) for f in p.factors)
        p.validate()
        assert p.sha256 != POLICY.sha256
    assert weight_sensitivity(POLICY, 10)


def test_extra_parcels_separate_and_original_unchanged():
    from pinned_scoring.synth import generate_extra
    ds = generate_extra()
    ids = sorted(ds.ground_truth)
    assert len(ids) == 200 and ids[0] == "P051" and ids[-1] == "P250"
    assert not set(ids) & set(generate(20260715).ground_truth)
    assert _bytes(generate_extra(), "dataset.json") == _bytes(ds, "dataset.json")
    committed = read_dataset(prefix="extra_")
    assert sorted(committed.ground_truth) == ids
    assert len(read_dataset().ground_truth) == 50
