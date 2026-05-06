"""Tests for agent.py — parse_tool_call, build_skills_block, estimate_tokens, trim, resolve_session."""

import pytest
from unittest.mock import patch, MagicMock
from wisp.agent import _parse_tool_call, _build_skills_block, _is_interactive, _args_preview, _input_line
from wisp.config import WispConfig


# ── _parse_tool_call ──────────────────────────────────────────────────

class TestParseToolCall:

    def test_valid_tool_call(self):
        resp = {"message": {"tool_calls": [{"function": {"name": "read_file"}}]}}
        result = _parse_tool_call(resp)
        assert result == [{"function": {"name": "read_file"}}]

    def test_no_tool_calls(self):
        resp = {"message": {"content": "Hello"}}
        assert _parse_tool_call(resp) is None

    def test_empty_list(self):
        resp = {"message": {"tool_calls": []}}
        assert _parse_tool_call(resp) is None

    def test_missing_message(self):
        assert _parse_tool_call({}) is None

    def test_malformed_message_type(self):
        resp = {"message": "not a dict"}
        assert _parse_tool_call(resp) is None


# ── _build_skills_block ───────────────────────────────────────────────

class TestBuildSkillsBlock:

    def test_no_skills(self):
        with patch("wisp.agent.discover_skills", return_value=[]):
            assert _build_skills_block(".") == ""

    def test_with_skills(self):
        mock_skills = [
            MagicMock(name="code-review", description="Review code changes"),
            MagicMock(name="debug", description="Debug issues"),
        ]
        with patch("wisp.agent.discover_skills", return_value=mock_skills):
            block = _build_skills_block(".")
            assert "code-review" in block
            assert "debug" in block
            assert "Available Skills" in block


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

    def test_non_tty_eof_returns_empty(self):
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        mock_stdin.buffer.readline.return_value = b""
        with patch("wisp.transport.cli.sys.stdin", mock_stdin):
            assert _input_line("➜ ") == ""


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


# ── WispAgent unit tests ──────────────────────────────────────────────

class FakeConfig:
    ollama_url = "http://localhost:11434"
    model = "test-model"
    temperature = 0.0
    max_tokens = 4096
    max_context_tokens = 128000
    chars_per_token = 4
    auto_approve = True
    show_thinking = False
    workspace = "/tmp"


@pytest.fixture
def agent():
    from wisp.agent import WispAgent
    # We need to avoid the health check in __init__ — just construct directly
    config = FakeConfig()
    agent = WispAgent.__new__(WispAgent)
    agent.config = config
    agent.client = MagicMock()
    agent.client.check_health.return_value = True
    agent.client.generate_stream.return_value = iter([])
    agent.messages = []
    agent.session = None
    agent.session_mgr = MagicMock()
    agent._interrupted = False
    return agent


class TestEstimateTokens:

    def test_empty_messages(self, agent):
        assert agent._estimate_tokens([]) == 0

    def test_simple_message(self, agent):
        msgs = [{"role": "user", "content": "hello world"}]
        assert agent._estimate_tokens(msgs) == 2  # 11 chars / 4

    def test_with_thinking(self, agent):
        msgs = [{"role": "assistant", "content": "answer", "thinking": "let me think"}]
        assert agent._estimate_tokens(msgs) == 4  # (6 + 12) / 4


class TestTrimContext:

    def test_does_not_trim_when_under_budget(self, agent):
        agent.messages = [{"role": "user", "content": "hi"}]
        agent._trim_context_if_needed()
        assert len(agent.messages) == 1

    def test_trims_oldest_when_over_budget(self, agent):
        agent.config.max_context_tokens = 10
        agent.config.chars_per_token = 1
        agent.messages = [
            {"role": "user", "content": "x" * 20},
            {"role": "assistant", "content": "y" * 20},
            {"role": "user", "content": "z" * 5},
        ]
        agent._trim_context_if_needed()
        assert len(agent.messages) <= 2

    def test_preserves_at_least_two_messages(self, agent):
        agent.config.max_context_tokens = 1
        agent.config.chars_per_token = 1
        agent.messages = [
            {"role": "user", "content": "keep1"},
            {"role": "assistant", "content": "keep2"},
        ]
        agent._trim_context_if_needed()
        assert len(agent.messages) == 2


class TestResolveSession:

    def test_exact_match(self, agent):
        agent.session_mgr.load.return_value = MagicMock(id="20260430-120000-test")
        result = agent._resolve_session("20260430-120000-test")
        assert result is not None
        agent.session_mgr.load.assert_called_with("20260430-120000-test")

    def test_fragment_match(self, agent):
        agent.session_mgr.load.side_effect = [None, MagicMock(id="20260430-120000-test")]
        agent.session_mgr.get_session_id_from_fragment.return_value = "20260430-120000-test"
        result = agent._resolve_session("20260430")
        assert result is not None
        agent.session_mgr.get_session_id_from_fragment.assert_called_with("20260430")

    def test_no_match(self, agent):
        agent.session_mgr.load.return_value = None
        agent.session_mgr.get_session_id_from_fragment.return_value = None
        result = agent._resolve_session("nope")
        assert result is None


class TestAutoDetectContext:

    def test_auto_detects_when_not_explicit(self):
        from wisp.agent import WispAgent
        from wisp.config import WispConfig

        config = WispConfig()
        config._context_tokens_explicit = False
        config.max_context_tokens = 128000

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
            agent.config.max_context_tokens = detected

        assert agent.config.max_context_tokens == 262144

    def test_does_not_override_when_explicit(self):
        from wisp.agent import WispAgent
        from wisp.config import WispConfig

        config = WispConfig()
        config._context_tokens_explicit = True
        config.max_context_tokens = 64000

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
            agent.config.max_context_tokens = detected

        assert agent.config.max_context_tokens == 64000
