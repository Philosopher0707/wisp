"""Input bar for composing and sending prompts."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, Input


class InputBar(Widget):
    """Bottom input area with prompt field and send button."""

    value = reactive("")
    is_streaming = reactive(False)
    token_estimate = reactive(0)

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Input(placeholder="Ask Wisp about this repository...", id="prompt-input")
            yield Button("Send", variant="primary", id="send-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        inp = self.query_one("#prompt-input", Input)
        text = inp.value.strip()
        if text:
            self.post_message(self.Submitted(text))
            inp.value = ""
            self.value = ""

    def set_disabled(self, disabled: bool) -> None:
        inp = self.query_one("#prompt-input", Input)
        inp.disabled = disabled
