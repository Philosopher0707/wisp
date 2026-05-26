"""Scrollable message list container for the chat pane."""

from __future__ import annotations

from textual.containers import VerticalScroll


class MessageList(VerticalScroll):
    """Auto-scrolling container for chat messages."""
    can_focus = True
    auto_scroll = True
