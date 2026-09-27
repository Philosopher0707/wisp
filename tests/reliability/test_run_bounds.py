"""The four mandatory bounds — no defaults, and a missing one fails the load.

The invariant this guards, stated once:

    max_steps · max_tokens_total · max_wall_clock_s · max_cost_usd

**All four required. None has a default.** A config missing any fails to load.
This eliminates the *"runaway agent burned $4,000 overnight"* class rather than
mitigating it — the configuration that would cause it cannot be constructed.

The load-bearing tests are the *missing* ones. A suite that only checks a valid
config parses would pass against an implementation that quietly defaults all four,
which is precisely the behaviour the invariant forbids.
"""
from __future__ import annotations

import pathlib

import pytest

from wisp.runtime.bounds import (
    BOUND_NAMES,
    CONSUMPTION,
    COUNTABLE,
    BoundsError,
    RunBounds,
    field_names,
    from_mapping,
    load,
)

VALID = {"max_steps": 40, "max_tokens_total": 400_000,
         "max_wall_clock_s": 300.0, "max_cost_usd": 2.0}

REPO = pathlib.Path(__file__).resolve().parents[2]


# ── The invariant ───────────────────────────────────────────────────────


class TestNoDefaults:
    def test_the_dataclass_has_no_defaults_at_all(self):
        """The strongest form of the invariant, checked structurally.

        A dataclass with defaults *is* a set of defaults, and the first
        `RunBounds()` that compiles becomes the policy for every run that forgot
        to declare one. So: no field may have a default.
        """
        import dataclasses

        with_defaults = [
            f.name for f in dataclasses.fields(RunBounds)
            if f.default is not dataclasses.MISSING
            or f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        ]
        assert not with_defaults, (
            f"field(s) {with_defaults} have defaults — a default bound is a policy "
            "nobody chose, and it is the one that will be used")

    def test_constructing_without_arguments_is_impossible(self):
        with pytest.raises(TypeError):
            RunBounds()  # type: ignore[call-arg]

    def test_the_declared_fields_are_exactly_the_four(self):
        assert field_names() == BOUND_NAMES, (
            "the dataclass and BOUND_NAMES disagree — a field added to one and not "
            "the other would be a ceiling this module does not enforce")

    @pytest.mark.parametrize("dropped", BOUND_NAMES)
    def test_a_config_missing_any_one_of_the_four_fails_to_load(self, dropped):
        """Parametrized over all four, so a fix for one cannot leave another
        silently optional."""
        partial = {k: v for k, v in VALID.items() if k != dropped}
        with pytest.raises(BoundsError) as excinfo:
            from_mapping(partial)
        assert dropped in str(excinfo.value), "the failure does not name the bound"
        assert dropped in excinfo.value.missing

    def test_a_config_missing_all_four_says_so(self):
        with pytest.raises(BoundsError) as excinfo:
            from_mapping({})
        assert set(excinfo.value.missing) == set(BOUND_NAMES)

    def test_an_unknown_bound_is_rejected(self):
        """A name this module does not enforce is a ceiling that does not hold —
        worse than a typo, because it looks like protection."""
        with pytest.raises(BoundsError):
            from_mapping({**VALID, "max_iterations": 10})


# ── The ranges, and why they differ ─────────────────────────────────────


class TestTheRanges:
    def test_the_two_sets_partition_the_four(self):
        assert COUNTABLE | CONSUMPTION == set(BOUND_NAMES)
        assert not (COUNTABLE & CONSUMPTION)

    @pytest.mark.parametrize("name", sorted(COUNTABLE))
    def test_a_countable_bound_may_be_zero(self, name):
        """Zero is a legitimate "start nothing" — and it must terminate, not hang.
        Rejecting it would make a legal configuration unrepresentable."""
        bounds = from_mapping({**VALID, name: 0})
        assert getattr(bounds, name) == 0

    @pytest.mark.parametrize("name", sorted(COUNTABLE))
    def test_a_countable_bound_may_not_be_negative(self, name):
        with pytest.raises(BoundsError):
            from_mapping({**VALID, name: -1})

    @pytest.mark.parametrize("name", sorted(CONSUMPTION))
    def test_a_consumption_bound_may_not_be_zero(self, name):
        """Zero here is not a small budget, it is a run that can never do
        anything — a configuration error, found at load rather than at the first
        tick."""
        with pytest.raises(BoundsError):
            from_mapping({**VALID, name: 0})

    def test_a_non_numeric_bound_is_rejected(self):
        with pytest.raises(BoundsError):
            from_mapping({**VALID, "max_steps": "forty"})

    def test_a_bool_is_not_a_number_here(self):
        """`True` is an `int` in Python, and `max_steps=True` would read as a
        ceiling of one step. Rejected explicitly."""
        with pytest.raises(BoundsError):
            from_mapping({**VALID, "max_steps": True})


