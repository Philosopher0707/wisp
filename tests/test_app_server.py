"""Tests for the protocol-first app-server layer."""

from dataclasses import dataclass

import pytest

from wisp.app_server import WispAppServer
from wisp.runtime_protocol import AppEvent, JsonRpcRequest


@dataclass(frozen=True)
class FakeThread:
    id: str
    title: str
    workspace: str
    status: str = "idle"
    created_at: str = "2026-05-07T00:00:00+00:00"
    updated_at: str = "2026-05-07T00:00:00+00:00"


@dataclass(frozen=True)
class FakeRun:
    id: str
    thread_id: str
    prompt: str
    status: str = "completed"
    created_at: str = "2026-05-07T00:00:00+00:00"
    updated_at: str = "2026-05-07T00:00:00+00:00"


class FakeSupervisor:
    def __init__(self):
        self.created = []
        self.executed = []
        self.threads = [
            FakeThread(id="thread-1", title="Repo thread", workspace="/tmp/repo")
        ]

    def list_threads(self):
        return list(self.threads)

    def create_thread(self, workspace: str, title: str | None = None):
        thread = FakeThread(
            id=f"thread-{len(self.threads) + 1}",
            title=title or "Workspace thread",
            workspace=workspace,
        )
        self.created.append((workspace, title))
        self.threads.append(thread)
        return thread

    async def execute_prompt(self, config, prompt: str, thread_id=None, title=None):
        self.executed.append((config.workspace, prompt, thread_id, title))
        thread = self.threads[0]
        run = FakeRun(id="run-1", thread_id=thread.id, prompt=prompt)
        events = [
            AppEvent(event="run.started", thread_id=thread.id, run_id=run.id, payload={"prompt": prompt}),
            AppEvent(event="agent.content", thread_id=thread.id, run_id=run.id, payload={"text": "Done"}),
            AppEvent(event="run.completed", thread_id=thread.id, run_id=run.id, payload={"turns": 1}),
        ]
        return thread, run, events

    def read_run_events(self, run_id: str):
        return [
            AppEvent(event="run.started", thread_id="thread-1", run_id=run_id, payload={"prompt": "x"}),
            AppEvent(event="run.completed", thread_id="thread-1", run_id=run_id, payload={"turns": 1}),
        ]


@pytest.mark.asyncio
async def test_threads_list_returns_threads():
    server = WispAppServer(supervisor=FakeSupervisor())
    response = await server.handle_request(JsonRpcRequest(id="1", method="threads.list"))
    assert response.error is None
    assert response.result["threads"][0]["id"] == "thread-1"


@pytest.mark.asyncio
async def test_threads_create_creates_thread():
    supervisor = FakeSupervisor()
    server = WispAppServer(supervisor=supervisor)
    response = await server.handle_request(
        JsonRpcRequest(
            id="2",
            method="threads.create",
            params={"workspace": "/tmp/new", "title": "New thread"},
        )
    )
    assert response.error is None
    assert response.result["thread"]["workspace"] == "/tmp/new"
    assert supervisor.created == [("/tmp/new", "New thread")]


@pytest.mark.asyncio
async def test_runs_execute_uses_supervisor():
    supervisor = FakeSupervisor()
    server = WispAppServer(supervisor=supervisor)
    response = await server.handle_request(
        JsonRpcRequest(
            id="3",
            method="runs.execute",
            params={"workspace": "/tmp/repo", "prompt": "Explain the repo"},
        )
    )
    assert response.error is None
    assert response.result["run"]["id"] == "run-1"
    assert response.result["events"][1]["event"] == "agent.content"
    assert supervisor.executed == [("/tmp/repo", "Explain the repo", None, None)]


@pytest.mark.asyncio
async def test_runs_events_returns_persisted_events():
    server = WispAppServer(supervisor=FakeSupervisor())
    response = await server.handle_request(
        JsonRpcRequest(id="4", method="runs.events", params={"run_id": "run-9"})
    )
    assert response.error is None
    assert [event["event"] for event in response.result["events"]] == [
        "run.started",
        "run.completed",
    ]


@pytest.mark.asyncio
async def test_unknown_method_returns_jsonrpc_error():
    server = WispAppServer(supervisor=FakeSupervisor())
    response = await server.handle_request(
        JsonRpcRequest(id="5", method="threads.destroy")
    )
    assert response.error is not None
    assert response.error.code == -32601
