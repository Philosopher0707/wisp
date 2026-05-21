"""Tests for the terminal app supervisor and persistence slice."""

import pytest

from wisp.config import WispConfig
from wisp.core.events import content, done
from wisp.adapters import UnifiedSessionStore
from wisp.runtime_protocol import AppEvent
from wisp.supervisor import WispSupervisor


def test_supervisor_creates_and_lists_threads(tmp_path):
    store = UnifiedSessionStore(sessions_dir=tmp_path / "sessions")
    supervisor = WispSupervisor(store=store, artifacts_dir=tmp_path / "artifacts")
    thread = supervisor.create_thread(workspace="/tmp/project", title="Project thread")
    threads = supervisor.list_threads()
    assert [item["id"] for item in threads] == [thread.id]
    assert threads[0]["title"] == "Project thread"


def test_supervisor_creates_run_and_log_file(tmp_path):
    store = UnifiedSessionStore(sessions_dir=tmp_path / "sessions")
    supervisor = WispSupervisor(store=store, artifacts_dir=tmp_path / "artifacts")
    thread = supervisor.create_thread(workspace="/tmp/project", title="Project thread")
    run = supervisor.start_run(thread.id, "Explain the repo")
    assert run.session_id == thread.id
    assert supervisor.run_log_path(run.id).parent.exists()


class FakeAgent:
    def __init__(self, config):
        self.config = config

    async def run(self, prompt):
        yield content(f"Echo: {prompt}")
        yield done("session-1", turns=1)


@pytest.mark.asyncio
async def test_supervisor_executes_prompt_and_persists_events(tmp_path):
    store = UnifiedSessionStore(sessions_dir=tmp_path / "sessions")
    supervisor = WispSupervisor(
        store=store,
        artifacts_dir=tmp_path / "artifacts",
    )
    config = WispConfig()
    config.workspace = "/tmp/project"

    thread, run, events = await supervisor.execute_prompt(config, "Explain the repo")

    saved = store.get_run(run.id)
    assert thread.workspace == "/tmp/project"
    assert saved is not None
    assert saved.status == "completed"
    assert [event.event for event in events] == [
        "run.started",
        "run.completed",
    ]

    logged = supervisor.read_run_events(run.id)
    assert [event.event for event in logged] == [event.event for event in events]
