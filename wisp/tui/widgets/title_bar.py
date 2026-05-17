"""Title bar widget displaying model, workspace, session, and connection state."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class TitleBar(Widget):
    """Top bar showing app identity and current context."""

    model_name = reactive("default", recompose=True)
    workspace_path = reactive("~", recompose=True)
    session_label = reactive("new session", recompose=True)
    connection_state = reactive("connected", recompose=True)

    def compose(self) -> ComposeResult:
        yield Static(
            f" Wisp  │  {self.model_name}  │  {self.workspace_path}",
            classes="app-name",
        )
        yield Static(
            f"  [{self.session_label}]  ● {self.connection_state}",
            classes="info-text",
        )
