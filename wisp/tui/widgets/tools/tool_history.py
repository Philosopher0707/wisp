"""Tool invocation history table."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


class ToolHistoryTable(Widget):
    """Table of recent tool calls and their results."""

    def __init__(self, entries: list[dict] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.entries = entries or []

    def compose(self) -> ComposeResult:
        if not self.entries:
            yield Static("No tool calls yet.", classes="info-text")
        else:
            for entry in self.entries:
                yield Static(
                    f"  {entry.get('name', '?')}  ({entry.get('duration_ms', 0)}ms)",
                    classes="tool-name",
                )
