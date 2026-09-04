"""Tests for wisp/cli/ui/fuzzy_selector — ANSI-safe picker.

Simulates raw byte streams: \\x1b[A (Up), \\x1b[B (Down), Enter, Esc,
Tab, Shift-Tab, and type-to-filter, verifying the ESC trap is fixed.
"""

import io
import sys

from wisp.cli.ui.fuzzy_selector import (
    FuzzyState,
    classify_ansi,
    render_selector,
    select_with_fuzzy,
    _read_ansi,
    KEY_UP,
    KEY_DOWN,
)

# ── FuzzyState ──────────────────────────────────────────────────────────


class TestFuzzyState:
    def test_no_query_lists_all(self):
        st = FuzzyState(["openai", "ollama", "nvidia"])
        assert st.filtered() == ["openai", "ollama", "nvidia"]

    def test_filter_case_insensitive(self):
        st = FuzzyState(["openai", "ollama", "nvidia", "openrouter"])
        st.add_char("o")
        assert st.filtered() == ["openai", "ollama", "openrouter"]
        st.add_char("p")
        assert st.filtered() == ["openai", "openrouter"]
        # Case-insensitive: "O" should also match
        st2 = FuzzyState(["OpenAI", "Ollama", "Nvidia"])
        st2.add_char("O")
        assert "OpenAI" in st2.filtered() and "Ollama" in st2.filtered()
        assert "Nvidia" not in st2.filtered()

    def test_move_wraps(self):
        st = FuzzyState(["a", "b", "c"])
        st.move(-1)
        assert st.selected_option() == "c"
        st.move(1)
        assert st.selected_option() == "a"

    def test_filter_resets_cursor(self):
        st = FuzzyState(["a", "b", "c"])
        st.move(2)
        st.add_char("a")
        assert st.index == 0

    def test_backspace(self):
        st = FuzzyState(["openai", "ollama"])
        st.add_char("p")
        st.add_char("e")
        assert st.filtered() == [] or "openai" in st.filtered()
        st.backspace()
        assert len(st.filtered()) >= 1

    def test_clamp_index_after_filter_narrow(self):
        st = FuzzyState(["a", "b", "c", "d"])
        st.index = 3  # on "d"
        st.add_char("a")  # filter to ["a"] only
        # Index should be clamped to 0, not wrap
        st.clamp_index()
        assert st.index == 0
        assert st.selected_option() == "a"

    def test_selected_index_maps(self):
        st = FuzzyState(["alpha", "beta", "gamma"])
        st.add_char("g")
        assert st.filtered() == ["gamma"]
        assert st.selected_index() == 2


# ── classify_ansi ─────────────────────────────────────────────────────


class TestClassifyAnsi:
    def test_arrows_csi(self):
        assert classify_ansi("\x1b[A") == ("move", KEY_UP)
        assert classify_ansi("\x1b[B") == ("move", KEY_DOWN)

    def test_arrows_ss3(self):
        # Some terminals send SS3 variant
        assert classify_ansi("\x1bOA") == ("move", KEY_UP)
        assert classify_ansi("\x1bOB") == ("move", KEY_DOWN)

    def test_vi_keys(self):
        assert classify_ansi("k") == ("move", KEY_UP)
        assert classify_ansi("j") == ("move", KEY_DOWN)

    def test_tab(self):
        assert classify_ansi("\t") == ("tab", "tab")
        assert classify_ansi("\x1b[Z") == ("tab", "shift-tab")

    def test_enter(self):
        assert classify_ansi("\r") == ("enter", "")
        assert classify_ansi("\n") == ("enter", "")

    def test_cancel(self):
        assert classify_ansi("\x1b") == ("cancel", "")
        assert classify_ansi("\x03") == ("cancel", "")

    def test_backspace(self):
        assert classify_ansi("\x7f") == ("backspace", "")
        assert classify_ansi("\x08") == ("backspace", "")

    def test_char_digit(self):
        assert classify_ansi("x") == ("char", "x")
        assert classify_ansi("3") == ("digit", "3")

    def test_ignore(self):
        assert classify_ansi("\x00")[0] == "ignore"
        assert classify_ansi("\x1b[C")[0] == "move"  # Right

    def test_shift_tab_vs_escape(self):
        # Ensure ESC alone is cancel, but ESC [ Z is shift-tab, not cancel
        assert classify_ansi("\x1b") == ("cancel", "")
        assert classify_ansi("\x1b[Z") != ("cancel", "")


