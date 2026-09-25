"""The criteria-derivation authority (ADR-0048) — the three failure modes, pinned.

`derive_acceptance` answers *"does this objective require a green suite?"* from the
objective's own prose, and until ADR-0048 nothing recorded that answer. These tests
exist for two reasons:

1. **Three of them characterise a DEFECT, not a behaviour we want.** MODE A is a
   measured false `GOAL_MET` on this repository's own benchmark task. They are
   written to reproduce the shape as it is, so that when the derivation is fixed
   *these* are the tests that go red — the newly-red set is the bug's
   documentation (the repo's established rule). Each is marked
   `DEFECT-PIN` in its docstring and asserts the shape through the real chain.
2. **The rest hold ADR-0048's rules**: three derivation outcomes, `UNDETERMINED`
   never promotes, the record cites the objective's words, and `derive_acceptance`
   is untouched.

The flag `WISP_CRITERIA_STRICT_DERIVATION` defaults **OFF**, so MODE A's
characterisation is what production does today. Nothing here monkeypatches the
env for the default-path tests: `explain_acceptance(strict=...)` is an explicit
argument, which is why the flag is read at the composition point instead of
inside the pure function.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from wisp.core.acceptance import Verdict, evaluate
from wisp.core.convergence import (
    CommandProbe,
    CriteriaDerivation,
    DerivationReason,
    Measurement,
    SymbolSpec,
    derive_acceptance,
    explain_acceptance,
)
from wisp.core.goal import GoalState, derive_goal_state

# ── the objective corpus ────────────────────────────────────────────────

#: This repository's OWN benchmark task. Its verifier runs `sum_to(5) == 15`, so
#: the objective plainly requires the suite to go green — but `_WANTS_FIX_RE`
#: finds no suite word, so on a red baseline the absolute criterion is advisory.
MODE_A_OBJECTIVE = (
    "totals.py defines sum_to(n) which should sum integers 1..n inclusive, but it is "
    "off by one: sum_to(5) returns 10 instead of 15. Fix the bug in totals.py.")

#: A prohibition the grammar reads as a requirement.
MODE_B_OBJECTIVE = (
    "Do not make the tests pass by editing them; instead add a missing type "
    "annotation to models.py.")

#: Nothing machine-checkable, on a workspace with no declared toolchain.
MODE_C_OBJECTIVE = (
    "settings.json holds app settings. Change only the retries value to 7. Keep "
    "formatting valid JSON; do not modify any other key.")

STATED_OBJECTIVE = (
    "Fix the failing test suite in this repository so that "
    "`python -m pytest tests/ -q` passes.")

UNSTATED_OBJECTIVE = (
    "Add a function shout(name) to strings_util.py that returns the name uppercased.")


# ── workspaces ──────────────────────────────────────────────────────────

def _workspace(root: pathlib.Path, *, with_toolchain: bool = True) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    if with_toolchain:
        (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n",
                                             encoding="utf-8")
        (root / "tests").mkdir(exist_ok=True)
        (root / "tests" / "test_a.py").write_text(
            "def test_a():\n    assert True\n", encoding="utf-8")
    (root / "totals.py").write_text("def sum_to(n):\n    return 0\n", encoding="utf-8")
    (root / "strings_util.py").write_text("def greet(n):\n    return n\n",
                                          encoding="utf-8")
    (root / "models.py").write_text("class M:\n    pass\n", encoding="utf-8")
    (root / "settings.json").write_text('{"retries": 3}\n', encoding="utf-8")
    return root


class _InjectedProbe(CommandProbe):
    """`CommandProbe` with the subprocess replaced by an injected payload.

    Only `_run_command` is overridden: `measure()` itself still runs, so the
    evidence records are built by the real code.
    """

    def __init__(self, specs, payloads):
        super().__init__(specs)
        self._payloads = payloads

    def _run_command(self, spec, workspace):  # noqa: D102 - base class documents it
        payload = dict(self._payloads.get(spec.criteria_id, {"exit": 1}))
        return payload, f"{spec.criteria_id}: injected"


def _red_payload(exit_code: int = 1, *, changed: bool = False) -> dict:
    return {
        "exit": exit_code,
        "collected": 3,
        "failed": 1 if exit_code else 0,
        "output_tail": "3 failed in 0.04s" if exit_code else "3 passed in 0.04s",
        "inputs_digest": "after" if changed else "baseline",
        "inputs_files": 2,
    }


def _judge(objective: str, ws: pathlib.Path, *, exit_code: int = 1,
           changed: bool = False, strict: bool = False,
           baseline_exit: int = 1):
    """Drive the real chain: derive → measure → evaluate → derive the goal state."""
    baseline = _measure(ws, _red_payload(baseline_exit))
    derivation = explain_acceptance(objective, str(ws), baseline=baseline,
                                    strict=strict)
    now = _measure(ws, _red_payload(exit_code, changed=changed),
                   specs=derivation.specs)
    verdict = evaluate(derivation.criteria, now.evidence, now.observations)
    state = derive_goal_state(terminal_outcome="succeeded",
                              acceptance_verdict=verdict.verdict.value)
    return derivation, verdict, state


def _measure(ws: pathlib.Path, payload: dict, specs=None) -> Measurement:
    if specs is None:
        _, specs = derive_acceptance("", str(ws))
    probe = _InjectedProbe(specs, {s.criteria_id: payload for s in specs})
    return probe.measure(str(ws))


# ══════════════════════════════════════════════════════════════════════════
# MODE A — a false GOAL_MET (DEFECT-PIN)
# ══════════════════════════════════════════════════════════════════════════


class TestModeAFalseGoalMet:
    """DEFECT-PIN — an objective that requires a green suite, given guards-only.

    Reproduces ADR-0048 MODE A: the bug is unfixed, the suite still fails, and
    the objective is reported met. **When the derivation is fixed, this class goes
    red and must be rewritten** — it is the bug's documentation, not coverage.
    """

    def test_the_grammar_does_not_see_a_suite_requirement(self, tmp_path):
        from wisp.core.convergence import _WANTS_FIX_RE

        assert _WANTS_FIX_RE.search(MODE_A_OBJECTIVE) is None, (
            "the grammar now matches FIX_BUG — MODE A may have moved; re-measure "
            "before deleting this test")

    def test_the_absolute_criterion_is_only_advisory_on_a_red_baseline(self, tmp_path):
        ws = _workspace(tmp_path / "a")
        derivation = explain_acceptance(MODE_A_OBJECTIVE, str(ws),
                                        baseline=_measure(ws, _red_payload()))
        by_id = {c.criteria_id: c for c in derivation.criteria}
        assert by_id["verify:cmd0"].required is False, (
            "the absolute criterion is required — MODE A is already closed")
        assert by_id["verify:cmd0:no_regression"].required is True
        assert by_id["verify:cmd0:inputs_unchanged"].required is True

    def test_an_attempt_that_changes_nothing_is_reported_goal_met(self, tmp_path):
        """The defect, end to end. Nothing was fixed; the verdict is `pass`."""
        ws = _workspace(tmp_path / "a")
        _derivation, verdict, state = _judge(MODE_A_OBJECTIVE, ws,
                                             exit_code=1, changed=False)
        assert verdict.verdict is Verdict.PASS, (
            "the no-op no longer passes — MODE A is closed; rewrite this class")
        assert state is GoalState.GOAL_MET, (
            "the no-op no longer reaches GOAL_MET — MODE A is closed")

    def test_strict_derivation_closes_it(self, tmp_path):
        """ADR-0048 R2 — the fix, through the same chain."""
        ws = _workspace(tmp_path / "a")
        derivation, verdict, state = _judge(MODE_A_OBJECTIVE, ws, exit_code=1,
                                            strict=True)
        assert derivation.undetermined == ("verify:cmd0",)
        assert verdict.verdict is Verdict.INCONCLUSIVE, (
            "strict derivation must not PASS an objective whose requirement the "
            "host could not determine")
        assert verdict.reason_codes == ["NO_EVIDENCE"]
        assert "verify:cmd0:requirement_declared" in verdict.unmet_criteria
        assert state is GoalState.GOAL_UNVERIFIED, (
            "an undetermined requirement must land on GOAL_UNVERIFIED, never "
            "GOAL_MET and never GOAL_FAILED")

    def test_the_strict_criterion_is_required_and_unevidenceable(self, tmp_path):
        """The mechanism: a required criterion the harness cannot evidence.

        A **red** baseline is load-bearing here, and that is the rule rather than
        an incidental: without one the absolute criterion is required anyway, so
        the derivation chose nothing and strict mode withholds nothing.
        """
        ws = _workspace(tmp_path / "a")
        derivation = explain_acceptance(MODE_A_OBJECTIVE, str(ws),
                                        baseline=_measure(ws, _red_payload()),
                                        strict=True)
        extra = [c for c in derivation.criteria
                 if c.criteria_id.endswith(":requirement_declared")]
        assert len(extra) == 1
        assert extra[0].required is True
        assert extra[0].check is None, (
            "a check would let the criterion be satisfied — and then the objective "
            "would complete on an undetermined requirement")

    def test_nothing_is_withheld_when_the_criterion_is_already_required(self, tmp_path):
        """The blast radius: no baseline, no advisory criterion, no withholding."""
        ws = _workspace(tmp_path / "a")
        derivation = explain_acceptance(MODE_A_OBJECTIVE, str(ws), strict=True)
        by_id = {c.criteria_id: c for c in derivation.criteria}
        assert by_id["verify:cmd0"].required is True
        assert derivation.undetermined == ()

    def test_strict_derivation_does_not_change_a_stated_objective(self, tmp_path):
        """The flag's blast radius: a STATED objective is untouched."""
        ws = _workspace(tmp_path / "a")
        plain = explain_acceptance(STATED_OBJECTIVE, str(ws),
                                   baseline=_measure(ws, _red_payload()))
        strict = explain_acceptance(STATED_OBJECTIVE, str(ws),
                                    baseline=_measure(ws, _red_payload()),
                                    strict=True)
        assert [c.criteria_id for c in strict.criteria] == \
               [c.criteria_id for c in plain.criteria]
        assert strict.undetermined == ()


