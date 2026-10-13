"""A healthy stream that is silent while the model writes a tool call is not a dead stream.

Field case (2026-10-13, nemotron-3-ultra via NVIDIA): a turn died with "provider sent no
data for 90s mid-stream" right after the model said it would create model.py. A live probe
of NVIDIA's endpoint the same day: 13 reasoning chunks in 6 s, then 63 s with no bytes at
all, then the whole 7,317-character tool call in ONE chunk. NVIDIA buffers tool-call
arguments, so the wire is silent for as long as the model takes to write the call, and a
90 s silence limit cuts off any longer call. Providers that do stream argument deltas had the
mirror problem: the adapter accumulated them without yielding anything, so the stall guard
saw the same silence while bytes were arriving.
"""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from wisp.core.engine import WispAgentCore
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import PermissionMode, SecurityPolicy

_TOOL_CALL = {"type": "tool_call", "name": "write_file", "id": "call_1",
              "arguments": {"path": "model.py", "content": "x = 1\n"}}


def _core(provider):
    return WispAgentCore(
        provider=provider,
        security=SecurityPolicy(permission_mode=PermissionMode.FULL),
        extensions=ExtensionHost(),
    )


class _BuffersToolCalls:
    """Reasons, then sends nothing while 'writing' a tool call, then sends it whole."""

    TOOL_CALL_SILENCE_S = 5.0

    def __init__(self, reason_first=True, silence=1.0):
        self.reason_first, self.silence = reason_first, silence

    def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
        if self.reason_first:
            yield {"type": "thinking", "text": "Now let me create the model.py file."}
        time.sleep(self.silence)
        yield dict(_TOOL_CALL)
        yield {"type": "done"}


class _SameSilenceNoAllowance(_BuffersToolCalls):
    TOOL_CALL_SILENCE_S = None


async def _drain(core):
    return [e async for e in core._guarded_provider_stream("s", [{"role": "user", "content": "go"}], None)]


def _stalled(events):
    return [e for e in events
            if e.get("type") == "provider_status" or e.get("type") == "error"]


@pytest.fixture
def tight_deadlines(monkeypatch):
    monkeypatch.delenv("WISP_CHUNK_DEADLINE", raising=False)
    monkeypatch.delenv("WISP_FIRST_TOKEN_DEADLINE", raising=False)
    monkeypatch.setattr(WispAgentCore, "CHUNK_DEADLINE_S", 0.3)
    monkeypatch.setattr(WispAgentCore, "FIRST_TOKEN_DEADLINE_S", 0.3)


