"""Deterministic scorer. Same observations + same policy + same clock = same receipt."""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext

from .contracts import Observation, Quality, sha256_json, utc
from .policy import Policy
from .receipt import Receipt

HUNDRED = Decimal(100)


def _normalise(value: Decimal, factor) -> Decimal:
    span = factor.high - factor.low
    ratio = min(Decimal(1), max(Decimal(0), (value - factor.low) / span))
    return (Decimal(1) - ratio if factor.invert else ratio) * HUNDRED


def score(entity_id: str, observations, policy: Policy, as_of: datetime) -> Receipt:
    clock = utc(as_of)
    rows = [o for o in observations if o.entity_id == entity_id]
    input_hash = sha256_json(
        sorted((o.wire() for o in rows), key=lambda w: (w["metric"], w["observed_at"]))
    )

    reasons, components, total = [], [], Decimal(0)
    with localcontext(Context(prec=50, rounding=ROUND_HALF_UP)):
        for factor in policy.factors:
            candidates = [o for o in rows if o.metric == factor.metric and o.observed_at <= clock]
            if not candidates:
                reasons.append(f"missing:{factor.metric}")
                continue
            latest = max(candidates, key=lambda o: o.observed_at)
            if latest.quality != Quality.VALID:
                reasons.append(f"invalid:{factor.metric}")
            elif latest.unit != factor.unit:
                reasons.append(f"unit:{factor.metric}")
            elif latest.provenance not in factor.allowed_provenance:
                reasons.append(f"provenance:{factor.metric}")
            elif (latest.valid_until <= clock
                  or clock - latest.observed_at > timedelta(hours=factor.max_age_hours)):
                reasons.append(f"expired:{factor.metric}")
            else:
                contribution = factor.weight * _normalise(latest.value, factor)
                total += contribution
                components.append((
                    factor.name, str(latest.value), latest.provenance.value,
                    str(contribution.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                ))

        if reasons:
            status, final, band = "incomplete", None, None
        else:
            status = "scored"
            final = total.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            band = policy.band(final)

    return Receipt(
        entity_id=entity_id, status=status, score=final, band=band,
        components=tuple(components), reasons=tuple(reasons),
        as_of=clock.isoformat(), policy_sha256=policy.sha256, input_sha256=input_hash,
    )
