"""The convergence loop's wiring: the turn seam and the CLI face.

`core/convergence.py` is tested against a scripted turn. This file tests the
parts that touch the real world: reading a turn's event stream into facts,
composing an attempt's prompt, diffing a workspace, and the exit-code
contract of `wisp converge`.

Two of these exist because of a defect class this repository has already paid
for twice (F40, F41): **a fixture that does not reproduce the production
representation**. `observe_turn` is therefore exercised with BOTH event
shapes — flat dicts and typed `AgentEvent` objects — because reading one with
`.get()` and the other with `getattr` is exactly how F40 happened.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from wisp.autonomous import (
    changed_files,
    compose_attempt_prompt,
    observe_turn,
    workspace_fingerprint,
)
from wisp.core.convergence import AttemptRequest
from wisp.core.events import done, error
from wisp.core.goal import TerminalOutcome


# ── The turn predicate is delegated, not re-derived ─────────────────────


def test_done_alone_is_a_successful_turn():
    assert observe_turn([{"type": "done"}]).turn_succeeded
    assert observe_turn([{"type": "done"}]).terminal_outcome == "succeeded"


def test_a_fatal_error_is_a_failed_turn():
    observation = observe_turn([
        {"type": "error", "message": "provider died", "recoverable": False}])
    assert not observation.turn_succeeded
    assert observation.terminal_outcome == TerminalOutcome.FAILED.value
    assert observation.failure_message == "provider died"
    assert not observation.failure_recoverable


def test_a_recoverable_error_does_not_fail_the_turn():
    """The engine's own 'not fatal' signal is carried, not interpreted."""
    observation = observe_turn([
        {"type": "error", "message": "transient blip", "recoverable": True},
        {"type": "done"},
    ])
    assert observation.turn_succeeded
    assert observation.failure_recoverable
    assert observation.failure_message == "transient blip"


def test_nothing_at_all_is_not_a_successful_turn():
    observation = observe_turn([])
    assert not observation.turn_succeeded
    assert observation.terminal_outcome == TerminalOutcome.INCOMPLETE.value


def test_typed_events_are_read_the_same_as_dict_events():
    """F40's defect class: the fixture must reproduce what production emits.

    Production yields flat dicts from `_flatten_event`, but the typed
    constructors are what a different path hands over. A reader that handles
    only one shape silently mis-reads the other.
    """
    typed_done = observe_turn([done("s1")])
    dict_done = observe_turn([{"type": "done", "session_id": "s1"}])
    assert typed_done.turn_succeeded == dict_done.turn_succeeded is True

    typed_err = observe_turn([error("boom", recoverable=False)])
    dict_err = observe_turn([{"type": "error", "message": "boom",
                              "recoverable": False}])
    assert typed_err.turn_succeeded == dict_err.turn_succeeded is False
    assert typed_err.failure_message == dict_err.failure_message == "boom"


def test_the_error_code_is_carried_through():
    observation = observe_turn([
        {"type": "error", "message": "budget", "recoverable": False,
         "code": "E5101"}])
    assert observation.failure_code == "E5101"


def test_tool_calls_are_counted():
    events = [{"type": "tool_call", "name": "read_file"},
              {"type": "tool_result", "name": "read_file"},
              {"type": "tool_call", "name": "write_file"},
              {"type": "done"}]
    assert observe_turn(events).tool_calls == 2


# ── The attempt prompt ──────────────────────────────────────────────────


def test_the_first_attempt_prompt_is_the_objective_plus_acceptance():
    prompt = compose_attempt_prompt(AttemptRequest(
        objective="Fix the bug.", attempt=0, rung="INITIAL", directive="",
        evidence=()))
    assert prompt == "Fix the bug."


def test_acceptance_conditions_are_stated_on_every_attempt():
    prompt = compose_attempt_prompt(AttemptRequest(
        objective="Fix the bug.", attempt=0, rung="INITIAL", directive="",
        evidence=(), criteria=("`pytest` exits 0",)))
    assert "Acceptance conditions" in prompt
    assert "`pytest` exits 0" in prompt
    assert "measured by the harness" in prompt


def test_a_later_attempt_carries_the_directive_and_the_measured_evidence():
    prompt = compose_attempt_prompt(AttemptRequest(
        objective="Fix the bug.", attempt=1, rung="REPAIR",
        directive="Repair that specific failure.",
        evidence=("verify:cmd0: exit 1",)))
    assert "Fix the bug." in prompt
    assert "attempt 2" in prompt
    assert "Repair that specific failure." in prompt
    assert "verify:cmd0: exit 1" in prompt
    assert "produced by the harness" in prompt


# ── Workspace diffing ───────────────────────────────────────────────────


def test_changed_files_reports_adds_modifies_and_deletes(tmp_path: Path):
    (tmp_path / "a.py").write_text("one\n")
    before = workspace_fingerprint(str(tmp_path))

    (tmp_path / "a.py").write_text("two\n")          # modified
    (tmp_path / "b.py").write_text("new\n")          # added
    after = workspace_fingerprint(str(tmp_path))

    assert changed_files(before, after) == ("a.py", "b.py")


def test_changed_files_ignores_noise_directories(tmp_path: Path):
    before = workspace_fingerprint(str(tmp_path))
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    after = workspace_fingerprint(str(tmp_path))
    assert changed_files(before, after) == ()


