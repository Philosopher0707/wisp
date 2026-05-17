"""Session picker screen for selecting or creating sessions."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Label, ListItem, ListView, Static
class SessionPickerScreen(Screen):
    """Lets the user pick an existing session or start fresh."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("ctrl+n", "new_session", "New session"),
    ]

    def __init__(self, server_url: str = "http://localhost:8000", **kwargs):
        super().__init__(**kwargs)
        self.server_url = server_url
        self._all_sessions: list[dict] = []
        self._loaded = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Sessions", classes="pane-title")
            yield Input(placeholder="Search sessions...", id="session-search")
            with VerticalScroll(id="session-list"):
                yield ListView(id="sessions")
            with Horizontal(classes="info-text"):
                yield Label("ctrl+n: New  │  esc: Back")

    def on_mount(self) -> None:
        # Defer until compose has run (install_screen calls on_mount early)
        self._loaded = False

    def _load_sessions(self) -> None:
        if self._loaded:
            return
        try:
            from wisp.session_store import get_store
            mgr = get_store()
            self._all_sessions = mgr.list_sessions()
        except Exception:
            self._all_sessions = []

        try:
            lv = self.query_one("#sessions", ListView)
            lv.clear()
            for s in self._all_sessions:
                title = s.get("title", "untitled")
                model = s.get("model", "")
                li = ListItem(Label(f"{title}  ({model})"))
                li.session_id = s.get("id")
                lv.append(li)
            self._loaded = True
        except Exception:
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "session-search":
            self._filter(event.value)

    def _filter(self, query: str) -> None:
        try:
            lv = self.query_one("#sessions", ListView)
        except Exception:
            return
        lv.clear()
        q = query.lower()
        for s in self._all_sessions:
            title = s.get("title", "").lower()
            if q in title:
                li = ListItem(Label(s.get("title", "")))
                li.session_id = s.get("id")
                lv.append(li)
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        selected = getattr(event.item, "session_id", None)
        if selected:
            self.app.current_session_id = selected
        else:
            self.app.current_session_id = None
        self.app.switch_screen("workspace")

    def _new_session(self) -> None:
        self.app.current_session_id = None
        self.app.switch_screen("workspace")

    def action_new_session(self) -> None:
        self._new_session()

    def action_go_back(self) -> None:
        self.app.switch_screen("splash")
