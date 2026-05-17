"""Regression: BaseException traps (SystemExit, GeneratorExit) must propagate, not be swallowed.

Previous code used `except BaseException as e:` in execute_tool's final handler,
which caught SystemExit (from sys.exit) and GeneratorExit (from generator cleanup)
and buried them in a JSON error string.  These must propagate uncaught.

KeyboardInterrupt was already handled correctly via explicit re-raise.
"""

import json
import pytest
from unittest.mock import patch

from wisp.tools import execute_tool


def _fake_raising_kb(*, command: str, workspace: str = ".", timeout: int = 60):
    raise KeyboardInterrupt("Simulated Ctrl+C")


def _fake_error(*, command: str, workspace: str = ".", timeout: int = 60):
    raise ValueError("Simulated tool bug")


def _fake_system_exit(*, command: str, workspace: str = ".", timeout: int = 60):
    raise SystemExit(0)


def _fake_generator_exit(*, command: str, workspace: str = ".", timeout: int = 60):
    raise GeneratorExit


class TestKeyboardInterruptPropagates:
    """KeyboardInterrupt must NOT be swallowed by broad except handlers."""

    def test_keyboard_interrupt_escapes_run_bash(self):
        """tool_run_bash must re-raise KeyboardInterrupt, not bury it."""

        async def fake_create_subprocess_shell(*args, **kwargs):
            raise KeyboardInterrupt("Simulated Ctrl+C from subprocess")

        with patch("asyncio.create_subprocess_shell", side_effect=fake_create_subprocess_shell):
            with pytest.raises(KeyboardInterrupt):
                from wisp.tools.bash import tool_run_bash
                tool_run_bash(command="sleep 100", workspace="/tmp")

    def test_keyboard_interrupt_escapes_registry(self):
        """execute_tool must re-raise KeyboardInterrupt, not convert to JSON."""
        with patch("wisp.tools.registry.TOOL_IMPLS", {"run_bash": _fake_raising_kb}):
            with pytest.raises(KeyboardInterrupt):
                execute_tool("run_bash", {"command": "x"}, "/tmp")

    def test_other_exceptions_still_caught(self):
        """Non-interrupt exceptions (e.g. ValueError) must still be handled cleanly."""
        with patch("wisp.tools.registry.TOOL_IMPLS", {"run_bash": _fake_error}):
            result = execute_tool("run_bash", {"command": "x"}, "/tmp")

        data = json.loads(result)
        assert data["status"] == "error"
        assert "Simulated tool bug" in data["data"]


class TestBaseExceptionMustPropagate:
    """SystemExit and GeneratorExit must NOT be swallowed by execute_tool."""

    def test_system_exit_escapes_registry(self):
        """A tool that calls sys.exit(0) must propagate — not buried as JSON error."""
        with patch("wisp.tools.registry.TOOL_IMPLS", {"run_bash": _fake_system_exit}):
            with pytest.raises(SystemExit):
                execute_tool("run_bash", {"command": "x"}, "/tmp")

    def test_generator_exit_escapes_registry(self):
        """GeneratorExit raised during tool execution must propagate uncaught."""
        with patch("wisp.tools.registry.TOOL_IMPLS", {"run_bash": _fake_generator_exit}):
            with pytest.raises(GeneratorExit):
                execute_tool("run_bash", {"command": "x"}, "/tmp")


class TestServerWebsocketHandlesException:
    """WebSocket handler must catch Exception, not swallow signals (BaseException)."""

    def test_server_run_catches_exception(self):
        """The inner _run() catches Exception so KeyboardInterrupt/SystemExit propagate.

        The server intentionally does NOT catch BaseException — signals and
        clean-exit requests must escape to the event loop, not be swallowed.
        """
        import ast
        from pathlib import Path

        src = Path("wisp/server.py").read_text()
        tree = ast.parse(src)

        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run":
                for item in node.body:
                    if isinstance(item, ast.Try):
                        for handler in item.handlers:
                            if isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
                                found = True
                                break
        assert found, "WebSocket handler must catch Exception (BaseException must NOT be caught — signals must escape)"
