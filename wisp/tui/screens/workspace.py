"""Main workspace — the core interaction surface of Wisp TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import TabbedContent, TabPane

from wisp.config import WispConfig
from wisp.tui.widgets.activity_bar import ActivityBar
from wisp.tui.widgets.chat.input_bar import InputBar
from wisp.tui.widgets.chat.message_list import MessageList
from wisp.tui.widgets.chat.user_message import UserMessage
from wisp.tui.widgets.chat.assistant_message import AssistantMessage
from wisp.tui.widgets.file_tree.tree_view import FileTree
from wisp.tui.widgets.file_tree.code_preview import CodePreview
from wisp.tui.widgets.file_tree.repo_map_summary import RepoMapSummary
from wisp.tui.widgets.agents.agent_grid import AgentGrid
from wisp.tui.widgets.agents.task_tree import TaskTree
from wisp.tui.widgets.agents.token_gauge import TokenGauge
from wisp.tui.widgets.monitor.log_viewer import LogViewer
from wisp.tui.widgets.monitor.metrics import PerformanceMetrics
from wisp.tui.widgets.status_bar import StatusBar
from wisp.tui.widgets.title_bar import TitleBar
from wisp.tui.data.ws_client import WispWSClient


class WorkspaceScreen(Screen):
    """Primary screen containing all panels, tabs, and the chat interface."""

    BINDINGS = [
        Binding("escape", "go_back", "Session picker"),
        Binding("ctrl+backslash", "toggle_context_panel", "Toggle context"),
        Binding("ctrl+p", "toggle_command_palette", "Command palette"),
    ]

    def __init__(
        self,
        server_url: str = "http://localhost:8000",
        session_id: str | None = None,
        config: WispConfig | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.server_url = server_url
        self.session_id = session_id
        self.wisp_config = config or WispConfig()
        self._context_visible = False
        self._ws_client: WispWSClient | None = None
        self._pending_prompt: str | None = None

    def compose(self) -> ComposeResult:
        yield TitleBar(id="title-bar")

        with Horizontal(id="body"):
            yield ActivityBar(id="activity-bar")

            with Vertical(id="primary-panel"):
                with TabbedContent(id="tabs"):
                    with TabPane("Chat", id="chat-tab"):
                        yield MessageList(id="chat-pane")
                    with TabPane("Files", id="files-tab"):
                        with Horizontal():
                            with Vertical():
                                yield FileTree(id="file-tree")
                            with Vertical():
                                yield CodePreview(id="code-preview")
                    with TabPane("Agents", id="agents-tab"):
                        with Vertical():
                            yield AgentGrid(id="agent-grid")
                            yield TaskTree(id="task-tree")
                            yield TokenGauge(id="token-gauge")
                    with TabPane("Monitor", id="monitor-tab"):
                        with Vertical():
                            yield LogViewer(id="log-viewer")
                            yield PerformanceMetrics(id="perf-metrics")

            with Vertical(id="context-panel", classes="hidden"):
                yield RepoMapSummary(id="repo-map")
                yield CodePreview(id="context-preview")

        yield InputBar(id="input-bar")
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        try:
            title_bar = self.query_one("#title-bar", TitleBar)
            title_bar.model_name = self.wisp_config.model
            title_bar.workspace_path = self.wisp_config.workspace or "~"
            title_bar.session_label = self.session_id or "new session"
        except Exception:
            pass  # DOM not composed yet (install_screen phase)
        self._start_ws()

    def _start_ws(self) -> None:
        self._ws_client = WispWSClient(self.server_url)
        self._ws_client.on_token = self._on_token
        self._ws_client.on_tool_call = self._on_tool_call
        self._ws_client.on_tool_result = self._on_tool_result
        self._ws_client.on_complete = self._on_complete
        self._ws_client.on_error = self._on_error
        self._ws_client.on_status = self._on_status
        import asyncio
        asyncio.create_task(self._ws_client.connect())

    async def _on_token(self, phase: str, text: str) -> None:
        try:
            chat = self.query_one("#chat-pane", MessageList)
            if chat.children:
                last = chat.children[-1]
                if isinstance(last, AssistantMessage):
                    if phase == "thinking":
                        last.append_thinking(text)
                    elif phase == "content":
                        last.append_content(text)
        except Exception:
            pass

    async def _on_tool_call(self, name: str, args: dict) -> None:
        pass

    async def _on_tool_result(self, name: str, result: str, duration: float) -> None:
        pass

    async def _on_complete(self, session_id: str = "") -> None:
        try:
            status = self.query_one("#status-bar", StatusBar)
            status.is_streaming = False
        except Exception:
            pass

    async def _on_error(self, message: str) -> None:
        try:
            status = self.query_one("#status-bar", StatusBar)
            status.connection_state = f"error: {message}"
            status.is_streaming = False
        except Exception:
            pass

    async def _on_status(self, message: str, level: str) -> None:
        try:
            status = self.query_one("#status-bar", StatusBar)
            status.connection_state = message
            if level == "error":
                status.is_streaming = False
        except Exception:
            pass
        # If we have a pending prompt and just connected, flush it
        if message == "Connected" and self._pending_prompt:
            pending = self._pending_prompt
            self._pending_prompt = None
            if self._ws_client and self._ws_client._ws:
                import asyncio
                asyncio.create_task(
                    self._ws_client.send_prompt(pending, session_id=self.session_id)
                )

    def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        import asyncio
        chat = self.query_one("#chat-pane", MessageList)
        chat.mount(UserMessage(event.text))
        chat.mount(AssistantMessage())

        if self._ws_client is None or self._ws_client._ws is None:
            # Not connected — stash prompt and wait for connection
            status = self.query_one("#status-bar", StatusBar)
            self._pending_prompt = event.text
            status.is_streaming = True
            status.connection_state = "Waiting for server..."
            return

        status = self.query_one("#status-bar", StatusBar)
        status.is_streaming = True
        asyncio.create_task(
            self._ws_client.send_prompt(event.text, session_id=self.session_id)
        )

    def on_icon_button_pressed(self, event) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        for tab_id in ["chat-tab", "files-tab", "agents-tab", "monitor-tab"]:
            if tab_id == f"{event.btn_name}-tab":
                tabs.active = tab_id

    def action_toggle_command_palette(self) -> None:
        def _on_palette_result(result: str | None) -> None:
            if not result:
                return
            if result == "new-session":
                self.app.current_session_id = None
                self.app.switch_screen("workspace")
                return
            if result == "go-back":
                self.action_go_back()
                return
            if result == "show-help":
                from wisp.tui.screens.help import HelpScreen
                self.app.push_screen(HelpScreen())
                return
            if result == "quit":
                self.app.exit()
                return
            # Tab switching
            tabs = self.query_one("#tabs", TabbedContent)
            tabs.active = result
        from wisp.tui.screens.command_palette import CommandPaletteScreen
        self.app.push_screen(CommandPaletteScreen(), _on_palette_result)

    def action_toggle_context_panel(self) -> None:
        self._context_visible = not self._context_visible
        panel = self.query_one("#context-panel")
        if self._context_visible:
            panel.remove_class("hidden")
        else:
            panel.add_class("hidden")

    def action_go_back(self) -> None:
        self.app.switch_screen("session_picker")
