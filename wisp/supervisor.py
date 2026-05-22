"""Supervisor layer for the terminal app runtime.

Uses UnifiedSessionStore for session/run/event persistence.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from wisp.config import WISP_CONFIG_DIR
from wisp.core.events import TYPE_DONE, TYPE_ERROR, AgentEvent
from wisp.runtime_protocol import AppEvent
from wisp.infra.store import UnifiedStore, get_store


class WispSupervisor:
    """Owns thread/run metadata and runtime artifact paths."""

    def __init__(
        self,
        store: UnifiedStore | None = None,
        artifacts_dir: Path | None = None,
    ):
        self.store = store or get_store()
        self.artifacts_dir = Path(artifacts_dir or (WISP_CONFIG_DIR / "artifacts"))
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def create_thread(self, workspace: str, title: str | None = None):
        thread_title = title or Path(workspace).name or "Workspace thread"
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        self.store.create_session(session_id, model="unknown", workspace=workspace, title=thread_title)
        return self.store.load_session(session_id)

    def list_threads(self):
        return self.store.list_sessions()

    def start_run(self, thread_id: str, prompt: str):
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        run = {
            "id": run_id,
            "session_id": thread_id,
            "prompt": prompt,
            "status": "pending",
            "created_at": now,
        }
        self.store.save_run(run)
        self.run_log_path(run_id).touch()
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
            try:
                events.append(AppEvent.from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
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
    ):
        workspace = config.workspace or "."
        thread = self.store.load_session(thread_id) if thread_id else None
        if thread is None:
            thread = self.create_thread(workspace=workspace, title=title)

        run = self.start_run(thread["id"], prompt)
        self.store.update_run_status(run["id"], "running")

        events: list[AppEvent] = []
        started = AppEvent(
            event="run.started",
            thread_id=thread["id"],
            run_id=run["id"],
            payload={"prompt": prompt, "workspace": workspace},
        )
        self.append_run_event(run["id"], started)
        events.append(started)

        failed = False
        try:
            from wisp.entry import run_headless
            result = await run_headless(
                prompt=prompt,
                model=getattr(config, "model", None),
                workspace=workspace,
                permission_mode=getattr(config, "permission_mode", "full"),
            )
            if not result.get("ok", False):
                failed = True
                app_event = AppEvent(
                    event="run.error",
                    thread_id=thread["id"],
                    run_id=run["id"],
                    payload={"message": result.get("error", "Unknown error"), "recoverable": False},
                )
            else:
                app_event = AppEvent(
                    event="run.completed",
                    thread_id=thread["id"],
                    run_id=run["id"],
                    payload={"content": result.get("content", ""), "turns": result.get("iterations", 0)},
                )
            self.append_run_event(run["id"], app_event)
            events.append(app_event)
        except Exception as exc:
            failed = True
            app_event = AppEvent(
                event="run.error",
                thread_id=thread["id"],
                run_id=run["id"],
                payload={"message": str(exc), "recoverable": False},
            )
            self.append_run_event(run["id"], app_event)
            events.append(app_event)

        if failed:
            self.store.update_run_status(run["id"], "failed")
        else:
            self.store.update_run_status(run["id"], "completed")

        saved_run = self.store.load_run(run["id"]) or run
        return thread, saved_run, events
