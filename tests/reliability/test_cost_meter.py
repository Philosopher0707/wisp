"""The cost meter — and the one default that must not exist.

`RunBounds.max_cost_usd` was a ceiling with no meter. This is the meter, and the
tests that matter are not the arithmetic (which is trivial) but the **unknown
model** path: a silent zero there would make the bound unenforceable in exactly the
case nobody tested.

`test_zero_is_not_an_available_policy` is the load-bearing one.
"""
from __future__ import annotations

import pytest

from wisp.runtime.cost import (
    PRICE_TABLE,
    PRICE_TABLE_VERSION,
    CostError,
    CostMeter,
    Price,
    UnknownModel,
    UnknownPolicy,
)


def _meter(policy=UnknownPolicy.FAIL_CLOSED, declared=None):
    return CostMeter(on_unknown=policy, declared=declared)


# ── The default that must not exist ─────────────────────────────────────


class TestTheUnknownPath:
    def test_zero_is_not_an_available_policy(self):
        """**The load-bearing test.** There are two honest answers — refuse, or
        charge a declared price. A silent zero is not one of them: the meter keeps
        reporting a number, the bound never fires, and the protection the config
        declares is absent precisely for a model the table has not seen."""
        assert {p.value for p in UnknownPolicy} == {"fail_closed", "declared"}
        assert "zero" not in " ".join(p.value for p in UnknownPolicy)

    def test_the_policy_is_required_and_has_no_default(self):
        with pytest.raises(TypeError):
            CostMeter()                              # type: ignore[call-arg]

    def test_a_bad_policy_is_rejected(self):
        with pytest.raises(CostError):
            CostMeter(on_unknown="fail_closed")      # type: ignore[arg-type]

    def test_fail_closed_refuses_an_unknown_model(self):
        with pytest.raises(UnknownModel) as excinfo:
            _meter().charge("some-model-shipped-yesterday", input_tokens=1000)
        assert excinfo.value.model == "some-model-shipped-yesterday"
        assert excinfo.value.code == "unknown_model"

    def test_fail_closed_leaves_the_spend_untouched(self):
        """A refused charge must not be half-applied — a meter that moves on a
        refusal reports a number nobody can account for."""
        meter = _meter()
        with pytest.raises(UnknownModel):
            meter.charge("unknown", input_tokens=1000)
        assert meter.spent_usd == 0.0 and meter.charges == []

    def test_declared_charges_the_operators_price(self):
        meter = _meter(UnknownPolicy.DECLARED, declared=Price(1.0, 2.0))
        assert meter.charge("unknown", input_tokens=1000, output_tokens=1000) == 3.0
        assert meter.spent_usd == 3.0

    def test_declared_without_a_price_is_refused(self):
        """A policy that names a behaviour with no value behind it would fail at the
        first unknown model instead of at construction."""
        with pytest.raises(CostError):
            _meter(UnknownPolicy.DECLARED)

    def test_a_known_model_is_unaffected_by_the_policy(self):
        for policy in UnknownPolicy:
            meter = _meter(policy, declared=Price(9.0, 9.0))
            assert meter.charge("local", input_tokens=1000) == 0.0


# ── The arithmetic ──────────────────────────────────────────────────────


class TestTheArithmetic:
    def test_input_and_output_are_priced_separately(self):
        """A single blended rate under-charges exactly the runs that generate the
        most — the ones dominated by output."""
        price = Price(input_per_1k=1.0, output_per_1k=10.0)
        assert price.cost(input_tokens=1000, output_tokens=0) == 1.0
        assert price.cost(input_tokens=0, output_tokens=1000) == 10.0
        assert price.cost(input_tokens=1000, output_tokens=1000) == 11.0

    def test_a_zero_priced_model_costs_nothing_and_that_is_explicit(self):
        """`local` is in the table at 0.0 — a **declared** zero, which is a decision,
        unlike the silent one the unknown path refuses."""
        assert PRICE_TABLE["local"].input_per_1k == 0.0
        assert _meter().charge("local", input_tokens=10_000) == 0.0

    def test_a_negative_price_is_refused(self):
        with pytest.raises(CostError):
            Price(-1.0, 1.0)

    def test_a_non_numeric_price_is_refused(self):
        with pytest.raises(CostError):
            Price("cheap", 1.0)                      # type: ignore[arg-type]

    def test_charges_accumulate(self):
        meter = _meter()
        meter.charge("local", input_tokens=1000)
        meter.charge("local", input_tokens=1000)
        assert len(meter.charges) == 2


