"""Regression pins for the persistence performance refactor.

Covers the behaviors changed in the memory/persistence optimization:

  A. AgentMemory.upsert is now APPEND-ONLY (last-wins) instead of a full
     file rewrite every turn, with a byte-identical short-circuit.
  B. UnifiedStore.list_sessions no longer parses the full messages JSON
     blob for the msg_count column; new rows store msg_count and legacy
     NULL rows are healed lazily while still returning correct counts.
  C. Turn persistence (DONE event + session save + memory fold) runs on a
     worker thread but the on-disk result is unchanged.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest

from wisp.agent_memory import AgentMemory, AGENT_MEMORY_DIR, SESSIONS_FILE
from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.core.runtime import AgentRuntime
from wisp.infra.store import UnifiedStore
from wisp.providers.mock import MockProvider


def _make_summary(session_id, turn):
    from wisp.summarizer import SessionSummary
    return SessionSummary(
        session_id=session_id,
        timestamp=f"2026-08-27T00:00:0{turn % 10}Z",
        workspace="/tmp/ws",
        summary=f"work {turn} " + "d" * 400,
        key_decisions=["a", "b"],
        user_preferences=["p"],
        open_tasks=["t"],
        files_touched=["f.py"],
    )


class TestAgentMemoryAppendOnly:
    def setup_method(self):
        self._orig_dir = AGENT_MEMORY_DIR
        self.tmp = tempfile.TemporaryDirectory()
        import wisp.agent_memory as am
        am.AGENT_MEMORY_DIR = Path(self.tmp.name)
        am.SESSIONS_FILE = am.AGENT_MEMORY_DIR / "sessions.jsonl"
        self._am = am
        self.mem = AgentMemory()

    def teardown_method(self):
        import wisp.agent_memory as am
        am.AGENT_MEMORY_DIR = self._orig_dir
        am.SESSIONS_FILE = self._orig_dir / "sessions.jsonl"
        self.tmp.cleanup()

    def test_upsert_appends_line_but_loads_deduped(self):
        s = self.mem
        s.upsert(_make_summary("s-1", 1))
        s.upsert(_make_summary("s-1", 2))
        s.upsert(_make_summary("s-1", 3))

        lines = self._am.SESSIONS_FILE.read_text().strip().split("\n")
        assert len(lines) == 3, "each upsert appends a line (append-only)"

        all_s = s.load_all()
        assert [x.session_id for x in all_s] == ["s-1"]
        assert all_s[0].summary == _make_summary("s-1", 3).summary

    def test_fresh_instance_reads_latest_after_multiple_upserts(self):
        s = self.mem
        s.upsert(_make_summary("s-x", 1))
        s.upsert(_make_summary("s-x", 2))
        fresh = AgentMemory()
        all_s = fresh.load_all()
        assert len(all_s) == 1
        assert all_s[0].summary == _make_summary("s-x", 2).summary

    def test_identical_upsert_short_circuits_no_io(self):
        s = self.mem
        s.upsert(_make_summary("s-id", 1))
        before = self._am.SESSIONS_FILE.stat().st_mtime
        time.sleep(0.01)
        s.upsert(_make_summary("s-id", 1))
        after = self._am.SESSIONS_FILE.stat().st_mtime
        assert before == after, "identical upsert must not rewrite the file"
        lines = self._am.SESSIONS_FILE.read_text().strip().split("\n")
        assert len(lines) == 1, "identical upsert must not append a line"

# ── store + off-thread pins appended below ────────────────────────────────


class TestUnifiedStoreListSessions:
    def test_msg_count_from_column_and_legacy_heal(self, tmp_path):
        store = UnifiedStore(tmp_path / "w.db")

        s1 = {
            "id": "s-new", "model": "m", "workspace": "/w", "title": "",
            "messages": [{"role": "user", "content": "x"} for _ in range(3)],
            "compaction_history": [], "created_at": "t", "updated_at": "t",
        }
        store.save_session(s1)
        listing = store.list_sessions(50)
        assert listing[0]["msg_count"] == 3

        # Legacy NULL row (pre-migration) must be healed lazily but correctly.
        store._get_conn().execute(
            "UPDATE sessions SET msg_count = NULL WHERE id = 's-new'")
        relisted = store.list_sessions(50)
        assert relisted[0]["msg_count"] == 3, \
            "legacy NULL msg_count must be healed to the real count"
        healed = store._get_conn().execute(
            "SELECT msg_count FROM sessions WHERE id='s-new'").fetchone()
        assert healed["msg_count"] == 3, "healed value must be persisted back"

    def test_update_run_status_does_not_touch_events(self, tmp_path):
        """update_run_status is a direct UPDATE — inline run events survive."""
        store = UnifiedStore(tmp_path / "w.db")
        store.create_session("sess", "m", "/w")
        rid = store.create_run("sess", "do it")
        run = store.load_run(rid)
        run["events"] = [{"type": "tool_call", "name": "read_file"}]
        store.save_run(run)

        store.update_run_status(rid, "running")
        assert store.load_run(rid)["status"] == "running"
        assert len(store.load_run(rid)["events"]) == 1

    def test_save_run_batches_events_in_transaction(self, tmp_path):
        store = UnifiedStore(tmp_path / "w.db")
        store.create_session("sess", "m", "/w")
        rid = store.create_run("sess", "do it")
        run = store.load_run(rid)
        run["events"] = [{"type": "t", "name": f"i{i}"} for i in range(50)]
        store.save_run(run)
        assert len(store.load_run(rid)["events"]) == 50


@pytest.mark.asyncio
async def test_persist_off_thread_still_saves_session(tmp_path):
    """Turn persistence moved to a worker thread; the on-disk session must
    still be updated (covers _persist_turn_state end to end)."""
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import SecurityPolicy
    from wisp.infra.telemetry import Telemetry

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("alpha")
    store = UnifiedStore(tmp_path / "w.db")
    config = WispConfig().replace(workspace=str(ws))
    provider = MockProvider(
        responses=["", "ok"],
        tool_calls=[[{"function": {"name": "read_file",
                                   "arguments": {"path": "a.txt"}}}]])

    def factory():
        return WispAgentCore(config=config, provider=provider,
                             security=SecurityPolicy(), tool_executor=None)

    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(),
        extensions=ExtensionHost(), telemetry=Telemetry(),
        core_factory=factory, config=config,
    )

    session = await runtime.get_or_create_session(
        "off-thread-persist", model="mock-model", workspace=str(ws))
    async for _ev in runtime.run_turn(session, "read a.txt"):
        pass

    # Reload from DB (not the in-memory session) to prove the worker-thread
    # save actually persisted.
    reloaded = store.load_session("off-thread-persist")
    assert reloaded is not None
    roles = [m.get("role") for m in reloaded["messages"]]
    assert "tool" in roles and "assistant" in roles
    assert reloaded["updated_at"]  # timestamp set inside the worker thread


from wisp.infra.security import SecurityPolicy  # noqa: E402


def test_guard_field_reader_helpers_importable():
    """Sanity: serializer module function and helpers still importable."""
    from wisp.core.runtime import _serialize_tool_exchanges
    import inspect
    assert inspect.isfunction(_serialize_tool_exchanges)
