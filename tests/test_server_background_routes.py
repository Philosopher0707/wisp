"""Tests for the background-agents server surface.

REST: /api/agents/background* routes against a stubbed composition root.
WebSocket: agents_list / agents_get / agents_cancel / agents_send frames.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wisp.multi_agent.background import BackgroundAgentManager
from wisp.multi_agent.task import SubagentContract
from tests.test_background_agents import FakeOrchestrator


def _app_with_root(root) -> FastAPI:
    from wisp.server.routes.background import router
    app = FastAPI()
    app.include_router(router)
    app.state.root = root
    return app


async def _finished_manager(session_id="sess-live") -> tuple[BackgroundAgentManager, str]:
    """Manager with one completed agent; returns (manager, agent_id)."""
    orch = FakeOrchestrator(delay=0.0, session_id=session_id)
    mgr = BackgroundAgentManager(orch)
    launch = await mgr.launch(SubagentContract(name="bg-x", task="t"))
    await mgr.result(launch["agent_id"], wait_seconds=2.0)
    return mgr, launch["agent_id"]


class TestBackgroundRestRoutes:
    def test_list_requires_root(self):
        client = TestClient(_app_with_root(None))
        resp = client.get("/api/agents/background")
        assert resp.status_code == 503

    def test_list_empty(self):
        root = SimpleNamespace(background_agents=BackgroundAgentManager(FakeOrchestrator()))
        client = TestClient(_app_with_root(root))
        resp = client.get("/api/agents/background")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"agents": [], "count": 0}

    @pytest.mark.asyncio
    async def test_list_shows_finished_agent(self):
        mgr, _ = await _finished_manager()
        root = SimpleNamespace(background_agents=mgr)
        client = TestClient(_app_with_root(root))
        body = client.get("/api/agents/background").json()
        assert body["count"] == 1
        assert body["agents"][0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_detail_404_unknown(self):
        mgr, _ = await _finished_manager()
        client = TestClient(_app_with_root(SimpleNamespace(background_agents=mgr)))
        assert client.get("/api/agents/background/bg-nope").status_code == 404

    @pytest.mark.asyncio
    async def test_detail_snapshot_fields(self):
        mgr, agent_id = await _finished_manager()
        client = TestClient(_app_with_root(SimpleNamespace(background_agents=mgr)))
        body = client.get(f"/api/agents/background/{agent_id}").json()
        assert body["agent_id"] == agent_id
        assert body["result"]["ok"] is True

    @pytest.mark.asyncio
    async def test_cancel_conflict_when_not_running(self):
        mgr, agent_id = await _finished_manager()
        client = TestClient(_app_with_root(SimpleNamespace(background_agents=mgr)))
        resp = client.post(f"/api/agents/background/{agent_id}/cancel")
        assert resp.status_code == 409

    def test_cancel_404_unknown(self):
        mgr = BackgroundAgentManager(FakeOrchestrator())
        client = TestClient(_app_with_root(SimpleNamespace(background_agents=mgr)))
        assert client.post("/api/agents/background/bg-x/cancel").status_code == 404

    @pytest.mark.asyncio
    async def test_send_starts_continuation(self):
        mgr, agent_id = await _finished_manager(session_id="sess-cont")
        client = TestClient(_app_with_root(SimpleNamespace(background_agents=mgr)))
        resp = client.post(
            f"/api/agents/background/{agent_id}/send",
            json={"message": "go deeper"},
        )
        assert resp.status_code == 200
        snap = await mgr.result(agent_id, wait_seconds=2.0)
        assert snap["turns"] == 2

    @pytest.mark.asyncio
    async def test_send_rejects_running_agent(self):
        orch = FakeOrchestrator(delay=5.0)
        mgr = BackgroundAgentManager(orch)
        launch = await mgr.launch(SubagentContract(name="bg-r", task="t"))
        client = TestClient(_app_with_root(SimpleNamespace(background_agents=mgr)))
        resp = client.post(
            f"/api/agents/background/{launch['agent_id']}/send",
            json={"message": "hi"},
        )
        assert resp.status_code == 409
        mgr.cancel(launch["agent_id"])

    @pytest.mark.asyncio
    async def test_send_validates_message(self):
        mgr, agent_id = await _finished_manager()
        client = TestClient(_app_with_root(SimpleNamespace(background_agents=mgr)))
        resp = client.post(
            f"/api/agents/background/{agent_id}/send",
            json={},
        )
        assert resp.status_code == 422  # pydantic required field


# ── WebSocket frame handling ─────────────────────────────────────────

def _ws_with_root(root, frames: list[str]):
    # The WS handler builds a transport from root.runtime before dispatch;
    # a bare mock satisfies construction without touching real sessions.
    if getattr(root, "runtime", None) is None:
        root.runtime = MagicMock()
    ws = AsyncMock()
    ws.app.state.root = root
    ws.receive_text.side_effect = [*frames, Exception("close")]
    return ws


def _sent(ws, msg_type: str) -> dict | None:
    for call in ws.send_json.call_args_list:
        payload = call.args[0] if call.args else call.kwargs.get("obj")
        if isinstance(payload, dict) and payload.get("type") == msg_type:
            return payload
    return None


class TestBackgroundWebsocketFrames:
    @pytest.mark.asyncio
    async def test_agents_list_frame(self):
        from wisp.server.routes.agents import agent_websocket
        mgr, _ = await _finished_manager()
        root = SimpleNamespace(background_agents=mgr)
        ws = _ws_with_root(root, ['{"type": "agents_list"}'])
        await agent_websocket(ws)
        sent = _sent(ws, "agents_list")
        assert sent is not None and sent["count"] == 1

    @pytest.mark.asyncio
    async def test_agents_list_without_root_errors(self):
        from wisp.server.routes.agents import agent_websocket
        ws = _ws_with_root(SimpleNamespace(), ['{"type": "agents_list"}'])
        await agent_websocket(ws)
        assert _sent(ws, "error") is not None

    @pytest.mark.asyncio
    async def test_agents_get_unknown_errors(self):
        from wisp.server.routes.agents import agent_websocket
        mgr = BackgroundAgentManager(FakeOrchestrator())
        root = SimpleNamespace(background_agents=mgr)
        ws = _ws_with_root(root, ['{"type": "agents_get", "agent_id": "bg-none"}'])
        await agent_websocket(ws)
        assert _sent(ws, "error") is not None

    @pytest.mark.asyncio
    async def test_agents_get_returns_snapshot(self):
        from wisp.server.routes.agents import agent_websocket
        mgr, agent_id = await _finished_manager()
        root = SimpleNamespace(background_agents=mgr)
        ws = _ws_with_root(root, [f'{{"type": "agents_get", "agent_id": "{agent_id}"}}'])
        await agent_websocket(ws)
        sent = _sent(ws, "agents_snapshot")
        assert sent is not None and sent["agent_id"] == agent_id

    @pytest.mark.asyncio
    async def test_agents_cancel_frame(self):
        from wisp.server.routes.agents import agent_websocket
        orch = FakeOrchestrator(delay=5.0)
        mgr = BackgroundAgentManager(orch)
        launch = await mgr.launch(SubagentContract(name="bg-c", task="t"))
        root = SimpleNamespace(background_agents=mgr)
        ws = _ws_with_root(root, [f'{{"type": "agents_cancel", "agent_id": "{launch["agent_id"]}"}}'])
        await agent_websocket(ws)
        assert _sent(ws, "agents_cancelled") is not None

    @pytest.mark.asyncio
    async def test_agents_send_frame(self):
        from wisp.server.routes.agents import agent_websocket
        mgr, agent_id = await _finished_manager(session_id="sess-ws")
        root = SimpleNamespace(background_agents=mgr)
        ws = _ws_with_root(root, [
            f'{{"type": "agents_send", "agent_id": "{agent_id}", "message": "again"}}'
        ])
        await agent_websocket(ws)
        assert _sent(ws, "agents_continuation") is not None
        snap = await mgr.result(agent_id, wait_seconds=2.0)
        assert snap["turns"] == 2



async def _next_event(queue: "asyncio.Queue", type_: str) -> dict:
    """Read past lifecycle noise to the next event of the wanted type."""
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=2.0)
        if event["type"] == type_:
            return event

class TestSettlementPubSub:
    """Manager-level fan-out: subscribers get one event per terminal state."""

    @pytest.mark.asyncio
    async def test_subscriber_receives_settlement(self):
        mgr, agent_id = await _finished_manager()
        queue = mgr.subscribe()
        launch = await mgr.launch(SubagentContract(name="bg-y", task="second"))
        await mgr.result(launch["agent_id"], wait_seconds=2.0)
        event = await _next_event(queue, "agent_settled")
        assert event["status"] == "completed"
        assert event["agent_id"] == launch["agent_id"]
        assert event["status"] == "completed"
        assert event["ok"] is True
        assert "task" in event

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(self):
        mgr, _ = await _finished_manager()
        queue = mgr.subscribe()
        mgr.unsubscribe(queue)
        launch = await mgr.launch(SubagentContract(name="bg-z", task="third"))
        await mgr.result(launch["agent_id"], wait_seconds=2.0)
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_cancel_publishes_settlement(self):
        orch = FakeOrchestrator(delay=10.0)
        mgr = BackgroundAgentManager(orch)
        queue = mgr.subscribe()
        launch = await mgr.launch(SubagentContract(name="bg-c", task="slow"))
        await asyncio.sleep(0.05)
        mgr.cancel(launch["agent_id"])
        event = await _next_event(queue, "agent_settled")
        assert event["status"] == "cancelled"
        assert event["ok"] is False


class TestWsSettlementPush:
    """WebSocket pusher: settlements become send_json frames per client."""

    @pytest.mark.asyncio
    async def test_pusher_delivers_frame(self):
        from wisp.server.routes.agents import agent_settlement_pusher
        import asyncio as _asyncio
        mock_ws = AsyncMock()
        mgr = BackgroundAgentManager(FakeOrchestrator(delay=0.0))
        task = _asyncio.create_task(agent_settlement_pusher(mgr, mock_ws))
        launch = await mgr.launch(SubagentContract(name="bg-push", task="t"))
        await mgr.result(launch["agent_id"], wait_seconds=2.0)
        for _ in range(50):
            if mock_ws.send_json.call_args_list:
                break
            await _asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except _asyncio.CancelledError:
            pass
        frames = [c.args[0] for c in mock_ws.send_json.call_args_list]
        settled = [f for f in frames if f.get("type") == "agent_settled"]
        assert settled and settled[0]["agent_id"] == launch["agent_id"]

    @pytest.mark.asyncio
    async def test_handler_wires_and_cleans_pusher(self):
        from wisp.server.routes.agents import agent_websocket
        mgr = BackgroundAgentManager(FakeOrchestrator())
        root = SimpleNamespace(runtime=MagicMock(), background_agents=mgr)
        mock_ws = AsyncMock()
        mock_ws.client = None
        mock_ws.app.state.root = root
        mock_ws.receive_text.side_effect = ['{"type": "ping"}', Exception("close")]
        with patch("wisp.server.routes.agents._auth") as mock_auth:
            mock_auth.required = False
            await agent_websocket(mock_ws)
        # Pusher subscribed during the loop and unsubscribed on disconnect.
        assert len(mgr._subscribers) == 0

    @pytest.mark.asyncio
    async def test_no_root_no_pusher(self):
        from wisp.server.routes.agents import agent_websocket
        mock_ws = AsyncMock()
        mock_ws.receive_text.side_effect = ['{"type": "ping"}', Exception("close")]
        with patch("wisp.server.routes.agents._auth") as mock_auth:
            mock_auth.required = False
            await agent_websocket(mock_ws)  # must not raise

class TestLifecycleEventStream:
    """Full lifecycle: started -> (progress on continuation) -> settled."""

    @pytest.mark.asyncio
    async def test_started_then_settled_in_order(self):
        orch = FakeOrchestrator(delay=0.0)
        mgr = BackgroundAgentManager(orch)
        queue = mgr.subscribe()
        launch = await mgr.launch(SubagentContract(name="bg-lc", task="t"))
        await mgr.result(launch["agent_id"], wait_seconds=2.0)
        events = []
        while not queue.empty():
            events.append(await asyncio.wait_for(queue.get(), timeout=1.0))
        types = [e["type"] for e in events]
        assert types == ["agent_started", "agent_settled"]
        assert all(e["agent_id"] == launch["agent_id"] for e in events)
        assert events[0]["task"].startswith("t")

    @pytest.mark.asyncio
    async def test_send_publishes_progress(self):
        orch = FakeOrchestrator(delay=0.0, session_id="sess-prog")
        mgr = BackgroundAgentManager(orch)
        queue = mgr.subscribe()
        launch = await mgr.launch(SubagentContract(name="bg-pr", task="first"))
        agent_id = launch["agent_id"]
        await mgr.result(agent_id, wait_seconds=2.0)
        cont = await mgr.send(agent_id, "go deeper")
        assert cont["ok"] is True
        event = await _next_event(queue, "agent_progress")
        assert event["turn"] == 2
        assert event["note"] == "continuation started"
