"""Session picker screen for selecting or creating sessions."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView, Static


class SessionPickerScreen(Widget):
    """Lets the user pick an existing session or start fresh."""

    def __init__(self, server_url: str = "http://localhost:8000", **kwargs):
        super().__init__(**kwargs)
        self.server_url = server_url
        self._all_sessions: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Sessions", classes="pane-title")
            yield Input(placeholder="Search sessions...", id="session-search")
            with VerticalScroll(id="session-list"):
                yield ListView(id="sessions")
            with Horizontal(classes="info-text"):
                yield Label("n: New  │  enter: Open  │  esc: Back")

    def on_mount(self) -> None:
        self._load_sessions()

    def _load_sessions(self) -> None:
        try:
            from wisp.session_store import get_store
            mgr = get_store()
            self._all_sessions = mgr.list_sessions()
        except Exception:
            self._all_sessions = []

        lv = self.query_one("#sessions", ListView)
        lv.clear()
        for s in self._all_sessions:
            title = s.get("title", "untitled")
            model = s.get("model", "")
            lv.append(ListItem(Label(f"{title}  ({model})")))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "session-search":
            self._filter(event.value)

    def _filter(self, query: str) -> None:
        lv = self.query_one("#sessions", ListView)
        lv.clear()
        q = query.lower()
        for s in self._all_sessions:
            title = s.get("title", "").lower()
            if q in title:
                lv.append(ListItem(Label(s.get("title", ""))))

    def on_key(self, event) -> None:
        if event.key == "n":
            self._new_session()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._new_session()

    def _new_session(self) -> None:
        self.app.current_session_id = None
        self.app.navigate("workspace")

    def action_new_session(self) -> None:
        self._new_session()