# ── The bound it exists to make enforceable ─────────────────────────────


class TestTheBoundCanNowFire:
    def test_exhaustion_fires_at_the_ceiling(self):
        """The point of the whole module: `max_cost_usd` can now be met."""
        meter = CostMeter(on_unknown=UnknownPolicy.DECLARED,
                          declared=Price(1.0, 1.0))
        assert meter.exhausted(1.0) is False
        meter.charge("unknown", input_tokens=1000)      # $1.00
        assert meter.exhausted(1.0) is True, (
            "the cost bound still cannot fire — the meter is not measuring")

    def test_the_boundary_is_inclusive(self):
        meter = _meter()
        meter.spent_usd = 2.0
        assert meter.exhausted(2.0) is True

    def test_a_run_under_its_ceiling_is_not_exhausted(self):
        meter = _meter()
        meter.charge("local", input_tokens=100_000)
        assert meter.exhausted(5.0) is False


# ── The table's identity ────────────────────────────────────────────────


class TestTheTableIsIdentifiable:
    def test_the_summary_carries_the_table_version(self):
        """A cost without the version it was computed under is not comparable with
        any other cost: prices change and two figures look identical."""
        summary = _meter().summary()
        assert summary["price_table_version"] == PRICE_TABLE_VERSION
        assert summary["as_of"]
        assert "spent_usd" in summary

    def test_the_table_has_a_floor(self):
        assert len(PRICE_TABLE) >= 4, "the table is too small to be a table"
        assert PRICE_TABLE_VERSION, "the table has no version"

    def test_every_entry_is_a_price(self):
        for model, price in PRICE_TABLE.items():
            assert isinstance(price, Price), f"{model} is not priced with a Price"


# ── The flaw that WIRING found ──────────────────────────────────────────


class TestAnUnknownModelMustNotBreakTheTurn:
    """Found by wiring, not by reading: `charge` raises under FAIL_CLOSED, and
    `Telemetry.record_turn` calls it — so a model the table had not seen would have
    crashed the turn. Refusing to CHARGE is not refusing to RUN.
    """

    def test_try_charge_counts_instead_of_raising(self):
        meter = _meter()
        assert meter.try_charge("shipped-yesterday", input_tokens=1000) is None
        assert meter.uncharged_calls == 1
        assert meter.spent_usd == 0.0

    def test_charge_still_raises_for_an_explicit_caller(self):
        """The explicit path keeps its failure — a caller that can handle it should
        see it. Only the telemetry path swallows."""
        with pytest.raises(UnknownModel):
            _meter().charge("shipped-yesterday", input_tokens=1000)

    def test_the_gap_is_VISIBLE_in_the_summary(self):
        """The whole point: not silent. A spend figure is only trustworthy if the
        calls it could not price are beside it."""
        meter = _meter()
        meter.try_charge("shipped-yesterday", input_tokens=1000)
        meter.try_charge("shipped-yesterday", input_tokens=1000)
        meter.try_charge("another-new-one", input_tokens=1000)
        s = meter.summary()
        assert s["uncharged_calls"] == 3
        assert s["uncharged_models"] == ["shipped-yesterday", "another-new-one"]

    def test_a_known_model_is_unaffected(self):
        meter = _meter()
        assert meter.try_charge("local", input_tokens=1000) == 0.0
        assert meter.uncharged_calls == 0

    def test_the_bound_still_holds_for_what_could_be_priced(self):
        """The gap must not disable the control for the models it CAN price."""
        meter = CostMeter(on_unknown=UnknownPolicy.FAIL_CLOSED)
        meter.try_charge("unknown-model", input_tokens=999_999)     # counted
        assert meter.spent_usd == 0.0
        meter.try_charge("gpt-4o", input_tokens=1000, output_tokens=1000)
        assert meter.spent_usd > 0.0

    def test_record_turn_does_not_raise_on_an_unknown_model(self):
        """**The flaw, driven through the seam that found it.**"""
        from wisp.infra.telemetry import Telemetry

        meter = _meter()
        tel = Telemetry(cost_meter=meter)
        tel.record_turn(1.0, 1000, 1000, model="shipped-yesterday")   # must not raise
        assert meter.uncharged_calls == 1
        assert tel.prompt_tokens_total == 1000, "the counters still counted"
