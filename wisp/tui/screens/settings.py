"""Settings modal screen for configuring Wisp."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


class SettingsScreen(ModalScreen):
    """Modal dialog for editing Wisp configuration."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Vertical(classes="card"):
            yield Static("Settings", classes="pane-title")
            yield Label("Model")
            yield Input(value=self.app.wisp_config.model if hasattr(self.app, 'wisp_config') else "llama3.2", id="setting-model")
            yield Label("Provider URL")
            yield Input(value="http://localhost:11434", id="setting-url")
            with Horizontal():
                yield Button("Save", variant="primary", id="save-settings")
                yield Button("Cancel", variant="default", id="cancel-settings")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-settings":
            self.dismiss()
        elif event.button.id == "save-settings":
            model = self.query_one("#setting-model", Input).value
            if hasattr(self.app, 'wisp_config'):
                self.app.wisp_config.model = model
            self.dismiss()

    def action_dismiss(self) -> None:
        self.dismiss()
