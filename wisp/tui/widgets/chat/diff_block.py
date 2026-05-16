"""Unified-diff display block for code changes."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


class DiffBlock(Widget):
    """Renders a syntax-highlighted unified diff."""

    def __init__(self, old_text: str = "", new_text: str = "", filename: str = "", **kwargs):
        super().__init__(**kwargs)
        self.old_text = old_text
        self.new_text = new_text
        self.filename = filename

    def compose(self) -> ComposeResult:
        label = f"--- {self.filename}\n+++ {self.filename}\n" if self.filename else ""
        yield Static(f"{label}- {self.old_text}\n+ {self.new_text}" if (self.old_text or self.new_text) else "(empty diff)")