# ══════════════════════════════════════════════════════════════════════════
# MODE B — a false promotion from a prohibition (DEFECT-PIN, residual R7)
# ══════════════════════════════════════════════════════════════════════════


class TestModeBFalsePromotion:
    """DEFECT-PIN — the grammar has no negation awareness.

    ADR-0048 R7 states this residual rather than hiding it: R1–R6 do **not** fix
    it, and the record is what makes it visible.
    """

    def test_a_prohibition_is_read_as_a_requirement(self, tmp_path):
        from wisp.core.convergence import _WANTS_FIX_RE

        match = _WANTS_FIX_RE.search(MODE_B_OBJECTIVE)
        assert match is not None, "the grammar became negation-aware — re-measure"
        assert "make the tests" in match.group(0)

    def test_it_is_classified_stated_and_promoted(self, tmp_path):
        ws = _workspace(tmp_path / "b")
        derivation = explain_acceptance(MODE_B_OBJECTIVE, str(ws),
                                        baseline=_measure(ws, _red_payload()))
        assert derivation.reason_for("verify:cmd0") == DerivationReason.STATED.value
        by_id = {c.criteria_id: c for c in derivation.criteria}
        assert by_id["verify:cmd0"].required is True

    def test_the_objective_that_never_asked_fails(self, tmp_path):
        """The defect, end to end: `goal_failed` on an objective about an annotation."""
        ws = _workspace(tmp_path / "b")
        _d, verdict, state = _judge(MODE_B_OBJECTIVE, ws, exit_code=1)
        assert verdict.verdict is Verdict.FAIL
        assert state is GoalState.GOAL_FAILED, (
            "MODE B no longer fails — re-measure before deleting this test")

    def test_the_record_makes_the_false_promotion_visible(self, tmp_path):
        """R3's purpose: the span is a *prohibition's* words, and a reader can see it."""
        ws = _workspace(tmp_path / "b")
        derivation = explain_acceptance(MODE_B_OBJECTIVE, str(ws))
        span = derivation.span_for("verify:cmd0")
        assert span, "a promotion must cite the objective's own words"
        assert "Do not" in MODE_B_OBJECTIVE[:MODE_B_OBJECTIVE.index(span)], (
            "the cited span should be preceded by the negation a reader needs to "
            "see in order to distrust the promotion")

    @pytest.mark.parametrize("objective", [
        "Make the linter pass on this repository by fixing the unused imports.",
        "Add a --verbose flag to the CLI. CI will pass without any test changes.",
    ])
    def test_other_measured_false_positives(self, objective):
        from wisp.core.convergence import _WANTS_FIX_RE

        assert _WANTS_FIX_RE.search(objective) is not None, (
            f"the grammar no longer matches {objective!r} — re-measure MODE B")