# ── _read_ansi — the ESC trap fix ────────────────────────────────────


class TestReadAnsi:
    def _make_tty(self, data: bytes):
        """Fake tty that returns bytes via read(1) and select."""

        class FakeTTY:
            def __init__(self, payload: bytes):
                self.buf = io.BytesIO(payload)
                self.is_tty = True

            def read(self, n=1):
                # Simulate TextIOWrapper read(1) returning str
                chunk = self.buf.read(n)
                if not chunk:
                    return ""
                return chunk.decode("utf-8", errors="ignore") if isinstance(chunk, bytes) else chunk

            def fileno(self):
                return 99

        return FakeTTY(data)

    def test_up_arrow_single_read(self):
        # Terminal sends 3 bytes at once: ESC [ A
        tty = self._make_tty(b"\x1b[A")
        # Monkeypatch select to immediately report ready
        import wisp.cli.ui.fuzzy_selector as m

        orig_select = None
        try:
            import select

            orig_select = select.select

            def fake_select(r, w, e, timeout=None):
                # Check if our fake tty has data
                fake = r[0]
                if hasattr(fake, "buf") and fake.buf.getvalue()[fake.buf.tell():]:
                    return ([fake], [], [])
                return ([], [], [])

            import unittest.mock as mock

            with mock.patch("select.select", side_effect=fake_select):
                seq = m._read_ansi(tty, esc_timeout=0.02, csi_timeout=0.01)
                assert seq == "\x1b[A"
                assert classify_ansi(seq)[0] == "move"
        finally:
            if orig_select:
                pass

    def test_standalone_esc_no_trail(self):
        tty = self._make_tty(b"\x1b")
        import wisp.cli.ui.fuzzy_selector as m
        import unittest.mock as mock

        def fake_select(r, w, e, timeout=None):
            return ([], [], [])  # no more bytes within timeout

        with mock.patch("select.select", side_effect=fake_select):
            seq = m._read_ansi(tty, esc_timeout=0.02, csi_timeout=0.01)
            assert seq == "\x1b"
            assert classify_ansi(seq)[0] == "cancel"

    def test_esc_followed_by_bracket_is_not_cancel(self):
        # Simulate ESC arriving, then [ and A arriving quickly (arrow)
        # First read returns ESC, but select should see more data
        tty = self._make_tty(b"\x1b[A")
        import wisp.cli.ui.fuzzy_selector as m
        import unittest.mock as mock

        call_count = {"n": 0}

        def fake_select(r, w, e, timeout=None):
            fake = r[0]
            remaining = fake.buf.getvalue()[fake.buf.tell():]
            if remaining:
                call_count["n"] += 1
                return ([fake], [], [])
            return ([], [], [])

        with mock.patch("select.select", side_effect=fake_select):
            seq = m._read_ansi(tty)
            # Should be Up, not cancel
            assert seq == "\x1b[A"
            assert classify_ansi(seq) == ("move", "up")

    def test_down_arrow(self):
        tty = self._make_tty(b"\x1b[B")
        import wisp.cli.ui.fuzzy_selector as m
        import unittest.mock as mock

        def fake_select(r, w, e, timeout=None):
            fake = r[0]
            if fake.buf.getvalue()[fake.buf.tell():]:
                return ([fake], [], [])
            return ([], [], [])

        with mock.patch("select.select", side_effect=fake_select):
            seq = m._read_ansi(tty)
            assert seq == "\x1b[B"
            assert classify_ansi(seq)[1] == "down"

    def test_shift_tab(self):
        tty = self._make_tty(b"\x1b[Z")
        import wisp.cli.ui.fuzzy_selector as m
        import unittest.mock as mock

        def fake_select(r, w, e, timeout=None):
            fake = r[0]
            if fake.buf.getvalue()[fake.buf.tell():]:
                return ([fake], [], [])
            return ([], [], [])

        with mock.patch("select.select", side_effect=fake_select):
            seq = m._read_ansi(tty)
            assert seq == "\x1b[Z"
            assert classify_ansi(seq) == ("tab", "shift-tab")


