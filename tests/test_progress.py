"""TDD tests for ProgressTracker — phase detection, file tracking, tool counting."""

from wisp.transport.progress import ProgressTracker, TurnProgress
from wisp.core.events import (
    tool_call,
)


class TestTurnProgress:
    """TurnProgress dataclass defaults."""

    def test_defaults(self):
        tp = TurnProgress()
        assert tp.phase == "understand"
        assert tp.tools_run == 0
        assert tp.tools_succeeded == 0
        assert tp.tools_failed == 0
        assert tp.files_changed == []
        assert tp.current_tool is None


class TestProgressTrackerLifecycle:
    """start_turn / elapsed / on_done."""

    def test_start_turn_resets_state(self):
        pt = ProgressTracker()
        pt.start_turn(3)
        assert pt.progress.turn_number == 3
        assert pt.progress.phase == "understand"
        assert pt.progress.tools_run == 0

    def test_elapsed_increases(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        elapsed = pt.elapsed
        assert elapsed >= 0

    def test_on_done_returns_stats(self):
        pt = ProgressTracker()
        pt.start_turn(2)
        pt.on_tool_call("read_file")
        pt.on_tool_result("read_file", "ok", 10.0)
        stats = pt.on_done()
        assert stats["turn_number"] == 2
        assert stats["tools_run"] == 1
        assert stats["phase"] == "understand"


class TestPhaseTransitions:
    """Phase detection from event patterns."""

    def test_starts_in_understand(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        assert pt.progress.phase == "understand"

    def test_read_tools_stay_understand(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("read_file")
        assert pt.progress.phase == "understand"
        pt.on_tool_call("search_symbols")
        assert pt.progress.phase == "understand"

    def test_plan_task_triggers_plan(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        result = pt.on_event(tool_call("plan_task", {"description": "do X"}))
        assert result == "plan"
        assert pt.progress.phase == "plan"

    def test_write_triggers_execute(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_event(tool_call("write_file", {"path": "a.py", "content": "x"}))
        assert pt.progress.phase == "execute"

    def test_edit_triggers_execute(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_event(tool_call("edit_file", {"path": "a.py", "old": "x", "new": "y"}))
        assert pt.progress.phase == "execute"

    def test_bash_triggers_execute(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_event(tool_call("run_bash", {"command": "pytest"}))
        assert pt.progress.phase == "execute"

    def test_test_after_write_triggers_verify(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_event(tool_call("write_file", {"path": "a.py", "content": "x"}))
        assert pt.progress.phase == "execute"
        pt.on_event(tool_call("run_tests", {}))
        assert pt.progress.phase == "verify"

    def test_lint_after_write_triggers_verify(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_event(tool_call("write_file", {"path": "x.py"}))
        pt.on_event(tool_call("lsp_diagnostics", {}))
        assert pt.progress.phase == "verify"

    def test_diagnose_after_write_triggers_verify(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_event(tool_call("write_file", {"path": "x.py"}))
        pt.on_event(tool_call("diagnose", {}))
        assert pt.progress.phase == "verify"

    def test_test_without_write_stays_understand(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_event(tool_call("run_tests", {}))
        assert pt.progress.phase == "understand"

    def test_phase_never_regresses(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_event(tool_call("write_file", {}))
        assert pt.progress.phase == "execute"
        pt.on_event(tool_call("read_file", {}))
        assert pt.progress.phase == "execute"  # stays execute, not back to understand

    def test_on_event_returns_phase_only_on_change(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        assert pt.on_event(tool_call("write_file", {})) == "execute"
        assert pt.on_event(tool_call("write_file", {})) is None  # already execute
        assert pt.on_event(tool_call("run_tests", {})) == "verify"


class TestToolCounting:
    """Tool run/succeeded/failed tracking."""

    def test_counts_tools(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("read_file")
        pt.on_tool_call("write_file")
        assert pt.progress.tools_run == 2

    def test_success_and_failure(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("read_file")
        pt.on_tool_result("read_file", "contents", 5.0)
        assert pt.progress.tools_succeeded == 1
        assert pt.progress.tools_failed == 0

    def test_error_result_counts_failure(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("run_bash")
        pt.on_tool_result("run_bash", "Error: command not found", 100.0)
        assert pt.progress.tools_succeeded == 0
        assert pt.progress.tools_failed == 1

    def test_json_error_result_counts_failure(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("run_bash")
        pt.on_tool_result("run_bash", '{"status": "error", "message": "fail"}', 50.0)
        assert pt.progress.tools_failed == 1

    def test_dict_result_with_error_status(self):
        """Dict result with status='error' counts as failure."""
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("read_file")
        pt.on_tool_result("read_file", {"status": "error", "message": "not found"}, 10.0)
        assert pt.progress.tools_failed == 1
        assert pt.progress.tools_succeeded == 0

    def test_dict_result_with_ok_status(self):
        """Dict result without status='error' counts as success."""
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("read_file")
        pt.on_tool_result("read_file", {"status": "ok", "content": "data"}, 10.0)
        assert pt.progress.tools_succeeded == 1
        assert pt.progress.tools_failed == 0

    def test_dict_result_plain_counts_success(self):
        """Dict result without status key counts as success."""
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("read_file")
        pt.on_tool_result("read_file", {"content": "some data"}, 10.0)
        assert pt.progress.tools_succeeded == 1
        assert pt.progress.tools_failed == 0

    def test_non_string_non_dict_result_counts_success(self):
        """Bytes/int results count as success (no crash)."""
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("run_bash")
        pt.on_tool_result("run_bash", 42, 5.0)
        assert pt.progress.tools_succeeded == 1
        assert pt.progress.tools_failed == 0


class TestFileTracking:
    """File change detection from tool results (args saved from tool_call)."""

    def test_write_file_tracks_path(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("write_file", {"path": "/tmp/foo.py"})
        pt.on_tool_result("write_file", "ok", 10.0)
        assert pt.progress.files_changed == ["/tmp/foo.py"]

    def test_edit_file_tracks_path(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("edit_file", {"path": "/tmp/bar.py"})
        pt.on_tool_result("edit_file", "ok", 5.0)
        assert pt.progress.files_changed == ["/tmp/bar.py"]

    def test_edit_file_multi_tracks_path(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("edit_file_multi", {"path": "/tmp/baz.py"})
        pt.on_tool_result("edit_file_multi", "ok", 8.0)
        assert pt.progress.files_changed == ["/tmp/baz.py"]

    def test_deduplicates_paths(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("edit_file", {"path": "/tmp/x.py"})
        pt.on_tool_result("edit_file", "ok", 5.0)
        pt.on_tool_call("edit_file", {"path": "/tmp/x.py"})
        pt.on_tool_result("edit_file", "ok", 3.0)
        assert pt.progress.files_changed == ["/tmp/x.py"]

    def test_read_file_not_tracked(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("read_file", {"path": "/tmp/x.py"})
        pt.on_tool_result("read_file", "content", 2.0)
        assert pt.progress.files_changed == []

    def test_multiple_files_preserve_order(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("write_file", {"path": "c.py"})
        pt.on_tool_result("write_file", "ok", 1.0)
        pt.on_tool_call("edit_file", {"path": "a.py"})
        pt.on_tool_result("edit_file", "ok", 1.0)
        pt.on_tool_call("edit_file", {"path": "b.py"})
        pt.on_tool_result("edit_file", "ok", 1.0)
        assert pt.progress.files_changed == ["c.py", "a.py", "b.py"]

    def test_no_path_key_not_tracked(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("write_file")
        pt.on_tool_result("write_file", "ok", 1.0)
        assert pt.progress.files_changed == []


class TestCurrentTool:
    """Current tool tracking."""

    def test_tracks_current_tool(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("read_file")
        assert pt.progress.current_tool == "read_file"

    def test_clears_current_on_result(self):
        pt = ProgressTracker()
        pt.start_turn(1)
        pt.on_tool_call("read_file")
        pt.on_tool_result("read_file", "ok", 1.0)
        assert pt.progress.current_tool is None


class TestDelegationFileTracking:
    """Subagent-reported files must count toward turn stats."""

    def _tracker_with_spawn_call(self):
        from wisp.transport.progress import ProgressTracker

        tracker = ProgressTracker()
        tracker.start_turn(1)
        tracker.on_tool_call("spawn", {"task": "x", "role": "coder"})
        return tracker

    def test_dict_result_files_counted(self):
        tracker = self._tracker_with_spawn_call()
        tracker.on_tool_result("spawn", {
            "status": "ok",
            "data": {"ok": True, "summary": "done"},
            "metadata": {"files_changed": ["a.py", "b.py"]},
        })
        assert tracker.progress.files_changed == ["a.py", "b.py"]

    def test_json_string_result_files_counted(self):
        import json

        tracker = self._tracker_with_spawn_call()
        tracker.on_tool_result("spawn", json.dumps({
            "status": "ok",
            "data": {"files": ["c.py"]},
        }))
        assert tracker.progress.files_changed == ["c.py"]

    def test_no_double_counting_across_shapes(self):
        tracker = self._tracker_with_spawn_call()
        tracker.on_tool_result("spawn", {
            "status": "ok",
            "metadata": {"files_changed": ["a.py"]},
        })
        tracker.on_tool_result("spawn", {
            "status": "ok",
            "metadata": {"files_changed": ["a.py", "d.py"]},
        })
        assert tracker.progress.files_changed == ["a.py", "d.py"]