# ══════════════════════════════════════════════════════════════════════════
# MODE C — "I cannot tell", and it is already honest
# ══════════════════════════════════════════════════════════════════════════


class TestModeCNoCriteria:
    """The branch that was already correct — and must stay correct."""

    def test_no_toolchain_yields_no_specs(self, tmp_path):
        ws = _workspace(tmp_path / "c", with_toolchain=False)
        derivation = explain_acceptance(MODE_C_OBJECTIVE, str(ws))
        assert derivation.specs == ()

    def test_no_required_criteria_is_inconclusive_not_a_pass(self, tmp_path):
        ws = _workspace(tmp_path / "c", with_toolchain=False)
        derivation = explain_acceptance(MODE_C_OBJECTIVE, str(ws))
        verdict = evaluate(derivation.criteria, (), {})
        assert verdict.verdict is Verdict.INCONCLUSIVE
        assert verdict.reason_codes == ["NO_REQUIRED_CRITERIA"]

    def test_it_lands_on_goal_unverified(self, tmp_path):
        ws = _workspace(tmp_path / "c", with_toolchain=False)
        derivation = explain_acceptance(MODE_C_OBJECTIVE, str(ws))
        verdict = evaluate(derivation.criteria, (), {})
        state = derive_goal_state(terminal_outcome="succeeded",
                                  acceptance_verdict=verdict.verdict.value)
        assert state is GoalState.GOAL_UNVERIFIED

    def test_an_empty_objective_is_not_a_fix_request(self, tmp_path):
        """The guard against the obvious over-correction."""
        ws = _workspace(tmp_path / "c")
        derivation = explain_acceptance("", str(ws))
        assert derivation.reason_for("verify:cmd0") == \
            DerivationReason.UNDETERMINED.value


