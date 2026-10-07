"""RC1, RC8: the ledger is deterministic, and OBSERVED facts come only from engine events."""

from __future__ import annotations

import pytest

from tests.reasoning.conftest import FAIL, ev
from wisp.core.reasoning.ledger import Fact, FactKind, Ledger, ObservedEvent, Status


class TestRC8Provenance:
    def test_an_observed_fact_cannot_be_constructed_directly(self):
        with pytest.raises(ValueError, match="only be created by Ledger.observe"):
            Fact(id="F1", kind=FactKind.TOOL_RESULT, status=Status.OBSERVED, source="c1", step=1, mutation_index=0, subject="x")

    def test_a_forged_mint_token_is_not_accepted(self):
        with pytest.raises(ValueError):
            Fact(id="F1", kind=FactKind.TOOL_RESULT, status=Status.OBSERVED, source="c1", step=1, mutation_index=0, subject="x", _mint=object())

    def test_an_observed_event_needs_a_source_id(self):
        with pytest.raises(ValueError, match="source id"):
            ObservedEvent("tool_result", "run_bash", "")

    def test_unknown_event_types_are_refused(self):
        with pytest.raises(ValueError):
            ObservedEvent("assistant_text", "x", "s1")

    def test_observe_only_takes_events_so_model_text_has_no_route_to_observed(self):
        led = Ledger()
        with pytest.raises(AttributeError):
            led.observe("All tests pass")  # type: ignore[arg-type]
        assert led.facts == ()

    def test_non_observed_statuses_are_allowed_to_be_built_without_the_mint(self):
        f = Fact(id="F1", kind=FactKind.TOOL_RESULT, status=Status.ASSUMED, source="", step=1, mutation_index=0, subject="x")
        assert f.status is Status.ASSUMED

    def test_every_fact_the_ledger_creates_is_observed_and_sourced(self, ledger):
        for i, e in enumerate([ev("read_file", "a"), ev("edit_file", "b", paths=("x.py",)), ev("run_bash", "c", command="pytest", text="ok")]):
            f = ledger.observe(e)
            assert f.status is Status.OBSERVED and f.source == e.source


class TestClassification:
    def test_a_recognised_runner_is_a_verification_run(self, ledger):
        f = ledger.observe(ev("run_bash", "c1", command="cd sub && pytest -q", text="3 passed"))
        assert f.kind is FactKind.VERIFICATION_RUN and f.runner == "pytest" and f.verify_kind == "test" and f.ok is True

    def test_a_failing_runner_is_recorded_as_a_failed_run(self, ledger):
        f = ledger.observe(ev("run_bash", "c1", command="pytest", text=FAIL))
        assert f.kind is FactKind.VERIFICATION_RUN and f.ok is False

    @pytest.mark.parametrize("command", ["true", "echo ok", "pytest || true", "pytest | tail", "pytest; echo done", "python script.py", "make clean"])
    def test_a_command_that_proves_nothing_is_a_plain_tool_result(self, ledger, command):
        f = ledger.observe(ev("run_bash", "c1", command=command, text="ok"))
        assert f.kind is FactKind.TOOL_RESULT

    def test_run_tests_is_a_test_run_and_a_vacuous_one_is_inconclusive(self, ledger):
        green = ledger.observe(ev("run_tests", "c1", text="## Test Results (5/5 passed)\n- Failed: 0, Errors: 0"))
        vacuous = ledger.observe(ev("run_tests", "c2", text="## Test Results (0/0 passed)\n- Failed: 0, Errors: 0"))
        red = ledger.observe(ev("run_tests", "c3", text="## Test Results (3/5 passed)\n- Failed: 2, Errors: 0"))
        assert (green.ok, vacuous.ok, red.ok) == (True, None, False)
        assert all(f.verify_kind == "test" for f in (green, vacuous, red))

    def test_a_denied_call_is_a_gate_decision(self, ledger):
        f = ledger.observe(ev("run_bash", "c1", command="rm -rf x", denied=True))
        assert f.kind is FactKind.GATE_DECISION and f.ok is False

    def test_provider_errors_are_facts(self, ledger):
        f = ledger.observe(ObservedEvent("provider_error", "openrouter", "e1", result_text="API error 402"))
        assert f.kind is FactKind.PROVIDER_ERROR and f.ok is False

    @pytest.mark.parametrize("tool", ["write_file", "edit_file", "edit_file_multi", "fs_mutate"])
    def test_mutating_tools_are_file_mutations(self, ledger, tool):
        f = ledger.observe(ev(tool, "c1", paths=("src/a.py",)))
        assert f.kind is FactKind.FILE_MUTATION and f.ok is True and f.paths == ("src/a.py",)

    def test_a_failed_mutation_is_recorded_as_failed_but_still_counts_as_an_attempt(self, ledger):
        run = ledger.observe(ev("run_bash", "c0", command="pytest", text="3 passed"))
        m = ledger.observe(ev("edit_file", "c1", paths=("a.py",), failed=True))
        assert m.ok is False and ledger.mutation_count == 1 and ledger.is_stale(run)


class TestStaleness:
    def test_a_verification_goes_stale_at_the_next_mutation(self, ledger):
        run = ledger.observe(ev("run_bash", "c1", command="pytest", text="ok"))
        assert not ledger.is_stale(run) and ledger.effective_status(run) is Status.OBSERVED
        ledger.observe(ev("edit_file", "c2", paths=("a.py",)))
        assert ledger.is_stale(run) and ledger.effective_status(run) is Status.UNKNOWN

    def test_a_run_after_the_edit_is_fresh(self, ledger):
        ledger.observe(ev("edit_file", "c1", paths=("a.py",)))
        run = ledger.observe(ev("run_bash", "c2", command="pytest", text="ok"))
        assert not ledger.is_stale(run)

    def test_ordinary_tool_results_never_go_stale(self, ledger):
        f = ledger.observe(ev("read_file", "c1"))
        ledger.observe(ev("edit_file", "c2", paths=("a.py",)))
        assert not ledger.is_stale(f)


class TestRC1Determinism:
    EVENTS = [ev("read_file", "a"), ev("edit_file", "b", paths=("x.py",)), ev("run_bash", "c", command="pytest", text="ok"), ev("run_bash", "d", command="rm x", denied=True)]

    def _build(self) -> Ledger:
        led = Ledger()
        for e in self.EVENTS:
            led.observe(e)
        return led

    def test_the_same_events_give_the_same_ledger_and_hash(self):
        a, b = self._build(), self._build()
        assert a.facts == b.facts and a.snapshot_hash() == b.snapshot_hash()

    def test_the_hash_depends_on_content_and_order(self):
        base = self._build().snapshot_hash()
        other = Ledger()
        for e in reversed(self.EVENTS):
            other.observe(e)
        assert other.snapshot_hash() != base
        changed = Ledger()
        for e in self.EVENTS[:-1] + [ev("run_bash", "d", command="rm x", denied=True, text="different")]:
            changed.observe(e)
        assert changed.snapshot_hash() != base

    def test_fact_ids_are_sequential_and_stable(self):
        assert [f.id for f in self._build().facts] == ["F1", "F2", "F3", "F4"]
