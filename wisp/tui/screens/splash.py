"""Splash screen shown on app startup."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.widgets import Static


class SplashScreen(Screen):
    """Startup screen with branding. Any key advances to session picker."""

    BINDINGS = [
        Binding("escape", "go_session_picker", "Continue", key_display="any key"),
    ]

    def __init__(self, server_url: str = "http://localhost:8000", **kwargs):
        super().__init__(**kwargs)
        self.server_url = server_url

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical():
                yield Static("⚡ Wisp", classes="app-name")
                yield Static("Enterprise AI Agent", classes="label-accent")
                yield Static(f"Connecting to {self.server_url}...", classes="info-text")
                yield Static("Press any key to continue", classes="info-text")

    def on_key(self, event) -> None:
        """Catch any non-binding keypress to advance. Deferred so event dispatch finishes cleanly."""
        event.stop()
        self.call_after_refresh(self.action_go_session_picker)

    def action_go_session_picker(self) -> None:
        self.app.switch_screen("session_picker")
