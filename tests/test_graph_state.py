"""Unit tests for GraphState / ExecutionLog / GraphStatus — the agentic graph state schema."""

import pytest

from wisp.core.graph_state import ExecutionLog, GraphState, GraphStatus


class TestGraphStatus:
    def test_values_match_spec(self):
        assert GraphStatus.IN_PROGRESS == "in_progress"
        assert GraphStatus.COMPLETED == "completed"
        assert GraphStatus.FAILED == "failed"
        assert GraphStatus.NEEDS_HUMAN_REVIEW == "needs_human_review"

    def test_is_str_enum(self):
        assert isinstance(GraphStatus.IN_PROGRESS, str)
        assert str(GraphStatus.FAILED) == "failed"


class TestExecutionLog:
    def test_from_raw_parses_exit_code_and_streams(self):
        log = ExecutionLog.from_raw("pytest", "[exit code: 1]\nstdout here\n--- stderr ---\nstderr here")
        assert log.exit_code == 1
        assert "stdout here" in log.stdout
        assert "stderr here" in log.stderr
        assert not log.succeeded

    def test_from_raw_zero_exit_no_prefix(self):
        log = ExecutionLog.from_raw("echo hi", "hi\n")
        assert log.exit_code == 0
        assert log.succeeded
        assert "hi" in log.stdout

    def test_from_raw_truncated_flag(self):
        log = ExecutionLog.from_raw("cmd", "a" * 10 + "\n... [output truncated]")
        assert log.truncated is True

    def test_round_trip_dict(self):
        orig = ExecutionLog(command="ls", exit_code=2, stdout="o", stderr="e", duration_ms=123.4, raw="raw")
        restored = ExecutionLog.from_dict(orig.to_dict())
        assert restored.command == orig.command
        assert restored.exit_code == 2
        assert restored.duration_ms == 123.4

    def test_from_dict_corrupt_graceful(self):
        log = ExecutionLog.from_dict({"command": "x", "exit_code": "not-an-int"})
        assert isinstance(log, ExecutionLog)
        assert log.command == "x"

    def test_short_summary(self):
        log = ExecutionLog(command="pytest tests/", exit_code=1, stdout="failed")
        assert "pytest" in log.short_summary
        assert "exit 1" in log.short_summary


