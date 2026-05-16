"""Token usage gauge widget."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class TokenGauge(Widget):
    """Visual gauge showing token budget consumption."""

    tokens_used = reactive(0, recompose=True)
    token_budget = reactive(32000, recompose=True)

    def compose(self) -> ComposeResult:
        pct = (self.tokens_used / max(self.token_budget, 1)) * 100
        bar_len = 20
        filled = min(int(pct / 100 * bar_len), bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)
        yield Static(f"Tokens: [{bar}] {self.tokens_used:,}/{self.token_budget:,}", classes="info-text")
