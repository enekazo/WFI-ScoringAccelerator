"""Seeded synthetic wildfire dataset with ground truth computed by the real scorer."""

import json
import random
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from ..contracts import Observation, Provenance
from ..policy import Policy
from ..scorer import score
from .faults import FAULTS, REASON_PREFIX, TARGET_METRICS, inject, tamper_policy

AS_OF = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
PARCEL_COUNT = 50
FAULTS_PER_TYPE = 5
DEFAULT_POLICY = Path(__file__).resolve().parents[3] / "examples" / "wildfire" / "policy.json"
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# metric -> (low, high) of the generated value range, in tenths of a unit
_RANGES = {
    "fuel_load": (20, 400),
    "dead_fuel_pct": (50, 950),
    "fuel_moisture_pct": (60, 380),
    "weather_danger": (50, 800),
    "wind_kmh": (0, 550),
}


@dataclass(frozen=True)
class Dataset:
    seed: int
    as_of: datetime
    policy_sha256: str
    observations: dict  # parcel id -> list[Observation] (as injected)
    ground_truth: dict  # parcel id -> record dict


def _clean_observations(rng: random.Random, parcel: str, policy: Policy, as_of) -> list:
    rows = []
    for factor in policy.factors:
        lo, hi = _RANGES[factor.metric]
        value = Decimal(rng.randint(lo, hi)) / 10
        age_cap = min(factor.max_age_hours - 1, 300)
        observed_at = as_of - timedelta(hours=rng.randint(1, age_cap))
        provenance = Provenance.MEASURED
        if Provenance.GENERATED in factor.allowed_provenance and rng.random() < 0.5:
            provenance = Provenance.GENERATED
        rows.append(Observation(
            entity_id=parcel, metric=factor.metric, value=value, unit=factor.unit,
            observed_at=observed_at,
            valid_until=as_of + timedelta(hours=rng.randint(2, 48)),
            provenance=provenance, source=f"{provenance.value}:{parcel}",
        ))
    return rows


def generate(seed: int, policy: Policy | None = None, start: int = 1,
             count: int = PARCEL_COUNT, faults_per_type: int = FAULTS_PER_TYPE) -> Dataset:
    """Defaults reproduce the original P001-P050 dataset byte for byte."""
    policy = policy or Policy.load(DEFAULT_POLICY)
    rng = random.Random(seed)
    ids = [f"P{n:03d}" for n in range(start, start + count)]
    plan = {pid: None for pid in ids}
    shuffled = ids[:]
    rng.shuffle(shuffled)
    for index, fault in enumerate(f for f in FAULTS for _ in range(faults_per_type)):
        plan[shuffled[index]] = fault

    observations, truth = {}, {}
    for pid in ids:
        clean = _clean_observations(rng, pid, policy, AS_OF)
        fault = plan[pid]
        metric = rng.choice(TARGET_METRICS) if fault and fault != "policy_tamper" else None
        faulted = inject(fault, clean, metric, AS_OF) if fault else clean
        clean_receipt = score(pid, clean, policy, AS_OF)
        if fault is None:
            status, reasons = "scored", []
        elif fault == "policy_tamper":
            status, reasons = "policy_mismatch", []
        else:
            status, reasons = "incomplete", [f"{REASON_PREFIX[fault]}:{metric}"]
        observations[pid] = faulted
        truth[pid] = {
            "parcel_id": pid,
            "fault": fault,
            "injected_metric": metric,
            "expected_status": status,
            "expected_reasons": reasons,
            "clean_status": clean_receipt.status,
            "expected_score": str(clean_receipt.score),
            "expected_band": clean_receipt.band,
            "tampered_policy_sha256": tamper_policy(policy).sha256 if fault == "policy_tamper" else None,
        }
    return Dataset(seed, AS_OF, policy.sha256, observations, truth)


EXTRA_SEED = 20261109
EXTRA_START = 51
EXTRA_COUNT = 200
EXTRA_FAULTS_PER_TYPE = 20  # same 20% fault share per type as the original 5 of 50
EXTRA_DATA_DIR = DEFAULT_DATA_DIR  # files are extra_dataset.json / extra_ground_truth.json


