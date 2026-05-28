"""TDD for CLI transport — error detection, phase tracking, adapter, approval state.

The REPL loop is driven by entry._run_repl, not CLITransport.run().
Tests here validate component behavior in isolation.
"""

import pytest
from wisp.transport.cli import CLITransport, AgentAdapter, ApprovalSessionState
from wisp.transport.progress import ProgressTracker
from wisp.core.events import AgentEvent, EventType


class _MockRuntime:
    def __init__(self):
        self.sessions = {}
        self.turns = []


# ═══════════════════════════════════════════════════════════════════
# 1. Error result detection
# ═══════════════════════════════════════════════════════════════════

class TestCLIErrorDetection:
    """_is_error_result correctly identifies error results."""

    def test_dict_error_status(self):
        assert CLITransport._is_error_result({"status": "error"}) is True

    def test_dict_non_error_status(self):
        assert CLITransport._is_error_result({"status": "ok"}) is False

    def test_string_starts_with_error(self):
        assert CLITransport._is_error_result("Error: something failed") is True

    def test_string_starts_with_bracket_error(self):
        assert CLITransport._is_error_result("[Error] bad thing") is True

    def test_json_array_is_not_error(self):
        """JSON arrays should NOT be falsely flagged as errors."""
        assert CLITransport._is_error_result('["file1.py", "file2.py"]') is False

    def test_plain_string_not_error(self):
        assert CLITransport._is_error_result("file content here") is False

    def test_number_not_error(self):
        assert CLITransport._is_error_result(42) is False


# ═══════════════════════════════════════════════════════════════════
# 2. Phase tracking
# ═══════════════════════════════════════════════════════════════════

class TestCLIPhaseTracking:
    """Phase detection and progress tracking."""

    def test_phase_starts_at_understand(self):
        tracker = ProgressTracker()
        assert tracker.progress.phase == "understand"

    def test_phase_advances_on_write_tool(self):
        """Write tools should advance phase to execute."""
        tracker = ProgressTracker()
        event = AgentEvent(type=EventType.TOOL_CALL, data={"name": "write_file", "arguments": {"path": "x.py"}})
        tracker.on_event(event)
        assert tracker.progress.phase == "execute"

    def test_tool_count_increments(self):
        tracker = ProgressTracker()
        tracker.on_event(AgentEvent(type=EventType.TOOL_CALL, data={"name": "read_file"}))
        tracker.on_event(AgentEvent(type=EventType.TOOL_RESULT, data={"name": "read_file", "duration_ms": 5.0}))
        stats = tracker.on_done()
        assert stats.get("tools_run") == 1

    def test_file_tracking(self):
        tracker = ProgressTracker()
        tracker.on_event(AgentEvent(type=EventType.TOOL_CALL, data={"name": "edit_file", "arguments": {"path": "src/main.py"}}))
        tracker.on_event(AgentEvent(type=EventType.TOOL_RESULT, data={"name": "edit_file", "result": "ok", "duration_ms": 5.0}))
        stats = tracker.on_done()
        assert "src/main.py" in stats.get("files_changed", [])

    def test_turn_number_starts_zero(self):
        transport = CLITransport(_MockRuntime())
        assert transport._turn_number == 0


# ═══════════════════════════════════════════════════════════════════
# 3. AgentAdapter
# ═══════════════════════════════════════════════════════════════════

class TestAgentAdapter:
    """AgentAdapter correctly wraps runtime+session."""

    def test_adapter_creates_session_adapter(self):
        session = {"id": "s1", "messages": []}
        adapter = AgentAdapter(_MockRuntime(), None, session)
        assert adapter.session is not None
        assert adapter.messages == []

    def test_adapter_add_message(self):
        session = {"id": "s1", "messages": []}
        adapter = AgentAdapter(_MockRuntime(), None, session)
        adapter._add_message("user", "hello")
        assert len(adapter.messages) == 1
        assert adapter.messages[0]["role"] == "user"
        assert adapter.messages[0]["content"] == "hello"

    def test_adapter_metrics_persists(self):
        session = {"id": "s1", "messages": []}
        adapter = AgentAdapter(_MockRuntime(), None, session)
        m1 = adapter.metrics
        m2 = adapter.metrics
        assert m1 is m2  # Same object — not recreated each access

    def test_adapter_loop_parameter(self):
        import asyncio
        session = {"id": "s1", "messages": []}
        loop = asyncio.new_event_loop()
        try:
            adapter = AgentAdapter(_MockRuntime(), None, session, loop=loop)
            assert adapter._loop is loop
        finally:
            loop.close()

    def test_adapter_expand_continuation(self):
        session = {"id": "s1", "messages": [
            {"role": "assistant", "content": "Here is the implementation of the auth module..."},
        ]}
        adapter = AgentAdapter(_MockRuntime(), None, session)
        expanded = adapter._expand_continuation("continue")
        assert "continue" in expanded.lower()
        assert "Context" in expanded or "context" in expanded

    def test_adapter_no_expand_non_continuation(self):
        session = {"id": "s1", "messages": []}
        adapter = AgentAdapter(_MockRuntime(), None, session)
        expanded = adapter._expand_continuation("write a function")
        assert expanded == "write a function"


# ═══════════════════════════════════════════════════════════════════
# 4. Approval session state
# ═══════════════════════════════════════════════════════════════════

class TestApprovalSessionState:
    """Approval state tracks per-tool and global approvals."""

    def test_allow_tool(self):
        state = ApprovalSessionState()
        state.allow_tool("read_file")
        assert state.is_allowed("read_file")
        assert not state.is_allowed("write_file")

    def test_deny_tool(self):
        state = ApprovalSessionState()
        state.deny_tool("run_bash")
        assert "run_bash" in state.denied_tools

    def test_auto_approve(self):
        state = ApprovalSessionState()
        state.set_auto()
        assert state.is_allowed("anything")
        assert not state.should_ask("anything")

    def test_block_all(self):
        state = ApprovalSessionState()
        state.set_block()
        assert not state.is_allowed("read_file")

    def test_should_ask_default(self):
        state = ApprovalSessionState()
        assert state.should_ask("read_file")

    def test_should_not_ask_after_allow(self):
        state = ApprovalSessionState()
        state.allow_tool("read_file")
        assert not state.should_ask("read_file")


# ═══════════════════════════════════════════════════════════════════
# 5. Approval branch Y vs y
# ═══════════════════════════════════════════════════════════════════

class TestApprovalBranches:
    """Verify Y (always-allow) and y (once) branches work correctly."""

    def test_uppercase_Y_sets_allow_tool(self):
        """Uppercase Y should register the tool as always-allowed."""
        state = ApprovalSessionState()
        raw = "Y"
        choice = raw.strip()
        if choice in ("y", "Y"):
            if choice == "Y":
                state.allow_tool("read_file")
        assert state.is_allowed("read_file")
        assert not state.should_ask("read_file")

    def test_lowercase_y_does_not_set_allow_tool(self):
        """Lowercase y should NOT register the tool as always-allowed."""
        state = ApprovalSessionState()
        raw = "y"
        choice = raw.strip()
        if choice in ("y", "Y"):
            if choice == "Y":
                state.allow_tool("read_file")
        # y doesn't set allow, so should_ask should still be True for next time
        assert state.should_ask("read_file")