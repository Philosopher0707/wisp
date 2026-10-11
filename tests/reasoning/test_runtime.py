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

    def test_a_shell_command_that_exits_nonzero_is_a_failure_even_though_the_tool_succeeded(self):
        t = rt.TurnReasoning(Mode.OBSERVE)
        out = "[exit code: 1]\nImportError: nope"
        acts = [t.observe_tool_result(ok("run_bash", cid=f"c{i}"), out, {"command": "python3 x.py"}).action for i in range(3)]
        assert acts == [Action.CONTINUE, Action.NUDGE, Action.ESCALATE]

    def test_a_successful_shell_command_is_not_a_failure(self):
        t = rt.TurnReasoning(Mode.OBSERVE)
        assert t.observe_tool_result(ok("run_bash"), "fine", {"command": "ls"}) is None

    def test_a_provider_affordability_error_plans_one_retry(self):
        t = rt.TurnReasoning(Mode.OBSERVE)
        d = t.observe_provider_error("402: you can only afford 5403", 16384)
        assert d.action is Action.RETRY_REQUEST and d.max_tokens == 5339

    def test_an_enforced_decision_is_handed_over_exactly_once(self):
        t = rt.TurnReasoning(Mode.ENFORCE)
        t.observe_provider_error("402: can only afford 5403", 16384)
        assert t.take_enforced("provider_error").action is Action.RETRY_REQUEST
        assert t.take_enforced("provider_error") is None

    def test_nothing_is_handed_over_in_observe(self):
        t = rt.TurnReasoning(Mode.OBSERVE)
        t.observe_provider_error("402: can only afford 5403", 16384)
        assert t.take_enforced("provider_error") is None and not any(r.get("applied") for r in t.journal)

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


class TestJournalFile:
    def test_rows_are_appended_as_json_lines(self, tmp_path):
        import json

        path = tmp_path / "j.jsonl"
        t = rt.TurnReasoning(Mode.OBSERVE, journal_path=str(path))
        t.observe_provider_error("402: can only afford 5403", 16384)
        t.observe_final("All tests pass.")
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1)) and {r["rule"] for r in rows if r.get("rule")} == {"R1", "R4"}
        assert rows == [json.loads(json.dumps(r, default=str)) for r in t.journal]

    def test_no_path_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rt.TurnReasoning(Mode.OBSERVE).observe_provider_error("can only afford 5403", 16384)
        assert list(tmp_path.iterdir()) == []

    def test_an_unwritable_path_never_fails_the_turn(self, tmp_path):
        t = rt.TurnReasoning(Mode.OBSERVE, journal_path=str(tmp_path / "missing-dir" / "j.jsonl"))
        assert t.observe_provider_error("can only afford 5403", 16384).action is Action.RETRY_REQUEST
        assert t.journal and not any(r["action"] == "core_error" for r in t.journal)

    def test_a_core_error_is_persisted_too(self, tmp_path, monkeypatch):
        path = tmp_path / "j.jsonl"
        monkeypatch.setattr(rt, "plan_request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        rt.TurnReasoning(Mode.OBSERVE, journal_path=str(path)).observe_provider_error("can only afford 100", 4096)
        assert '"core_error"' in path.read_text()

    def test_the_setting_reaches_the_engine(self, tmp_path, monkeypatch):
        from tests.reasoning import personas as P

        monkeypatch.setenv("WISP_REASONING_JOURNAL", str(tmp_path / "live.jsonl"))
        o = P.run(next(p for p in P.PERSONAS if p.name == "HitsARecoverableLimit"), "observe", tmp_path / "w")
        assert o.journal and (tmp_path / "live.jsonl").read_text().count("\n") == len(o.journal)
