"""TurnReasoning and shell_write_paths in isolation (the live-engine behaviour is in test_seam.py)."""

from __future__ import annotations

import pytest

from wisp.core.reasoning import runtime as rt
from wisp.core.reasoning.decision import Action, Mode
from wisp.core.reasoning.shellwrites import shell_write_paths


def ok(name, **kw):
    return {"type": "tool_result", "name": name, "result": {"status": "ok", "data": "x"}, "tool_call_id": kw.pop("cid", "c1"), **kw}


def denied(rule="OUTSIDE_WORKSPACE"):
    return {"type": "tool_result", "name": "run_bash", "tool_call_id": "c2",
            "result": {"status": "POLICY_DENIED", "authorized": False, "executed": False, "reason": f"[Blocked by the harness gate: [path:{rule}] x. Nothing was run.]"}}


class TestShellWrites:
    @pytest.mark.parametrize("cmd,expected", [
        ("sed -i 's/a/b/' src/x.py", ("src/x.py",)), ("echo hi > out.txt", ("out.txt",)), ("cat x > /dev/null", ()), ("ls -la", ()), ("pytest -q 2>&1 | tail", ()),
        ("'unterminated", ()), ("", ()), ("cd sub && sed -i s/a/b/ f.py", ("f.py",)), ("rm -rf build", ("build",)),
    ])
    def test_known_commands(self, cmd, expected):
        assert shell_write_paths(cmd) == expected

    def test_never_raises(self):
        for bad in [None, 5, "\x00", "a" * 50_000, "$(" * 500]:
            assert isinstance(shell_write_paths(bad), tuple)  # type: ignore[arg-type]


class TestTurnReasoning:
    def test_a_verification_run_that_logs_to_a_file_is_still_a_run(self):
        t = rt.TurnReasoning(Mode.OBSERVE)
        t.observe_tool_result(ok("run_bash"), "5 passed", {"command": "pytest -q > log.txt"})
        assert t.ledger.mutation_count == 0 and t.ledger.runs()

    def test_a_non_verification_shell_write_is_a_mutation(self):
        t = rt.TurnReasoning(Mode.OBSERVE)
        t.observe_tool_result(ok("run_bash"), "", {"command": "echo x > out.txt"})
        assert t.ledger.mutation_count == 1

    def test_a_failed_shell_command_is_not_assumed_to_have_written(self):
        t = rt.TurnReasoning(Mode.OBSERVE)
        t.observe_tool_result({"type": "tool_result", "name": "run_bash", "tool_call_id": "c", "result": {"status": "error", "data": "boom"}}, None, {"command": "echo x > out.txt"})
        assert t.ledger.mutation_count == 0

    def test_the_same_gate_rule_twice_is_journaled_as_a_nudge(self):
        t = rt.TurnReasoning(Mode.OBSERVE)
        assert t.observe_refusal(denied(), {"command": "x"}).action is Action.CONTINUE
        d = t.observe_refusal(denied(), {"command": "y"})
        assert d.action is Action.NUDGE and "OUTSIDE_WORKSPACE" in d.note
        assert t.journal[-1]["rule"] == "R3" and t.journal[-1]["evidence_ids"]

    def test_the_same_failure_three_times_escalates(self):
        t = rt.TurnReasoning(Mode.OBSERVE)
        bad = lambda i: {"type": "tool_result", "name": "run_bash", "tool_call_id": f"c{i}", "result": {"status": "error", "data": f"ImportError at line {i}"}}  # noqa: E731
        acts = [t.observe_tool_result(bad(i), f"ImportError at line {i}", {"command": "python x.py"}).action for i in range(3)]
        assert acts == [Action.CONTINUE, Action.NUDGE, Action.ESCALATE]

    def test_a_provider_affordability_error_plans_one_retry(self):
        t = rt.TurnReasoning(Mode.OBSERVE)
        d = t.observe_provider_error("402: you can only afford 5403", 16384)
        assert d.action is Action.RETRY_REQUEST and d.max_tokens == 5339

    def test_a_broken_provider_error_path_degrades(self, monkeypatch):
        monkeypatch.setattr(rt, "plan_request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        t = rt.TurnReasoning(Mode.OBSERVE)
        assert t.observe_provider_error("can only afford 100", 4096) is None
        assert t.journal[-1]["action"] == "core_error" and t.journal[-1]["seam"] == "provider_error"

    def test_junk_events_never_raise(self):
        t = rt.TurnReasoning(Mode.ENFORCE)
        for junk in [{}, {"name": None}, {"result": 5}, {"type": "tool_result", "name": "x", "result": object()}]:
            t.observe_tool_result(junk, object(), None)
        t.observe_final(None)  # type: ignore[arg-type]
        t.observe_provider_error(None, None)  # type: ignore[arg-type]

    def test_the_journal_is_append_only_and_sequenced(self):
        t = rt.TurnReasoning(Mode.OBSERVE)
        t.observe_provider_error("can only afford 5403", 16384)
        t.observe_final("All tests pass.")
        assert [r["seq"] for r in t.journal] == list(range(1, len(t.journal) + 1))
        assert isinstance(t.journal, tuple)
