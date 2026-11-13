"""Cheap, testable run controls: cost meter, budget guard, kill switch.

Prices are a documented ASSUMPTION (USD per 1M tokens, standard list price for the deployed
model); change PRICES_USD_PER_M if your contract differs. Figures are estimates, not billing.
"""

import os
from dataclasses import dataclass, field

PRICES_USD_PER_M = {
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-5.5": (5.00, 25.00),
    "gpt-6.1-sol": (5.00, 25.00),
    "gpt-6.1-terra": (2.00, 10.00),
    "grok-4.7": (3.00, 15.00),
}
KILL_SWITCH_ENV = "PINNED_KILL_SWITCH"


class BudgetExceeded(RuntimeError):
    """Raised before a call when the estimated spend has reached the cap."""


class KillSwitchEngaged(RuntimeError):
    """Raised when the operator has disabled the agent's tools."""


def kill_switch_engaged() -> bool:
    return os.environ.get(KILL_SWITCH_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class CostMeter:
    model: str = "gpt-4.1"
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_label: dict = field(default_factory=dict)

    def add(self, usage, label: str = "run") -> None:
        """Record a usage object/dict with input_tokens/output_tokens (Responses API) or
        prompt_tokens/completion_tokens (chat API). None is ignored."""
        if usage is None:
            return
        get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
        i = get("input_tokens") or get("prompt_tokens") or 0
        o = get("output_tokens") or get("completion_tokens") or 0
        self.calls += 1
        self.input_tokens += i
        self.output_tokens += o
        slot = self.by_label.setdefault(label, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        slot["calls"] += 1
        slot["input_tokens"] += i
        slot["output_tokens"] += o

    @property
    def usd(self) -> float:
        p_in, p_out = PRICES_USD_PER_M[self.model]
        return (self.input_tokens * p_in + self.output_tokens * p_out) / 1_000_000

    def summary(self) -> dict:
        return {"model": self.model, "calls": self.calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "usd_estimate": round(self.usd, 4),
                "prices_usd_per_m_tokens": {"input": PRICES_USD_PER_M[self.model][0],
                                            "output": PRICES_USD_PER_M[self.model][1]}}


@dataclass
class BudgetGuard:
    """Aborts (raises BudgetExceeded) once estimated spend reaches cap_usd."""
    meter: CostMeter
    cap_usd: float

    def check(self) -> None:
        if self.meter.usd >= self.cap_usd:
            raise BudgetExceeded(
                f"estimated spend ${self.meter.usd:.4f} reached cap ${self.cap_usd:.2f}; aborting")
