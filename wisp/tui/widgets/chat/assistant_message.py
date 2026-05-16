"""Assistant message container with reactive content and thinking text."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget

from .thinking_block import ThinkingBlock
from .content_block import ContentBlock


class AssistantMessage(Widget):
    """Container for one assistant response turn."""

    thinking_text = reactive("", recompose=True)
    content_text = reactive("", recompose=True)

    def compose(self) -> ComposeResult:
        yield ThinkingBlock()
        yield ContentBlock()

    def append_thinking(self, text: str) -> None:
        self.thinking_text += text

    def append_content(self, text: str) -> None:
        self.content_text += text
