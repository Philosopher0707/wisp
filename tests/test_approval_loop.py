"""Approval dead-loop regression suite — piped stdin against real code.

Covers the reported failure (``y`` must approve, never interrupt) and the
handshake invariants: cancel is a recorded verdict, external cancellation
propagates, concurrent prompts serialize, large writes stay off-loop.
"""

from __future__ import annotations

import asyncio
import io
import os
from unittest.mock import MagicMock

import pytest

from wisp.cli.approval import (
    ApprovalCancelled,
    ApprovalVerdict,
    normalize_answer,
    prompt_for_approval,
)


def _pipe_stdin(monkeypatch, data: bytes):
    """Replace sys.stdin with a pipe-backed text stream (isatty False)."""
    r, w = os.pipe()
    os.write(w, data)
    os.close(w)
    stream = os.fdopen(r, "r", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", stream)
    return stream


def _transport(**kw):
    from wisp.config import WispConfig
    from wisp.transport.cli import CLITransport

    cfg = WispConfig()
    t = CLITransport(runtime=MagicMock(), config=cfg, **kw)
    t._force_approval_mode = False
    return t


@pytest.fixture()
def quiet_spinner(monkeypatch):
    """Silence spinner animation threads created by approvals."""
    from wisp.transport.spinner import Spinner

    started: list[str] = []

    def _quiet_start(self, label=""):
        started.append(label)

    monkeypatch.setattr(Spinner, "start", _quiet_start)
    yield started


def test_verdict_mapping():
    assert prompt_for_approval("y", is_file_edit=True) == ApprovalVerdict.APPROVE
    assert prompt_for_approval("Y", is_file_edit=False) == ApprovalVerdict.APPROVE_ALWAYS
    assert prompt_for_approval("yes", is_file_edit=False) == ApprovalVerdict.APPROVE
    assert prompt_for_approval("v", is_file_edit=True) == ApprovalVerdict.VIEW
    assert prompt_for_approval("v", is_file_edit=False) == ApprovalVerdict.REJECT
    assert prompt_for_approval("n", is_file_edit=False) == ApprovalVerdict.REJECT
    assert prompt_for_approval("N", is_file_edit=False) == ApprovalVerdict.REJECT_ALWAYS
    assert prompt_for_approval("a", is_file_edit=False) == ApprovalVerdict.AUTO_ALL
    assert prompt_for_approval("d", is_file_edit=False) == ApprovalVerdict.BLOCK_ALL
    assert prompt_for_approval("c", is_file_edit=False) == ApprovalVerdict.CANCEL
    assert prompt_for_approval("", is_file_edit=False) == ApprovalVerdict.REJECT
    assert prompt_for_approval("nonsense", is_file_edit=True) == ApprovalVerdict.REJECT


def test_ctrl_c_byte_is_the_only_keyboard_interrupt():
    with pytest.raises(KeyboardInterrupt):
        prompt_for_approval("\x03", is_file_edit=False)
    assert normalize_answer(b"\x03\n") == "\x03"
    assert normalize_answer("y\n") == "y"
    assert normalize_answer(None) == ""
    assert normalize_answer("\x1b[32my\x1b[0m\n") == "y"


def test_y_approves_without_cancel(monkeypatch, quiet_spinner):
    _pipe_stdin(monkeypatch, b"y\n")
    t = _transport()

    async def _go():
        return await t.approve({"name": "write_file",
                                "arguments": {"path": "a.py", "content": "x"}})

    assert asyncio.run(_go()) is True


def test_v_toggle_then_y_approves(monkeypatch, quiet_spinner, capsys):
    _pipe_stdin(monkeypatch, b"v\ny\n")
    t = _transport()

    async def _go():
        return await t.approve({"name": "edit_file",
                                "arguments": {"path": "a.py",
                                              "old_text": "x = 1\n",
                                              "new_text": "x = 2\n"}})

    assert asyncio.run(_go()) is True
    err = capsys.readouterr().err
    assert "x = 1" in err and "x = 2" in err  # toggle rendered the diff, then y approved


def test_piped_ctrl_c_byte_raises_keyboard_interrupt(monkeypatch, quiet_spinner):
    _pipe_stdin(monkeypatch, b"\x03\n")
    t = _transport()

    async def _go():
        return await t.approve({"name": "write_file",
                                "arguments": {"path": "a.py", "content": "x"}})

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(_go())


def test_external_cancel_propagates_not_converted(monkeypatch, quiet_spinner):
    r, w = os.pipe()  # held open: reader blocks, nothing answered
    stream = os.fdopen(r, "r", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", stream)
    t = _transport()

    async def _go():
        task = asyncio.ensure_future(t.approve({"name": "write_file",
                                                "arguments": {"path": "a.py", "content": "x"}}))
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        finally:
            # Release the fd so the (already stop-signalled) reader thread
            # drains instead of parking; loop shutdown joins to_thread
            # workers and would hang on a blocked input() otherwise.
            try:
                os.close(w)
            except OSError:
                pass

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_go())
    stream.close()


def test_concurrent_approvals_serialize(monkeypatch, quiet_spinner):
    reads: list[str] = []

    class _CountingStdin(io.StringIO):
        def readline(self, *a, **k):
            line = super().readline(*a, **k)
            reads.append(line)
            return line

        def isatty(self):
            return False

    monkeypatch.setattr("sys.stdin", _CountingStdin("y\ny\n"))
    t = _transport()
    assert t._approval_lock is None  # lazy: no loop binding at construction

    async def _go():
        first = asyncio.ensure_future(t.approve({"name": "write_file",
                                                 "arguments": {"path": "a.py", "content": "x"}}))
        second = asyncio.ensure_future(t.approve({"name": "edit_file",
                                                  "arguments": {"path": "b.py",
                                                                "old_text": "1\n",
                                                                "new_text": "2\n"}}))
        return await asyncio.gather(first, second)

    assert asyncio.run(_go()) == [True, True]
    assert t._approval_lock is not None and not t._approval_lock.locked()
    assert len(reads) == 2  # each prompt consumed exactly one line


def test_cancel_choice_purges_with_explicit_result():
    from wisp.tool_executor import ToolExecutor

    calls: list[str] = []

    async def _cancelling_handler(name, args, reason):
        calls.append(name)
        raise ApprovalCancelled(name)

    async def _go(tmp_path):
        from wisp.config import WispConfig

        ex = ToolExecutor(config=WispConfig(), hook_manager=None, mcp=None,
                          file_lock=None, lsp_manager=None,
                          subagent_orchestrator=None, extensions=None)
        events = []
        async for ev in ex.execute("write_file",
                                   {"path": "a.py", "content": "x"},
                                   str(tmp_path),
                                   approval_handler=_cancelling_handler):
            events.append(ev)
        return events, ex

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        events, ex = asyncio.run(_go(Path(td)))
    try:
        ex._tool_pool.shutdown(wait=False)
    except Exception:
        pass
    assert calls == ["write_file"]  # asked once: no re-prompt loop
    assert len(events) == 2  # approval_request + explicit tool_result
    text = json_text(events[-1])
    assert "Cancelled by user" in text
    assert "do not retry" in text


def json_text(event) -> str:
    data = getattr(event, "data", None)
    if isinstance(data, dict):
        for key in ("result", "output", "text", "message"):
            if isinstance(data.get(key), str):
                return data[key]
        return str(data)
    return str(getattr(event, "text", event))


def test_large_write_file_off_loop_fast(tmp_path):
    from wisp.config import WispConfig
    from wisp.tool_executor import ToolExecutor

    content = "".join(f"line {i}: payload data here\n" for i in range(600))

    async def _allow(name, args, reason):
        return True, None

    async def _go():
        import time

        ex = ToolExecutor(config=WispConfig(), hook_manager=None, mcp=None,
                          file_lock=None, lsp_manager=None,
                          subagent_orchestrator=None, extensions=None)
        t0 = time.monotonic()
        results = []
        async for ev in ex.execute("write_file",
                                   {"path": "big.py", "content": content},
                                   str(tmp_path),
                                   approval_handler=_allow):
            results.append(ev)
        return time.monotonic() - t0, results, ex

    elapsed, _, ex = asyncio.run(_go())
    try:
        ex._tool_pool.shutdown(wait=False)
    except Exception:
        pass
    assert elapsed < 10.0
    assert (tmp_path / "big.py").read_text() == content


def test_gate_converts_cancel_to_recorded_denial():
    from wisp.core.approval_gate import ApprovalGate

    async def _cancelling(event):
        raise ApprovalCancelled("write_file")

    async def _go():
        gate = ApprovalGate(security=MagicMock())
        gate.security.check.return_value = MagicMock(allowed=False, reason="needs review",
                                                     modified_args=None)
        return await gate.check({"name": "write_file", "arguments": {}},
                                {"workspace": "."},
                                approval_handler=_cancelling)

    allowed, reason = asyncio.run(_go())
    assert allowed is False
    assert "cancelled by user" in (reason or "")
