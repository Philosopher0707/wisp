"""Cross-session memory: facts + summaries must reach future sessions.

Regression for the audit finding: remember stored facts that nothing
ever injected — _build_memory_block passed a literal empty list and no
code path wrote session summaries.
"""

from datetime import datetime, timezone

import pytest

from wisp.agent_memory import AgentMemory, SessionSummary
from wisp.core.stateless import format_cross_session_block


@pytest.fixture
def isolated_agent_memory(tmp_path, monkeypatch):
    """Point the singleton at a temp JSONL; reset between tests."""
    import wisp.agent_memory as am

    monkeypatch.setattr(am, "SESSIONS_FILE", tmp_path / "sessions.jsonl")
    monkeypatch.setattr(am, "AGENT_MEMORY_DIR", tmp_path)
    monkeypatch.setattr(am, "_agent_memory_singleton", None)
    yield am


def _summary(sid="s1", ws="/tmp/ws", files=None, text="did things"):
    return SessionSummary(
        session_id=sid,
        timestamp=datetime.now(timezone.utc).isoformat(),
        workspace=ws,
        summary=text,
        files_touched=files or [],
    )


class TestFormatBlock:
    def test_empty_inputs_render_nothing(self):
        assert format_cross_session_block([], []) == ""

    def test_facts_render_under_header(self):
        out = format_cross_session_block(
            [{"content": "User prefers vim"}], [])
        assert "## Cross-Session Memory" in out
        assert "- User prefers vim" in out

    def test_important_facts_rank_first(self):
        facts = [
            {"content": "b plain"},
            {"content": "a important", "important": True},
        ]
        out = format_cross_session_block(facts, [])
        assert out.index("a important") < out.index("b plain")

    def test_facts_capped_at_15(self):
        facts = [{"content": f"fact {i}"} for i in range(25)]
        out = format_cross_session_block(facts, [])
        assert "fact 24" not in out and "fact 14" in out

    def test_summaries_appended_via_formatter(self):
        s = _summary(files=["a.py"], text="refactored auth")
        out = format_cross_session_block([], [s])
        assert "Previous Session Context" in out
        assert "refactored auth" in out and "a.py" in out


class TestEngineInjection:
    def test_future_session_system_prompt_contains_memory(self, monkeypatch):
        monkeypatch.setattr(
            "wisp.memory.list_all_facts",
            lambda: [{"content": "User prefers tabs over spaces"}],
        )
        from wisp.core.engine import WispAgentCore
        from wisp.infra.security import PermissionMode, SecurityPolicy
        from wisp.infra.extensions import ExtensionHost

        captured = []

        class P:
            def generate_stream_events(self, system_prompt, messages,
                                       tools=None, checkpoint_every=50):
                captured.append(system_prompt)
                yield {"type": "content", "text": "ok", "data": {}}
                yield {"type": "done"}

        core = WispAgentCore(provider=P(), security=SecurityPolicy(
            permission_mode=PermissionMode.FULL), extensions=ExtensionHost())

        import uuid
        ws = f"/tmp/mem-test-{uuid.uuid4().hex[:8]}"

        async def run():
            sess = {"id": "fresh-session", "messages": [], "model": "m",
                    "workspace": ws}
            async for _ in core.turn(sess, "hello"):
                pass

        asyncio_new = __import__("asyncio").new_event_loop()
        try:
            asyncio_new.run_until_complete(run())
        finally:
            asyncio_new.close()

        assert any("tabs over spaces" in sp for sp in captured), (
            "remembered fact never reached a future session's prompt")


class TestRuntimeSummaryRecording:
    def _rt(self):
        from unittest.mock import MagicMock
        from wisp.core.runtime import AgentRuntime
        rt = AgentRuntime(store=MagicMock(), security=MagicMock(), core_factory=MagicMock(),
                          extensions=MagicMock(), telemetry=MagicMock())
        return rt

    def test_helper_writes_and_upserts_summary(self, isolated_agent_memory):
        rt = self._rt()
        sess = {"id": "sess-mem", "workspace": "/tmp/ws",
                "messages": [], "model": "m"}

        rt._note_touched_file("sess-mem", {
            "name": "write_file",
            "arguments": {"path": "src/auth.py"}})
        rt._record_session_memory("sess-mem", sess, "fix the login bug")

        mem = AgentMemory()
        all_s = mem.load_all()
        assert len(all_s) == 1
        assert all_s[0].session_id == "sess-mem"
        assert "fix the login bug" in all_s[0].summary
        assert "src/auth.py" in all_s[0].files_touched

        # second turn merges files, updates latest request — not frozen
        rt._note_touched_file("sess-mem", {
            "name": "edit_file",
            "arguments": {"path": "src/token.py"}})
        rt._record_session_memory("sess-mem", sess, "now add refresh tokens")

        all_s = AgentMemory().load_all()
        assert len(all_s) == 1, "upsert must replace, not append"
        assert set(all_s[0].files_touched) == {"src/auth.py", "src/token.py"}
        assert "refresh tokens" in all_s[0].summary

    def test_recording_survives_across_runtime_instances(self, isolated_agent_memory):
        rt1, rt2 = self._rt(), self._rt()
        sess = {"id": "sess-x", "workspace": "/w", "messages": [], "model": "m"}
        rt1._record_session_memory("sess-x", sess, "first runtime")
        # new process/instance: cache cold, file read back
        rt2._record_session_memory("sess-x", sess, "second runtime")
        all_s = AgentMemory().load_all()
        assert len(all_s) == 1 and "second runtime" in all_s[0].summary

    def test_non_file_tools_touch_nothing(self):
        rt = self._rt()
        rt._note_touched_file("s", {"name": "run_bash",
                                    "arguments": {"command": "ls"}})
        assert rt._touched_files.get("s") is None


class TestMemoryCacheInvalidation:
    """A fact remembered mid-process must reach the NEXT turn's prompt."""

    def test_remember_busts_static_prompt_cache(self, monkeypatch):
        import uuid
        from wisp.core.engine import WispAgentCore
        from wisp.infra.security import PermissionMode, SecurityPolicy
        from wisp.infra.extensions import ExtensionHost

        ws = f"/tmp/mem-cache-{uuid.uuid4().hex[:8]}"
        facts = [[]]
        monkeypatch.setattr("wisp.memory.list_all_facts",
                            lambda: list(facts[0]))

        captured = []

        class P:
            def generate_stream_events(self, system_prompt, messages,
                                       tools=None, checkpoint_every=50):
                captured.append(system_prompt)
                yield {"type": "content", "text": "ok", "data": {}}
                yield {"type": "done"}

        core = WispAgentCore(provider=P(), security=SecurityPolicy(
            permission_mode=PermissionMode.FULL), extensions=ExtensionHost())

        async def run():
            sess = {"id": "cache-test", "messages": [], "model": "m",
                    "workspace": ws}
            async for _ in core.turn(sess, "one"):
                pass
            # user remembers something between turns (same process!)
            from wisp.memory import _get_memory_file
            facts[0] = [{"content": "mid-process fact MARKER-Q"}]
            _get_memory_file().touch()
            async for _ in core.turn(sess, "two"):
                pass

        loop = __import__("asyncio").new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

        assert len(captured) == 2
        assert "MARKER-Q" not in captured[0]
        assert "MARKER-Q" in captured[1], (
            "remembered fact invisible until restart — cache never busted")
