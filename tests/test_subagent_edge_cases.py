"""Edge case tests for subagent gap fixes.

Covers boundary conditions, error paths, and stress scenarios for:
1. Legacy code pollution (backward compat imports)
2. Tool schema stub (spawn_subagent delegation)
3. Context partitioner (message filtering)
4. MessageBus persistence (crash recovery)
5. AgentRegistry heartbeats (stale detection)
"""

import json
import os
import tempfile
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from wisp.multi_agent.bus import MessageBus
from wisp.multi_agent.context_partition import ContextPartitioner, partition_context
from wisp.multi_agent.protocol import AgentEvent, EventType
from wisp.multi_agent.registry import AgentRegistry, AgentRecord, AgentStatus
from wisp.tools import execute_tool


# ── 1. Legacy Code Pollution Edge Cases ────────────────────────────

class TestLegacyImportsEdgeCases:
    """Edge cases for backward-compatible imports."""

    def test_deprecation_warning_on_import(self):
        """Importing old modules should emit DeprecationWarning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            import wisp.subagent as sa
            import wisp.subagent_runner as sr

            dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(dep_warnings) >= 2, "Should emit at least 2 deprecation warnings"

    def test_old_types_are_new_types(self):
        """Old type names should be exact aliases to new types."""
        from wisp.subagent import SubagentTask, SubagentResult, SubagentRunner
        from wisp.multi_agent.task import SubagentContract
        from wisp.multi_agent.orchestrator import SubagentOrchestrator

        assert SubagentTask is SubagentContract
        assert SubagentRunner is SubagentOrchestrator

    def test_old_runner_has_spawn_method(self):
        """SubagentRunner alias should have the spawn method."""
        from wisp.subagent import SubagentRunner
        assert hasattr(SubagentRunner, 'run')

    def test_double_import_no_error(self):
        """Importing twice should not raise."""
        import wisp.subagent
        import wisp.subagent
        # Should not raise

    def test_import_in_thread(self):
        """Import should work from non-main thread."""
        result = []

        def _import():
            try:
                from wisp.subagent import SubagentRunner
                result.append(True)
            except Exception as e:
                result.append(e)

        t = threading.Thread(target=_import)
        t.start()
        t.join()
        assert result[0] is True


# ── 2. Tool Schema Stub Edge Cases ─────────────────────────────────

class TestToolSchemaStubEdgeCases:
    """Edge cases for spawn_subagent stub."""

    def test_stub_returns_error_json(self):
        """Stub should return valid JSON with error status inside data."""
        result = execute_tool("spawn_subagent", {"task": "test"}, ".")
        outer = json.loads(result)
        assert outer["status"] == "ok"  # execute_tool wraps it
        inner = json.loads(outer["data"])
        assert inner["status"] == "error"
        assert "spawn_subagent" in inner["tool"]

    def test_stub_with_empty_args(self):
        """Stub should handle empty args gracefully."""
        result = execute_tool("spawn_subagent", {}, ".")
        outer = json.loads(result)
        inner = json.loads(outer["data"])
        assert inner["status"] == "error"

    def test_stub_with_nested_args(self):
        """Stub should handle complex nested args."""
        args = {
            "task": "test",
            "tools": ["all"],
            "max_iterations": 10,
            "nested": {"deep": {"value": 1}},
        }
        result = execute_tool("spawn_subagent", args, ".")
        outer = json.loads(result)
        inner = json.loads(outer["data"])
        assert inner["status"] == "error"

    def test_stub_guides_to_proper_api(self):
        """Error message should guide user to proper API."""
        result = execute_tool("spawn_subagent", {"task": "test"}, ".")
        outer = json.loads(result)
        inner = json.loads(outer["data"])
        assert "agent loop" in inner["data"] or "spawn_subagents" in inner["data"]

    def test_stub_does_not_execute(self):
        """Stub should NOT actually spawn a subagent."""
        result = execute_tool("spawn_subagent", {"task": "test"}, ".")
        outer = json.loads(result)
        inner = json.loads(outer["data"])
        # Should be error, not success
        assert inner["status"] == "error"


# ── 3. Context Partitioner Edge Cases ───────────────────────────────

class TestContextPartitionerEdgeCases:
    """Edge cases for context partitioning."""

    def test_none_messages(self):
        """None input should not crash."""
        cp = ContextPartitioner()
        result = cp.partition(None, "test")  # type: ignore
        assert result == []

    def test_malformed_messages(self):
        """Messages missing 'role' or 'content' should not crash."""
        cp = ContextPartitioner()
        messages = [
            {},  # empty
            {"role": "user"},  # no content
            {"content": "hello"},  # no role
            {"role": "user", "content": "valid"},
        ]
        result = cp.partition(messages, "test")
        assert len(result) > 0

    def test_very_large_message_list(self):
        """Should handle 1000+ messages without crashing."""
        cp = ContextPartitioner(max_messages=5)
        messages = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(1000)
        ]
        result = cp.partition(messages, "test")
        assert len(result) <= 6  # max_messages + last user

    def test_all_system_messages(self):
        """If all messages are system, should still work."""
        cp = ContextPartitioner()
        messages = [
            {"role": "system", "content": f"Rule {i}"}
            for i in range(5)
        ]
        result = cp.partition(messages, "test")
        assert len(result) > 0

    def test_no_user_messages(self):
        """If no user messages, should not crash."""
        cp = ContextPartitioner()
        messages = [
            {"role": "assistant", "content": "Hello"},
            {"role": "assistant", "content": "World"},
        ]
        result = cp.partition(messages, "test")
        # Should return something, even without user messages
        assert isinstance(result, list)

    def test_unicode_content(self):
        """Should handle unicode content."""
        cp = ContextPartitioner()
        messages = [
            {"role": "user", "content": "测试中文内容 🎉 émojis"},
            {"role": "assistant", "content": "Réponse en français"},
        ]
        result = cp.partition(messages, "test")
        assert len(result) > 0

    def test_very_long_content(self):
        """Should handle very long message content."""
        cp = ContextPartitioner()
        messages = [
            {"role": "user", "content": "x" * 100000},
        ]
        result = cp.partition(messages, "test")
        assert len(result) > 0

    def test_max_messages_zero(self):
        """max_messages=0 should still include last user message."""
        cp = ContextPartitioner(max_messages=0)
        messages = [
            {"role": "user", "content": "First"},
            {"role": "user", "content": "Last"},
        ]
        result = cp.partition(messages, "test")
        assert len(result) >= 1
        assert any(m["content"] == "Last" for m in result)

    def test_task_with_special_chars(self):
        """Task with regex special chars should not crash."""
        cp = ContextPartitioner()
        messages = [{"role": "user", "content": "test"}]
        result = cp.partition(messages, "Fix auth.py [urgent] (test)")
        assert len(result) > 0

    def test_partition_context_convenience(self):
        """partition_context function should work with defaults."""
        messages = [{"role": "user", "content": "test"}]
        result = partition_context(messages, "task")
        assert len(result) > 0


# ── 4. MessageBus Persistence Edge Cases ───────────────────────────

class TestMessageBusPersistenceEdgeCases:
    """Edge cases for MessageBus crash recovery."""

    def test_load_from_nonexistent_file(self):
        """Loading from non-existent file should not crash."""
        bus = MessageBus(persist_path=Path("/nonexistent/path/bus.jsonl"))
        assert bus.history() == []

    def test_load_corrupted_jsonl(self):
        """Corrupted lines should be skipped, valid ones loaded."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"valid": true}\n')
            f.write('this is not json\n')
            f.write('{"another": "valid"}\n')
            path = Path(f.name)

        try:
            bus = MessageBus(persist_path=path)
            # Should load without crashing
            history = bus.history(limit=10)
            assert isinstance(history, list)
        finally:
            path.unlink(missing_ok=True)

    def test_persist_empty_event(self):
        """Emitting minimal event should persist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bus.jsonl"
            bus = MessageBus(persist_path=path)
            event = AgentEvent(
                event_type=EventType.BROADCAST,
                source_agent="test",
                payload={},
            )
            bus.emit(event)
            assert path.exists()
            assert path.stat().st_size > 0

    def test_concurrent_emit(self):
        """Concurrent emits should not corrupt persistence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bus.jsonl"
            bus = MessageBus(persist_path=path)

            def emit_many():
                for i in range(50):
                    event = AgentEvent(
                        event_type=EventType.BROADCAST,
                        source_agent=f"agent-{threading.current_thread().name}",
                        payload={"i": i},
                    )
                    bus.emit(event)

            threads = [threading.Thread(target=emit_many) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Verify file is valid JSONL
            lines = path.read_text().strip().split("\n")
            assert len(lines) == 200  # 4 threads * 50 events
            for line in lines:
                data = json.loads(line)
                assert "event_id" in data

    def test_clear_removes_file(self):
        """clear() should remove persistence file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bus.jsonl"
            bus = MessageBus(persist_path=path)
            bus.emit(AgentEvent(event_type=EventType.BROADCAST, source_agent="test"))
            assert path.exists()
            bus.clear()
            assert not path.exists()

    def test_compact_persistence(self):
        """compact_persistence should reduce file size."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bus.jsonl"
            bus = MessageBus(max_history=10, persist_path=path)
            for i in range(100):
                bus.emit(AgentEvent(
                    event_type=EventType.BROADCAST,
                    source_agent="test",
                    payload={"i": i},
                ))
            original_size = path.stat().st_size
            bus.compact_persistence(max_events=10)
            new_size = path.stat().st_size
            assert new_size < original_size

    def test_history_limit_larger_than_history(self):
        """history(limit) with limit > size should return all."""
        bus = MessageBus()
        bus.emit(AgentEvent(event_type=EventType.BROADCAST, source_agent="test"))
        result = bus.history(limit=1000)
        assert len(result) == 1

    def test_emit_without_persist_path(self):
        """Emit without persist_path should work (no crash)."""
        bus = MessageBus()
        event = AgentEvent(
            event_type=EventType.BROADCAST,
            source_agent="test",
            payload={"data": "x" * 10000},
        )
        bus.emit(event)
        assert len(bus.history()) == 1


# ── 5. AgentRegistry Heartbeats Edge Cases ─────────────────────────

class TestAgentRegistryHeartbeatEdgeCases:
    """Edge cases for AgentRegistry stale detection."""

    def test_heartbeat_nonexistent_agent(self):
        """Heartbeating non-existent agent should not crash."""
        reg = AgentRegistry()
        reg.heartbeat("nonexistent")
        # Should not raise

    def test_detect_stale_empty_registry(self):
        """detect_stale on empty registry should return empty."""
        reg = AgentRegistry()
        stale = reg.detect_stale_agents()
        assert stale == []

    def test_detect_stale_all_heartbeated(self):
        """All agents heartbeated recently — none stale."""
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a", role="coder"))
        reg.register(AgentRecord(agent_id="b", role="tester"))
        reg.heartbeat("a")
        reg.heartbeat("b")
        stale = reg.detect_stale_agents(max_stale_seconds=60)
        assert stale == []

    def test_detect_stale_mixed(self):
        """Some heartbeated, some not — only stale ones returned."""
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="fresh", role="coder"))
        reg.register(AgentRecord(agent_id="stale", role="tester"))
        reg.heartbeat("fresh")

        # Make stale agent old
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        reg._agents["stale"].spawned_at = old_time

        stale = reg.detect_stale_agents(max_stale_seconds=60)
        assert "stale" in stale
        assert "fresh" not in stale
        assert reg.get("stale").status == AgentStatus.CRASHED

    def test_detect_stale_already_crashed(self):
        """Already crashed agents should not be re-detected."""
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a", role="coder"))
        reg._agents["a"].status = AgentStatus.CRASHED
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        reg._agents["a"].spawned_at = old_time

        stale = reg.detect_stale_agents(max_stale_seconds=60)
        assert "a" not in stale

    def test_detect_stale_zero_threshold(self):
        """Zero threshold should mark all non-heartbeated as stale."""
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a", role="coder"))
        time.sleep(0.01)
        stale = reg.detect_stale_agents(max_stale_seconds=0)
        assert "a" in stale

    def test_persistence_auto_save(self):
        """Changes should auto-save to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "registry.json"
            reg = AgentRegistry(persist_path=path)
            reg.register(AgentRecord(agent_id="a", role="coder"))
            assert path.exists()

            # Load and verify
            reg2 = AgentRegistry(persist_path=path)
            assert reg2.get("a") is not None

    def test_persistence_survives_crash(self):
        """Registry should recover state after simulated crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "registry.json"
            reg1 = AgentRegistry(persist_path=path)
            reg1.register(AgentRecord(agent_id="a", role="coder"))
            reg1.register(AgentRecord(agent_id="b", role="tester"))
            reg1.heartbeat("a")
            reg1.update_status("b", AgentStatus.WORKING, "task-1")

            # Simulate crash: create new instance
            reg2 = AgentRegistry(persist_path=path)
            assert reg2.get("a").role == "coder"
            assert reg2.get("b").status == AgentStatus.WORKING
            assert reg2.get("b").current_task == "task-1"

    def test_concurrent_heartbeats(self):
        """Concurrent heartbeats should not corrupt registry."""
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a", role="coder"))

        def heartbeat_many():
            for _ in range(100):
                reg.heartbeat("a")

        threads = [threading.Thread(target=heartbeat_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have a valid heartbeat timestamp
        record = reg.get("a")
        assert record.last_heartbeat is not None

    def test_invalid_iso_timestamp(self):
        """Invalid ISO timestamp should not crash stale detection."""
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a", role="coder"))
        reg._agents["a"].last_heartbeat = "not-a-valid-timestamp"
        stale = reg.detect_stale_agents()
        assert "a" in stale  # Invalid = treat as stale

    def test_timezone_awareness(self):
        """Timestamps should be timezone-aware."""
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a", role="coder"))
        reg.heartbeat("a")
        record = reg.get("a")
        # Parse and verify it's a valid ISO timestamp
        ts = datetime.fromisoformat(record.last_heartbeat)
        assert ts.tzinfo is not None

    def test_unregister_clears_persistence(self):
        """Unregister should trigger auto-save."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "registry.json"
            reg = AgentRegistry(persist_path=path)
            reg.register(AgentRecord(agent_id="a", role="coder"))
            reg.unregister("a")

            reg2 = AgentRegistry(persist_path=path)
            assert reg2.get("a") is None