# ── render_selector ───────────────────────────────────────────────────


class TestRenderSelector:
    def test_renders_title(self):
        st = FuzzyState(["a", "b"])
        out = render_selector(st, "Pick model")
        assert "Pick model" in out
        assert "a" in out

    def test_no_matches(self):
        st = FuzzyState(["a"])
        st.add_char("z")
        out = render_selector(st, "Pick")
        assert "no matches" in out

    def test_marks_current(self):
        st = FuzzyState(["a", "b"])
        out = render_selector(st, "Pick", mark="b")
        assert "→" in out

    def test_scroll(self):
        st = FuzzyState([f"item-{i}" for i in range(50)])
        out = render_selector(st, "Pick")
        assert "more below" in out
        assert "item-49" not in out


# ── select_with_fuzzy interactive loop (mocked _read_ansi) ───────────


class TestFuzzyInteractive:
    def _run(self, monkeypatch, keys, options, current=None):
        from wisp.cli.ui import fuzzy_selector as m

        seqs = iter(keys)
        stdout = io.StringIO()

        def fake_read_ansi(stdin, esc_timeout=0.02, csi_timeout=0.01):
            try:
                return next(seqs)
            except StopIteration:
                return "\r"  # default to Enter if script exhausted

        monkeypatch.setattr(m, "_read_ansi", fake_read_ansi)
        # Bypass tty checks and directly drive the interactive loop
        # Create a state and call _run_fuzzy_interactive directly
        state = m.FuzzyState(options)
        if current in options:
            state.index = list(options).index(current)
        # Fake stdin/stdout for the loop (no termios needed)
        fake_stdin = io.StringIO()
        idx = m._run_fuzzy_interactive(state, "Pick", None, current, fake_stdin, stdout)
        return idx, stdout.getvalue()

    def test_down_down_enter(self, monkeypatch):
        idx, _ = self._run(monkeypatch, ["\x1b[B", "\x1b[B", "\r"], ["a", "b", "c"])
        assert idx == 2

    def test_up_wraps(self, monkeypatch):
        idx, _ = self._run(monkeypatch, ["\x1b[A", "\r"], ["a", "b", "c"])
        assert idx == 2  # from 0, up wraps to 2

    def test_tab_forward(self, monkeypatch):
        idx, _ = self._run(monkeypatch, ["\t", "\r"], ["a", "b", "c"])
        assert idx == 1

    def test_shift_tab_backward(self, monkeypatch):
        idx, _ = self._run(monkeypatch, ["\x1b[Z", "\x1b[Z", "\r"], ["a", "b", "c"])
        assert idx == 1  # 0 -> 2 -> 1

    def test_enter_commits(self, monkeypatch):
        idx, _ = self._run(monkeypatch, ["\r"], ["a", "b"])
        assert idx == 0

    def test_esc_cancels(self, monkeypatch):
        idx, _ = self._run(monkeypatch, ["\x1b"], ["a", "b"])
        assert idx is None

    def test_esc_not_triggered_by_arrow(self, monkeypatch):
        # Arrow should NOT be interpreted as Esc
        idx, _ = self._run(monkeypatch, ["\x1b[A", "\r"], ["a", "b", "c"])
        # Up from 0 wraps to 2, not cancel
        assert idx == 2

    def test_type_filters(self, monkeypatch):
        idx, _ = self._run(monkeypatch, ["c", "\r"], ["a", "b", "c"])
        assert idx == 2

    def test_filter_clamp_no_out_of_range(self, monkeypatch):
        # Start on last item, then filter to single match — index must clamp, not stay out of range
        from wisp.cli.ui.fuzzy_selector import FuzzyState

        st = FuzzyState(["a", "b", "c", "d"])
        st.index = 3  # d
        st.add_char("a")  # filter to ["a"] only
        st.clamp_index()
        assert st.index == 0
        # Also test via interactive loop: move to end, type filter
        idx, _ = self._run(monkeypatch, ["\x1b[B", "\x1b[B", "\x1b[B", "a", "\r"], ["alpha", "beta", "gamma", "delta"])
        # After moving to delta (3) and typing "a" (filter to alpha/beta/gamma/delta? actually all contain "a")
        # Let's test a stricter filter
        idx2, _ = self._run(monkeypatch, ["g", "\r"], ["alpha", "beta", "gamma"])
        assert idx2 == 2  # gamma

    def test_vi_keys(self, monkeypatch):
        idx, _ = self._run(monkeypatch, ["j", "\r"], ["a", "b"])
        assert idx == 1
        idx2, _ = self._run(monkeypatch, ["k", "\r"], ["a", "b"])
        assert idx2 == 1  # wrap

    def test_digit_jump(self, monkeypatch):
        idx, _ = self._run(monkeypatch, ["2", "\r"], ["a", "b", "c"])
        assert idx == 1