class TestProvidersThatBufferToolCalls:
    @pytest.mark.asyncio
    async def test_silence_after_reasoning_is_allowed_for_a_declared_buffering_provider(self, tight_deadlines):
        events = await _drain(_core(_BuffersToolCalls(reason_first=True)))
        assert _stalled(events) == [], events
        assert [e for e in events if e.get("type") == "tool_call"], events

    @pytest.mark.asyncio
    async def test_silence_before_any_output_is_allowed_too(self, tight_deadlines):
        """No reasoning first: the whole call is the first thing on the wire."""
        events = await _drain(_core(_BuffersToolCalls(reason_first=False)))
        assert _stalled(events) == [], events
        assert [e for e in events if e.get("type") == "tool_call"], events

    @pytest.mark.asyncio
    async def test_the_guard_reports_provider_activity_to_a_watcher(self, tight_deadlines):
        """The subagent runner extends its wait on this signal; the guard is what sends it."""
        from wisp.core.provider_stream import watch_provider_activity

        with watch_provider_activity() as clock:
            start = clock.last
            events = await _drain(_core(_BuffersToolCalls(reason_first=True, silence=0.3)))
        assert [e for e in events if e.get("type") == "tool_call"], events
        assert clock.last >= start + 0.25, "the tool call's arrival was not reported"

    @pytest.mark.asyncio
    async def test_the_same_silence_still_stalls_without_the_allowance(self, tight_deadlines):
        events = await _drain(_core(_SameSilenceNoAllowance(reason_first=True)))
        assert any(e.get("type") == "provider_status" for e in events), events

    @pytest.mark.asyncio
    async def test_an_explicit_deadline_setting_wins(self, tight_deadlines, monkeypatch):
        monkeypatch.setenv("WISP_CHUNK_DEADLINE", "0.3")
        events = await _drain(_core(_BuffersToolCalls(reason_first=True)))
        assert any(e.get("type") == "provider_status" for e in events), events

    def test_nvidia_declares_the_allowance_and_its_read_timeout_exceeds_it(self, monkeypatch):
        import requests

        from wisp.providers.nvidia import NVIDIAProvider
        from wisp.providers.openai import OpenAIProvider

        seen = {}

        def capture(*args, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            raise requests.exceptions.ConnectionError("captured")

        # The production path: the adapter sends through the hardened transport.
        monkeypatch.setattr("wisp.core.transport.hardened_post", capture)
        for cls in (NVIDIAProvider, OpenAIProvider):
            seen.clear()
            list(cls(api_key="k", model="m").generate_stream_events("s", [{"role": "user", "content": "x"}]))
            read_timeout = seen["timeout"].read
            allowance = getattr(cls, "TOOL_CALL_SILENCE_S", None)
            if cls is NVIDIAProvider:
                assert allowance and allowance >= 300
                assert read_timeout > allowance
            else:
                assert allowance is None
                assert read_timeout == 120


# ── Providers that stream argument deltas (OpenAI-compatible SSE) ───────────

def _sse_server(pieces: list[str], gap: float):
    """One request: a reasoning chunk, then the tool call's arguments in `pieces`, `gap` apart."""

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def send(delta, finish=None):
                body = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(b"data: " + json.dumps(body).encode() + b"\n\n")
                self.wfile.flush()

            send({"reasoning_content": "Now let me create the model.py file."})
            send({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                  "function": {"name": "write_file", "arguments": ""}}]})
            for piece in pieces:
                time.sleep(gap)
                send({"tool_calls": [{"index": 0, "function": {"arguments": piece}}]})
            send({}, "tool_calls")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TestProvidersThatStreamArguments:
    @pytest.mark.asyncio
    async def test_argument_deltas_keep_the_stream_alive(self, tight_deadlines, monkeypatch):
        """Bytes arrive every 0.2 s; the stall limit is 0.6 s; the call takes about 3 s."""
        from wisp.providers.openai import OpenAIProvider

        monkeypatch.setattr(WispAgentCore, "CHUNK_DEADLINE_S", 0.6)
        args = json.dumps({"path": "model.py", "content": "y = 2\n" * 1500})
        # Pieces over 512 bytes: the HTTP client reads in 512-byte blocks, so smaller
        # pieces would arrive in bursts rather than as sent.
        pieces = [args[i:i + 600] for i in range(0, len(args), 600)]
        srv = _sse_server(pieces, gap=0.2)
        try:
            provider = OpenAIProvider(base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1",
                                      model="m", api_key="k")
            started = time.monotonic()
            events = await _drain(_core(provider))
            elapsed = time.monotonic() - started
        finally:
            srv.shutdown()
            srv.server_close()
        assert _stalled(events) == [], [e.get("type") for e in events]
        calls = [e for e in events if e.get("type") == "tool_call"]
        assert len(calls) == 1 and calls[0]["arguments"]["content"] == "y = 2\n" * 1500
        assert elapsed > 2 * WispAgentCore.CHUNK_DEADLINE_S, f"the call must outlast the limit ({elapsed:.1f}s)"
        # Liveness is consumed by the guard: nothing downstream sees it.
        assert not [e for e in events if e.get("type") == "stream_progress"]


