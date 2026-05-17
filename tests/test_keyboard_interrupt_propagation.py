"""Regression: KeyboardInterrupt must propagate through the tool chain, not be swallowed.

The issue: tool_run_bash called subprocess.run inside try/except Exception.
When a user pressed Ctrl+C during a long-running bash command, Python raised
KeyboardInterrupt — but the broad except Exception swallowed it, converting it
into a ToolError / JSON error string.  The user never saw the signal, and in
server mode the exception propagated past the WebSocket handler because it
caught only Exception, crashing the entire event loop.
"""

import json
import pytest
from unittest.mock import patch

from wisp.tools import execute_tool


def _fake_raising_kb(*, command: str, workspace: str = ".", timeout: int = 60):
    raise KeyboardInterrupt("Simulated Ctrl+C")


def _fake_error(*, command: str, workspace: str = ".", timeout: int = 60):
    raise ValueError("Simulated tool bug")


class TestKeyboardInterruptPropagates:
    """KeyboardInterrupt must NOT be swallowed by broad except handlers."""

    def test_keyboard_interrupt_escapes_run_bash(self):
        """tool_run_bash must re-raise KeyboardInterrupt, not bury it."""

        def fake_run(*args, **kwargs):
            raise KeyboardInterrupt("Simulated Ctrl+C from subprocess")

        with patch("subprocess.run", side_effect=fake_run):
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


class TestServerWebsocketHandlesKeyboardInterrupt:
    """WebSocket handler must catch BaseException, not crash the event loop."""

    def test_server_run_catches_base_exception(self):
        """The inner _run() must catch BaseException and attempt graceful cleanup."""
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
                            if isinstance(handler.type, ast.Name) and handler.type.id == "BaseException":
                                found = True
                                break
        assert found, "WebSocket handler must catch BaseException (not just Exception)"
