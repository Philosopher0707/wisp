"""Left-rail activity bar for tab navigation."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static


class IconButton(Widget):
    """A single icon button in the activity bar."""

    DEFAULT_CSS = """
    IconButton {
        width: 2;
        height: 3;
        content-align: center middle;
    }
    IconButton:hover {
        background: #21262d;
    }
    IconButton.active {
        background: #0c2d6b;
        border-left: solid #58a6ff;
    }
    """

    class Pressed(Message):
        """Emitted when the icon button is clicked."""
        def __init__(self, btn_name: str) -> None:
            super().__init__()
            self.btn_name = btn_name

    def __init__(self, icon: str, btn_name: str, tooltip: str = "", active: bool = False):
        super().__init__()
        self.icon = icon
        self.btn_name = btn_name
        self.tooltip = tooltip
        self.active = active
        if active:
            self.add_class("active")

    def compose(self) -> ComposeResult:
        yield Static(self.icon)

    def on_click(self) -> None:
        self.post_message(self.Pressed(self.btn_name))

    def watch_active(self, value: bool) -> None:
        if value:
            self.add_class("active")
        else:
            self.remove_class("active")


class ActivityBar(Widget):
    """Vertical icon rail for switching between primary panels."""

    active_tab = "chat"

    TABS = [
        ("💬", "chat", "Chat view"),
        ("📁", "files", "File browser"),
        ("🤖", "agents", "Agent dashboard"),
        ("📊", "monitor", "Performance monitor"),
    ]

    def compose(self) -> ComposeResult:
        for icon, name, tip in self.TABS:
            active = name == self.active_tab
            yield IconButton(icon, name, tooltip=tip, active=active)

    def on_icon_button_pressed(self, event: IconButton.Pressed) -> None:
        self.active_tab = event.btn_name
        for child in self.query(IconButton):
            child.active = (child.btn_name == event.btn_name)
