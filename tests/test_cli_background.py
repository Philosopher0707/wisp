"""CLI agent receives background-agent lifecycle notices.

The WebSocket transport pushes spawn_background settlements over WS
(server/routes/agents.py); the CLI transport printed nothing — agents
settled silently and users had to poll subagent_result blind.
"""

import asyncio

import io

import pytest

from wisp.config import WispConfig
from wisp.transport.cli import CLITransport


class FakeManager:
    """Minimal BackgroundAgentManager surface: subscribe/unsubscribe/publish."""

    def __init__(self):
        self._subscribers: set = set()

    def subscribe(self):
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict) -> None:
        for q in list(self._subscribers):
            q.put_nowait(event)


def _transport(manager) -> tuple[CLITransport, "io.StringIO"]:
    t = CLITransport(runtime=object(), config=WispConfig(), background_agents=manager)
    out = io.StringIO()
    t._stdout = out
    return t, out


SETTLED_OK = {
    "type": "agent_settled",
    "agent_id": "bg-123",
    "label": "researcher",
    "status": "completed",
    "ok": True,
    "turns": 3,
    "elapsed_seconds": 12.4,
    "task": "research VESPA",
    "summary": "found the algorithm",
}
STARTED = {
    "type": "agent_started",
    "agent_id": "bg-124",
    "label": "",
    "status": "running",
}


class TestCliBackgroundNotices:
    @pytest.mark.asyncio
    async def test_settled_notice_rendered(self):
        manager = FakeManager()
        transport, out = _transport(manager)

        async def first_send_triggers_watch():
            await transport.send({"type": "content", "text": "hi"})

        await first_send_triggers_watch()
        assert transport._bg_task is not None
        await asyncio.sleep(0.02)  # let the watcher reach subscribe()
        manager.publish(SETTLED_OK)
        await asyncio.sleep(0.05)
        blob = out.getvalue()
        assert "[bg]" in blob
        assert "researcher" in blob
        assert "bg-123" in blob
        assert "subagent_result" in blob
        transport.stop()

    @pytest.mark.asyncio
    async def test_started_notice_and_anonymous_label(self):
        manager = FakeManager()
        transport, out = _transport(manager)
        await transport.send({"type": "content", "text": "hi"})
        await asyncio.sleep(0)
        manager.publish(STARTED)
        await asyncio.sleep(0.05)
        blob = out.getvalue()
        assert "started" in blob
        assert "bg-124" in blob  # label empty → id shown
        transport.stop()

    @pytest.mark.asyncio
    async def test_error_settlement_uses_fail_mark(self):
        manager = FakeManager()
        transport, out = _transport(manager)
        await transport.send({"type": "content", "text": "hi"})
        await asyncio.sleep(0)
        manager.publish({**SETTLED_OK, "ok": False, "error": "boom"})
        await asyncio.sleep(0.05)
        assert "boom" in out.getvalue()
        transport.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_watcher(self):
        manager = FakeManager()
        transport, _ = _transport(manager)
        await transport.send({"type": "content", "text": "hi"})
        task = transport._bg_task
        transport.stop()
        await asyncio.sleep(0.02)
        assert task.cancelled() or task.done()
        assert transport._bg_task is None

    @pytest.mark.asyncio
    async def test_no_manager_no_task(self):
        transport, _ = _transport(None)
        await transport.send({"type": "content", "text": "hi"})
        assert transport._bg_task is None
