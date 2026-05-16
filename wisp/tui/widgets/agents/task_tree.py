"""Hierarchical task tree for agent workflows."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class TaskTree(Widget):
    """Tree showing task breakdown."""

    tasks = reactive[list[dict]]([], recompose=True)

    def compose(self) -> ComposeResult:
        if not self.tasks:
            yield Static("No tasks defined.", classes="info-text")
        else:
            for t in self.tasks:
                status_icon = "✓" if t.get("done") else "○"
                yield Static(f"  {status_icon} {t.get('label', '?')}")