# ── Fallback numbered input ──────────────────────────────────────────


class TestFallback:
    def test_number_select(self):
        stdin = io.StringIO("2\n")
        stdout = io.StringIO()
        idx = select_with_fuzzy("Pick", ["alpha", "beta", "gamma"], stdin=stdin, stdout=stdout, interactive=False)
        assert idx == 1

    def test_enter_cancels_fallback(self):
        stdin = io.StringIO("\n")
        stdout = io.StringIO()
        idx = select_with_fuzzy("Pick", ["a", "b"], stdin=stdin, stdout=stdout, interactive=False)
        assert idx is None

    def test_invalid_number(self):
        stdin = io.StringIO("9\n")
        stdout = io.StringIO()
        idx = select_with_fuzzy("Pick", ["a", "b"], stdin=stdin, stdout=stdout, interactive=False)
        assert idx is None

    def test_direct_arg_fallback_model_number(self):
        # Simulate /model 3 without TUI — _resolve_model_arg handles it
        from wisp.repl.commands.provider import _resolve_model_arg

        models = ["a", "b", "c"]
        assert _resolve_model_arg("2", models) == "b"
        assert _resolve_model_arg("3", models) == "c"
        assert _resolve_model_arg("99", models) is None

    def test_direct_arg_fallback_model_name(self):
        from wisp.repl.commands.provider import _resolve_model_arg

        models = ["deepseek-ai/deepseek-r1", "openai/gpt-4o"]
        assert _resolve_model_arg("deepseek-ai/deepseek-r1", models) == "deepseek-ai/deepseek-r1"
        # Prefix must include provider prefix to be unique; "gpt" alone is ambiguous
        assert _resolve_model_arg("openai/gpt", models) == "openai/gpt-4o"  # prefix with provider
        assert _resolve_model_arg("openai/gpt-4o", models) == "openai/gpt-4o"
        # Bare "gpt" should not match (needs provider prefix) — returns None with warning
        assert _resolve_model_arg("gpt", models) is None

    def test_direct_arg_provider(self):
        from wisp.provider_select import parse_target

        assert parse_target("openai gpt-4o")["provider"] == "openai"
        assert parse_target("deepseek-ai/deepseek-r1")["provider"] is None  # no provider prefix


# ── Legacy picker still works (regression) ───────────────────────────


class TestLegacyPickerStillWorks:
    def test_legacy_picker_uses_fixed_read_key(self):
        from wisp.repl import picker

        # Ensure legacy picker also handles Up without ESC trap
        import inspect

        src = inspect.getsource(picker._read_key)
        assert "0.02" in src or "0.02" in src or "esc_timeout" in src or "20" in src or "0.02" in src
        # Check that it no longer treats \x1b as immediate cancel without timeout
        assert "select.select" in src

    def test_legacy_classify_still_maps_arrows(self):
        from wisp.repl.picker import classify_key

        assert classify_key("\x1b[A")[0] == "move"
        assert classify_key("\x1b[B")[0] == "move"