# ── Exhaustion ──────────────────────────────────────────────────────────


class TestExhaustion:
    def test_a_run_inside_its_bounds_may_continue(self):
        b = RunBounds(**VALID)
        assert b.exhausted(steps=1, tokens=10, elapsed_s=1.0, cost_usd=0.01) == ()

    def test_every_bound_can_be_the_one_that_trips(self):
        b = RunBounds(**VALID)
        cases = {
            "max_steps": dict(steps=40, tokens=0, elapsed_s=0.0, cost_usd=0.0),
            "max_tokens_total": dict(steps=0, tokens=400_000, elapsed_s=0.0, cost_usd=0.0),
            "max_wall_clock_s": dict(steps=0, tokens=0, elapsed_s=300.0, cost_usd=0.0),
            "max_cost_usd": dict(steps=0, tokens=0, elapsed_s=0.0, cost_usd=2.0),
        }
        for name, usage in cases.items():
            assert name in b.exhausted(**usage), f"{name} did not trip"

    def test_it_reports_every_bound_that_is_out_not_just_the_first(self):
        """A run can be out of budget in more than one way at once. Collapsing
        that to one reason loses the one the reader needs, so the caller decides
        precedence and this refuses to."""
        b = RunBounds(**VALID)
        out = b.exhausted(steps=999, tokens=999_999, elapsed_s=9999.0, cost_usd=99.0)
        assert set(out) == set(BOUND_NAMES)

    def test_the_boundary_is_inclusive(self):
        """`>=`, not `>`: at exactly the ceiling the run has spent it."""
        b = RunBounds(**VALID)
        assert "max_steps" in b.exhausted(steps=40, tokens=0, elapsed_s=0.0, cost_usd=0.0)


# ── The shipped config ──────────────────────────────────────────────────


class TestTheShippedConfig:
    def test_the_default_config_loads(self):
        bounds = load(REPO / "wisp" / "configs" / "default.yaml")
        assert bounds.max_steps > 0 and bounds.max_cost_usd > 0

    def test_a_config_with_no_bounds_key_is_missing_all_four(self):
        """It does not inherit them — the whole point."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "no_bounds.yaml"
            p.write_text("name: x\n", encoding="utf-8")
            with pytest.raises(BoundsError) as excinfo:
                load(p)
            assert set(excinfo.value.missing) == set(BOUND_NAMES)

    def test_a_missing_file_names_itself(self):
        with pytest.raises(BoundsError):
            load(REPO / "wisp" / "configs" / "does-not-exist.yaml")

    def test_a_malformed_yaml_is_a_bounds_error_not_a_crash(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "bad.yaml"
            p.write_text("bounds: [unclosed\n", encoding="utf-8")
            with pytest.raises(BoundsError):
                load(p)


# ── The floor ───────────────────────────────────────────────────────────


class TestTheFloor:
    def test_the_bound_set_is_not_empty(self):
        assert len(BOUND_NAMES) == 4, "the invariant is 'four', not 'some'"

    def test_the_failure_names_its_source(self):
        with pytest.raises(BoundsError) as excinfo:
            from_mapping({}, source="configs/team.yaml")
        assert "configs/team.yaml" in str(excinfo.value), (
            "the failure does not name the file — an operator cannot fix a config "
            "they cannot identify")
