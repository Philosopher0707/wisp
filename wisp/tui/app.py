"""Wisp Enterprise TUI — full Textual-based terminal application.

Screens are composed directly in the app body with visibility toggling.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.reactive import reactive
from textual.widget import Widget

from wisp.config import WispConfig

CSS_DIR = Path(__file__).resolve().parent / "css"


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
    current_screen_name: reactive[str] = reactive("splash")

    def __init__(
        self,
        config: WispConfig | None = None,
        server_url: str = "http://localhost:8000",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.wisp_config = config or WispConfig()
        self.server_url = server_url
        self.current_session_id: str | None = None

    def compose(self) -> ComposeResult:
        from wisp.tui.screens.splash import SplashScreen
        from wisp.tui.screens.session_picker import SessionPickerScreen
        from wisp.tui.screens.workspace import WorkspaceScreen

        self._splash = SplashScreen(server_url=self.server_url, id="splash-screen")
        self._session_picker = SessionPickerScreen(server_url=self.server_url, id="session-picker-screen")
        self._workspace = WorkspaceScreen(
            server_url=self.server_url, session_id=None, config=self.wisp_config, id="workspace-screen"
        )

        self._splash.display = True
        self._session_picker.display = False
        self._workspace.display = False

        yield self._splash
        yield self._session_picker
        yield self._workspace

    def on_mount(self) -> None:
        self.current_screen_name = "splash"

    def navigate(self, screen_name: str) -> None:
        """Switch which composed screen is visible."""
        self.current_screen_name = screen_name

        # Guard: if compose() hasn't run (e.g. in unit tests), be a no-op
        if not hasattr(self, "_splash"):
            return

        self._splash.display = (screen_name == "splash")
        self._session_picker.display = (screen_name == "session_picker")
        self._workspace.display = (screen_name == "workspace")

        if screen_name == "session_picker":
            self._session_picker._load_sessions()

        if screen_name == "workspace":
            self._workspace.session_id = self.current_session_id

        if screen_name == "help":
            from wisp.tui.screens.help import HelpScreen
            self.push_screen(HelpScreen())
        elif screen_name == "settings":
            from wisp.tui.screens.settings import SettingsScreen
            self.push_screen(SettingsScreen())

    def action_toggle_command_palette(self) -> None:
        scr = self._visible_screen()
        if scr and hasattr(scr, "action_toggle_command_palette"):
            scr.action_toggle_command_palette()

    def action_toggle_context_panel(self) -> None:
        scr = self._visible_screen()
        if scr and hasattr(scr, "action_toggle_context_panel"):
            scr.action_toggle_context_panel()

    def action_show_help(self) -> None:
        self.navigate("help")

    def _visible_screen(self) -> Widget | None:
        if not hasattr(self, "_splash"):
            return None
        if self._splash.display:
            return self._splash
        if self._session_picker.display:
            return self._session_picker
        if self._workspace.display:
            return self._workspace
        return None
