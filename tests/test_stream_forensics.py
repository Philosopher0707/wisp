"""Empty-stream forensics: stats event, rich retry log, jittered backoff.

NVIDIA's endpoint closes HTTP-200 SSE streams with zero deltas, most often
under the parallel load of map_reduce subagent fan-outs. The guarded stream
already retried; now each close is explainable (server-sent-nothing vs
unusable chunks) and retries back off instead of hammering a throttling
window.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from wisp.core.stateless import WispAgentCore
from wisp.providers.openai import OpenAIProvider


def _sse(chunks: list[dict]) -> list[bytes]:
    lines = [f"data: {json.dumps(c)}".encode() for c in chunks]
    lines.append(b"data: [DONE]")
    return lines


def _provider_stream(chunks: list[dict]):
    provider = OpenAIProvider(model="m", api_key="k")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_lines.return_value = _sse(chunks)
    with patch("requests.post", return_value=mock_resp):
        yield from provider.generate_stream_events(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}]
        )


class TestStreamStatsEvent:
    def test_stats_counts_usable_and_empty(self):
        events = list(_provider_stream([
            {"choices": []},                                        # empty-choice chunk
            {"choices": [{"delta": {"content": "hey"}}]},
            {"choices": [{"delta": {"reasoning": "hmm"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]))
        stats = [e for e in events if e["type"] == "stream_stats"]
        assert len(stats) == 1
        st = stats[0]
        assert st["sse_lines"] == 5          # 4 chunks + [DONE]
        assert st["usable_deltas"] == 2      # content + reasoning
        assert st["empty_choice_chunks"] == 1
        assert st["finish_reason"] == "stop"

    def test_throttled_close_yields_zero_everything(self):
        # The throttle signature: HTTP 200, [DONE] immediately, no deltas.
        events = list(_provider_stream([]))
        stats = [e for e in events if e["type"] == "stream_stats"]
        assert stats and stats[0]["usable_deltas"] == 0
        assert stats[0]["sse_lines"] == 1

    def test_tool_call_delta_counts_as_usable(self):
        events = list(_provider_stream([
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "read_file"}}
            ]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]))
        stats = [e for e in events if e["type"] == "stream_stats"]
        assert stats[0]["usable_deltas"] >= 1


class TestGuardedRetryForensics:
    @staticmethod
    def _core_with_streams(streams):
        """Core whose provider replays canned event lists per attempt."""
        core = WispAgentCore.__new__(WispAgentCore)
        core.provider = None
        iters = iter(streams)

        async def _stream(*args, **kwargs):
            for ev in next(iters):
                yield ev

        core._stream_events_async = _stream
        return core

    @pytest.mark.asyncio
    async def test_retry_log_includes_stats_and_backoff_sleeps(self, caplog):
        empty = [{"type": "stream_stats", "sse_lines": 1,
                  "usable_deltas": 0, "empty_choice_chunks": 1,
                  "finish_reason": "stop"}, {"type": "done"}]
        good = [{"type": "content", "text": "ok"},
                {"type": "done"}]
        core = self._core_with_streams([empty, good])

        sleeps = []
        import asyncio as aio
        with patch.object(aio, "sleep", side_effect=lambda s: sleeps.append(s)):
            events = [e async for e in core._guarded_provider_stream("s", [], None)]

        types = [e.get("type") for e in events]
        assert "content" in types                     # second attempt served
        assert any(s > 0 for s in sleeps), f"expected backoff sleep, got {sleeps}"
        warned = [r.message for r in caplog.records if "retrying" in r.message]
        assert warned and "sse_lines=1" in warned[0]
        assert "usable=0" in warned[0]

    @pytest.mark.asyncio
    async def test_attempts_env_extends_retries(self):
        empty = [{"type": "stream_stats", "sse_lines": 1,
                  "usable_deltas": 0, "empty_choice_chunks": 1,
                  "finish_reason": "stop"}, {"type": "done"}]
        core = self._core_with_streams([empty, empty, empty,
                                        [{"type": "content", "text": "late"},
                                         {"type": "done"}]])
        import asyncio as aio
        with patch.object(aio, "sleep", side_effect=lambda s: None), \
             patch.dict("os.environ", {"WISP_STREAM_ATTEMPTS": "4"}):
            events = [e async for e in core._guarded_provider_stream("s", [], None)]
        assert any(e.get("type") == "content" for e in events)

    @pytest.mark.asyncio
    async def test_stats_event_not_counted_as_meaningful(self, caplog):
        # A stream that ONLY emits bookkeeping must still be retried —
        # stream_stats must never satisfy got_meaningful on its own.
        only_stats = [{"type": "stream_stats", "sse_lines": 3,
                       "usable_deltas": 0, "empty_choice_chunks": 2,
                      "finish_reason": "stop"}, {"type": "done"}]
        core = self._core_with_streams([only_stats, [{"type": "content", "text": "x"},
                                                     {"type": "done"}]])
        sleeps = []
        import asyncio as aio
        with patch.object(aio, "sleep", side_effect=lambda s: sleeps.append(s)):
            events = [e async for e in core._guarded_provider_stream("s", [], None)]
        assert any(e.get("type") == "content" for e in events)
        assert sleeps, "stats-only stream must trigger the retry path"


class TestTransientStatusRetry:
    @staticmethod
    def _core_with_event_streams(streams):
        core = WispAgentCore.__new__(WispAgentCore)
        core.provider = None
        iters = iter(streams)

        async def _stream(*args, **kwargs):
            for ev in next(iters):
                yield ev

        core._stream_events_async = _stream
        return core

    @pytest.mark.asyncio
    async def test_429_retried_then_success(self):
        rate = [{"type": "error", "message": "API error 429: x", "status": 429}]
        core = self._core_with_event_streams(
            [rate, rate, [{"type": "content", "text": "made it"},
                          {"type": "done"}]])
        waits = []
        import asyncio as aio
        with patch.object(aio, "sleep", side_effect=lambda s: waits.append(s)):
            events = [e async for e in core._guarded_provider_stream("s", [], None)]
        assert any(e.get("type") == "content" for e in events)
        assert len(waits) == 2 and waits[1] > waits[0], \
            f"429 backoff must scale: {waits}"

    @pytest.mark.asyncio
    async def test_permanent_api_error_surfaces_immediately(self):
        fatal = [{"type": "error", "message": "API error 401: bad key", "status": 401}]
        core = self._core_with_event_streams([fatal, fatal])
        events = [e async for e in core._guarded_provider_stream("s", [], None)]
        msgs = [str(e.get("message", "")) for e in events if e.get("type") == "error"]
        assert msgs and "401" in msgs[0]
        assert not any("no usable response" in m or "kept rejecting" in m
                       for m in msgs), "permanent errors must not burn retries"

    @pytest.mark.asyncio
    async def test_exhausted_429_reports_rate_limit(self):
        rate = [{"type": "error", "message": "API error 429: x", "status": 429}]
        core = self._core_with_event_streams([rate, rate, rate])
        import asyncio as aio
        with patch.object(aio, "sleep", side_effect=lambda s: None):
            events = [e async for e in core._guarded_provider_stream("s", [], None)]
        assert any("rate limited" in str(e.get("message", ""))
                   for e in events if e.get("type") == "error")


class TestWebFetchHint:
    def test_hint_string_names_search_tool(self):
        # The guidance lives in the error string; assert at source so any
        # rewording keeps pointing models at the tool that fixes guessing.
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "wisp" / "tools" / "web.py").read_text()
        assert "Use web_search to find a valid URL" in src
        assert src.count("web_search") >= 1
