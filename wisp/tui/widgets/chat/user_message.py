"""User message widget."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


class UserMessage(Widget):
    """A user's chat message."""

    def __init__(self, text: str, timestamp: str | None = None, **kwargs):
        for key in ("timestamp",):
            kwargs.pop(key, None)
        super().__init__(**kwargs)
        self._text = text
        self._timestamp = timestamp

    def compose(self) -> ComposeResult:
        yield Static(self._text, classes="user-bubble")
