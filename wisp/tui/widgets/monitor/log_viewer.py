"""Scrollable log viewer for agent events."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class LogViewer(Widget):
    """Real-time event log viewer."""

    entries = reactive[list[str]]([], recompose=True)

    def append(self, line: str) -> None:
        self.entries = [*self.entries[-49:], str(line)]

    def compose(self) -> ComposeResult:
        if not self.entries:
            yield Static("No log entries yet.", classes="info-text")
        else:
            for entry in self.entries[-50:]:
                yield Static(entry, classes="info-text")
