"""Read-only tool layer over verified receipts, plus a guard around an injected LLM callable.

Nothing here computes or alters stored scores. what_if re-runs the real scorer on modified
copies of observations and labels the result as a counterfactual.
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from .contracts import Observation, Provenance, Quality
from .receipt import Receipt
from .scorer import score
from .verifier import VerificationResult, Violation, verify_narration

_SENTENCE_SPLIT = re.compile(r"\.(?!\d)|[;\n!?]")


class ReceiptStore:
    """In-memory receipt store. Receipts are hash-verified on load; observations are optional."""

    def __init__(self, policy=None, observations=(), as_of: datetime | None = None):
        self._receipts: dict = {}
        self.policy = policy
        self._observations = tuple(observations)
        self.as_of = as_of

    def load(self, wire: dict) -> Receipt:
        receipt = Receipt.from_wire(wire)  # raises ValueError on tampering
        self._receipts[receipt.entity_id] = receipt
        return receipt

    def load_many(self, wires) -> None:
        for wire in wires:
            self.load(wire)

    def get(self, entity_id: str) -> Receipt:
        try:
            return self._receipts[entity_id]
        except KeyError:
            raise KeyError(f"no receipt for entity {entity_id!r}") from None

    def entity_ids(self) -> tuple:
        return tuple(self._receipts)

    def observations_for(self, entity_id: str) -> tuple:
        return tuple(o for o in self._observations if o.entity_id == entity_id)


def get_receipt(store: ReceiptStore, entity_id: str) -> dict:
    return {"counterfactual": False, "receipt": store.get(entity_id).wire()}


def list_reasons(store: ReceiptStore, entity_id: str) -> dict:
    receipt = store.get(entity_id)
    return {"counterfactual": False, "entity_id": entity_id, "status": receipt.status,
            "reasons": list(receipt.reasons)}


def compare(store: ReceiptStore, entity_a: str, entity_b: str) -> dict:
    a, b = store.get(entity_a), store.get(entity_b)
    comparable = a.score is not None and b.score is not None
    by_a = {c[0]: Decimal(c[3]) for c in a.components}
    by_b = {c[0]: Decimal(c[3]) for c in b.components}
    return {
        "counterfactual": False,
        "entities": [entity_a, entity_b],
        "scores": [None if a.score is None else str(a.score),
                   None if b.score is None else str(b.score)],
        "bands": [a.band, b.band],
        "status": [a.status, b.status],
        "score_difference": str(a.score - b.score) if comparable else None,
        "contribution_differences": {
            name: str(by_a[name] - by_b[name]) for name in by_a if name in by_b},
    }


def what_if(store: ReceiptStore, entity_id: str, overrides: dict) -> dict:
    """Re-run the real scorer with overridden observations. Never touches stored receipts.

    overrides: {metric: value | {"value":..., "unit":..., "provenance":..., "quality":...}}
    """
    if store.policy is None or store.as_of is None:
        raise ValueError("what_if needs a store configured with policy, observations and as_of")
    base = store.get(entity_id)
    rows = list(store.observations_for(entity_id))
    metrics = {f.metric: f for f in store.policy.factors}
    for metric, change in overrides.items():
        if metric not in metrics:
            raise ValueError(f"unknown metric {metric!r}")
        spec = change if isinstance(change, dict) else {"value": change}
        existing = [o for o in rows if o.metric == metric and o.observed_at <= store.as_of]
        latest = max(existing, key=lambda o: o.observed_at, default=None)
        rows = [o for o in rows if o.metric != metric]
        if latest is not None:
            changes = {k: spec[k] for k in ("value", "unit", "provenance", "quality") if k in spec}
            rows.append(replace(latest, **changes))
        else:
            observed = store.as_of - timedelta(minutes=1)
            rows.append(Observation(
                entity_id, metric, spec["value"], spec.get("unit", metrics[metric].unit),
                observed, observed + timedelta(days=30),
                spec.get("provenance", Provenance.MEASURED),
                spec.get("quality", Quality.VALID), "what_if"))
    hypothetical = score(entity_id, rows, store.policy, store.as_of)
    return {
        "counterfactual": True,
        "entity_id": entity_id,
        "overrides": {k: (v if isinstance(v, dict) else {"value": v}) for k, v in overrides.items()},
        "stored_score": None if base.score is None else str(base.score),
        "hypothetical": hypothetical.wire(),
    }


@dataclass(frozen=True)
class GuardedAnswer:
    blocked: bool
    text: str | None
    violations: tuple


def _entities_in(text: str, store: ReceiptStore) -> list:
    low = text.lower()
    return [e for e in store.entity_ids()
            if re.search(r"(?<![a-z0-9])" + re.escape(e.lower()) + r"(?![a-z0-9])", low)]


def guarded_answer(llm_callable, question: str, store: ReceiptStore,
                   entity_id: str | None = None) -> GuardedAnswer:
    """Ask the injected LLM, then verify its narration against stored receipts."""
    known_ids = set(store.entity_ids())
    explicit_missing = bool(entity_id and entity_id not in known_ids)
    scope = [entity_id] if (entity_id and not explicit_missing) else _entities_in(question, store)
    context = {e: store.get(e).wire() for e in scope}
    text = llm_callable(question, context)
    if not isinstance(text, str):
        return GuardedAnswer(True, None, (Violation("invalid_output", "LLM returned non-text"),))
    if not scope:
        scope = _entities_in(text, store)
    if not scope:
        return GuardedAnswer(True, None, (Violation(
            "no_receipt", "no stored receipt identified for this answer"),))

    violations: list = []
    if explicit_missing:
        violations.append(Violation("no_receipt", f"no stored receipt for entity {entity_id!r}"))
    current = scope[0]
    for sentence in _SENTENCE_SPLIT.split(text):
        mentioned = _entities_in(sentence, store)
        if mentioned:
            current = mentioned[0]
            targets = mentioned if len(mentioned) > 1 else [current]
        else:
            targets = [current]
        for target in targets:
            result: VerificationResult = verify_narration(sentence, store.get(target))
            violations.extend(result.violations)
    unique = tuple(dict.fromkeys(violations))
    return GuardedAnswer(bool(unique), None if unique else text, unique)