# ══════════════════════════════════════════════════════════════════════════
# The rules themselves
# ══════════════════════════════════════════════════════════════════════════


class TestR1ThreeOutcomes:
    @pytest.mark.parametrize("objective,expected", [
        (STATED_OBJECTIVE, DerivationReason.STATED),
        (UNSTATED_OBJECTIVE, DerivationReason.UNSTATED),
        (MODE_A_OBJECTIVE, DerivationReason.UNDETERMINED),
    ])
    def test_the_classification(self, tmp_path, objective, expected):
        ws = _workspace(tmp_path / "r1")
        derivation = explain_acceptance(objective, str(ws))
        assert derivation.reason_for("verify:cmd0") == expected.value

    def test_unstated_requires_a_checkable_alternative(self, tmp_path):
        """`UNSTATED` is inferred from a derived SymbolSpec, not from a path alone."""
        ws = _workspace(tmp_path / "r1")
        with_symbol = explain_acceptance(UNSTATED_OBJECTIVE, str(ws))
        assert any(isinstance(s, SymbolSpec) for s in with_symbol.specs), (
            "UNSTATED must be justified by a machine-checkable requirement")
        # The same objective with the named file absent names nothing checkable.
        (ws / "strings_util.py").unlink()
        without = explain_acceptance(UNSTATED_OBJECTIVE, str(ws))
        assert without.reason_for("verify:cmd0") == \
            DerivationReason.UNDETERMINED.value

    def test_only_stated_promotes(self, tmp_path):
        ws = _workspace(tmp_path / "r1")
        baseline = _measure(ws, _red_payload())
        for objective, should_promote in ((STATED_OBJECTIVE, True),
                                          (UNSTATED_OBJECTIVE, False),
                                          (MODE_A_OBJECTIVE, False)):
            d = explain_acceptance(objective, str(ws), baseline=baseline)
            by_id = {c.criteria_id: c for c in d.criteria}
            assert by_id["verify:cmd0"].required is should_promote, objective

    def test_symbol_specs_have_no_reason_entry(self, tmp_path):
        """`reasons` is per COMMAND spec — a symbol is not a suite requirement."""
        ws = _workspace(tmp_path / "r1")
        d = explain_acceptance(UNSTATED_OBJECTIVE, str(ws))
        assert all(cid.startswith("verify:") for cid, _, _ in d.reasons)


