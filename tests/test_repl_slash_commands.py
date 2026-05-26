"""Test slash commands work in the new REPL."""

import pytest
from unittest.mock import MagicMock, patch
from io import StringIO


class FakeRuntime:
    def __init__(self):
        self.store = MagicMock()
        self.telemetry = MagicMock()
        self.security = MagicMock()

    async def get_or_create_session(self, session_id, model, workspace):
        return {
            "id": session_id,
            "model": model,
            "workspace": workspace,
            "messages": [],
        }

    async def run_turn(self, session, prompt):
        yield {"type": "content", "text": f"Echo: {prompt}"}
        yield {"type": "done"}


class FakeConfig:
    model = "test-model"
    workspace = "/tmp"
    show_thinking = False
    auto_approve = False
    max_context_tokens = 128000
    chars_per_token = 4
    permission_mode = "auto"

    def replace(self, **kwargs):
        import copy
        inst = copy.copy(self)
        for k, v in kwargs.items():
            setattr(inst, k, v)
        return inst


@pytest.fixture
def transport():
    from wisp.transport.cli import CLITransport
    return CLITransport(FakeRuntime(), FakeConfig())


@pytest.mark.asyncio
async def test_help_command(transport, capsys):
    """/help should list commands without calling the LLM."""
    stdin = StringIO("/help\n/exit\n")
    stdout = StringIO()

    with patch("sys.stdout", stdout):
        await transport.run(stdin, stdout, "test-session", "test-model", "/tmp")
    output = stdout.getvalue()
    assert "Available commands" in output


@pytest.mark.asyncio
async def test_clear_command(transport, capsys):
    """/clear should clear session messages."""
    stdin = StringIO("/clear\n/exit\n")
    stdout = StringIO()

    with patch("sys.stdout", stdout):
        await transport.run(stdin, stdout, "test-session", "test-model", "/tmp")
    output = stdout.getvalue()
    assert "Cleared" in output


@pytest.mark.asyncio
async def test_session_command(transport, capsys):
    """/session should show session info."""
    stdin = StringIO("/session\n/exit\n")
    stdout = StringIO()

    with patch("sys.stdout", stdout):
        await transport.run(stdin, stdout, "test-session", "test-model", "/tmp")
    output = stdout.getvalue()
    assert "test-session" in output


@pytest.mark.asyncio
async def test_exit_command(transport):
    """/exit should terminate the REPL."""
    stdin = StringIO("/exit\n")
    stdout = StringIO()

    await transport.run(stdin, stdout, "test-session", "test-model", "/tmp")
    output = stdout.getvalue()
    assert "Wisp ready" in output


@pytest.mark.asyncio
async def test_thinking_command(transport, capsys):
    """/thinking should toggle thinking display."""
    stdin = StringIO("/thinking\n/exit\n")
    stdout = StringIO()

    with patch("sys.stdout", stdout):
        await transport.run(stdin, stdout, "test-session", "test-model", "/tmp")
    output = stdout.getvalue()
    assert "thinking" in output.lower() or "ON" in output or "OFF" in output


@pytest.mark.asyncio
async def test_unknown_command(transport, capsys):
    """Unknown slash command should show error."""
    stdin = StringIO("/unknown_cmd\n/exit\n")
    stdout = StringIO()

    with patch("sys.stdout", stdout):
        await transport.run(stdin, stdout, "test-session", "test-model", "/tmp")
    output = stdout.getvalue()
    assert "Unknown command" in output
