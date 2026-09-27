"""What a run has spent, and the table that says what a token costs.

## Why this exists

`RunBounds.max_cost_usd` was a **ceiling with no meter**: the bound was required and
enforced *as a number*, and nothing computed spend, so it could never fire. A bound
that cannot fire is worse than no bound, because it reads as protection. This is the
meter.

## The decision that matters is not the arithmetic

Cost is `tokens × price`, and that is trivial. What is not trivial is **what happens
when the model is not in the table**, and there are only two honest answers:

* **fail closed** — refuse, because a run whose cost cannot be computed cannot be
  bounded;
* **charge an explicitly declared price** — the operator states what an unknown
  model costs.

**What is not on the list is `0`.** A silent zero is the dangerous default: the
meter keeps reporting a number, the bound never fires, and the protection that the
config declares is absent in exactly the case nobody tested — a new model name. So
`on_unknown` is a **required argument with no default**, the same shape as
`OutagePolicy` in `wisp/runtime/idempotency.py` and for the same reason.

## The table goes stale, and says so

Prices change, and a table is a **snapshot with a date**. `PRICE_TABLE_AS_OF` is
part of the table's identity, and every cost the meter reports carries the version it
was computed under — so a cost recorded under an old table is identifiable as one,
rather than being silently compared against a cost from today.

The numbers below are **placeholders**, not a price list: they exist so the meter is
testable and so the shape of the data is visible. A deployment replaces them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

#: The table's identity. Bump it when a price changes — a cost without the version
#: it was computed under is not comparable with any other cost.
PRICE_TABLE_VERSION = "2026-09-27"
PRICE_TABLE_AS_OF = "2026-09-27"


class CostError(RuntimeError):
    """Base for this module's failures. `code` is what a caller branches on."""

    code = "cost_error"


class UnknownModel(CostError):
    """The model is not in the table and no policy covers it."""

    code = "unknown_model"

    def __init__(self, model: str, *, policy: "UnknownPolicy") -> None:
        super().__init__(
            f"no price for model {model!r} and the policy is {policy.value!r}. "
            "A cost that cannot be computed cannot be bounded, and silently "
            "charging zero would make `max_cost_usd` unenforceable in exactly the "
            "case nobody tested — a new model name."
        )
        self.model = model


class UnknownPolicy(StrEnum):
    """What to do about a model that is not in the table. **No default.**"""

    #: Refuse the charge. A run whose cost is unknown cannot be bounded.
    FAIL_CLOSED = "fail_closed"
    #: Charge a price the caller declares. The operator's answer, made explicit.
    DECLARED = "declared"


@dataclass(frozen=True)
class Price:
    """US dollars per 1,000 tokens, split by direction.

    Two numbers rather than one because output is typically several times input, and
    a single blended rate under-charges exactly the runs that generate the most.
    """

    input_per_1k: float
    output_per_1k: float

    def __post_init__(self) -> None:
        for name in ("input_per_1k", "output_per_1k"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CostError(f"{name} must be a number, got {value!r}")
            if value < 0:
                raise CostError(
                    f"{name} must be >= 0, got {value} — a negative price would make "
                    "spend decrease as the run does more work")

    def cost(self, *, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens / 1000.0) * self.input_per_1k + \
               (output_tokens / 1000.0) * self.output_per_1k


#: A **placeholder** table. Replace it in deployment; the version above is what
#: makes a recorded cost identifiable rather than silently incomparable.
PRICE_TABLE: Mapping[str, Price] = {
    "gpt-4o": Price(2.50, 10.00),
    "gpt-4o-mini": Price(0.15, 0.60),
    "claude-sonnet": Price(3.00, 15.00),
    "claude-haiku": Price(0.25, 1.25),
    "local": Price(0.0, 0.0),
}


@dataclass
class CostMeter:
    """Accumulates spend, and answers "has the cost bound been met?".

    Holds the **version** it is charging under, so a spend figure is always
    attributable to a table.
    """

    on_unknown: UnknownPolicy
    declared: Price | None = None
    table: Mapping[str, Price] = field(default_factory=lambda: PRICE_TABLE)
    version: str = PRICE_TABLE_VERSION
    spent_usd: float = 0.0
    charges: list[tuple[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.on_unknown, UnknownPolicy):
            raise CostError(
                f"on_unknown must be an UnknownPolicy, got {self.on_unknown!r}. "
                "There is no default: charging zero silently makes `max_cost_usd` "
                "unenforceable for any model the table has not seen."
            )
        if self.on_unknown is UnknownPolicy.DECLARED and self.declared is None:
            raise CostError(
                "on_unknown=DECLARED requires a `declared` Price — otherwise the "
                "policy names a behaviour with no value behind it")

    def price_for(self, model: str) -> Price:
        price = self.table.get(model)
        if price is not None:
            return price
        if self.on_unknown is UnknownPolicy.DECLARED:
            return self.declared                      # type: ignore[return-value]
        raise UnknownModel(model, policy=self.on_unknown)

    def charge(self, model: str, *, input_tokens: int = 0,
               output_tokens: int = 0) -> float:
        """Add one call's cost and return it."""
        amount = self.price_for(model).cost(input_tokens=input_tokens,
                                            output_tokens=output_tokens)
        self.spent_usd += amount
        self.charges.append((model, amount))
        return amount

    def exhausted(self, max_cost_usd: float) -> bool:
        """Whether the declared ceiling has been met. The meter's whole purpose.

        `>=`, not `>`: at exactly the ceiling the run has spent it.
        """
        return self.spent_usd >= max_cost_usd

    def summary(self) -> dict[str, object]:
        """The spend, **with the table version it was computed under**.

        A cost without its version is not comparable with any other cost — prices
        change, and two figures from different tables look identical.
        """
        return {"spent_usd": round(self.spent_usd, 6),
                "price_table_version": self.version,
                "as_of": PRICE_TABLE_AS_OF,
                "calls": len(self.charges)}


__all__ = ["PRICE_TABLE", "PRICE_TABLE_VERSION", "PRICE_TABLE_AS_OF", "Price",
           "CostMeter", "CostError", "UnknownModel", "UnknownPolicy"]