class TestR2UndeterminedNeverPromotes:
    def test_strict_never_promotes_an_undetermined_spec(self, tmp_path):
        ws = _workspace(tmp_path / "r2")
        d = explain_acceptance(MODE_A_OBJECTIVE, str(ws),
                               baseline=_measure(ws, _red_payload()), strict=True)
        by_id = {c.criteria_id: c for c in d.criteria}
        assert by_id["verify:cmd0"].required is False, (
            "strict mode must not promote — that would be MODE B")
        assert by_id["verify:cmd0:requirement_declared"].required is True

    def test_strict_never_produces_a_fail(self, tmp_path):
        """Absence of a determination is not a determination of failure."""
        ws = _workspace(tmp_path / "r2")
        _d, verdict, _s = _judge(MODE_A_OBJECTIVE, ws, strict=True)
        assert verdict.verdict is not Verdict.FAIL

    def test_a_green_baseline_still_completes_under_strict(self, tmp_path):
        """Strict mode must not block an objective whose suite already passes."""
        ws = _workspace(tmp_path / "r2")
        _d, verdict, state = _judge(MODE_A_OBJECTIVE, ws, baseline_exit=0,
                                    exit_code=0, strict=True)
        assert state is GoalState.GOAL_MET


class TestR3TheRecord:
    def test_the_record_carries_the_span(self, tmp_path):
        ws = _workspace(tmp_path / "r3")
        d = explain_acceptance(STATED_OBJECTIVE, str(ws))
        span = d.span_for("verify:cmd0")
        assert span and span in STATED_OBJECTIVE, (
            "a promotion must cite a span that is actually in the objective")

    def test_an_unpromoted_spec_cites_nothing(self, tmp_path):
        ws = _workspace(tmp_path / "r3")
        d = explain_acceptance(MODE_A_OBJECTIVE, str(ws))
        assert d.span_for("verify:cmd0") == "", (
            "there is nothing to cite — an empty span is the honest answer")

    def test_unknown_ids_are_empty_not_errors(self, tmp_path):
        ws = _workspace(tmp_path / "r3")
        d = explain_acceptance(STATED_OBJECTIVE, str(ws))
        assert d.reason_for("no:such:criterion") == ""
        assert d.span_for("no:such:criterion") == ""

    def test_the_record_is_a_frozen_value(self, tmp_path):
        ws = _workspace(tmp_path / "r3")
        d = explain_acceptance(STATED_OBJECTIVE, str(ws))
        assert isinstance(d, CriteriaDerivation)
        with pytest.raises(Exception):
            d.strict = True  # type: ignore[misc]


