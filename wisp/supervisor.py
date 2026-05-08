"""Supervisor layer for the terminal app runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from wisp.config import WISP_CONFIG_DIR
from wisp.core.agent import WispAgentCore
from wisp.core.events import TYPE_DONE, TYPE_ERROR, AgentEvent
from wisp.persistence.sqlite_store import RunRecord, SQLiteStateStore, ThreadRecord
from wisp.runtime_protocol import AppEvent


class WispSupervisor:
    """Owns thread/run metadata and runtime artifact paths."""

    def __init__(
        self,
        store: SQLiteStateStore | None = None,
        artifacts_dir: Path | None = None,
        agent_factory: Callable = WispAgentCore,
    ):
        self.store = store or SQLiteStateStore(WISP_CONFIG_DIR / "app.db")
        self.artifacts_dir = Path(artifacts_dir or (WISP_CONFIG_DIR / "artifacts"))
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.agent_factory = agent_factory

    def create_thread(self, workspace: str, title: str | None = None) -> ThreadRecord:
        thread_title = title or Path(workspace).name or "Workspace thread"
        return self.store.create_thread(title=thread_title, workspace=workspace)

    def list_threads(self) -> list[ThreadRecord]:
        return self.store.list_threads()

    def start_run(self, thread_id: str, prompt: str) -> RunRecord:
        run = self.store.create_run(thread_id=thread_id, prompt=prompt, status="queued")
        self.run_log_path(run.id).touch()
        return run

    def run_log_path(self, run_id: str) -> Path:
        path = self.artifacts_dir / "runs" / f"{run_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def append_run_event(self, run_id: str, event: AppEvent) -> None:
        path = self.run_log_path(run_id)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def read_run_events(self, run_id: str) -> list[AppEvent]:
        path = self.run_log_path(run_id)
        if not path.exists():
            return []
        events: list[AppEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            events.append(AppEvent.from_dict(json.loads(line)))
        return events

    def _translate_agent_event(self, thread_id: str, run_id: str, event: AgentEvent) -> AppEvent:
        if event.type == TYPE_DONE:
            return AppEvent(
                event="run.completed",
                thread_id=thread_id,
                run_id=run_id,
                payload=event.data,
            )
        return AppEvent(
            event=f"agent.{event.type}",
            thread_id=thread_id,
            run_id=run_id,
            payload=event.data,
        )

    async def execute_prompt(
        self,
        config,
        prompt: str,
        thread_id: str | None = None,
        title: str | None = None,
    ) -> tuple[ThreadRecord, RunRecord, list[AppEvent]]:
        workspace = config.workspace or "."
        thread = self.store.get_thread(thread_id) if thread_id else None
        if thread is None:
            thread = self.create_thread(workspace=workspace, title=title)

        run = self.start_run(thread.id, prompt)
        self.store.update_run_status(run.id, "running")

        events: list[AppEvent] = []
        started = AppEvent(
            event="run.started",
            thread_id=thread.id,
            run_id=run.id,
            payload={"prompt": prompt, "workspace": workspace},
        )
        self.append_run_event(run.id, started)
        events.append(started)

        failed = False
        agent = self.agent_factory(config)
        async for agent_event in agent.run(prompt):
            app_event = self._translate_agent_event(thread.id, run.id, agent_event)
            self.append_run_event(run.id, app_event)
            events.append(app_event)
            if agent_event.type == TYPE_ERROR and not agent_event.data.get("recoverable", True):
                failed = True

        if failed:
            self.store.update_run_status(run.id, "failed")
        else:
            self.store.update_run_status(run.id, "completed")

        saved_run = self.store.get_run(run.id) or run
        return thread, saved_run, events