def generate_extra(seed: int = EXTRA_SEED, policy: Policy | None = None) -> Dataset:
    """200 extra parcels P051-P250 with a new seed; P001-P050 are untouched."""
    return generate(seed, policy, start=EXTRA_START, count=EXTRA_COUNT,
                    faults_per_type=EXTRA_FAULTS_PER_TYPE)


def validate_parcel_count(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("parcelCount must be a positive integer.")
    return value


def generate_deployment(count: int = 150) -> Dataset:
    """Extend the recorded dataset without changing the existing parcel observations."""
    validate_parcel_count(count)
    base = generate(20260715)
    observations = dict(list(base.observations.items())[:count])
    ground_truth = dict(list(base.ground_truth.items())[:count])
    for start in range(EXTRA_START, count + 1, EXTRA_COUNT):
        extra = generate(
            EXTRA_SEED + (start - EXTRA_START) // EXTRA_COUNT,
            start=start, count=EXTRA_COUNT, faults_per_type=EXTRA_FAULTS_PER_TYPE,
        )
        take = min(EXTRA_COUNT, count - start + 1)
        observations.update(list(extra.observations.items())[:take])
        ground_truth.update(list(extra.ground_truth.items())[:take])
    return replace(base, observations=observations, ground_truth=ground_truth)


def _dump(document) -> str:
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def dataset_document(ds: Dataset) -> dict:
    return {
        "seed": ds.seed,
        "as_of": ds.as_of.isoformat(),
        "policy_sha256": ds.policy_sha256,
        "parcels": {pid: [o.wire() for o in rows] for pid, rows in ds.observations.items()},
    }


def write_dataset(ds: Dataset, directory=DEFAULT_DATA_DIR, prefix: str = "") -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, doc in ((f"{prefix}dataset.json", dataset_document(ds)),
                      (f"{prefix}ground_truth.json", {"seed": ds.seed, "parcels": ds.ground_truth})):
        with open(directory / name, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_dump(doc))


def read_dataset(directory=DEFAULT_DATA_DIR, prefix: str = "") -> Dataset:
    directory = Path(directory)
    doc = json.loads((directory / f"{prefix}dataset.json").read_text(encoding="utf-8"))
    truth = json.loads((directory / f"{prefix}ground_truth.json").read_text(encoding="utf-8"))
    observations = {
        pid: [Observation(
            entity_id=w["entity_id"], metric=w["metric"], value=Decimal(w["value"]),
            unit=w["unit"], observed_at=datetime.fromisoformat(w["observed_at"]),
            valid_until=datetime.fromisoformat(w["valid_until"]),
            provenance=w["provenance"], quality=w["quality"], source=w["source"],
        ) for w in rows]
        for pid, rows in doc["parcels"].items()
    }
    return Dataset(doc["seed"], datetime.fromisoformat(doc["as_of"]),
                   doc["policy_sha256"], observations, truth["parcels"])


def weight_sensitivity(policy: Policy, pct=20) -> list:
    """Return (label, Policy) pairs, each factor's weight moved by +/-pct then renormalised."""
    quantum = Decimal("0.000001")
    factor_pct = Decimal(str(pct)) / 100
    results = []
    for index, factor in enumerate(policy.factors):
        for sign, tag in ((1, "up"), (-1, "down")):
            raw = [f.weight for f in policy.factors]
            raw[index] = factor.weight * (1 + sign * factor_pct)
            total = sum(raw)
            weights = [(w / total).quantize(quantum, rounding=ROUND_HALF_UP) for w in raw]
            big = max(range(len(weights)), key=lambda i: weights[i])
            weights[big] += Decimal(1) - sum(weights)
            factors = tuple(replace(f, weight=w) for f, w in zip(policy.factors, weights))
            variant = replace(policy, name=f"{policy.name}+{factor.name}-{tag}-{pct}pct",
                              factors=factors)
            variant.validate()
            results.append((f"{factor.name}:{tag}", variant))
    return results
