"""Minimal Textual-based terminal app shell for Wisp."""

from __future__ import annotations

from pathlib import Path

from wisp.config import WispConfig
from wisp.supervisor import WispSupervisor

try:  # pragma: no cover - import path depends on environment
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, RichLog, Static

    TEXTUAL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised through fallback behavior
    TEXTUAL_AVAILABLE = False

    class WispTUIApp:
        """Fallback shim when Textual is unavailable in the environment."""

        TITLE = "Wisp Terminal App"

        def __init__(self, config: WispConfig, supervisor: WispSupervisor | None = None):
            self.config = config
            self.supervisor = supervisor or WispSupervisor()
            self.title = self.TITLE

        def format_timeline_event(self, event):
            return f"{event.event}: {event.payload}"

        async def submit_prompt(self, prompt: str):
            raise RuntimeError(
                "The terminal app requires the 'textual' package. "
                "Install project dependencies to use `wisp tui`."
            )

        def run(self):
            raise RuntimeError(
                "The terminal app requires the 'textual' package. "
                "Install project dependencies to use `wisp tui`."
            )

else:
    class WispTUIApp(App):
        """Foundational full-screen terminal app for Wisp."""

        TITLE = "Wisp Terminal App"
        CSS = """
        #body {
            height: 1fr;
        }

        #threads-pane, #timeline-pane, #details-pane {
            width: 1fr;
            border: round $panel;
            padding: 0 1;
        }

        .pane-title {
            text-style: bold;
            margin-bottom: 1;
        }

        #prompt {
            dock: bottom;
        }
        """

        def __init__(self, config: WispConfig, supervisor: WispSupervisor | None = None):
            super().__init__()
            self.config = config
            self.supervisor = supervisor or WispSupervisor()

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id="body"):
                with Vertical(id="threads-pane"):
                    yield Static("Threads", classes="pane-title")
                    yield ListView(id="threads")
                with Vertical(id="timeline-pane"):
                    yield Static("Run Timeline", classes="pane-title")
                    yield RichLog(id="timeline", wrap=True, highlight=True)
                with Vertical(id="details-pane"):
                    yield Static("Details", classes="pane-title")
                    yield Static("", id="details")
            yield Input(placeholder="Ask Wisp about this repository...", id="prompt")
            yield Footer()

        def on_mount(self) -> None:
            self.refresh_threads()
            timeline = self.query_one("#timeline", RichLog)
            details = self.query_one("#details", Static)
            timeline.write(f"Workspace: {self.config.workspace}")
            details.update(
                "\n".join(
                    [
                        f"Provider: {self.config.provider}",
                        f"Model: {self.config.model}",
                        f"Workspace: {self.config.workspace}",
                    ]
                )
            )

        def refresh_threads(self) -> None:
            thread_list = self.query_one("#threads", ListView)
            thread_list.clear()
            for thread in self.supervisor.list_threads():
                thread_list.append(
                    ListItem(
                        Label(f"{thread.title}\n{thread.workspace}", id=f"thread-{thread.id}")
                    )
                )

        def format_timeline_event(self, event: object) -> str:
            event_name = getattr(event, "event", "")
            payload = getattr(event, "payload", {}) or {}
            run_id = getattr(event, "run_id", "")

            if event_name == "run.started":
                return f"[run {run_id}] started: {payload.get('prompt', '')}"
            if event_name == "agent.content":
                return payload.get("text", "")
            if event_name == "agent.thinking":
                return f"thinking: {payload.get('text', '')}"
            if event_name == "agent.tool_call":
                return f"tool: {payload.get('name', '')}"
            if event_name == "agent.tool_result":
                return f"tool result: {payload.get('name', '')}"
            if event_name == "run.completed":
                return f"[run {run_id}] completed"
            if event_name == "agent.error":
                return f"error: {payload.get('message', '')}"
            return f"{event_name}: {payload}"

        async def submit_prompt(self, prompt: str):
            if not prompt:
                return None

            workspace = self.config.workspace or str(Path.cwd())
            threads = self.supervisor.list_threads()
            thread_id = threads[-1].id if threads else None
            thread, run, events = await self.supervisor.execute_prompt(
                self.config,
                prompt,
                thread_id=thread_id,
            )

            self.refresh_threads()
            timeline = self.query_one("#timeline", RichLog)
            for app_event in events:
                timeline.write(self.format_timeline_event(app_event))

            return thread, run, events

        async def on_input_submitted(self, event: Input.Submitted) -> None:
            prompt = event.value.strip()
            if not prompt:
                return

            await self.submit_prompt(prompt)
            event.input.value = ""
