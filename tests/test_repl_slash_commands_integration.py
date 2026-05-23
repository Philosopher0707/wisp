"""Integration test: slash commands through the actual _run_repl path."""

import pytest
import asyncio
import sys
from io import StringIO
from unittest.mock import MagicMock, patch


class FakeCore:
    async def turn(self, session, prompt):
        yield {"type": "content", "text": f"Echo: {prompt}"}
        yield {"type": "done"}


class FakeRuntime:
    def __init__(self):
        self.store = MagicMock()
        self.telemetry = MagicMock()
        self.security = MagicMock()
        self._core = FakeCore()

    async def get_or_create_session(self, session_id, model, workspace):
        return {
            "id": session_id,
            "model": model,
            "workspace": workspace,
            "messages": [],
        }

    async def run_turn(self, session, prompt, approval_handler=None):
        async for event in self._core.turn(session, prompt):
            yield event

    def _get_core(self):
        return self._core


class FakeConfig:
    model = "test-model"
    workspace = "/tmp"
    show_thinking = False
    auto_approve = False
    max_context_tokens = 128000
    chars_per_token = 4
    permission_mode = "auto"
    db_path = None


class FakeRoot:
    def __init__(self):
        self.runtime = FakeRuntime()
        self.config = FakeConfig()


def test_repl_help_command():
    """/help in REPL should show commands without LLM call."""
    from wisp.entry import _run_repl
    from wisp.transport.cli import CLITransport

    root = FakeRoot()
    transport = CLITransport(root.runtime, root.config)

    stdin = StringIO("/help\n/exit\n")
    stdout = StringIO()

    with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
        _run_repl(transport, root, root.config)

    output = stdout.getvalue()
    assert "Available commands" in output


def test_repl_clear_command():
    """/clear in REPL should clear messages."""
    from wisp.entry import _run_repl
    from wisp.transport.cli import CLITransport

    root = FakeRoot()
    transport = CLITransport(root.runtime, root.config)

    stdin = StringIO("/clear\n/exit\n")
    stdout = StringIO()

    with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
        _run_repl(transport, root, root.config)

    output = stdout.getvalue()
    assert "Cleared" in output


def test_repl_session_command():
    """/session in REPL should show session info."""
    from wisp.entry import _run_repl
    from wisp.transport.cli import CLITransport

    root = FakeRoot()
    transport = CLITransport(root.runtime, root.config)

    stdin = StringIO("/session\n/exit\n")
    stdout = StringIO()

    with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
        _run_repl(transport, root, root.config)

    output = stdout.getvalue()
    assert "Session ID:" in output


def test_repl_unknown_command():
    """Unknown slash command should show error."""
    from wisp.entry import _run_repl
    from wisp.transport.cli import CLITransport

    root = FakeRoot()
    transport = CLITransport(root.runtime, root.config)

    stdin = StringIO("/unknown_cmd\n/exit\n")
    stdout = StringIO()

    with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
        _run_repl(transport, root, root.config)

    output = stdout.getvalue()
    assert "Unknown command" in output


def test_repl_normal_prompt_still_works():
    """Non-slash prompts should still run through the runtime."""
    from wisp.entry import _run_repl
    from wisp.transport.cli import CLITransport

    root = FakeRoot()
    transport = CLITransport(root.runtime, root.config)

    stdin = StringIO("hello\n/exit\n")
    stdout = StringIO()

    with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
        _run_repl(transport, root, root.config)

    output = stdout.getvalue()
    assert "Echo: hello" in output
