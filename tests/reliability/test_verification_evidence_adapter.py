"""The verification-evidence adapter, asserted through the live turn path.

Why this file exists
--------------------
`VerificationFloorGuard` classifies a shell tool's **output text**, and the
shell success encoding is positional — `tools/bash.py::_format_bash_output`
emits `[exit code: N]` as the FIRST thing on the line, and only for a non-zero
exit. `core/verification.py::_verify_result_is_success` tests exactly that
position, and `tests/test_verification_contract.py` pins the two together.

Every one of those unit tests feeds the guard the **formatted output**. The one
production call site did not: it handed over `result_event["result"]`, which is
the executor's JSON **envelope**, so the text began `{"status": "ok" …` and the
positional test never fired. A mutation followed by a command that exited 3 was
recorded as P3 `pass` / `goal_met`.

These tests drive the **live runtime** and assert the end of that chain, which
is the only place the defect was ever observable. They fail against the
pre-repair adapter — see the phase report's non-vacuity section, where the old
expression was restored in an isolated probe and every one of them went red.

They are deliberately end-to-end rather than unit-level: a unit test of
`_verify_result_is_success` cannot detect a caller that passes the wrong
argument, which is precisely why the defect survived a green suite.
"""
from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.core.runtime import AgentRuntime
from wisp.core.session_repo import SessionRepository
from wisp.core.verification import VerificationFloorGuard, _VERIFY_FAILURE_PREFIX
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import PermissionMode, SecurityPolicy
from wisp.infra.store import UnifiedStore
from wisp.infra.telemetry import Telemetry
from wisp.tool_executor import ToolExecutor


class _Provider:
    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages, tools=None):
        self.calls += 1
        yield from self._rounds[min(self.calls - 1, len(self._rounds) - 1)]()


def _tool_round(name, args, cid):
    def _g():
        yield {"type": "tool_calls",
               "calls": [{"id": cid, "type": "function",
                          "function": {"name": name, "arguments": args}}]}
        yield {"type": "done", "done_reason": "tool_calls"}
    return _g


def _content_round(text="done"):
    def _g():
        yield {"type": "content", "text": text}
        yield {"type": "done", "done_reason": "stop"}
    return _g


class _Turn:
    """One live turn: the emitted events, the guard, and the recorded goal state."""

    def __init__(self, events, guard, record):
        self.events = events
        self.guard = guard
        self.record = record

    @property
    def acceptance(self):
        return self.record.get("acceptance_verdict")

    @property
    def goal(self):
        return self.record.get("goal_state")


def _run_turn(ws, rounds, *, mode=PermissionMode.ASK_ALL, sid="vea", goal_state=True):
    config = WispConfig().replace(workspace=str(ws), permission_mode=mode,
                                  goal_state=goal_state, max_iterations=6)
    store = UnifiedStore(ws / f"{sid}.db")
    repo = SessionRepository(store)
    provider = _Provider(rounds)
    executor = ToolExecutor(config)
    holder: dict = {}

    def factory():
        core = WispAgentCore(config=config, provider=provider,
                             security=SecurityPolicy(permission_mode=mode),
                             tool_executor=executor)
        holder["core"] = core
        return core

    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(permission_mode=mode),
        extensions=ExtensionHost(), telemetry=Telemetry(),
        core_factory=factory, session_repo=repo, config=config)
    session = {"id": sid, "model": "mock", "workspace": str(ws), "messages": []}

    async def approve(event, *a, **kw):
        return True

    async def _main():
        return [ev async for ev in runtime.run_turn(
            session, prompt="go", approval_handler=approve)]

    events = asyncio.run(_main())
    guard = getattr(holder.get("core"), "_last_guard", None)
    records = (repo.reconstruct(sid).get("_journal") or {}).get("goal_states") or []
    return _Turn(events, guard, records[0] if records else {})


def _mutate_then(ws, name, args, *, sid):
    """A real mutation followed by one real tool call, then a final summary."""
    return _run_turn(
        ws,
        [_tool_round("write_file",
                     {"path": str(ws / f"{sid}.txt"), "content": "x"}, "c0"),
         _tool_round(name, args, "c1"),
         _content_round()],
        sid=sid)


# ══════════════════════════════════════════════════════════════════════════
# 1. The critical regression — a genuinely failing verification
# ══════════════════════════════════════════════════════════════════════════


