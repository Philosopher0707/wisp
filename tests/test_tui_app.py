"""Tests for the minimal terminal app shell."""

from types import SimpleNamespace

import pytest

from wisp.config import WispConfig
from wisp.persistence.sqlite_store import SQLiteStateStore
from wisp.runtime_protocol import AppEvent
from wisp.supervisor import WispSupervisor
from wisp.tui.app import WispTUIApp


def test_tui_app_constructs_with_supervisor(tmp_path):
    store = SQLiteStateStore(tmp_path / "wisp.db")
    supervisor = WispSupervisor(store=store, artifacts_dir=tmp_path / "artifacts")
    app = WispTUIApp(config=WispConfig(), supervisor=supervisor)
    assert app.title == "Wisp Terminal App"


def test_tui_formats_content_event(tmp_path):
    store = SQLiteStateStore(tmp_path / "wisp.db")
    supervisor = WispSupervisor(store=store, artifacts_dir=tmp_path / "artifacts")
    app = WispTUIApp(config=WispConfig(), supervisor=supervisor)
    line = app.format_timeline_event(
        AppEvent(
            event="agent.content",
            thread_id="thread-1",
            run_id="run-1",
            payload={"text": "Hello from Wisp"},
        )
    )
    assert "Hello from Wisp" in line


class FakeSupervisor:
    def __init__(self):
        self.prompts = []
        self.threads = []

    def list_threads(self):
        return list(self.threads)

    async def execute_prompt(self, config, prompt, thread_id=None, title=None):
        self.prompts.append(prompt)
        thread = SimpleNamespace(id="thread-1", title="Workspace thread", workspace=config.workspace)
        run = SimpleNamespace(id="run-1", status="completed")
        self.threads = [thread]
        return thread, run, [
            AppEvent(event="run.started", thread_id=thread.id, run_id=run.id, payload={"prompt": prompt}),
            AppEvent(event="agent.content", thread_id=thread.id, run_id=run.id, payload={"text": "Completed"}),
            AppEvent(event="run.completed", thread_id=thread.id, run_id=run.id, payload={"turns": 1}),
        ]


@pytest.mark.asyncio
async def test_tui_submit_prompt_uses_supervisor():
    config = WispConfig()
    config.workspace = "/tmp/project"
    supervisor = FakeSupervisor()
    app = WispTUIApp(config=config, supervisor=supervisor)

    async with app.run_test() as pilot:
        await pilot.pause()
        await app.submit_prompt("Describe this project")
        assert supervisor.prompts == ["Describe this project"]
