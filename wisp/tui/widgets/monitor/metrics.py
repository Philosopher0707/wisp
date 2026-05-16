"""Live performance metrics panel."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class PerformanceMetrics(Widget):
    """Shows session uptime, message/tool/token counters."""

    session_uptime = reactive("0:00", recompose=True)
    message_count = reactive(0, recompose=True)
    tool_calls = reactive(0, recompose=True)
    tokens_total = reactive(0, recompose=True)

    def compose(self) -> ComposeResult:
        yield Static(f"⏱ {self.session_uptime}  |  💬 {self.message_count} msgs  |  🔧 {self.tool_calls} tools  |  📊 {self.tokens_total:,} tokens", classes="info-text")
