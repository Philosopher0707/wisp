"""Tests for the typed session-dict view (ROADMAP.md Theme 1 / D2)."""

import pytest

from wisp.core.session_view import SessionView


def _session() -> dict:
    return {
        "id": "sess-123",
        "title": "My session",
        "model": "qwen2.5-coder",
        "workspace": "/tmp/proj",
        "messages": [{"role": "user", "content": "hi"}],
    }


class TestConstruction:
    def test_wraps_dict(self):
        view = SessionView(_session())
        assert view.id == "sess-123"

    def test_rejects_non_dict(self):
        with pytest.raises(TypeError, match="got NoneType"):
            SessionView(None)  # type: ignore[arg-type]

    def test_coerce_accepts_dict(self):
        view = SessionView.coerce(_session())
        assert view is not None and view.id == "sess-123"

    def test_coerce_none_session_yields_none(self):
        """Command boundaries hold Any-typed sessions; coerce absorbs the check."""
        assert SessionView.coerce(None) is None
        assert SessionView.coerce("not-a-session") is None


class TestTypedAccessors:
    def test_all_properties(self):
        view = SessionView(_session())
        assert view.title == "My session"
        assert view.model == "qwen2.5-coder"
        assert view.workspace == "/tmp/proj"

    def test_missing_keys_default_not_raise(self):
        view = SessionView({})
        assert view.id == ""
        assert view.title == ""
        assert view.model == ""

    def test_display_title_falls_back(self):
        assert SessionView({}).display_title() == "(untitled)"
        assert SessionView({"title": "T"}).display_title() == "T"
        assert SessionView({}).display_title("(none)") == "(none)"


class TestLiveAliasing:
    """The view is a lens on one dict, not a snapshot."""

    def test_messages_is_live_reference(self):
        data = _session()
        view = SessionView(data)
        view.messages.append({"role": "assistant", "content": "yo"})
        assert len(data["messages"]) == 2

    def test_raw_escape_hatch_mutates_underlying(self):
        data = _session()
        view = SessionView(data)
        view.raw["title"] = "renamed"
        assert data["title"] == "renamed"

    def test_messages_creates_list_when_missing(self):
        view = SessionView({"id": "x"})
        assert view.messages == []
        view.messages.append({"role": "user", "content": "hi"})
        assert view.raw["messages"] == [{"role": "user", "content": "hi"}]

    def test_messages_rejects_corrupt_shape(self):
        view = SessionView({"messages": "not-a-list"})
        with pytest.raises(TypeError, match="must be a list"):
            view.messages