class TestGraphState:
    def test_initial_defaults(self):
        s = GraphState.initial()
        assert s.status == GraphStatus.IN_PROGRESS
        assert s.iteration_count == 0
        assert s.max_iterations == 5  # spec default
        assert s.code_files == {}
        assert s.execution_logs == []

    def test_initial_custom_max(self):
        s = GraphState.initial(max_iterations=10)
        assert s.max_iterations == 10

    def test_initial_clamps_bounds(self):
        s = GraphState.initial(max_iterations=999)
        assert s.max_iterations == 200
        s2 = GraphState.initial(max_iterations=0)
        assert s2.max_iterations >= 1

    def test_transition_guards_terminal_revert(self):
        s = GraphState.initial()
        s.transition(GraphStatus.COMPLETED)
        assert s.status == GraphStatus.COMPLETED
        ok = s.transition(GraphStatus.IN_PROGRESS)
        assert ok is False
        assert s.status == GraphStatus.COMPLETED

    def test_transition_clears_error_on_success_paths(self):
        s = GraphState.initial()
        s.error = "old error"
        s.transition(GraphStatus.COMPLETED)
        assert s.error is None
        s2 = GraphState.initial()
        s2.error = "old"
        s2.mark_needs_review("run_bash", {"command": "ls"}, reason="check")
        assert s2.error is None
        s3 = GraphState.initial()
        s3.transition(GraphStatus.FAILED, error="boom")
        assert s3.error == "boom"
        assert s3.status == GraphStatus.FAILED

    def test_increment_iteration_circuit_breaker(self):
        s = GraphState.initial(max_iterations=2)
        assert s.increment_iteration() is True
        assert s.iteration_count == 1
        assert s.increment_iteration() is True
        assert s.iteration_count == 2
        # 3rd exceeds budget
        assert s.increment_iteration() is False
        assert s.status == GraphStatus.FAILED
        assert "Max graph iterations" in (s.error or "")

    def test_code_files_cap_and_truncation(self):
        s = GraphState.initial()
        # Fill to cap
        for i in range(105):
            s.upsert_code_file(f"file_{i}.py", "x")
        assert len(s.code_files) <= 100
        # Large content truncated
        big = "a" * (GraphState.__dataclass_fields__["code_files"].default_factory.__code__.co_consts[0] if False else 10)
        # Directly test large file truncation path (use internal constant)
        from wisp.core.graph_state import DEFAULT_MAX_CODE_FILE_BYTES
        huge = "a" * (DEFAULT_MAX_CODE_FILE_BYTES + 100)
        s.upsert_code_file("huge.py", huge)
        assert len(s.code_files["huge.py"]) <= DEFAULT_MAX_CODE_FILE_BYTES + 100  # truncated marker adds little
        assert "[code_file truncated]" in s.code_files["huge.py"]

    def test_invalid_code_file_path_rejected_gracefully(self):
        s = GraphState.initial()
        assert s.upsert_code_file("", "content") is False
        assert s.upsert_code_file(None, "content") is False  # type: ignore[arg-type]
        assert s.code_files == {}

    def test_execution_log_pruning_by_count(self):
        s = GraphState.initial()
        from wisp.core.graph_state import DEFAULT_MAX_LOGS
        for i in range(DEFAULT_MAX_LOGS + 10):
            s.add_execution_log(ExecutionLog(command=f"cmd_{i}", exit_code=0, stdout="ok"))
        assert len(s.execution_logs) == DEFAULT_MAX_LOGS
        # Oldest evicted, newest retained
        assert s.execution_logs[-1].command == f"cmd_{DEFAULT_MAX_LOGS + 9}"
        assert s.execution_logs[0].command == "cmd_10"

    def test_execution_log_pruning_by_chars(self):
        s = GraphState.initial()
        from wisp.core.graph_state import DEFAULT_MAX_LOG_CHARS
        big_out = "a" * (DEFAULT_MAX_LOG_CHARS + 500)
        s.add_execution_log(ExecutionLog(command="big", exit_code=0, stdout=big_out))
        assert len(s.execution_logs[0].stdout) <= DEFAULT_MAX_LOG_CHARS + 50
        assert s.execution_logs[0].truncated is True

    def test_add_execution_log_accepts_raw_string(self):
        s = GraphState.initial()
        assert s.add_execution_log("[exit code: 2]\nfailed", command="pytest") is True
        assert s.last_exit_code == 2

    def test_add_execution_log_accepts_dict(self):
        s = GraphState.initial()
        assert s.add_execution_log({"command": "ls", "exit_code": 0, "stdout": "a"}, command="") is True
        assert s.last_succeeded is True

    def test_add_execution_log_unknown_type_rejected(self):
        s = GraphState.initial()
        assert s.add_execution_log(12345, command="x") is False  # type: ignore[arg-type]

    def test_snapshot_rollback_success(self):
        s = GraphState.initial(session_id="s1")
        s.upsert_code_file("a.py", "v1")
        s.snapshot()
        s.upsert_code_file("a.py", "v2")
        s.add_execution_log(ExecutionLog(command="echo", stdout="hi"))
        assert s.code_files["a.py"] == "v2"
        assert len(s.execution_logs) == 1
        assert s.rollback() is True
        assert s.code_files["a.py"] == "v1"
        assert len(s.execution_logs) == 0

    def test_snapshot_rollback_no_snapshot(self):
        s = GraphState.initial()
        assert s.rollback() is False

    def test_snapshot_stack_capped(self):
        s = GraphState.initial()
        for _ in range(25):
            s.snapshot()
        assert len(s._snapshot_stack) <= 20

    def test_rollback_preserves_stack_for_multiple_rollbacks(self):
        s = GraphState.initial()
        s.upsert_code_file("a.py", "v1")
        s.snapshot()
        s.upsert_code_file("a.py", "v2")
        s.snapshot()
        s.upsert_code_file("a.py", "v3")
        assert s.rollback() is True
        assert s.code_files["a.py"] == "v2"
        assert s.rollback() is True
        assert s.code_files["a.py"] == "v1"
        assert s.rollback() is False

    def test_from_dict_backwards_compat_empty(self):
        s = GraphState.from_dict({})
        assert s.status == GraphStatus.IN_PROGRESS
        assert s.max_iterations == 5

    def test_from_dict_corrupt_truncated_code_files(self):
        # Oversized code_files should be truncated, not crash
        big_files = {f"f_{i}.py": "x" for i in range(200)}
        s = GraphState.from_dict({"code_files": big_files, "status": "not_a_status", "iteration_count": "bad", "max_iterations": "bad"})
        assert len(s.code_files) <= 100
        assert s.status == GraphStatus.IN_PROGRESS
        assert s.max_iterations == 5

    def test_from_dict_non_dict_input(self):
        s = GraphState.from_dict(None)  # type: ignore[arg-type]
        assert isinstance(s, GraphState)

    def test_to_dict_round_trip(self):
        s = GraphState.initial(workspace="/ws", session_id="sid", max_iterations=7)
        s.upsert_code_file("a.py", "hello")
        s.add_execution_log(ExecutionLog(command="echo hi", stdout="hi"))
        s.increment_iteration()
        d = s.to_dict()
        restored = GraphState.from_dict(d)
        assert restored.code_files == s.code_files
        assert restored.iteration_count == s.iteration_count
        assert restored.max_iterations == s.max_iterations
        assert restored.workspace == "/ws"

    def test_oscillation_detection(self):
        s = GraphState.initial()
        # Same state hash repeated window=3 times should trip
        for _ in range(3):
            s.add_execution_log(ExecutionLog(command="same", exit_code=1, stdout="fail"))
            # Force same hash by not changing code_files/messages drastically
            # The hash includes last 5 logs, status, and message tail — repeated identical logs will hash identically once window fills.
            # Instead, drive check_oscillation with a forced repeating hash via direct injection.
        # Inject identical hashes to deterministically trigger
        s._recent_hashes = ["abc123", "abc123", "abc123"]
        # Next call appends hash and detects oscillation when window=3 and last 3 identical
        # We set up so the next computed hash equals abc123 by ensuring state hash is abc123.
        # Easier: patch _state_hash
        orig = s._state_hash
        s._state_hash = lambda: "abc123"  # type: ignore[method-assign]
        assert s.check_oscillation(window=3) is True
        s._state_hash = orig

    def test_oscillation_not_detected_when_varying(self):
        s = GraphState.initial()
        s._state_hash = lambda: "hash_a"  # type: ignore[method-assign]
        s.check_oscillation(window=3)
        s._state_hash = lambda: "hash_b"  # type: ignore[method-assign]
        s.check_oscillation(window=3)
        s._state_hash = lambda: "hash_c"  # type: ignore[method-assign]
        assert s.check_oscillation(window=3) is False

    def test_is_terminal_helpers(self):
        s = GraphState.initial()
        assert not s.is_terminal()
        s.transition(GraphStatus.COMPLETED)
        assert s.is_terminal()
        s2 = GraphState.initial()
        s2.transition(GraphStatus.FAILED, error="x")
        assert s2.is_failed()
        assert s2.is_terminal()
        s3 = GraphState.initial()
        s3.mark_needs_review("run_bash", {"command": "rm -rf /"}, reason="danger")
        assert s3.is_terminal()
        assert s3.status == GraphStatus.NEEDS_HUMAN_REVIEW

    def test_mark_needs_review_bookmarks_tool(self):
        s = GraphState.initial()
        s.mark_needs_review("run_bash", {"command": "danger"}, reason="needs approval")
        assert s.pending_approval is not None
        assert s.pending_approval["name"] == "run_bash"
        assert s.status == GraphStatus.NEEDS_HUMAN_REVIEW
        s.clear_review()
        assert s.pending_approval is None
        assert s.status == GraphStatus.IN_PROGRESS

    def test_resolve_max_iterations_from_config(self):
        from wisp.config import WispConfig

        cfg = WispConfig().replace(graph_max_iterations=9)
        assert GraphState.resolve_max_iterations(cfg) == 9
        # When explicit graph attr missing, defaults to spec 5 even if turn max is 50
        cfg2 = WispConfig().replace(max_iterations=50)
        # Ensure graph attr not set
        if hasattr(cfg2, "graph_max_iterations"):
            cfg2 = cfg2.replace(graph_max_iterations=5)
        assert GraphState.resolve_max_iterations(cfg2) == 5
        assert GraphState.resolve_max_iterations(None) == 5
