"""Splash screen shown on app startup."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.widget import Widget
from textual.widgets import Static


class SplashScreen(Widget):
    """Startup screen with branding. Advances on any keypress."""

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

    def on_key(self) -> None:
        self.app.navigate("session_picker")
