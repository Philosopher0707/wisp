"""Compaction must not erase what the agent did.

Before this record, a session over 50 messages was replaced by the bare text `[Compacted N messages]` whenever no summarizer model was configured (the
default: `compaction_model` is empty), so the next turn could not know which files it had written or which tests it had run, and repeated work or claimed
results it no longer had. The record is built by the harness from the tool calls themselves, so it does not depend on a model.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from wisp.core.compaction_record import MAX_FILES, build_record
from wisp.core.runtime import AgentRuntime


def call(i, name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def result(i, text):
    return {"role": "tool", "tool_call_id": f"c{i}", "content": text}


def user(text):
    return {"role": "user", "content": text}


FAILED = "[exit code: 1]\nFAILED tests/test_a.py::test_x - assert 1 == 2"
GREEN = "3 passed in 0.1s"


class TestFiles:
    def test_written_and_edited_files_are_listed_once_in_order(self):
        msgs = [call(1, "write_file", path="src/a.py", content="x"), result(1, "ok"), call(2, "edit_file", path="src/b.py", old="1", new="2"), result(2, "ok"),
                call(3, "edit_file", path="src/a.py", old="x", new="y"), result(3, "ok")]
        rec = build_record(msgs)
        assert "src/a.py, src/b.py" in rec and rec.count("src/a.py") == 1

    def test_reading_is_not_editing(self):
        rec = build_record([call(1, "read_file", path="src/a.py"), result(1, "contents")])
        assert "src/a.py" not in rec

    def test_arguments_may_arrive_as_a_dict_or_as_broken_json(self):
        msgs = [{"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "write_file", "arguments": {"path": "d.py"}}}]}, result(1, "ok"),
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c2", "function": {"name": "write_file", "arguments": "{not json"}}]}, result(2, "ok")]
        assert "d.py" in build_record(msgs)

    def test_a_long_list_is_bounded_and_says_how_much_was_left_out(self):
        msgs = []
        for i in range(MAX_FILES + 25):
            msgs += [call(i, "write_file", path=f"f{i}.py", content=""), result(i, "ok")]
        rec = build_record(msgs)
        assert "and 25 more" in rec and len(rec) < 6000


class TestVerification:
    def test_a_failing_and_a_passing_run_are_recorded_with_their_result(self):
        msgs = [call(1, "run_bash", command="python -m pytest tests/test_a.py -q"), result(1, FAILED), call(2, "run_bash", command="ruff check ."), result(2, "All checks passed!")]
        rec = build_record(msgs)
        assert "python -m pytest tests/test_a.py -q" in rec and "FAILED" in rec
        assert "ruff check ." in rec and "passed" in rec

    @pytest.mark.parametrize("command", ["python -m pytest | tail -3", "echo done", "pytest || true", "pytest --collect-only"])
    def test_a_command_that_proves_nothing_is_not_listed_as_verification(self, command):
        rec = build_record([call(1, "run_bash", command=command), result(1, "whatever")])
        assert "Verification runs" not in rec

    def test_run_tests_counts_only_when_it_ran_something_and_nothing_failed(self):
        green = "## Test Results (3/3 passed)\n- Failed: 0, Errors: 0"
        empty = "## Test Results (0/0 passed)\n- Failed: 0, Errors: 0"
        assert "run_tests" in build_record([call(1, "run_tests", path="tests"), result(1, green)])
        assert "Verification runs" not in build_record([call(1, "run_tests", path="tests"), result(1, empty)])

    def test_a_run_followed_by_an_edit_is_marked_as_possibly_out_of_date(self):
        msgs = [call(1, "run_bash", command="pytest -q"), result(1, GREEN), call(2, "edit_file", path="a.py", old="a", new="b"), result(2, "ok")]
        assert "edited after this run" in build_record(msgs)

    def test_a_run_after_the_last_edit_is_not_marked(self):
        msgs = [call(1, "edit_file", path="a.py", old="a", new="b"), result(1, "ok"), call(2, "run_bash", command="pytest -q"), result(2, GREEN)]
        assert "edited after this run" not in build_record(msgs)

    def test_the_record_tells_the_next_turn_not_to_treat_old_results_as_current(self):
        rec = build_record([call(1, "run_bash", command="pytest -q"), result(1, GREEN)])
        assert "run it again" in rec.lower()


class TestRequests:
    def test_what_the_user_asked_survives_trimmed(self):
        rec = build_record([user("please fix the login bug " + "x" * 1000), {"role": "assistant", "content": "ok"}, user("and add a test")])
        assert "fix the login bug" in rec and "and add a test" in rec and len(rec) < 2000

    def test_only_the_most_recent_requests_are_kept(self):
        rec = build_record([user(f"request number {i}") for i in range(12)])
        assert "request number 11" in rec and "request number 7" in rec
        assert "request number 6" not in rec and "request number 0" not in rec

    def test_nothing_to_record_gives_an_empty_string(self):
        assert build_record([{"role": "assistant", "content": "hello"}, call(1, "read_file", path="a"), result(1, "x")]) == ""


class _Compactor:
    def __init__(self, summary, fallback):
        self._r = SimpleNamespace(summary=summary, fallback_truncation=fallback)

    async def compact(self, messages, keep_recent):
        return self._r


@pytest.fixture
def runtime(tmp_path):
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import PermissionMode, SecurityPolicy
    from wisp.infra.store import UnifiedStore
    from wisp.infra.telemetry import Telemetry

    class _Core:
        async def turn(self, *a, **k):
            yield {"type": "done"}

    return AgentRuntime(store=UnifiedStore(tmp_path / "t.db"), security=SecurityPolicy(permission_mode=PermissionMode.FULL), extensions=ExtensionHost(), telemetry=Telemetry(),
                        core_factory=lambda: _Core())


def long_session(runtime_session):
    msgs = [user("make the parser faster")]
    msgs += [call(1, "write_file", path="src/parser.py", content="x"), result(1, "ok"), call(2, "run_bash", command="python -m pytest tests/test_parser.py -q"), result(2, FAILED)]
    for i in range(10, 60):
        msgs += [{"role": "assistant", "content": f"thinking {i}"}, user(f"continue {i}")]
    runtime_session["messages"] = msgs
    return runtime_session


class TestRuntimeCompaction:
    @pytest.mark.asyncio
    async def test_without_a_summarizer_the_session_keeps_the_record_and_says_the_conversation_is_gone(self, runtime):
        session = long_session(await runtime.get_or_create_session("s1", "m", "/tmp"))
        await runtime.maybe_compact(session, max_messages=20)
        text = "\n".join(m["content"] for m in session["messages"] if m["role"] == "system")
        assert "src/parser.py" in text and "python -m pytest tests/test_parser.py -q" in text and "FAILED" in text
        assert "no summarizer" in text.lower()

    @pytest.mark.asyncio
    async def test_a_failing_summarizer_also_leaves_the_record(self, runtime):
        runtime.compactor = _Compactor("", True)
        session = long_session(await runtime.get_or_create_session("s1", "m", "/tmp"))
        await runtime.maybe_compact(session, max_messages=20)
        text = "\n".join(m["content"] for m in session["messages"] if m["role"] == "system")
        assert "src/parser.py" in text and "no summarizer" in text.lower()

    @pytest.mark.asyncio
    async def test_a_model_summary_gets_the_record_appended_not_replaced(self, runtime):
        runtime.compactor = _Compactor("The user wants a faster parser; decided to keep the tokenizer.", False)
        session = long_session(await runtime.get_or_create_session("s1", "m", "/tmp"))
        await runtime.maybe_compact(session, max_messages=20)
        text = "\n".join(m["content"] for m in session["messages"] if m["role"] == "system")
        assert "keep the tokenizer" in text and "src/parser.py" in text and "no summarizer" not in text.lower()

    @pytest.mark.asyncio
    async def test_messages_that_are_kept_are_not_recorded_twice(self, runtime):
        session = await runtime.get_or_create_session("s1", "m", "/tmp")
        msgs = [user(f"old {i}") for i in range(40)]
        msgs += [call(1, "write_file", path="recent.py", content=""), result(1, "ok")]
        session["messages"] = msgs
        await runtime.maybe_compact(session, max_messages=20)
        summaries = "\n".join(m["content"] for m in session["messages"] if m["role"] == "system")
        assert "recent.py" not in summaries
        assert any(m.get("tool_calls") for m in session["messages"])
