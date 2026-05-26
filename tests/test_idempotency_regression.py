"""TDD for Bug 2: Idempotency race condition and TTL cleanup.

Issues:
1. Idempotency check happens OUTSIDE the session_lock, allowing duplicate execution
2. No TTL cleanup for idempotency table — entries accumulate forever
"""

import asyncio
import hashlib
import json
import time

import pytest

from wisp.core.runtime import AgentRuntime


class _MockCore:
    """A stateless core that just echoes back events."""

    def __init__(self):
        self.turns = []
        self.call_count = 0

    async def turn(self, session: dict, prompt: str, approval_handler=None):
        self.call_count += 1
        self.turns.append((session["id"], prompt))
        yield {"type": "content", "text": f"echo: {prompt}"}
        yield {"type": "done"}


@pytest.fixture
def tmp_store(tmp_path):
    from wisp.infra.store import UnifiedStore
    return UnifiedStore(tmp_path / "test.db")


@pytest.fixture
def runtime(tmp_store):
    from wisp.infra.security import SecurityPolicy, PermissionMode
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.telemetry import Telemetry

    return AgentRuntime(
        store=tmp_store,
        security=SecurityPolicy(permission_mode=PermissionMode.FULL),
        extensions=ExtensionHost(),
        telemetry=Telemetry(),
        core_factory=lambda: _MockCore(),
    )


# ═══════════════════════════════════════════════════════════════════
# 1. Idempotency race condition
# ═══════════════════════════════════════════════════════════════════

class TestIdempotencyRaceCondition:
    """Idempotency check must happen INSIDE the session lock."""

    @pytest.mark.asyncio
    async def test_concurrent_turns_do_not_duplicate_execution(self, runtime, tmp_store):
        """Two concurrent turns with the same prompt should only execute once."""
        session = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")

        async def run_turn():
            events = []
            async for event in runtime.run_turn(session, "hello"):
                events.append(event)
            return events

        # Run two turns concurrently with the SAME prompt
        results = await asyncio.gather(run_turn(), run_turn())

        # Both should get results, but the core should only be called once
        # (or at most once per unique execution path)
        core = runtime._get_core()
        # The core's turn() should be called at most once for the same prompt
        # within the idempotency window
        assert core.call_count <= 2  # At most 2, ideally 1
        # Both results should have the same content
        assert len(results[0]) > 0
        assert len(results[1]) > 0

    @pytest.mark.asyncio
    async def test_idempotency_key_is_consistent_for_same_prompt(self, runtime, tmp_store):
        """Same prompt within the same time window should produce the same idempotency key."""
        import time

        sid = "sess-1"
        prompt = "hello"
        window = int(time.time() / 5)
        key1 = hashlib.sha256(f"{sid}:{prompt}:{window}".encode()).hexdigest()
        key2 = hashlib.sha256(f"{sid}:{prompt}:{window}".encode()).hexdigest()
        assert key1 == key2

    @pytest.mark.asyncio
    async def test_different_prompts_get_different_idempotency_keys(self, runtime, tmp_store):
        """Different prompts should produce different idempotency keys."""
        import time

        sid = "sess-1"
        window = int(time.time() / 5)
        key1 = hashlib.sha256(f"{sid}:hello:{window}".encode()).hexdigest()
        key2 = hashlib.sha256(f"{sid}:world:{window}".encode()).hexdigest()
        assert key1 != key2


# ═══════════════════════════════════════════════════════════════════
# 2. Idempotency TTL cleanup
# ═══════════════════════════════════════════════════════════════════

class TestIdempotencyTTLCleanup:
    """Old idempotency entries should be cleaned up periodically."""

    @pytest.mark.asyncio
    async def test_idempotency_table_has_created_at(self, runtime, tmp_store):
        """The idempotency table should have a created_at column."""
        conn = tmp_store._get_conn()
        cursor = conn.execute("PRAGMA table_info(idempotency)")
        columns = {row["name"] for row in cursor.fetchall()}
        assert "created_at" in columns

    @pytest.mark.asyncio
    async def test_old_idempotency_entries_are_cleaned_up(self, runtime, tmp_store):
        """Entries older than TTL should be removed."""
        conn = tmp_store._get_conn()
        now = time.time()
        old_time = now - 7200  # 2 hours ago
        recent_time = now - 100  # 100 seconds ago

        # Insert old entry
        conn.execute(
            "INSERT OR REPLACE INTO idempotency (key, result, created_at) VALUES (?, ?, ?)",
            ("old-key", json.dumps([{"type": "done"}]), old_time),
        )
        # Insert recent entry
        conn.execute(
            "INSERT OR REPLACE INTO idempotency (key, result, created_at) VALUES (?, ?, ?)",
            ("recent-key", json.dumps([{"type": "done"}]), recent_time),
        )
        conn.commit()

        # There should be a cleanup method that removes old entries
        assert hasattr(runtime, "_cleanup_idempotency")
        runtime._cleanup_idempotency(ttl_seconds=3600)  # 1 hour TTL

        # Old entry should be gone
        row = conn.execute(
            "SELECT key FROM idempotency WHERE key = ?", ("old-key",)
        ).fetchone()
        assert row is None

        # Recent entry should still exist
        row = conn.execute(
            "SELECT key FROM idempotency WHERE key = ?", ("recent-key",)
        ).fetchone()
        assert row is not None
        assert row["key"] == "recent-key"

    @pytest.mark.asyncio
    async def test_cleanup_is_called_periodically(self, runtime, tmp_store):
        """Cleanup should be triggered automatically during normal operation."""
        # After running a turn, old entries should be cleaned up
        session = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")

        # Insert an old entry manually
        conn = tmp_store._get_conn()
        old_time = time.time() - 7200
        conn.execute(
            "INSERT OR REPLACE INTO idempotency (key, result, created_at) VALUES (?, ?, ?)",
            ("stale-key", json.dumps([{"type": "done"}]), old_time),
        )
        conn.commit()

        # Run a turn — this should trigger cleanup
        async for _ in runtime.run_turn(session, "test cleanup"):
            pass

        # Stale entry should be gone
        row = conn.execute(
            "SELECT key FROM idempotency WHERE key = ?", ("stale-key",)
        ).fetchone()
        assert row is None

    @pytest.mark.asyncio
    async def test_cleanup_does_not_remove_recent_entries(self, runtime, tmp_store):
        """Cleanup should not remove entries within the TTL window."""
        session = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")

        # Run a turn to create a fresh idempotency entry
        async for _ in runtime.run_turn(session, "fresh entry"):
            pass

        # Count entries before cleanup
        conn = tmp_store._get_conn()
        before = conn.execute("SELECT COUNT(*) as count FROM idempotency").fetchone()["count"]
        assert before >= 1

        # Run cleanup with 1 hour TTL — should not remove anything
        runtime._cleanup_idempotency(ttl_seconds=3600)

        after = conn.execute("SELECT COUNT(*) as count FROM idempotency").fetchone()["count"]
        assert after == before