class TestDeltasArriveAsSent:
    @pytest.mark.asyncio
    async def test_small_argument_deltas_keep_the_stream_alive(self, tight_deadlines, monkeypatch):
        """Real deltas are a few tokens each. `iter_lines()` waited for 512-byte blocks, so a slow
        stream of small deltas reached the guard in bursts, with silences longer than the limit."""
        from wisp.providers.openai import OpenAIProvider

        monkeypatch.setattr(WispAgentCore, "CHUNK_DEADLINE_S", 0.6)
        args = json.dumps({"path": "model.py", "content": "z = 3\n" * 60})
        pieces = [args[i:i + 24] for i in range(0, len(args), 24)][:15]
        pieces[-1] = args[24 * 14:]
        srv = _sse_server(pieces, gap=0.2)
        try:
            provider = OpenAIProvider(base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1",
                                      model="m", api_key="k")
            events = await _drain(_core(provider))
        finally:
            srv.shutdown()
            srv.server_close()
        assert _stalled(events) == [], [e.get("type") for e in events]
        calls = [e for e in events if e.get("type") == "tool_call"]
        assert len(calls) == 1 and calls[0]["arguments"]["content"] == "z = 3\n" * 60

    def test_lines_are_split_across_reads_and_crlf(self):
        from wisp.providers._stream_lines import split_lines

        reads = [b"data: {\"a\"", b": 1}\r\n\r\ndata: [DO", b"NE]\n", b"tail-without-newline"]
        assert list(split_lines(iter(reads))) == [
            b'data: {"a": 1}', b"", b"data: [DONE]", b"tail-without-newline"]


# ── Subagents: the runner's first-event limit ──────────────────────────────

class _SlowFirstEventCore:
    """A child whose first event comes after the runner's first-token deadline."""

    report_activity = False

    def __init__(self, **kwargs):
        pass

    async def turn(self, session_dict, task, **kwargs):
        if self.report_activity:
            from wisp.core.provider_stream import note_provider_activity
            for _ in range(6):
                await asyncio.sleep(0.2)
                note_provider_activity()
        else:
            await asyncio.sleep(1.0)
        yield {"type": "content", "text": "written"}
        yield {"type": "done"}


class _ActiveSlowFirstEventCore(_SlowFirstEventCore):
    report_activity = True


def _run_child(tmp_path, monkeypatch, core_cls, provider):
    from unittest.mock import AsyncMock, patch

    from wisp.config import WispConfig
    from wisp.multi_agent import SubagentContract, SubagentOrchestrator

    monkeypatch.delenv("WISP_FIRST_TOKEN_DEADLINE", raising=False)
    cfg = WispConfig().replace(workspace=str(tmp_path))
    o = SubagentOrchestrator(config=cfg, workspace=tmp_path)
    monkeypatch.setattr(o._runner.__class__, "FIRST_TOKEN_DEADLINE_S", 0.4)
    contract = SubagentContract(name="c", task="t", timeout_seconds=60, max_retries=0,
                                worktree_isolated=False)
    with patch("wisp.core.engine.WispAgentCore", core_cls), \
         patch("wisp.providers.factory.ProviderFactory.from_config", return_value=provider), \
         patch("wisp.tools.context.get_turn_deadline", return_value=None), \
         patch.object(o, "_resolve_worktree", new=AsyncMock(return_value=None)), \
         patch.object(o, "_fire_subagent_hook", new=AsyncMock()):
        return asyncio.run(o.run(contract))


class TestSubagentFirstEvent:
    def test_a_child_on_a_buffering_provider_gets_the_allowance(self, tmp_path, monkeypatch):
        result = _run_child(tmp_path, monkeypatch, _SlowFirstEventCore, _BuffersToolCalls())
        assert result.success, result.output
        assert "FIRST TOKEN TIMEOUT" not in result.output

    def test_provider_activity_extends_the_first_event_wait(self, tmp_path, monkeypatch):
        result = _run_child(tmp_path, monkeypatch, _ActiveSlowFirstEventCore, _SameSilenceNoAllowance())
        assert result.success, result.output

    def test_a_silent_child_without_allowance_still_times_out(self, tmp_path, monkeypatch):
        result = _run_child(tmp_path, monkeypatch, _SlowFirstEventCore, _SameSilenceNoAllowance())
        assert not result.success
        assert "FIRST TOKEN TIMEOUT" in result.output
