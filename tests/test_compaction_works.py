"""Compaction summarizes, shrinks the session the REPL actually uses, and never leaves a broken history.

Reported 2026-10-13: "auto context compaction in repl is not working, and when using the slash
command the summary is not generating and agent get crashed". Reproduced in the real REPL (pty,
a local provider stub, and real NVIDIA):

* no summarization request was ever sent: the summarizer runs only with `compaction_model`
  set, and nothing sets it, so every compaction kept excerpts and said "what was said is gone";
* `/compact` on 14 messages printed "14 → 15 messages (-1 removed)": a forced compaction kept
  `max_messages // 2` (more than the session) and added an empty summary;
* compaction replaced `session["messages"]` with a new list, and `/compact` compacted a copy of
  the session, so the REPL's `AgentAdapter.messages` and the session its next turn runs on kept
  the old history;
* when compaction raised, `/compact` truncated to the last 10 messages, which can start on a tool
  result whose call was cut away — a history strict providers reject on the next request.
"""

import pytest

from wisp.core.compaction import Compactor
from wisp.core.runtime import AgentRuntime


class _SummaryProvider:
    def __init__(self, text="SUMMARY: decided to use a cosine schedule; model.py written."):
        self.text, self.calls = text, []

    def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
        self.calls.append(messages)
        yield {"type": "content", "text": self.text}
        yield {"type": "done"}


@pytest.fixture
def runtime(tmp_path):
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import PermissionMode, SecurityPolicy
    from wisp.infra.store import UnifiedStore
    from wisp.infra.telemetry import Telemetry

    provider = _SummaryProvider()
    rt = AgentRuntime(
        store=UnifiedStore(tmp_path / "test.db"),
        security=SecurityPolicy(permission_mode=PermissionMode.FULL),
        extensions=ExtensionHost(),
        telemetry=Telemetry(),
        core_factory=lambda: None,
        compactor=Compactor(provider_factory=lambda model: provider, compaction_model="session-model"),
    )
    rt._summary_provider = provider
    return rt


def _conversation(session, exchanges=7):
    """User turns with a tool exchange in each: the shape a coding session has."""
    for i in range(exchanges):
        session["messages"].append({"role": "user", "content": f"step {i}"})
        session["messages"].append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": {"path": f"f{i}.py"}}}]})
        session["messages"].append({"role": "tool", "tool_call_id": f"c{i}", "content": f"contents of f{i}"})
        session["messages"].append({"role": "assistant", "content": f"done step {i}"})


def _starts_cleanly(messages):
    """No tool result without its assistant tool call before it."""
    open_ids = set()
    for m in messages:
        if m.get("role") == "assistant":
            open_ids |= {tc.get("id") for tc in m.get("tool_calls") or []}
        if m.get("role") == "tool" and m.get("tool_call_id") not in open_ids:
            return False
    return True


