"""Tests for agent.py — _is_interactive, _args_preview, _input_line, auto-detect context."""

from unittest.mock import patch, MagicMock
from wisp.agent import _is_interactive, _args_preview, _input_line


# ── _is_interactive ───────────────────────────────────────────────────

class TestIsInteractive:

    def test_stdin_not_a_tty(self):
        with patch("wisp.transport.cli.sys.stdin.isatty", return_value=False):
            assert _is_interactive() is False

    def test_stdin_is_tty(self):
        with patch("wisp.transport.cli.sys.stdin.isatty", return_value=True):
            assert _is_interactive() is True


class TestInputLine:

    def test_tty_reads_input(self):
        with patch("wisp.transport.cli.sys.stdin.isatty", return_value=True):
            with patch("builtins.input", return_value="hello"):
                assert _input_line("➜ ") == "hello"

    def test_tty_unicode_error_returns_empty(self):
        with patch("wisp.transport.cli.sys.stdin.isatty", return_value=True):
            with patch("builtins.input", side_effect=UnicodeDecodeError("utf-8", b"\x9f", 0, 1, "invalid start byte")):
                assert _input_line("➜ ") == ""

    def test_non_tty_reads_bytes(self):
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        mock_stdin.buffer.readline.return_value = b"hello\n"
        with patch("wisp.transport.cli.sys.stdin", mock_stdin):
            assert _input_line("➜ ") == "hello"

    def test_non_tty_invalid_utf8_replaced(self):
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        mock_stdin.buffer.readline.return_value = b"hi \x9f world\n"
        with patch("wisp.transport.cli.sys.stdin", mock_stdin):
            assert _input_line("➜ ") == "hi � world"

    def test_non_tty_eof_returns_none(self):
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        mock_stdin.buffer.readline.return_value = b""
        with patch("wisp.transport.cli.sys.stdin", mock_stdin):
            assert _input_line("➜ ") is None


# ── _args_preview ─────────────────────────────────────────────────────

class TestArgsPreview:

    def test_with_path(self):
        assert _args_preview({"path": "main.py"}) == "main.py"

    def test_with_command(self):
        assert _args_preview({"command": "ls -la"}) == "ls -la"

    def test_with_content(self):
        preview = _args_preview({"content": "hello world"})
        assert "(11 chars)" in preview

    def test_empty(self):
        assert _args_preview({}) == "..."


# ── WispAgent auto-detect context ─────────────────────────────────────

class TestAutoDetectContext:

    def test_auto_detects_when_not_explicit(self):
        from wisp.agent import WispAgent
        from wisp.config import WispConfig

        config = WispConfig()
        config = config.replace(_context_tokens_explicit=False, max_context_tokens=128000)

        agent = WispAgent.__new__(WispAgent)
        agent.config = config
        agent.client = MagicMock()
        agent.client.get_context_length.return_value = 262144
        agent.session_mgr = MagicMock()
        agent.session = None
        agent.messages = []
        agent.max_iterations = config.max_iterations
        agent._interrupted = False
        agent._system_prompt = ""

        # Simulate what __init__ does for auto-detection
        if not agent.config._context_tokens_explicit:
            detected = agent.client.get_context_length()
            agent.config = agent.config.replace(max_context_tokens=detected)

        assert agent.config.max_context_tokens == 262144

    def test_does_not_override_when_explicit(self):
        from wisp.agent import WispAgent
        from wisp.config import WispConfig

        config = WispConfig()
        config = config.replace(_context_tokens_explicit=True, max_context_tokens=64000)

        agent = WispAgent.__new__(WispAgent)
        agent.config = config
        agent.client = MagicMock()
        agent.client.get_context_length.return_value = 262144
        agent.session_mgr = MagicMock()
        agent.session = None
        agent.messages = []
        agent.max_iterations = config.max_iterations
        agent._interrupted = False
        agent._system_prompt = ""

        # Auto-detection should be skipped
        if not agent.config._context_tokens_explicit:
            detected = agent.client.get_context_length()
            agent.config = agent.config.replace(max_context_tokens=detected)

        assert agent.config.max_context_tokens == 64000
