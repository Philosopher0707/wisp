"""Simple command palette modal for the TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


_COMMANDS = {
    "chat": "chat-tab",
    "files": "files-tab",
    "agents": "agents-tab",
    "monitor": "monitor-tab",
    "new": "new-session",
    "back": "go-back",
    "help": "show-help",
    "quit": "quit",
}


class CommandPaletteScreen(ModalScreen[str | None]):
    """Minimal command palette — type a command name and press Enter."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("ctrl+c", "dismiss", "Close"),
    ]

    CSS = """
    CommandPaletteScreen {
        align: center middle;
    }
    #palette-container {
        width: 50;
        height: auto;
        padding: 1 2;
        border: solid $accent;
        background: $surface;
    }
    #palette-input {
        width: 100%;
    }
    #palette-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-container"):
            yield Static("Command Palette", classes="pane-title")
            yield Input(placeholder="Type command…", id="palette-input")
            yield Static(
                "Commands: " + ", ".join(sorted(_COMMANDS.keys())),
                id="palette-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#palette-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        cmd = event.value.strip().lower()
        if cmd in _COMMANDS:
            self.dismiss(_COMMANDS[cmd])
        else:
            self.dismiss(None)
