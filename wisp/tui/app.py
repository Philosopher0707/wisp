"""Wisp Enterprise TUI — full Textual-based terminal application.

Screens are installed by name and navigated via push_screen/switch_screen.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import App
from textual.binding import Binding
from textual.reactive import reactive
from textual.widget import Widget

from wisp.config import WispConfig

CSS_DIR = Path(__file__).resolve().parent / "css"

# Screen name constants
SPLASH = "splash"
SESSION_PICKER = "session_picker"
WORKSPACE = "workspace"


class WispTUIApp(App):
    """Enterprise-grade terminal UI for the Wisp coding agent."""

    TITLE = "Wisp"
    SUB_TITLE = "Enterprise AI Coding Agent"

    CSS_PATH = [
        CSS_DIR / "app.tcss",
        CSS_DIR / "chat.tcss",
    ]

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+p", "toggle_command_palette", "Command palette"),
        Binding("ctrl+backslash", "toggle_context_panel", "Toggle context"),
        Binding("f1", "show_help", "Help"),
    ]

    theme_mode: reactive[str] = reactive("dark")
    current_screen_name: reactive[str] = reactive(SPLASH)

    def __init__(
        self,
        config: WispConfig | None = None,
        server_url: str = "http://localhost:8000",
        transport: Any | None = None,
        runtime: Any | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.wisp_config = config or WispConfig()
        self.server_url = server_url
        self.runtime = runtime
        self.current_session_id: str | None = None
        self.transport = transport
        # Screen instances (set in on_mount)
        self._splash: Widget | None = None
        self._session_picker: Widget | None = None
        self._workspace: Widget | None = None

    def on_mount(self) -> None:
        from wisp.tui.screens.splash import SplashScreen
        from wisp.tui.screens.session_picker import SessionPickerScreen
        from wisp.tui.screens.workspace import WorkspaceScreen

        self._splash = SplashScreen(server_url=self.server_url)
        self._session_picker = SessionPickerScreen(server_url=self.server_url)
        self._workspace = WorkspaceScreen(
            server_url=self.server_url, session_id=None, config=self.wisp_config,
            runtime=self.runtime,
        )

        self.install_screen(self._splash, name=SPLASH)
        self.install_screen(self._session_picker, name=SESSION_PICKER)
        self.install_screen(self._workspace, name=WORKSPACE)

        self.push_screen(SPLASH)
        self.current_screen_name = SPLASH

    def navigate(self, screen_name: str) -> None:
        """Switch to a named screen. No-op if app hasn't mounted yet."""
        self.current_screen_name = screen_name

        # Guard: if screens haven't been installed yet (bare-app unit tests)
        if self._splash is None:
            return

        if screen_name in (SPLASH, SESSION_PICKER, WORKSPACE):
            self.switch_screen(screen_name)

        if screen_name == SESSION_PICKER:
            self._session_picker._load_sessions()

        if screen_name == WORKSPACE:
            self._workspace.session_id = self.current_session_id

        if screen_name == "help":
            from wisp.tui.screens.help import HelpScreen
            self.push_screen(HelpScreen())
        elif screen_name == "settings":
            from wisp.tui.screens.settings import SettingsScreen
            self.push_screen(SettingsScreen())

    def action_toggle_command_palette(self) -> None:
        scr = self._active_screen()
        if scr and hasattr(scr, "action_toggle_command_palette"):
            scr.action_toggle_command_palette()

    def action_toggle_context_panel(self) -> None:
        scr = self._active_screen()
        if scr and hasattr(scr, "action_toggle_context_panel"):
            scr.action_toggle_context_panel()

    def action_show_help(self) -> None:
        self.navigate("help")

    def _active_screen(self) -> Widget | None:
        """Return the currently relevant screen for action proxying."""
        if self._workspace and self.screen is self._workspace:
            return self._workspace
        if self._session_picker and self.screen is self._session_picker:
            return self._session_picker
        return None
