"""Help screen showing keyboard shortcuts and documentation."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class HelpScreen(ModalScreen):
    """Keyboard shortcut reference."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(classes="card"):
            yield Static("Keyboard Shortcuts", classes="pane-title")
            yield Static(
                "\n".join([
                    "ctrl+q      Quit",
                    "ctrl+p      Command palette",
                    "ctrl+\\     Toggle context panel",
                    "f1          This help",
                    "n           New session (in session picker)",
                    "esc         Dismiss / go back",
                    "tab         Switch focus",
                ]),
                classes="info-text",
            )

    def action_dismiss(self) -> None:
        self.dismiss()
