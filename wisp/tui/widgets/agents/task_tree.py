"""Hierarchical task tree for agent workflows."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class TaskTree(Widget):
    """Tree showing task breakdown."""

    tasks = reactive[list[dict]]([], recompose=True)

    def upsert_task(self, key: str, label: str, done: bool) -> None:
        tasks = [t for t in self.tasks if t.get("key") != key]
        tasks.append({"key": key, "label": label, "done": done})
        self.tasks = tasks

    @property
    def running_count(self) -> int:
        return sum(1 for t in self.tasks if not t.get("done"))

    def compose(self) -> ComposeResult:
        if not self.tasks:
            yield Static("No tasks defined.", classes="info-text")
        else:
            for t in self.tasks:
                status_icon = "✓" if t.get("done") else "○"
                yield Static(f"  {status_icon} {t.get('label', '?')}")
