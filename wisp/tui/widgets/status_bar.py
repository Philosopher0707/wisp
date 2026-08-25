"""Status bar widget showing streaming state, token count, and active agents."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class StatusBar(Widget):
    """Bottom bar with live session metrics."""

    connection_state = reactive("disconnected", recompose=True)
    is_streaming = reactive(False, recompose=True)
    token_count = reactive(0, recompose=True)
    active_agents = reactive(0, recompose=True)
    elapsed_s = reactive(0, recompose=True)

    def compose(self) -> ComposeResult:
        stream_indicator = "⬤ streaming" if self.is_streaming else "○ idle"
        waiting = f" · {self.elapsed_s}s" if self.is_streaming and self.elapsed_s else ""
        yield Static(
            f" {stream_indicator}{waiting}  │  {self.token_count:,} tokens  │  {self.active_agents} agents  │  ● {self.connection_state}",
            classes="info-text",
        )
