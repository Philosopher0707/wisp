"""Repo map summary widget showing codebase structure overview."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


class RepoMapSummary(Widget):
    """Compact overview of repository structure."""

    def compose(self) -> ComposeResult:
        yield Static("Repository Map", classes="pane-title")
        yield Static("Scanning workspace...", classes="info-text")
