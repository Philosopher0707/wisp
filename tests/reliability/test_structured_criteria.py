"""ADR-0050 — the objective-declared criteria block.

ADR-0048 R6 decided the structured path was *viable* and fixed its boundary; it explicitly deferred
*"the grammar, the validation surface, and the rejection behaviour"*. ADR-0050 decides them, and this
file holds all three.

The load-bearing property is **rejection, not fallback**: a declaration the host cannot use **stops
the run**. ADR-0048 R6 states the reason — *a silent downgrade **is** MODE A* — so a host that quietly
fell back to the prose grammar would measure something other than what the caller declared while
appearing to measure it.

Two things this file does deliberately:

* **It drives `converge_on_objective`, not only the parser.** The previous phase shipped a type error
  past 55 passing tests because every one of them called the unit directly and none drove the caller
  (F67). The wiring is where the bug was; the wiring is what these tests exercise.
* **It states what MODE A and MODE B do on each path.** The declaration does not make the classifier
  better — it makes the *objective* complete. Both modes stay reachable without a declaration, and the
  tests say so rather than implying the fix is universal.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re

import pytest

from wisp.core.acceptance import Verdict, evaluate
from wisp.core.convergence import (
    DECLARATION_CLOSE,
    DECLARATION_KINDS,
    DECLARATION_OPEN,
    CriteriaDeclarationRejected,
    DerivationReason,
    Measurement,
    derive_acceptance,
    explain_acceptance,
    parse_criteria_declaration,
)

#: MODE A's objective, verbatim: this repository's own benchmark task. Its verifier runs the suite
#: and its prose never says so.
MODE_A_OBJECTIVE = (
    "totals.py defines sum_to(n) which should sum integers 1..n inclusive, but it is "
    "off by one: sum_to(5) returns 10 instead of 15. Fix the bug in totals.py.")

#: MODE B's objective, verbatim: a prohibition the prose grammar reads as a requirement.
MODE_B_OBJECTIVE = (
    "Do not make the tests pass by editing them; instead add a missing type "
    "annotation to models.py.")


def _workspace(root: pathlib.Path) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n", encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    (root / "totals.py").write_text("def sum_to(n):\n    return 0\n", encoding="utf-8")
    (root / "models.py").write_text("class M:\n    pass\n", encoding="utf-8")
    return root


def _declared(body: str, prose: str = "Fix the bug.") -> str:
    return f"{DECLARATION_OPEN}\n{body}\n{DECLARATION_CLOSE}\n{prose}"


def _red(criteria_id: str = "verify:cmd0") -> Measurement:
    """A red baseline for ONE criteria id.

    The id matters. A declared spec's criteria id is `declared:cmdN`, not `verify:cmdN`, so a
    baseline keyed `verify:cmd0` leaves `base is None` for a declared spec — and `criteria_for`
    then makes the absolute criterion **required regardless of `promote_absolute`**. A mutation
    probe found exactly that: un-promoting the declared criterion was not caught, because the
    test's baseline did not cover the id under test. **A test for the promotion must supply a
    baseline that makes the promotion the deciding factor.**
    """
    return Measurement(observations={criteria_id: {
        "exit": 1, "collected": 0, "failed": 1, "output_tail": "",
        "inputs_digest": "x", "inputs_files": 1}}, lines=())


# ══════════════════════════════════════════════════════════════════════════
# R1/R2 — placement and the closed grammar
# ══════════════════════════════════════════════════════════════════════════


class TestTheGrammar:
    def test_an_absent_declaration_is_not_an_error(self, tmp_path):
        """R7 — absence leaves the prose grammar to run, unchanged."""
        ws = _workspace(tmp_path / "g")
        assert parse_criteria_declaration(MODE_A_OBJECTIVE, str(ws)) is None

    def test_the_block_must_be_at_the_head(self, tmp_path):
        """R1 — an objective that *discusses* declarations must not carry one."""
        ws = _workspace(tmp_path / "g")
        quoted = f"Consider this block:\n{_declared('command_succeeds: ls')}"
        assert parse_criteria_declaration(quoted, str(ws)) is None, (
            "a block quoted mid-prose was treated as a declaration")

    def test_leading_whitespace_is_tolerated(self, tmp_path):
        ws = _workspace(tmp_path / "g")
        d = parse_criteria_declaration("\n\n  " + _declared("command_succeeds: ls"), str(ws))
        assert d is not None

    def test_comments_and_blank_lines_are_allowed(self, tmp_path):
        ws = _workspace(tmp_path / "g")
        d = parse_criteria_declaration(
            _declared("# the suite\n\ncommand_succeeds: ls\n\n# and that is it"), str(ws))
        assert len(d.entries) == 1

    def test_both_kinds_parse(self, tmp_path):
        ws = _workspace(tmp_path / "g")
        d = parse_criteria_declaration(
            _declared("command_succeeds: ls\nsymbol_defined: totals.py::sum_to"), str(ws))
        assert [k for k, _ in d.entries] == ["command_succeeds", "symbol_defined"]
        assert len(d.specs) == 2

    def test_the_kind_vocabulary_is_exactly_two(self):
        assert DECLARATION_KINDS == ("command_succeeds", "symbol_defined")

    @pytest.mark.parametrize("body,reason", [
        ("# nothing but a comment", "empty"),
        ("make_it_green: yes", "unknown-kind"),
        ("command_succeeds", "malformed-line"),
        ("command_succeeds:", "malformed-line"),
        (": ls", "malformed-line"),
        ("symbol_defined: totals.py", "malformed-line"),
    ])
    def test_the_rejected_shapes(self, tmp_path, body, reason):
        """R2/R4 — the grammar rejects what it does not understand, and says which shape."""
        ws = _workspace(tmp_path / "g")
        with pytest.raises(CriteriaDeclarationRejected) as exc:
            parse_criteria_declaration(_declared(body), str(ws))
        assert exc.value.reason == reason

    def test_an_unterminated_block_is_rejected(self, tmp_path):
        ws = _workspace(tmp_path / "g")
        with pytest.raises(CriteriaDeclarationRejected) as exc:
            parse_criteria_declaration(f"{DECLARATION_OPEN}\ncommand_succeeds: ls\n", str(ws))
        assert exc.value.reason == "unterminated"

    def test_a_malformed_open_is_rejected(self, tmp_path):
        ws = _workspace(tmp_path / "g")
        with pytest.raises(CriteriaDeclarationRejected) as exc:
            parse_criteria_declaration(
                f"{DECLARATION_OPEN} extra\ncommand_succeeds: ls\n{DECLARATION_CLOSE}\n", str(ws))
        assert exc.value.reason == "malformed-open"

    def test_the_message_names_the_way_out(self, tmp_path):
        """R4 — the caller must be able to act on the rejection."""
        ws = _workspace(tmp_path / "g")
        with pytest.raises(CriteriaDeclarationRejected) as exc:
            parse_criteria_declaration(_declared("make_it_green: yes"), str(ws))
        text = str(exc.value)
        assert "NOT ignored" in text and "NOT used" in text
        assert "remove the block" in text, "the message must name the fallback path"


# ══════════════════════════════════════════════════════════════════════════
# R3 — the validation surface
# ══════════════════════════════════════════════════════════════════════════


class TestTheValidationSurface:
    def test_a_runnable_command_is_accepted(self, tmp_path):
        ws = _workspace(tmp_path / "v")
        assert parse_criteria_declaration(_declared("command_succeeds: ls"), str(ws))

    def test_a_workspace_relative_executable_is_accepted(self, tmp_path):
        ws = _workspace(tmp_path / "v")
        tool = ws / "check.sh"
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)
        assert parse_criteria_declaration(
            _declared("command_succeeds: ./check.sh"), str(ws))

    def test_an_unresolvable_command_is_rejected(self, tmp_path):
        """R3 — the host validates runnability. It cannot validate outcome, and does not try."""
        ws = _workspace(tmp_path / "v")
        with pytest.raises(CriteriaDeclarationRejected) as exc:
            parse_criteria_declaration(
                _declared("command_succeeds: definitely-not-a-real-binary-xyz --go"), str(ws))
        assert exc.value.reason == "unmeasurable-spec"
        assert "not runnable" in exc.value.detail

    def test_a_symbol_in_an_existing_file_is_accepted(self, tmp_path):
        ws = _workspace(tmp_path / "v")
        assert parse_criteria_declaration(
            _declared("symbol_defined: totals.py::sum_to"), str(ws))

    def test_a_symbol_in_a_missing_file_is_rejected(self, tmp_path):
        ws = _workspace(tmp_path / "v")
        with pytest.raises(CriteriaDeclarationRejected) as exc:
            parse_criteria_declaration(_declared("symbol_defined: nope.py::f"), str(ws))
        assert "does not exist" in exc.value.detail

    def test_a_non_identifier_symbol_is_rejected(self, tmp_path):
        ws = _workspace(tmp_path / "v")
        with pytest.raises(CriteriaDeclarationRejected) as exc:
            parse_criteria_declaration(_declared("symbol_defined: totals.py::not a name"), str(ws))
        assert "not a Python identifier" in exc.value.detail

    def test_a_path_outside_the_workspace_is_rejected(self, tmp_path):
        """Fail-closed containment — the same rule the rest of the tree uses."""
        ws = _workspace(tmp_path / "v")
        with pytest.raises(CriteriaDeclarationRejected) as exc:
            parse_criteria_declaration(
                _declared("symbol_defined: ../../../etc/hosts::x"), str(ws))
        assert "outside the workspace" in exc.value.detail

    def test_an_absolute_path_outside_is_rejected(self, tmp_path):
        ws = _workspace(tmp_path / "v")
        with pytest.raises(CriteriaDeclarationRejected) as exc:
            parse_criteria_declaration(
                _declared("symbol_defined: /etc/hosts::x"), str(ws))
        assert "outside the workspace" in exc.value.detail


# ══════════════════════════════════════════════════════════════════════════
# R5 — the declaration precedes the inference
# ══════════════════════════════════════════════════════════════════════════


class TestTheDeclarationPrecedes:
    def test_a_declared_objective_reports_declared(self, tmp_path):
        ws = _workspace(tmp_path / "p")
        d = explain_acceptance(_declared("command_succeeds: ls"), str(ws),
                               use_declaration=True)
        assert d.reason_for("declared:cmd0") == DerivationReason.DECLARED.value

    def test_the_declared_criterion_is_required_even_on_a_red_baseline(self, tmp_path):
        """The mechanism that closes MODE A."""
        ws = _workspace(tmp_path / "p")
        d = explain_acceptance(_declared("command_succeeds: ls"), str(ws),
                               baseline=_red("declared:cmd0"), use_declaration=True)
        absolute = next(c for c in d.criteria if c.criteria_id == "declared:cmd0")
        assert absolute.required is True, (
            "the declared criterion is advisory — MODE A is open on the declared path")

    def test_the_prose_grammar_is_not_consulted(self, tmp_path):
        """R5 — the prose cannot contribute a criterion when the objective has declared."""
        ws = _workspace(tmp_path / "p")
        # The prose contains a *different* command and a symbol; neither may appear.
        objective = _declared("command_succeeds: ls",
                              "Fix the failing suite in totals.py so it passes.")
        d = explain_acceptance(objective, str(ws), baseline=_red(), use_declaration=True)
        ids = {c.criteria_id for c in d.criteria}
        assert ids and all(i.startswith("declared:") for i in ids), (
            f"a prose-derived criterion leaked into a declared objective: {ids}")
        assert not any(i.startswith("verify:cmd") for i in ids)
        assert not any(i.startswith("symbol:") for i in ids)

    def test_the_declared_specs_replace_the_detected_ones(self, tmp_path):
        ws = _workspace(tmp_path / "p")
        d = explain_acceptance(_declared("symbol_defined: totals.py::sum_to"), str(ws),
                               baseline=_red(), use_declaration=True)
        assert [s.criteria_id for s in d.specs] == ["declared:symbol0"]


# ══════════════════════════════════════════════════════════════════════════
# What this does to MODE A and MODE B
# ══════════════════════════════════════════════════════════════════════════


class TestModeA:
    def test_mode_a_is_closed_under_the_declared_path(self, tmp_path):
        """The fix: an objective that states its conditions gets a required criterion."""
        ws = _workspace(tmp_path / "a")
        # MODE A's own objective, with a declaration naming what its verifier actually runs.
        objective = _declared("command_succeeds: ls",
                              "The suite must pass. " + MODE_A_OBJECTIVE)
        d = explain_acceptance(objective, str(ws), baseline=_red("declared:cmd0"),
                               use_declaration=True)
        assert next(c for c in d.criteria
                    if c.criteria_id == "declared:cmd0").required is True
        probe_ok = evaluate(d.criteria, (), {})
        # A no-op cannot satisfy it: the declared command criterion is required and carries no
        # evidence, so rule 3 yields INCONCLUSIVE rather than the PASS that guards-only gave.
        assert probe_ok.verdict is Verdict.INCONCLUSIVE, (
            "a no-op reached a decisive verdict on a declared objective")

    def test_mode_a_is_still_reachable_without_a_declaration(self, tmp_path):
        """The honest boundary: the declaration completes the objective, it does not fix prose."""
        ws = _workspace(tmp_path / "a")
        d = explain_acceptance(MODE_A_OBJECTIVE, str(ws), baseline=_red())
        absolute = next((c for c in d.criteria if c.criteria_id == "verify:cmd0"), None)
        assert absolute is not None and absolute.required is False, (
            "MODE A is no longer reachable without a declaration — re-measure the report")
        assert d.reason_for("verify:cmd0") == DerivationReason.UNDETERMINED.value

    def test_the_declared_path_changes_the_criteria_not_just_the_label(self, tmp_path):
        """Non-vacuity: the two paths must produce materially different criteria."""
        ws = _workspace(tmp_path / "a")
        prose = explain_acceptance(MODE_A_OBJECTIVE, str(ws), baseline=_red())
        declared = explain_acceptance(
            _declared("command_succeeds: ls", MODE_A_OBJECTIVE), str(ws),
            baseline=_red(), use_declaration=True)
        assert {c.criteria_id for c in prose.criteria} != \
               {c.criteria_id for c in declared.criteria}


class TestModeB:
    def test_mode_b_is_still_reachable_without_a_declaration(self, tmp_path):
        ws = _workspace(tmp_path / "b")
        d = explain_acceptance(MODE_B_OBJECTIVE, str(ws), baseline=_red())
        assert d.reason_for("verify:cmd0") == DerivationReason.STATED.value
        absolute = next(c for c in d.criteria if c.criteria_id == "verify:cmd0")
        assert absolute.required is True, (
            "MODE B is no longer reachable without a declaration — re-measure the report")

    def test_mode_b_is_closed_structurally_under_the_declared_path(self, tmp_path):
        """Not fixed by negation awareness — fixed by *not reading the prose at all*."""
        ws = _workspace(tmp_path / "b")
        objective = _declared("command_succeeds: ls", MODE_B_OBJECTIVE)
        d = explain_acceptance(objective, str(ws), baseline=_red(), use_declaration=True)
        assert all(c.criteria_id.startswith("declared:") for c in d.criteria)
        assert d.reason_for("declared:cmd0") == DerivationReason.DECLARED.value
        # The prohibition is still IN the objective — it is simply not consulted.
        assert "Do not make the tests pass" in objective


# ══════════════════════════════════════════════════════════════════════════
# R8 — the flag, and `derive_acceptance`'s frozen signature
# ══════════════════════════════════════════════════════════════════════════


class TestTheFlagAndTheFrozenSignature:
    def test_the_env_var_name(self):
        from wisp.autonomous import STRUCTURED_DECLARATION_ENV

        assert STRUCTURED_DECLARATION_ENV == "WISP_CRITERIA_STRUCTURED_DECLARATION"

    def test_the_helper_defaults_to_false(self, monkeypatch):
        from wisp.autonomous import _structured_declaration_enabled

        monkeypatch.delenv("WISP_CRITERIA_STRUCTURED_DECLARATION", raising=False)
        assert _structured_declaration_enabled() is False

    @pytest.mark.parametrize("value,expected", [
        ("1", True), ("true", True), ("on", True),
        ("", False), ("0", False), ("false", False), ("nope", False),
    ])
    def test_the_helper_reads_truthy(self, monkeypatch, value, expected):
        from wisp.autonomous import _structured_declaration_enabled

        monkeypatch.setenv("WISP_CRITERIA_STRUCTURED_DECLARATION", value)
        assert _structured_declaration_enabled() is expected

    def test_explain_acceptance_defaults_to_not_consulting_a_declaration(self, tmp_path):
        ws = _workspace(tmp_path / "f")
        d = explain_acceptance(_declared("command_succeeds: ls"), str(ws), baseline=_red())
        assert d.reason_for("verify:cmd0") != DerivationReason.DECLARED.value

    def test_derive_acceptance_never_sees_a_declaration(self, tmp_path):
        """ADR-0009 — its signature is frozen, so it cannot."""
        import inspect

        ws = _workspace(tmp_path / "f")
        assert list(inspect.signature(derive_acceptance).parameters) == \
            ["goal", "workspace", "baseline"]
        criteria, _specs = derive_acceptance(
            _declared("command_succeeds: ls"), str(ws), baseline=_red())
        assert not any(c.criteria_id.startswith("declared:") for c in criteria)

    def test_criteria_for_is_untouched(self):
        import inspect

        from wisp.core.convergence import criteria_for

        assert list(inspect.signature(criteria_for).parameters) == \
            ["specs", "baseline", "promote_absolute"]

    def test_the_flag_is_read_once_at_the_composition_point(self):
        repo = pathlib.Path(__file__).resolve().parents[2]
        readers = [p.relative_to(repo).as_posix()
                   for p in (repo / "wisp").rglob("*.py")
                   if "STRUCTURED_DECLARATION_ENV" in p.read_text(encoding="utf-8")]
        assert readers == ["wisp/autonomous.py"], (
            f"the flag is read in {readers}; it must be read once or two callers can disagree")


# ══════════════════════════════════════════════════════════════════════════
# R6 — the model gains no channel
# ══════════════════════════════════════════════════════════════════════════


class TestTheModelGainsNoChannel:
    def test_the_parser_takes_an_objective_and_a_workspace_only(self):
        """No provider, no model, no callable that could author a spec."""
        import inspect

        params = list(inspect.signature(parse_criteria_declaration).parameters)
        assert params == ["objective", "workspace"], (
            f"parse_criteria_declaration gained {params} — a model-authored declaration "
            "channel is what ADR-0045 R1 and ADR-0048 R6 forbid")

    def test_the_objective_is_supplied_by_the_caller(self):
        """Structural: `converge_on_objective` takes the objective as an argument."""
        import inspect

        from wisp.autonomous import converge_on_objective

        params = list(inspect.signature(converge_on_objective).parameters)
        assert params[0] == "objective_text", (
            "the objective must come from the caller, not be constructed from model output")

    def test_convergence_does_not_import_a_provider(self):
        repo = pathlib.Path(__file__).resolve().parents[2]
        text = (repo / "wisp/core/convergence.py").read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+wisp\.providers", text, re.M), (
            "convergence.py imports a provider — the derivation must read no model output")


# ══════════════════════════════════════════════════════════════════════════
# The wiring — driven, not assumed
# ══════════════════════════════════════════════════════════════════════════


class _StubRuntime:
    def __init__(self, events):
        self._events = events
        self.prompts: list[str] = []

    async def get_or_create_session(self, *, session_id, model, workspace):
        return {"id": session_id, "model": model, "workspace": workspace, "messages": []}

    async def run_turn(self, session, prompt, **kwargs):
        self.prompts.append(prompt)
        for event in self._events:
            yield event


class _StubRoot:
    def __init__(self, events):
        self.runtime = _StubRuntime(events)


def _drive(ws, objective, journal, *, flag: bool, monkeypatch=None):
    from wisp.autonomous import converge_on_objective

    if monkeypatch is not None:
        if flag:
            monkeypatch.setenv("WISP_CRITERIA_STRUCTURED_DECLARATION", "1")
        else:
            monkeypatch.delenv("WISP_CRITERIA_STRUCTURED_DECLARATION", raising=False)
    root = _StubRoot([{"type": "done"}])
    result = asyncio.run(converge_on_objective(
        objective, str(ws), root=root, max_attempts=1, journal_path=journal))
    return result, journal, root


class TestTheWiring:
    """`converge_on_objective` — the only production caller. F67's lesson."""

    def test_the_flag_off_path_is_inert(self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path / "w")
        _result, journal, root = _drive(
            ws, _declared("command_succeeds: ls"), tmp_path / "off.jsonl",
            flag=False, monkeypatch=monkeypatch)
        assert root.runtime.prompts, "no attempt ran"
        record = next(json.loads(x) for x in journal.read_text().splitlines()
                      if x.strip() and json.loads(x).get("kind") == "derivation")
        assert all(r["reason"] != DerivationReason.DECLARED.value
                   for r in record["reasons"]), (
            "the declared path ran with the flag OFF")

    def test_the_flag_on_path_reports_declared(self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path / "w")
        _result, journal, _root = _drive(
            ws, _declared("command_succeeds: ls"), tmp_path / "on.jsonl",
            flag=True, monkeypatch=monkeypatch)
        record = next(json.loads(x) for x in journal.read_text().splitlines()
                      if x.strip() and json.loads(x).get("kind") == "derivation")
        assert record["reasons"][0]["reason"] == DerivationReason.DECLARED.value

    def test_a_rejected_declaration_raises_and_is_journalled(self, tmp_path, monkeypatch):
        """R4 — loud, and recorded. Never a fallback."""
        ws = _workspace(tmp_path / "w")
        journal = tmp_path / "bad.jsonl"
        with pytest.raises(CriteriaDeclarationRejected) as exc:
            _drive(ws, _declared("make_it_green: yes"), journal,
                   flag=True, monkeypatch=monkeypatch)
        assert exc.value.reason == "unknown-kind"
        lines = [json.loads(x) for x in journal.read_text().splitlines() if x.strip()]
        kinds = [x.get("kind") for x in lines]
        assert "declaration_rejected" in kinds, (
            f"the rejection was not journalled; kinds={kinds}")
        rejection = next(x for x in lines if x.get("kind") == "declaration_rejected")
        assert rejection["reason"] == "unknown-kind"

    def test_a_rejected_declaration_does_not_fall_back(self, tmp_path, monkeypatch):
        """The property the whole design turns on: no prose criteria were produced."""
        ws = _workspace(tmp_path / "w")
        journal = tmp_path / "bad2.jsonl"
        with pytest.raises(CriteriaDeclarationRejected):
            _drive(ws, _declared("make_it_green: yes"), journal,
                   flag=True, monkeypatch=monkeypatch)
        lines = [json.loads(x) for x in journal.read_text().splitlines() if x.strip()]
        assert not any(x.get("kind") == "derivation" for x in lines), (
            "a declaration was rejected and the prose grammar ran anyway")

    def test_an_objective_without_a_declaration_is_unaffected_by_the_flag(
            self, tmp_path, monkeypatch):
        """R7 — the flag's blast radius is exactly the declared path."""
        ws = _workspace(tmp_path / "w")
        _r, off, _ = _drive(ws, MODE_A_OBJECTIVE, tmp_path / "a.jsonl",
                            flag=False, monkeypatch=monkeypatch)
        _r, on, _ = _drive(ws, MODE_A_OBJECTIVE, tmp_path / "b.jsonl",
                           flag=True, monkeypatch=monkeypatch)

        def reasons(path):
            rec = next(json.loads(x) for x in path.read_text().splitlines()
                       if x.strip() and json.loads(x).get("kind") == "derivation")
            return [(r["criteria_id"], r["reason"]) for r in rec["reasons"]]

        assert reasons(off) == reasons(on), (
            "the flag changed an objective that carries no declaration")
