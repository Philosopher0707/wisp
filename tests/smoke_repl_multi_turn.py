#!/usr/bin/env python3
"""Smoke test: drive REPL through multiple turns, verify no event-loop crash."""

import sys
from io import StringIO
from unittest.mock import MagicMock, patch

from wisp.entry import _run_repl


def test_multi_turn_no_crash():
    """Simulate 3 REPL turns and assert RuntimeError is not raised."""
    root = MagicMock()
    root.config.model = "test"
    root.config.workspace = "/tmp"

    # Track how many times run_turn was called
    call_count = 0

    async def mock_run_turn(session, prompt):
        nonlocal call_count
        call_count += 1
        # Yield 2 content events per turn
        yield {"type": "content", "delta": f"Reply to: {prompt}\n"}
        yield {"type": "turn_complete"}

    async def mock_get_session(**kwargs):
        return {"id": "smoke", "messages": []}

    root.runtime.run_turn = mock_run_turn
    root.runtime.get_or_create_session = mock_get_session

    transport = MagicMock()

    # Feed 3 prompts then EOF
    stdin = StringIO("hello\nworld\nbye\n")
    stdout = StringIO()
    stderr = StringIO()

    with patch("sys.stdin", stdin):
        with patch("sys.stdout", stdout):
            with patch("sys.stderr", stderr):
                try:
                    _run_repl(transport, root, root.config)
                except RuntimeError as exc:
                    print(f"FAILED: RuntimeError raised: {exc}", file=sys.stderr)
                    sys.exit(1)

    stderr_str = stderr.getvalue()
    stdout_str = stdout.getvalue()

    if "Event loop is closed" in stderr_str:
        print(f"FAILED: 'Event loop is closed' found in stderr:\n{stderr_str}", file=sys.stderr)
        sys.exit(1)

    if "RuntimeError" in stderr_str:
        print(f"FAILED: RuntimeError found in stderr:\n{stderr_str}", file=sys.stderr)
        sys.exit(1)

    if call_count != 3:
        print(f"FAILED: expected 3 turns, got {call_count}", file=sys.stderr)
        sys.exit(1)

    print("PASS: 3 REPL turns completed without event-loop crash.")
    print(f"run_turn calls: {call_count}")
    print(f"stderr: {repr(stderr_str)[:200]}")


def test_ctrl_c_during_turn_survives():
    """Raise KeyboardInterrupt mid-turn; verify loop survives and next turn runs."""
    root = MagicMock()
    root.config.model = "test"
    root.config.workspace = "/tmp"

    call_count = 0

    async def mock_run_turn(session, prompt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield {"type": "thinking", "delta": "Thinking...\n"}
            raise KeyboardInterrupt("Simulated Ctrl+C")
        yield {"type": "content", "delta": f"Reply to: {prompt}\n"}
        yield {"type": "turn_complete"}

    async def mock_get_session(**kwargs):
        return {"id": "smoke", "messages": []}

    root.runtime.run_turn = mock_run_turn
    root.runtime.get_or_create_session = mock_get_session

    transport = MagicMock()

    # Two prompts: first interrupted, second runs to completion
    stdin = StringIO("interrupt me\nsecond turn\n")
    stdout = StringIO()
    stderr = StringIO()

    with patch("sys.stdin", stdin):
        with patch("sys.stdout", stdout):
            with patch("sys.stderr", stderr):
                try:
                    _run_repl(transport, root, root.config)
                except RuntimeError as exc:
                    print(f"FAILED: RuntimeError raised: {exc}", file=sys.stderr)
                    sys.exit(1)

    stdout_str = stdout.getvalue()
    stderr_str = stderr.getvalue()

    if "Event loop is closed" in stderr_str:
        print("FAILED: 'Event loop is closed' in stderr", file=sys.stderr)
        sys.exit(1)

    if "RuntimeError" in stderr_str:
        print("FAILED: RuntimeError in stderr", file=sys.stderr)
        sys.exit(1)

    if call_count != 2:
        print(f"FAILED: expected 2 turns (1 interrupted + 1 normal), got {call_count}", file=sys.stderr)
        sys.exit(1)

    # Verify resume message was printed after interrupt
    if "resume" not in stdout_str.lower() and "interrupted" not in stdout_str.lower():
        print(f"FAILED: expected resume message in stdout, got:\n{stdout_str}", file=sys.stderr)
        sys.exit(1)

    # Verify second turn produced content
    # transport._render_event is mocked, so content goes via _flush_content mock calls
    transport._flush_content.assert_called()

    print("PASS: Ctrl+C mid-turn survived; loop stayed alive for next turn.")
    print(f"run_turn calls: {call_count}")
    print(f"stderr: {repr(stderr_str)[:200]}")


if __name__ == "__main__":
    test_multi_turn_no_crash()
    print()
    test_ctrl_c_during_turn_survives()
