"""Collapsible thinking/reasoning block with append support."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class ThinkingBlock(Widget):
    """Shows model's internal reasoning in a collapsible section."""

    expanded = reactive(False, recompose=True)
    text = reactive("", recompose=True)

    def compose(self) -> ComposeResult:
        yield Static("Thinking...", id="thinking-header")
        yield Static(self.text, id="thinking-body")

    def append(self, chunk: str) -> None:
        self.text += chunk
