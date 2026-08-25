"""TDD for typed-ahead input capture during turns."""

import time

import pytest


class TestExtractLines:
    def test_complete_lines_extracted(self):
        from wisp.transport.typeahead import extract_lines
        buf = bytearray(b"fix the bug\nrun tests\n")
        lines, rest = extract_lines(buf)
        assert lines == ["fix the bug", "run tests"]
        assert bytes(rest) == b""

    def test_partial_line_stays_buffered(self):
        from wisp.transport.typeahead import extract_lines
        buf = bytearray(b"fix the bug\nadd ha")
        lines, rest = extract_lines(buf)
        assert lines == ["fix the bug"]
        assert bytes(rest) == b"add ha"

    def test_ansi_escape_sequences_stripped(self):
        from wisp.transport.typeahead import extract_lines
        # Arrow keys arrive as CSI sequences around/inside text
        buf = bytearray(b"\x1b[A\x1b[Bstatus\x1b[D\n")
        lines, _ = extract_lines(buf)
        assert lines == ["status"]

    def test_bare_escape_fragment_dropped(self):
        from wisp.transport.typeahead import extract_lines
        buf = bytearray(b"\x1b\x1b[\nreal prompt\n")
        lines, _ = extract_lines(buf)
        assert "real prompt" in lines and len(lines) == 1

    def test_carriage_returns_from_paste_removed(self):
        from wisp.transport.typeahead import extract_lines
        buf = bytearray(b"one two\r\nthree\r\n")
        lines, _ = extract_lines(buf)
        assert lines == ["one two", "three"]

    def test_empty_lines_skipped(self):
        from wisp.transport.typeahead import extract_lines
        lines, _ = extract_lines(bytearray(b"\n\n\nx\n"))
        assert lines == ["x"]


class TestTypeAheadLifecycle:
    @pytest.fixture
    def patched_stdin(self, monkeypatch):
        """Fake a POSIX tty stdin whose reads come from a script."""
        import io

        class FakeStdin(io.StringIO):
            def isatty(self):
                return True

            def fileno(self):
                return self._fd

        fake = FakeStdin()
        monkeypatch.setattr("wisp.transport.typeahead.sys.stdin", fake)
        return fake

    def test_disabled_when_not_a_tty(self, monkeypatch):
        import io
        from wisp.transport.typeahead import TypeAheadBuffer

        class Pipe(io.StringIO):
            def isatty(self):
                return False

        monkeypatch.setattr("wisp.transport.typeahead.sys.stdin", Pipe())
        buffer = TypeAheadBuffer()
        buffer.start()
        assert buffer.enabled is False
        assert buffer.drain() == ([], "")

    def test_disabled_on_windows(self, monkeypatch):
        import io
        from wisp.transport.typeahead import TypeAheadBuffer

        class Tty(io.StringIO):
            def isatty(self):
                return True

        monkeypatch.setattr("wisp.transport.typeahead.os.name", "nt")
        monkeypatch.setattr("wisp.transport.typeahead.sys.stdin", Tty())
        buffer = TypeAheadBuffer()
        buffer.start()
        assert buffer.enabled is False

    def test_capture_and_drain_roundtrip(self, monkeypatch):
        """Reader picks up scripted chunks; drain stops it deterministically."""
        from wisp.transport import typeahead as ta

        chunks = [b"/help\n", b"count the "]
        calls = {"n": 0}

        def fake_select(rlist, *_a, **_k):
            if calls["n"] < len(chunks):
                return list(rlist), [], []
            return [], [], []

        def fake_read(fd, size):
            idx = calls["n"]
            if idx >= len(chunks):
                time.sleep(0.2)
                return b""
            calls["n"] += 1
            return chunks[idx]

        monkeypatch.setattr(ta.select, "select", fake_select)
        monkeypatch.setattr(ta.os, "read", fake_read)

        buffer = ta.TypeAheadBuffer()

        class Tty:
            def isatty(self):
                return True

            def fileno(self):
                return 0

        monkeypatch.setattr("wisp.transport.typeahead.sys.stdin", Tty())
        buffer.start()
        assert buffer.enabled is True

        deadline = time.time() + 3.0
        while calls["n"] < len(chunks) and time.time() < deadline:
            time.sleep(0.01)

        lines, partial = buffer.drain(timeout=2.0)
        assert lines == ["/help"]
        assert partial == "count the"