class TestAFailingVerificationIsNotSuccess:
    def test_the_command_really_failed(self, tmp_path):
        """Precondition: exit 3, not a harness artefact."""
        turn = _mutate_then(tmp_path, "run_bash", {"command": "exit 3"}, sid="fail")
        envelope = None
        for ev in turn.events:
            if ev.get("type") == "tool_result" and ev.get("name") == "run_bash":
                envelope = ev.get("result")
        assert envelope is not None, "run_bash produced no tool_result event"
        parsed = json.loads(envelope)
        assert parsed["status"] == "ok", "the TOOL succeeded; the COMMAND did not"
        assert parsed["metadata"]["exit_code"] == 3
        assert parsed["data"].startswith(_VERIFY_FAILURE_PREFIX)

    def test_the_guard_records_a_failure_not_a_success(self, tmp_path):
        turn = _mutate_then(tmp_path, "run_bash", {"command": "exit 3"}, sid="fail")
        assert turn.guard is not None
        assert turn.guard.wrote_code is True
        assert turn.guard.verify_ok_after_edit is False, (
            "a command that exited 3 was recorded as verified")
        assert turn.guard.resolved() is False

    def test_p3_is_not_pass_and_the_goal_is_not_met(self, tmp_path):
        turn = _mutate_then(tmp_path, "run_bash", {"command": "exit 3"}, sid="fail")
        assert turn.acceptance != "pass", "a failing verification produced P3 pass"
        assert turn.acceptance == "fail"
        assert turn.goal != "goal_met", "a failing verification produced GOAL_MET"
        assert turn.goal == "goal_failed"


# ══════════════════════════════════════════════════════════════════════════
# 2. The success control — the repair must not fail everything
# ══════════════════════════════════════════════════════════════════════════


class TestASuccessfulVerificationStillVerifies:
    def test_the_guard_records_success(self, tmp_path):
        turn = _mutate_then(tmp_path, "run_bash", {"command": "echo fine"}, sid="ok")
        assert turn.guard.wrote_code is True
        assert turn.guard.verify_ok_after_edit is True
        assert turn.guard.resolved() is True

    def test_p3_passes_and_the_goal_is_met(self, tmp_path):
        turn = _mutate_then(tmp_path, "run_bash", {"command": "echo fine"}, sid="ok")
        assert turn.acceptance == "pass"
        assert turn.goal == "goal_met"


# ══════════════════════════════════════════════════════════════════════════
# 3. The Non-OK Envelope Rule — reachable refusal shapes are not evidence
# ══════════════════════════════════════════════════════════════════════════


class TestARefusedVerificationIsNotEvidence:
    """Three shapes reach this boundary; none of them is verification.

    Each was confirmed reachable by the phase's probe. They are different
    values — a non-`ok` envelope, and a bare block message — which is why the
    adapter cannot simply stringify whatever arrives.
    """

    def test_a_tool_level_error_envelope_is_not_evidence(self, tmp_path):
        """A `run_bash` that times out returns a non-`ok` envelope."""
        turn = _mutate_then(tmp_path, "run_bash",
                            {"command": "sleep 5", "timeout": 1}, sid="timeout")
        assert turn.guard.verify_ok_after_edit is not True, (
            "a timed-out verification was recorded as verified")
        assert turn.guard.resolved() is False
        assert turn.acceptance == "fail"
        assert turn.goal == "goal_failed"

    def test_a_blocked_command_message_is_not_evidence(self, tmp_path):
        """A dangerous command is refused with a bare message, not an envelope."""
        turn = _mutate_then(tmp_path, "run_bash",
                            {"command": "rm -rf / --no-preserve-root"}, sid="danger")
        assert turn.guard.verify_ok_after_edit is not True, (
            "a blocked verification was recorded as verified")
        assert turn.guard.resolved() is False
        assert turn.acceptance == "fail"

    def test_a_pre_dispatch_denial_keeps_its_existing_contract(self, tmp_path):
        """AUTO_EDIT denies run_bash before dispatch — unchanged by this repair.

        The call never reaches the evidence fold, so the guard has no
        verification outcome at all. This is the contract the Non-OK rule is
        written to preserve, and it must stay exactly as it was.
        """
        turn = _run_turn(
            tmp_path,
            [_tool_round("write_file",
                         {"path": str(tmp_path / "denied.txt"), "content": "x"}, "c0"),
             _tool_round("run_bash", {"command": "echo nope"}, "c1"),
             _content_round()],
            mode=PermissionMode.AUTO_EDIT, sid="denied")
        assert turn.guard.wrote_code is True
        assert turn.guard.verify_ok_after_edit is None, (
            "a denied verification must leave no verdict, as before")
        assert turn.guard.resolved() is False
        assert turn.acceptance == "fail"