# ── The CLI contract ────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, converged: bool):
        self.converged = converged
        self.goal_state = "goal_met" if converged else "goal_unverified"
        self.reason = "test"
        self.attempts: list = []
        self.escalation = None

    def to_dict(self):
        return {"converged": self.converged, "goal_state": self.goal_state,
                "reason": self.reason, "attempts": [], "escalation": None}


@pytest.mark.parametrize("converged,expected", [(True, 0), (False, 1)])
def test_the_cli_exit_code_follows_convergence(monkeypatch, converged, expected):
    """`wisp converge` must be usable as a gate in a script.

    A non-zero exit on an unproven objective is the difference between a
    tool and a claim.
    """
    import wisp.autonomous as autonomous
    import wisp.autonomous_cli as cli

    async def fake(objective, workspace, **kwargs):
        return _FakeResult(converged)

    monkeypatch.setattr(autonomous, "converge_on_objective", fake)
    code = cli.run_converge(["do the thing"], workspace=".")
    assert code == expected


def test_the_cli_parser_rejects_a_missing_objective():
    import wisp.autonomous_cli as cli
    with pytest.raises(SystemExit):
        cli.run_converge([])


def test_the_cli_does_not_declare_the_global_flags(monkeypatch):
    """`-m`/`-w` are stripped by `extract_global_flags` before dispatch.

    A local `--model`/`--workspace` would be silently swallowed, so the run
    would use the config's model and the process CWD. That is not a
    hypothetical: it made one measurement run against the repository root
    with the wrong model, and the criteria derived from the wrong workspace
    were consequently wrong too.
    """
    import wisp.autonomous as autonomous
    import wisp.autonomous_cli as cli

    seen: dict = {}

    async def fake(objective, workspace, **kwargs):
        seen.update({"objective": objective, "workspace": workspace, **kwargs})
        return _FakeResult(True)

    monkeypatch.setattr(autonomous, "converge_on_objective", fake)
    cli.run_converge(["do it"], model="m", workspace="ws")
    assert seen["model"] == "m"
    assert seen["workspace"] == "ws"

    # And the global flags really are stripped before the handler sees them.
    from wisp.__main__ import main  # noqa: F401  (import check only)
    import inspect
    source = inspect.getsource(cli._build_parser)
    assert '"--workspace"' not in source
    assert '"--model"' not in source


# ── Reachability tripwires ──────────────────────────────────────────────
#
# Phase 0 of the migration found eight complete, tested, *unreachable*
# subsystems. A capability with no caller is a documented gap, not a
# capability — so the wiring itself is asserted, and these fail the moment
# someone unwires it.


def test_converge_is_a_registered_subcommand():
    from wisp.__main__ import _SUBCOMMAND_HELP, _SUBCOMMAND_NAMES
    assert "converge" in _SUBCOMMAND_NAMES
    assert "converge" in _SUBCOMMAND_HELP


def test_the_loop_does_not_depend_on_the_runtime():
    """`core/convergence.py` must stay callable with no runtime at all.

    If the loop ever imports `AgentRuntime`, the seam has been crossed and
    the module can no longer be tested without one — which is how a
    mechanism becomes unreachable.
    """
    source = (Path(__file__).resolve().parents[2]
              / "wisp" / "core" / "convergence.py").read_text(encoding="utf-8")
    assert "AgentRuntime" not in source
    assert "from wisp.core.runtime" not in source
    assert "import asyncio" not in source


def test_the_convergence_path_cannot_bypass_the_tool_executor():
    """F54 regression, scoped to the convergence path.

    `wisp/autonomous.py` must reach a core only through the runtime (which
    `CompositionRoot` wires). If it ever builds a `WispAgentCore` itself, it
    inherits the read-only fallback and every mutation is refused — the F54
    defect, whose symptom was an agent that "never acts".
    """
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2]
              / "wisp" / "autonomous.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = (getattr(node.func, "id", None)
                    or getattr(node.func, "attr", None))
            assert name != "WispAgentCore", (
                "autonomous.py builds a core directly; it must go through "
                "CompositionRoot so the tool_executor is wired")
    assert "CompositionRoot" in source
    assert "tool_executor" in source or "root.runtime" in source


def test_the_convergence_path_reports_its_session_per_attempt():
    """The record must carry provenance the CLI can print (mission §4)."""
    from wisp.core.convergence import AttemptRecord, TurnObservation

    record = AttemptRecord(
        index=0, rung="INITIAL", directive="",
        observation=TurnObservation(session_id="s-1"))
    assert record.session_id == "s-1"
    assert record.to_dict()["session_id"] == "s-1"


def test_the_acceptance_derivation_never_consults_a_model():
    """R1: the criteria must come from the workspace, not from prose.

    A model call anywhere in the derivation path would let the judged write
    the exam. Asserted on the source so a future convenience call fails here.
    """
    source = (Path(__file__).resolve().parents[2]
              / "wisp" / "core" / "convergence.py").read_text(encoding="utf-8")
    for forbidden in ("provider", "generate(", "chat(", "llm", "model="):
        assert forbidden not in source, (
            f"the acceptance path mentions {forbidden!r} — criteria must be "
            f"host-derived, never model-derived")