class TestRuntimeCompaction:
    @pytest.mark.asyncio
    async def test_compaction_changes_the_session_list_in_place(self, runtime):
        session = await runtime.get_or_create_session("s1", "m", "/tmp")
        _conversation(session, 15)
        held = session["messages"]  # what AgentAdapter.messages holds
        await runtime.maybe_compact(session, max_messages=20)
        assert session["messages"] is held
        assert len(held) < 60

    @pytest.mark.asyncio
    async def test_a_forced_compaction_of_a_short_session_shrinks_it(self, runtime):
        session = await runtime.get_or_create_session("s2", "m", "/tmp")
        _conversation(session, 4)  # 16 messages, under the default threshold
        before = len(session["messages"])
        result = await runtime.maybe_compact(session, force=True)
        assert result and result["after_count"] < before, result
        assert runtime._summary_provider.calls, "the summarizer was not asked"
        assert "cosine schedule" in session["messages"][0]["content"]
        assert _starts_cleanly(session["messages"])

    @pytest.mark.asyncio
    async def test_nothing_to_compact_changes_nothing(self, runtime):
        session = await runtime.get_or_create_session("s3", "m", "/tmp")
        session["messages"] += [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        snapshot = list(session["messages"])
        assert await runtime.maybe_compact(session, force=True) is None
        assert session["messages"] == snapshot

    @pytest.mark.asyncio
    async def test_a_session_without_compaction_history_still_compacts(self, runtime):
        session = await runtime.get_or_create_session("s4", "m", "/tmp")
        _conversation(session, 15)
        session.pop("compaction_history", None)
        result = await runtime.maybe_compact(session, max_messages=20)
        assert result and result["compacted"]
        assert len(session["compaction_history"]) == 1


class TestSummarizer:
    def test_without_a_compaction_model_the_session_model_summarizes(self, tmp_path):
        from wisp.composition import CompositionRoot
        from wisp.config import WispConfig

        cfg = WispConfig().replace(workspace=str(tmp_path), model="the-session-model", compaction_model="")
        assert CompositionRoot(config=cfg).compactor.compaction_model == "the-session-model"

    def test_a_configured_compaction_model_still_wins(self, tmp_path):
        from wisp.composition import CompositionRoot
        from wisp.config import WispConfig

        cfg = WispConfig().replace(workspace=str(tmp_path), model="big", compaction_model="small")
        assert CompositionRoot(config=cfg).compactor.compaction_model == "small"

    def test_the_text_sent_to_the_summarizer_is_bounded(self):
        compactor = Compactor(provider_factory=lambda m: None, compaction_model="m")
        huge = [{"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 50_000} for i in range(300)]
        text = compactor._format_messages(huge)
        assert len(text) <= compactor.max_summary_input_chars
        assert "[tool]" in text


class TestSlashCompact:
    def _adapter(self, runtime, session):
        import asyncio

        from wisp.transport.cli import AgentAdapter

        class _Cfg:
            compact_keep_recent = 10
        return AgentAdapter(runtime=runtime, config=_Cfg(), session=session, loop=asyncio.new_event_loop())

    def test_compact_shrinks_the_session_the_next_turn_uses(self, runtime):
        import asyncio

        from wisp.commands import dispatch

        session = asyncio.new_event_loop().run_until_complete(runtime.get_or_create_session("s5", "m", "/tmp"))
        _conversation(session, 4)
        before = len(session["messages"])
        adapter = self._adapter(runtime, session)
        assert dispatch("/compact", adapter) is True
        assert len(adapter.session["messages"]) < before
        assert adapter.messages is adapter.session["messages"]
        assert "cosine schedule" in adapter.session["messages"][0]["content"]
        stored = runtime.store.load_session("s5")
        assert len(stored["messages"]) == len(adapter.session["messages"])

    def test_when_compaction_fails_the_fallback_keeps_a_valid_history(self, runtime, monkeypatch, capsys):
        import asyncio

        from wisp.commands import dispatch

        session = asyncio.new_event_loop().run_until_complete(runtime.get_or_create_session("s6", "m", "/tmp"))
        session["messages"].append({"role": "system", "content": "persona"})
        _conversation(session, 4)

        async def boom(*a, **k):
            raise RuntimeError("summarizer exploded")
        monkeypatch.setattr(runtime, "maybe_compact", boom)
        adapter = self._adapter(runtime, session)
        dispatch("/compact", adapter)
        msgs = adapter.session["messages"]
        assert adapter.messages is msgs
        assert _starts_cleanly(msgs), [m.get("role") for m in msgs]
        assert msgs[0] == {"role": "system", "content": "persona"}
        assert "summarizer exploded" in capsys.readouterr().out


class TestCompactionIsJournaled:
    """Replay after a compaction rebuilt the uncompacted transcript, so the next turn's digest
    failed ("the turn recorded X but replay produced Y", field session 13fb33ef: three
    compactions, divergence at the first digest after the first one) and recovery could not use
    the journal. Nothing emitted the `compacted` event, and applying it never shortened the
    transcript."""

    def _runtime(self, tmp_path):
        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.core.session_repo import SessionRepository
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.security import SecurityPolicy
        from wisp.infra.store import UnifiedStore
        from wisp.infra.telemetry import Telemetry

        class _P:
            def generate_stream_events(self, system_prompt, messages, tools=None):
                yield {"type": "content", "text": f"reply {len(messages)}"}
                yield {"type": "done", "done_reason": "stop"}

        ws = tmp_path / "ws"
        ws.mkdir()
        config = WispConfig().replace(workspace=str(ws))
        store = UnifiedStore(tmp_path / "wisp.db")
        repo = SessionRepository(store)
        runtime = AgentRuntime(
            store=store, security=SecurityPolicy(), extensions=ExtensionHost(), telemetry=Telemetry(),
            core_factory=lambda: WispAgentCore(config=config, provider=_P(), security=SecurityPolicy(),
                                               tool_executor=None),
            session_repo=repo, config=config)
        session = {"id": f"journal-{tmp_path.name}", "model": "mock", "workspace": str(ws), "messages": []}
        return runtime, repo, session

    @staticmethod
    async def _turns(runtime, session, n):
        for i in range(n):
            [ev async for ev in runtime.run_turn(session, prompt=f"prompt {i}")]

    def _assert_replays(self, repo, session):
        from wisp.core.replay_digest import projection

        seqs = [e.sequence_num for e in repo.load_events(session["id"])]
        assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), "the journal must stay gap-free and strictly increasing"
        out = repo.reconstruct(session["id"])  # raises ReplayDivergence on a mismatch
        assert out["_source"] == "journal"
        assert projection(out["messages"]) == projection(session["messages"])
        assert len(out["compaction_history"]) == len(session["compaction_history"]) >= 1

    def test_a_forced_compaction_replays(self, tmp_path):
        import asyncio
        runtime, repo, session = self._runtime(tmp_path)
        asyncio.run(self._turns(runtime, session, 7))
        assert asyncio.run(runtime.maybe_compact(session, force=True))
        asyncio.run(self._turns(runtime, session, 2))
        self._assert_replays(repo, session)

    def test_an_automatic_compaction_inside_a_turn_replays(self, tmp_path):
        import asyncio
        runtime, repo, session = self._runtime(tmp_path)
        runtime.max_messages = 8
        asyncio.run(self._turns(runtime, session, 9))
        assert session["compaction_history"], "the threshold never triggered"
        self._assert_replays(repo, session)
