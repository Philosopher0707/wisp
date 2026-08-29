"""Tests for the interactive picker (wisp/repl/picker.py).

Pure selection logic and key classification are tested directly; the
interactive tty path is exercised through the numbered-list fallback
(interactive=False) so tests never depend on a real terminal.
"""

import io

from wisp.repl.picker import PickerState, classify_key, render_menu, select_option


# ── PickerState (pure filter/cursor logic) ───────────────────────────


class TestPickerState:
    def test_no_query_lists_all(self):
        st = PickerState(["openai", "ollama", "nvidia", "openrouter"])
        assert st.filtered() == ["openai", "ollama", "nvidia", "openrouter"]

    def test_filter_is_case_insensitive_substring(self):
        st = PickerState(["openai", "ollama", "nvidia", "openrouter"])
        st.add_char("o")
        assert st.filtered() == ["openai", "ollama", "openrouter"]
        add_chars(st, "pe")
        assert st.filtered() == ["openai", "openrouter"]

    def test_move_wraps(self):
        st = PickerState(["a", "b", "c"])
        st.move(-1)
        assert st.selected_option() == "c"
        st.move(1)
        assert st.selected_option() == "a"

    def test_filter_resets_cursor(self):
        st = PickerState(["a", "b", "c"])
        st.move(2)  # cursor on 'c'
        st.add_char("a")
        assert st.index == 0

    def test_backspace_widens(self):
        st = PickerState(["openai", "ollama"])
        add_chars(st, "op")
        assert st.filtered() == ["openai"]
        st.backspace()
        assert st.filtered() == ["openai", "ollama"]

    def test_select_digit(self):
        st = PickerState(["a", "b", "c"])
        assert st.select_digit("3") == 2
        assert st.select_digit("9") is None

    def test_selected_index_maps_to_original(self):
        # 'b' is index 1 in the original list even after filtering
        st = PickerState(["alpha", "beta", "gamma"])
        add_chars(st, "g")
        assert st.filtered() == ["gamma"]
        assert st.selected_index() == 2

    def test_empty_selection_returns_none(self):
        st = PickerState(["a", "b"])
        add_chars(st, "zzz")
        assert st.selected_option() is None
        assert st.selected_index() is None


# ── classify_key ─────────────────────────────────────────────────────


class TestClassifyKey:
    def test_arrows(self):
        assert classify_key("\x1b[A")[0] == "move"
        assert classify_key("\x1b[A")[1] == "up"
        assert classify_key("\x1b[B")[1] == "down"

    def test_vi_keys(self):
        assert classify_key("k") == ("move", "up")
        assert classify_key("j") == ("move", "down")

    def test_tab_and_enter(self):
        assert classify_key("\t") == ("tab", "tab")
        assert classify_key("\x1b[Z")[0] == "tab"
        assert classify_key("\r") == ("enter", "")
        assert classify_key("\n") == ("enter", "")

    def test_cancel_keys(self):
        assert classify_key("\x1b") == ("cancel", "")
        assert classify_key("\x03") == ("cancel", "")  # Ctrl+C
        assert classify_key("\x04") == ("cancel", "")  # Ctrl+D

    def test_char_and_digit(self):
        assert classify_key("x") == ("char", "x")
        assert classify_key("3") == ("digit", "3")

    def test_ignore(self):
        assert classify_key("\x00") == ("ignore", "")


# ── render_menu ──────────────────────────────────────────────────────


class TestRenderMenu:
    def test_renders_title_and_options(self):
        st = PickerState(["a", "b"])
        out = render_menu(st, "Pick one")
        assert "Pick one" in out
        assert "a" in out
        assert "b" in out

    def test_marks_current(self):
        st = PickerState(["a", "b", "c"])
        st.move(1)
        out = render_menu(st, "Pick one", mark="c")
        assert " →" in out  # current model highlighted
        assert "❯" in out  # cursor marker present

    def test_no_matches_hint(self):
        st = PickerState(["a"])
        add_chars(st, "none")
        out = render_menu(st, "Pick one")
        assert "no matches" in out

    def test_long_list_scrolls(self):
        st = PickerState([f"item-{i}" for i in range(50)])
        out = render_menu(st, "Pick one")
        # Scrolled window — not every item is shown
        assert "item-49" not in out
        assert "more below" in out


