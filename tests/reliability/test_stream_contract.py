"""G1B provider-neutral stream contract + turn-level completion honesty.

Every adapter must satisfy identical SEMANTIC guarantees (wire behavior
may differ):
  success -> terminal present, content complete
  failed-mid-stream -> error event, NO terminal
  truncated (EOF w/o terminal) -> NO terminal event
  malformed chunk -> skipped-or-error, never a forged terminal
  duplicate chunk -> passed through (no safe identity -> no dedup;
    deferred by design, pinned here)

Turn level: any non-complete round-trip ends WITHOUT done.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
from unittest.mock import MagicMock, patch

from wisp.core.engine import WispAgentCore


def _turn_events(provider, timeout_s=30):
    core = WispAgentCore(provider=provider)
    ws = tempfile.mkdtemp(prefix="g1b-ct_")
    session = {"id": "ct", "messages": [], "model": "mock", "workspace": ws}

    async def _collect():
        evs = []
        async for ev in core.turn(session, "go"):
            evs.append(ev)
        return evs

    try:
        return asyncio.run(asyncio.wait_for(_collect(), timeout_s)), core
    except asyncio.TimeoutError:
        return None, core


def _types(evs):
    return [e.get("type") for e in evs] if evs is not None else None


# ── OpenAI dialect (SSE over requests.post) ──

def _sse_resp(chunks, status=200):
    lines = []
    for c in chunks:
        lines.append(c if isinstance(c, bytes) else f"data: {json.dumps(c)}".encode())
    resp = MagicMock()
    resp.status_code = status
    resp.iter_lines.return_value = lines
    return resp


def _openai_provider():
    from wisp.providers.openai import OpenAIProvider
    return OpenAIProvider(model="t", api_key="sk-test")


def test_openai_success_has_terminal():
    chunks = [
        {"choices": [{"delta": {"content": "hi"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    with patch("requests.post", return_value=_sse_resp(chunks)):
        evs = list(_openai_provider().generate_stream_events("s", []))
    types = [e["type"] for e in evs]
    assert "done" in types
    assert not [e for e in evs if e["type"] == "error"]


def test_openai_mid_stream_conn_error_no_terminal():
    import requests as req_module

    chunks = [{"choices": [{"delta": {"content": "part"}}]}]

    def _boom(*a, **k):
        resp = _sse_resp(chunks)
        orig = resp.iter_lines.return_value
        def _gen():
            yield orig[0]
            raise req_module.exceptions.ConnectionError("reset")
        resp.iter_lines.return_value = _gen()
        return resp

    with patch("requests.post", side_effect=_boom):
        evs = list(_openai_provider().generate_stream_events("s", []))
    types = [e["type"] for e in evs]
    assert "done" not in types, types
    assert "error" in types, types
    assert any(e["text"] == "part" for e in evs if e["type"] == "content")


def test_openai_eof_without_done_is_truncated():
    # lines end after content: adapter emits no terminal (no finish chunk,
    # no [DONE] handling that forges one — pinned here).
    resp = MagicMock()
    resp.status_code = 200
    resp.iter_lines.return_value = [
        b'data: {"choices": [{"delta": {"content": "half"}}]}',
    ]
    with patch("requests.post", return_value=resp):
        evs = list(_openai_provider().generate_stream_events("s", []))
    types = [e["type"] for e in evs]
    assert "done" not in types, types


def test_openai_malformed_chunk_skipped_no_forged_terminal():
    resp = MagicMock()
    resp.status_code = 200
    resp.iter_lines.return_value = [
        b'data: {"choices": [{"delta": {"content": "ok"}}]}',
        b'data: {NOT JSON',
        b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}',
        b'data: [DONE]',
    ]
    with patch("requests.post", return_value=resp):
        evs = list(_openai_provider().generate_stream_events("s", []))
    types = [e["type"] for e in evs]
    assert "done" in types, types  # clean terminal still terminates
    assert [e["text"] for e in evs if e["type"] == "content"] == ["ok"]


# ── Ollama dialect (NDJSON, done:true terminal) ──

def _ollama_provider():
    from wisp.providers.ollama import OllamaProvider
    return OllamaProvider(base_url="http://localhost:11434", model="qwen")


def _nd(resp_lines):
    resp = MagicMock()
    resp.iter_lines.return_value = resp_lines
    return resp


def test_ollama_success_terminal():
    # Direct-path dialect: dicts; terminal is {"type": "done"}.
    p = _ollama_provider()
    with patch.object(p, "_stream_post", return_value=_nd([
            b'{"message": {"content": "hi"}}', b'{"done": true}'])):
        evs = list(p.generate_stream_events("s", []))
    types = [e["type"] for e in evs]
    assert "done" in types, types
    assert "error" not in types, types


def test_ollama_eof_before_done_no_complete():
    p = _ollama_provider()
    with patch.object(p, "_stream_post", return_value=_nd([
            b'{"message": {"content": "half"}}'])):
        evs = list(p.generate_stream_events("s", []))
    types = [e["type"] for e in evs]
    assert "done" not in types, types
    assert "content" in types, types  # partial preserved


def test_ollama_conn_error_no_complete():
    p = _ollama_provider()
    with patch.object(p, "_stream_post",
                      side_effect=Exception("Connection refused")):
        evs = list(p.generate_stream_events("s", []))
    types = [e["type"] for e in evs]
    assert "done" not in types, types
    assert "error" in types, types


def test_ollama_duplicate_lines_pass_through():
    # No safe chunk identity at this layer -> no dedup (deferred by
    # design). Pinned: both copies surface; completion still terminal.
    p = _ollama_provider()
    with patch.object(p, "_stream_post", return_value=_nd([
            b'{"message": {"content": "same"}}',
            b'{"message": {"content": "same"}}',
            b'{"done": true}'])):
        evs = list(p.generate_stream_events("s", []))
    texts = [e["text"] for e in evs if e["type"] == "content"]
    assert texts == ["same", "same"], texts
    assert "done" in [e["type"] for e in evs]


# ── Turn level: non-complete round-trips never emit done ──

class _RaisingMidStream:
    def __init__(self, exc):
        self.exc = exc

    def generate_stream_events(self, system_prompt, messages, tools=None,
                               checkpoint_every=50):
        from wisp.stream_events import TokenBatch
        yield TokenBatch(phase="content", text="prefix ", batch_index=0)
        raise self.exc


def test_turn_mid_error_no_done():
    import requests
    evs, _ = _turn_events(_RaisingMidStream(requests.ConnectionError("rst")))
    assert evs is not None
    assert "done" not in _types(evs), _types(evs)
    assert "error" in _types(evs), _types(evs)
    assert "content" in _types(evs)  # partial retained (§3)


def test_turn_pre_stream_error_no_done():
    import requests

    class _Boom:
        def generate_stream_events(self, *a, **k):
            raise requests.ConnectionError("refused")
            yield  # pragma: no cover

    evs, _ = _turn_events(_Boom())
    assert evs is not None
    assert "done" not in _types(evs), _types(evs)
    assert "error" in _types(evs), _types(evs)


def test_turn_timeout_before_first_token_no_done():
    class _Silent:
        def generate_stream_events(self, *a, **k):
            time.sleep(30)
            yield  # pragma: no cover

    core = WispAgentCore(provider=_Silent())
    core.FIRST_TOKEN_DEADLINE_S = 0.2
    ws = tempfile.mkdtemp(prefix="g1b-ct_")
    session = {"id": "ct", "messages": [], "model": "mock", "workspace": ws}

    async def _collect():
        async for ev in core.turn(session, "go"):
            yield ev

    async def _main():
        return [ev async for ev in _collect()]

    import os
    os.environ["WISP_STREAM_ATTEMPTS"] = "1"
    try:
        evs = asyncio.run(asyncio.wait_for(_main(), 30))
    finally:
        del os.environ["WISP_STREAM_ATTEMPTS"]
    assert "done" not in _types(evs), _types(evs)
    assert "error" in _types(evs), _types(evs)


def test_turn_timeout_mid_stream_no_done():
    from wisp.stream_events import TokenBatch

    class _Stall:
        def generate_stream_events(self, *a, **k):
            yield TokenBatch(phase="content", text="part ", batch_index=0)
            time.sleep(30)
            yield TokenBatch(phase="content", text="late", batch_index=1)

    core = WispAgentCore(provider=_Stall())
    core.CHUNK_DEADLINE_S = 0.2
    core.FIRST_TOKEN_DEADLINE_S = 5.0
    ws = tempfile.mkdtemp(prefix="g1b-ct_")
    session = {"id": "ct", "messages": [], "model": "mock", "workspace": ws}

    async def _main():
        return [ev async for ev in core.turn(session, "go")]

    evs = asyncio.run(asyncio.wait_for(_main(), 30))
    assert "done" not in _types(evs), _types(evs)
    # stall notice (provider_status/chunk_stall) or error — never success
    assert any(t in ("provider_status", "error") for t in _types(evs)), _types(evs)


def test_turn_cancel_mid_stream_no_done():
    from wisp.stream_events import TokenBatch

    class _Slow:
        def generate_stream_events(self, *a, **k):
            i = 0
            while True:
                time.sleep(0.05)
                yield TokenBatch(phase="content", text="x", batch_index=i)
                i += 1

    core = WispAgentCore(provider=_Slow())
    ws = tempfile.mkdtemp(prefix="g1b-ct_")
    session = {"id": "ct", "messages": [], "model": "mock", "workspace": ws}

    async def _main():
        task = asyncio.ensure_future(_collect())
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            return await task
        except asyncio.CancelledError:
            return "cancelled"

    async def _collect():
        evs = []
        async for ev in core.turn(session, "go"):
            evs.append(ev)
        return evs

    out = asyncio.run(_main())
    assert out == "cancelled", out


def test_turn_tool_call_then_mid_error_surfaces_error():
    # Caller integration (§20): tool executes via the normal path, then the
    # NEXT round fails mid-stream -> error surfaced, turn NOT done.
    from wisp.stream_events import TokenBatch, ToolCallBatch
    import requests

    calls = []

    class _Script:
        def generate_stream_events(self, *a, **k):
            if not calls:
                calls.append(1)
                yield ToolCallBatch(phase="tool_calls", calls=[
                    {"id": "c1", "name": "read_file",
                     "arguments": {"path": "nope-missing.txt"}}])
                return
            yield TokenBatch(phase="content", text="later ", batch_index=0)
            raise requests.ConnectionError("reset on round 2")

    evs, _ = _turn_events(_Script())
    assert evs is not None
    assert "tool_result" in _types(evs), _types(evs)
    assert "error" in _types(evs), _types(evs)
    assert "done" not in _types(evs), _types(evs)


def test_partial_tool_call_denied_stays_denied(tmp_path):
    # Security regression (§21): truncated-shaped tool args still pass
    # through authorize; a denial blocks the effect. Authorization untouched.
    # Layer 1 — executor level with salvaged-shape args:
    from wisp.config import WispConfig
    from wisp.tool_executor import ToolExecutor

    seen = []

    async def _deny(name, args, reason):
        seen.append((name, dict(args)))
        return False, {}

    async def _run():
        ex = ToolExecutor(config=WispConfig(), hook_manager=None, mcp=None,
                          file_lock=None, lsp_manager=None,
                          subagent_orchestrator=None, extensions=None)
        evs = []
        async for ev in ex.execute(
                "write_file",
                {"content": "truncated", "path": "output.txt"},
                str(tmp_path), approval_handler=_deny):
            evs.append(ev)
        return evs

    evs = asyncio.run(_run())
    assert seen and seen[0][0] == "write_file", seen
    assert not (tmp_path / "output.txt").exists(), "denied write took effect"

    def _etype(e):
        return e.get("type") if isinstance(e, dict) else str(getattr(e, "type", ""))

    assert any(_etype(e) == "tool_result" for e in evs)