class TestR4DeriveAcceptanceIsUnchanged:
    """ADR-0009 — a pinned internal signature is not widened to carry a new concern."""

    def test_the_signature_is_untouched(self):
        import inspect

        sig = inspect.signature(derive_acceptance)
        assert list(sig.parameters) == ["goal", "workspace", "baseline"]
        assert sig.parameters["baseline"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_it_returns_the_two_tuple(self, tmp_path):
        ws = _workspace(tmp_path / "r4")
        out = derive_acceptance(STATED_OBJECTIVE, str(ws))
        assert isinstance(out, tuple) and len(out) == 2

    def test_it_agrees_with_the_non_strict_explanation(self, tmp_path):
        ws = _workspace(tmp_path / "r4")
        baseline = _measure(ws, _red_payload())
        for objective in (STATED_OBJECTIVE, UNSTATED_OBJECTIVE, MODE_A_OBJECTIVE,
                          MODE_C_OBJECTIVE, ""):
            a, b = derive_acceptance(objective, str(ws), baseline=baseline)
            d = explain_acceptance(objective, str(ws), baseline=baseline)
            assert [c.criteria_id for c in a] == [c.criteria_id for c in d.criteria]
            assert [c.required for c in a] == [c.required for c in d.criteria]
            assert b == d.specs

    def test_it_never_applies_strict(self, tmp_path):
        """The flag is read at the composition point, so this path is inert."""
        ws = _workspace(tmp_path / "r4")
        criteria, _specs = derive_acceptance(MODE_A_OBJECTIVE, str(ws))
        assert not any(c.criteria_id.endswith(":requirement_declared")
                       for c in criteria)

    def test_criteria_for_is_untouched(self, tmp_path):
        """~40 call sites depend on it; the body must not have gained a parameter."""
        import inspect

        from wisp.core.convergence import criteria_for

        sig = inspect.signature(criteria_for)
        assert list(sig.parameters) == ["specs", "baseline", "promote_absolute"]

    def test_no_module_outside_the_composition_point_reads_the_flag(self):
        """`WISP_CRITERIA_STRICT_DERIVATION` has exactly one reader."""
        repo = pathlib.Path(__file__).resolve().parents[2]
        readers = []
        for path in (repo / "wisp").rglob("*.py"):
            if "STRICT_DERIVATION_ENV" in path.read_text(encoding="utf-8"):
                readers.append(path.relative_to(repo).as_posix())
        assert readers == ["wisp/autonomous.py"], (
            f"the flag is read in {readers}; it must be read once, at the "
            "composition point, or two callers can disagree")


class TestR5TheFlagDefaultsOff:
    def test_the_env_var_name(self):
        from wisp.autonomous import STRICT_DERIVATION_ENV

        assert STRICT_DERIVATION_ENV == "WISP_CRITERIA_STRICT_DERIVATION"

    def test_the_helper_defaults_to_false(self, monkeypatch):
        from wisp.autonomous import _strict_derivation_enabled

        monkeypatch.delenv("WISP_CRITERIA_STRICT_DERIVATION", raising=False)
        assert _strict_derivation_enabled() is False

    @pytest.mark.parametrize("value,expected", [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
        ("", False), ("0", False), ("false", False), ("off", False), ("nope", False),
    ])
    def test_the_helper_reads_truthy(self, monkeypatch, value, expected):
        from wisp.autonomous import _strict_derivation_enabled

        monkeypatch.setenv("WISP_CRITERIA_STRICT_DERIVATION", value)
        assert _strict_derivation_enabled() is expected

    def test_explain_acceptance_defaults_to_non_strict(self, tmp_path):
        ws = _workspace(tmp_path / "r5")
        d = explain_acceptance(MODE_A_OBJECTIVE, str(ws))
        assert d.strict is False and d.undetermined == ()


class TestR6NoModelChannel:
    """ADR-0045 R1 stands: the model may not declare criteria. Tripwire."""

    def test_no_function_takes_criteria_from_model_output(self):
        """`explain_acceptance` takes prose + a workspace, and nothing else.

        A parameter accepting model-authored criteria would be the judged writing
        the exam. If one appears, this fails.
        """
        import inspect

        params = set(inspect.signature(explain_acceptance).parameters)
        assert params == {"goal", "workspace", "baseline", "strict"}, (
            f"explain_acceptance gained {params - {'goal','workspace','baseline','strict'}} "
            "— a model-authored criteria channel is exactly what ADR-0045 R1 forbids")

    def test_the_derivation_reads_no_provider(self):
        """Structural: `convergence.py` must not import a provider module."""
        repo = pathlib.Path(__file__).resolve().parents[2]
        tree = ast.parse((repo / "wisp/core/convergence.py").read_text())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        assert not any("provider" in m for m in imported), (
            f"convergence.py imports a provider module: {imported}")


class TestTheDerivationIsJournalled:
    """ADR-0048 R3 — the record is durable, not just returned."""

    def _run(self, objective: str, ws: pathlib.Path, journal: pathlib.Path):
        import asyncio

        from wisp.core.convergence import (
            ConvergenceController,
            Objective,
            TurnObservation,
        )

        async def run_turn(request):
            # The turn does nothing; the verdict comes from the probe, and the
            # point of this test is the derivation line the controller writes
            # before attempt 0 — so the attempt only has to be well-formed.
            return TurnObservation(turn_succeeded=True,
                                   terminal_outcome="succeeded")

        controller = ConvergenceController(
            run_turn=run_turn, probe=_InjectedProbe((), {}), max_attempts=1,
            journal_path=journal)
        baseline = _measure(ws, _red_payload())
        derivation = explain_acceptance(objective, str(ws), baseline=baseline)
        obj = Objective(goal=objective, workspace=str(ws),
                        criteria=derivation.criteria,
                        derivation=derivation.reasons)
        asyncio.run(controller.converge(obj))
        return journal

    def test_a_derivation_line_is_written(self, tmp_path):
        import json

        ws = _workspace(tmp_path / "j")
        journal = self._run(MODE_A_OBJECTIVE, ws, tmp_path / "j.jsonl")
        lines = [json.loads(x) for x in journal.read_text().splitlines() if x.strip()]
        kinds = [line.get("kind") for line in lines]
        assert "derivation" in kinds, f"no derivation record; kinds={kinds}"
        record = next(line for line in lines if line.get("kind") == "derivation")
        entry = record["reasons"][0]
        assert entry["criteria_id"] == "verify:cmd0"
        assert entry["reason"] == DerivationReason.UNDETERMINED.value
        assert entry["span"] == ""

    def test_the_span_is_durable_for_a_stated_objective(self, tmp_path):
        import json

        ws = _workspace(tmp_path / "j")
        journal = self._run(STATED_OBJECTIVE, ws, tmp_path / "j2.jsonl")
        record = next(json.loads(x) for x in journal.read_text().splitlines()
                      if x.strip() and json.loads(x).get("kind") == "derivation")
        assert record["reasons"][0]["reason"] == DerivationReason.STATED.value
        assert record["reasons"][0]["span"]


class TestNonVacuity:
    """The instrument checks itself, as the repo's discipline requires."""

    def test_the_injected_probe_drives_the_real_measure(self, tmp_path):
        """If `_InjectedProbe` bypassed `measure`, the evidence would be absent
        and every verdict above would be INCONCLUSIVE for the wrong reason."""
        ws = _workspace(tmp_path / "nv")
        m = _measure(ws, _red_payload())
        assert m.evidence, "no evidence records — the probe is not driving measure()"
        assert all(ev.producer == CommandProbe.PRODUCER for ev in m.evidence)

    def test_a_schema_only_workspace_is_the_control(self, tmp_path):
        """The complement of MODE A: with a SymbolSpec the guards are correct."""
        ws = _workspace(tmp_path / "nv")
        d = explain_acceptance(UNSTATED_OBJECTIVE, str(ws),
                               baseline=_measure(ws, _red_payload()))
        by_id = {c.criteria_id: c for c in d.criteria}
        assert by_id["symbol:shout"].required is True, (
            "the symbol criterion must be required — otherwise UNSTATED would be "
            "as weak as MODE A")