# ══════════════════════════════════════════════════════════════════════════
# 4. The adapter's contract — what the authority is handed
# ══════════════════════════════════════════════════════════════════════════


class TestTheAuthorityReceivesTheFormattedOutput:
    def test_a_failing_shell_result_arrives_as_text_not_an_envelope(
            self, tmp_path, monkeypatch):
        seen: list[tuple[str, object]] = []
        original = VerificationFloorGuard.note_tool_result

        def spy(self, name, result_text, args=None):
            seen.append((name, result_text))
            return original(self, name, result_text, args)

        monkeypatch.setattr(VerificationFloorGuard, "note_tool_result", spy)
        _mutate_then(tmp_path, "run_bash", {"command": "exit 3"}, sid="contract")

        shell = [text for name, text in seen if name == "run_bash"]
        assert shell, "run_bash never reached the verification authority"
        text = shell[-1]
        assert isinstance(text, str)
        assert text.startswith(_VERIFY_FAILURE_PREFIX), (
            "the authority was handed a representation whose failure marker is "
            f"not positional: {text[:80]!r}")
        assert not text.lstrip().startswith("{"), (
            "the authority was handed the JSON envelope, not the output")

    def test_a_successful_shell_result_arrives_as_text(self, tmp_path, monkeypatch):
        seen: list[tuple[str, object]] = []
        original = VerificationFloorGuard.note_tool_result

        def spy(self, name, result_text, args=None):
            seen.append((name, result_text))
            return original(self, name, result_text, args)

        monkeypatch.setattr(VerificationFloorGuard, "note_tool_result", spy)
        _mutate_then(tmp_path, "run_bash", {"command": "echo fine"}, sid="contract_ok")

        text = [t for name, t in seen if name == "run_bash"][-1]
        assert isinstance(text, str)
        assert not text.lstrip().startswith("{")
        assert "fine" in text


# ══════════════════════════════════════════════════════════════════════════
# 5. Collateral boundaries — the repair changes nothing else
# ══════════════════════════════════════════════════════════════════════════


class TestTheRepairChangesNothingElse:
    def test_a_failed_mutation_still_counts_as_an_attempted_mutation(self, tmp_path):
        """The guard classifies mutating tools by NAME, not by their text.

        A write that fails at the tool level must therefore still set
        `wrote_code` — otherwise the turn would move from `FAIL` to
        `INCONCLUSIVE`, which is a semantic change this repair must not make.
        """
        turn = _run_turn(
            tmp_path,
            [_tool_round("write_file",
                         {"path": str(tmp_path), "content": "x"}, "c0"),
             _content_round()],
            sid="badwrite")
        assert turn.guard.wrote_code is True, (
            "a failed mutation stopped counting as a mutation")
        assert turn.guard.verify_ok_after_edit is None
        assert turn.acceptance == "fail"
        assert turn.goal == "goal_failed"

    def test_a_read_only_turn_is_untouched(self, tmp_path):
        (tmp_path / "seed.txt").write_text("seed\n")
        turn = _run_turn(
            tmp_path,
            [_tool_round("read_file", {"path": "seed.txt"}, "c0"),
             _content_round()],
            sid="readonly")
        assert turn.guard.wrote_code is False
        assert turn.guard.resolved() is False

    def test_the_verification_authority_is_still_the_only_writer(self):
        """No second writer appeared: `note_tool_result` has one caller."""
        import ast
        import pathlib as _p
        source = (_p.Path(__file__).resolve().parents[2]
                  / "wisp" / "core" / "stateless.py").read_text()
        tree = ast.parse(source)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "note_tool_result"
        ]
        assert len(calls) == 1, (
            f"expected exactly one note_tool_result call site, found {len(calls)}")