# ── select_option fallback path (no tty) ─────────────────────────────


class TestSelectOptionFallback:
    def test_returns_index_for_number(self):
        stdin = io.StringIO("2\n")
        stdout = io.StringIO()
        idx = select_option(
            "Pick model",
            ["alpha", "beta", "gamma"],
            interactive=False,
            stdin=stdin,
            stdout=stdout,
        )
        assert idx == 1  # "beta"

    def test_enter_cancels(self):
        stdin = io.StringIO("\n")
        stdout = io.StringIO()
        idx = select_option("Pick", ["a", "b"], interactive=False,
                            stdin=stdin, stdout=stdout)
        assert idx is None

    def test_invalid_number_returns_none(self):
        stdin = io.StringIO("9\n")
        stdout = io.StringIO()
        idx = select_option("Pick", ["a", "b"], interactive=False,
                            stdin=stdin, stdout=stdout)
        assert idx is None

    def test_empty_options_returns_none(self):
        assert select_option("Pick", [], interactive=False) is None

    def test_out_of_range_starts_at_current(self):
        stdout = io.StringIO()
        stdin = io.StringIO("1\n")
        idx = select_option("Pick", ["a", "b", "c"], current="c",
                            interactive=False, stdin=stdin, stdout=stdout)
        # Current is marked but typed "1" still selects "a" (fallback is
        # a plain numbered prompt — '→' is informational only).
        assert idx == 0


# ── Interactive loop (tty path, driven without a real terminal) ──────


class TestInteractiveLoop:
    """Drive _run_interactive with a scripted _read_key to verify the
    ↑/↓/Tab/Esc wiring that the tty path uses."""

    def _run(self, monkeypatch, keys_script, options):
        from wisp.repl import picker

        keys = iter(keys_script)
        stdout = io.StringIO()
        monkeypatch.setattr(picker, "_read_key", lambda stdin: next(keys))
        idx = picker._run_interactive(
            picker.PickerState(options), "Pick", None, None,
            io.StringIO(), stdout,
        )
        return idx, stdout.getvalue()

    def test_down_down_enter_selects_third(self, monkeypatch):
        idx, frame = self._run(monkeypatch, ["\x1b[B", "\x1b[B", "\r"], ["a", "b", "c"])
        assert idx == 2
        assert "Pick" in frame

    def test_tab_cycles_forward(self, monkeypatch):
        idx, _ = self._run(monkeypatch, ["\t", "\r"], ["a", "b", "c"])
        assert idx == 1

    def test_shift_tab_cycles_backward(self, monkeypatch):
        idx, _ = self._run(monkeypatch, ["\x1b[Z", "\x1b[Z", "\r"], ["a", "b", "c"])
        assert idx == 1  # from 0: back→2, back→1

    def test_escape_cancels(self, monkeypatch):
        idx, _ = self._run(monkeypatch, ["\x1b", "\x03"], ["a", "b", "c"])
        assert idx is None

    def test_up_key_wraps_to_last(self, monkeypatch):
        # cursor on 'a', Up wraps to 'c' (index 2)
        idx, _ = self._run(monkeypatch, ["\x1b[A", "\r"], ["a", "b", "c"])
        assert idx == 2

    def test_type_to_filter_narrows_enter_selects(self, monkeypatch):
        # type 'c' then enter → matches 'c' only
        idx, _ = self._run(monkeypatch, ["c", "\r"], ["a", "b", "c"])
        assert idx == 2

    def test_vi_keys_work(self, monkeypatch):
        # vi 'j' = down, 'k' = up
        idx, _ = self._run(monkeypatch, ["j", "\r"], ["a", "b"])
        assert idx == 1
        idx2, _ = self._run(monkeypatch, ["k", "\r"], ["a", "b"])
        assert idx2 == 1  # wraps to bottom


def add_chars(state, text: str) -> None:
    for ch in text:
        state.add_char(ch)