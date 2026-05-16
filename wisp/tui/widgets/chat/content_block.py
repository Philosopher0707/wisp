"""Rich text content block with streaming append."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class ContentBlock(Widget):
    """Displays the assistant's markdown output, supporting incremental updates."""

    text = reactive("", recompose=True)

    def compose(self) -> ComposeResult:
        yield Static(self.text, id="content-richtext")

    def append(self, chunk: str) -> None:
        self.text += chunk
