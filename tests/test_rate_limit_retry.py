"""One owner for rate-limit retry, and the server's `Retry-After` honoured (ADR 2026-10-06-rate-limit-retry).

The bug (a real REPL session, OpenRouter answering 429): `hardened_post` retried a 429 three times in under two seconds, and
`guarded_provider_stream` then retried the whole request three times more, so one user message became up to nine HTTP requests
in twenty seconds against an endpoint that was already throttling, the `Retry-After` header was never read, and the error said
"after 3 attempts". These tests hold the repaired behaviour: three requests, spaced by what the server asked for.
"""
from __future__ import annotations

import asyncio
import email.utils
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from wisp.core import provider_stream as PS
from wisp.core.events import canonical_event
from wisp.core.transport import hardened_post, parse_retry_after


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
# parse_retry_after
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("raw,expected", [
    ("5", 5.0), ("0", 0.0), ("2.5", 2.5), ("  7 ", 7.0), (3, 3.0), (1.5, 1.5), ("120", 120.0),
    ("-3", None), ("abc", None), ("", None), (None, None), ("nan", None), ("inf", None), ("-inf", None), (True, None),
    (MagicMock(), None), ([], None), ("1e999", None)])
def test_retry_after_seconds_and_garbage(raw, expected):
    assert parse_retry_after(raw) == expected


def test_retry_after_accepts_an_http_date_and_never_goes_negative():
    now = 1_700_000_000.0
    future = email.utils.formatdate(now + 42, usegmt=True)
    past = email.utils.formatdate(now - 500, usegmt=True)
    assert parse_retry_after(future, now=now) == pytest.approx(42.0, abs=1.0)
    assert parse_retry_after(past, now=now) == 0.0
    assert parse_retry_after("Wed, 99 Foo 2099 99:99:99 GMT", now=now) is None


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
# hardened_post: status retry is optional, transport retry is not
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.status_code, self.headers, self.text = status, headers or {}, ""

    def close(self) -> None:
        pass


class _Session:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes, self.calls = list(outcomes), 0

    def post(self, url, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else self.outcomes_last
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    outcomes_last: Any = None


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("wisp.core.transport.time.sleep", lambda s: None)


def test_by_default_a_429_is_still_retried_inside_hardened_post():
    s = _Session([_Resp(429), _Resp(429), _Resp(200)])
    assert hardened_post(s, "http://x", json={}).status_code == 200 and s.calls == 3


def test_with_retry_status_off_a_429_comes_straight_back_after_one_request():
    s = _Session([_Resp(429, {"Retry-After": "9"}), _Resp(200)])
    resp = hardened_post(s, "http://x", json={}, retry_status=False)
    assert resp.status_code == 429 and s.calls == 1 and resp.headers["Retry-After"] == "9"


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_with_retry_status_off_no_transient_status_is_retried(status):
    s = _Session([_Resp(status), _Resp(200)])
    assert hardened_post(s, "http://x", json={}, retry_status=False).status_code == status and s.calls == 1


def test_transport_errors_are_still_retried_when_status_retry_is_off():
    s = _Session([ConnectionResetError("reset"), ConnectionResetError("reset"), _Resp(200)])
    assert hardened_post(s, "http://x", json={}, retry_status=False).status_code == 200 and s.calls == 3


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
# the provider: one request per attempt, and the server's advice travels up
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════


class _HttpResp:
    def __init__(self, status: int, headers: dict[str, str], body: str = "{}") -> None:
        self.status_code, self.headers, self.text = status, headers, body

    def close(self) -> None:
        pass

    def iter_lines(self):
        return iter(())


def _provider():
    from wisp.providers.openai import OpenAIProvider
    return OpenAIProvider(base_url="https://example.invalid/v1", model="m", api_key="k")


def test_the_provider_sends_one_request_and_carries_retry_after_in_its_error_event():
    with patch("requests.post", return_value=_HttpResp(429, {"Retry-After": "7"})) as post:
        events = list(_provider().generate_stream_events("sys", [{"role": "user", "content": "hi"}]))
    assert post.call_count == 1
    (err,) = [e for e in events if e.get("type") == "error"]
    assert err["status"] == 429 and err["retry_after"] == 7.0


def test_a_permanent_error_carries_no_retry_after():
    with patch("requests.post", return_value=_HttpResp(401, {"Retry-After": "7"})):
        (err,) = [e for e in _provider().generate_stream_events("sys", [{"role": "user", "content": "hi"}]) if e.get("type") == "error"]
    assert err["status"] == 401 and "retry_after" not in err


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
# the guarded stream: backoff, Retry-After, fail fast, honest counts
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════


def _error_stream(status: int, retry_after: float | None):
    async def gen():
        ev: dict[str, Any] = {"type": "error", "message": f"API error {status}", "status": status}
        if retry_after is not None:
            ev["retry_after"] = retry_after
        yield ev
    return gen()


async def _run(open_stream, max_attempts: int = 3) -> list[dict[str, Any]]:
    out = []
    async for ev in PS.guarded_provider_stream(open_stream, canonical_event, first_token_deadline_s=5, chunk_deadline_s=5,
                                               max_attempts=max_attempts):
        out.append(ev)
    return out


@pytest.fixture
def waits(monkeypatch):
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)
    monkeypatch.setattr(PS.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(PS.random, "uniform", lambda a, b: b)  # worst-case jitter: bounds are checked exactly
    return recorded


@pytest.mark.asyncio
async def test_a_429_without_advice_backs_off_for_seconds_not_fractions(waits):
    opened = []
    out = await _run(lambda: opened.append(1) or _error_stream(429, None))
    assert len(opened) == 3, "exactly max_attempts requests, never more"
    assert waits == [3.0 * 1 + 1.5, 3.0 * 2 + 1.5], waits
    assert any("kept rejecting" in str(e.get("message", "")) and "after 3 attempts" in str(e.get("message", "")) for e in out)


@pytest.mark.asyncio
async def test_a_5xx_keeps_its_gentler_backoff(waits):
    await _run(lambda: _error_stream(503, None))
    assert waits == [1.5 * 1 + 1.5, 1.5 * 2 + 1.5]


@pytest.mark.asyncio
async def test_the_server_advice_sets_the_wait(waits):
    opened = []
    await _run(lambda: opened.append(1) or _error_stream(429, 12.0))
    assert len(opened) == 3 and waits == [13.0, 13.0], "wait what the server asked (plus a second of jitter), every time"


@pytest.mark.asyncio
async def test_a_retry_after_of_zero_still_pauses(waits):
    await _run(lambda: _error_stream(429, 0.0))
    assert all(w >= 1.0 for w in waits)


@pytest.mark.asyncio
async def test_advice_longer_than_this_client_will_wait_fails_fast_with_the_number(waits):
    opened = []
    out = await _run(lambda: opened.append(1) or _error_stream(429, 600.0))
    assert len(opened) == 1 and waits == [], "no hammering, no multi-minute sleep"
    (err,) = [e for e in out if e.get("type") == "error"]
    assert "600" in err["message"] and "429" in err["message"] and "Try again in" in err["message"]
    assert err.get("data", {}).get("recoverable", err.get("recoverable", True)) is not False


@pytest.mark.asyncio
async def test_the_cap_is_inclusive_at_the_boundary(waits):
    opened = []
    await _run(lambda: opened.append(1) or _error_stream(429, PS.MAX_RETRY_AFTER_S))
    assert len(opened) == 3, "advice equal to the cap is waited for, not refused"
    opened.clear()
    await _run(lambda: opened.append(1) or _error_stream(429, PS.MAX_RETRY_AFTER_S + 1))
    assert len(opened) == 1


@pytest.mark.asyncio
async def test_a_429_that_clears_is_recovered_from(waits):
    calls = []

    def open_stream():
        calls.append(1)
        if len(calls) == 1:
            return _error_stream(429, 2.0)

        async def ok():
            yield {"type": "content", "text": "hello"}
            yield {"type": "done"}
        return ok()
    out = await _run(open_stream)
    assert [e for e in out if e.get("type") == "content"] and len(calls) == 2 and waits == [3.0]


@pytest.mark.asyncio
async def test_cancelling_during_the_wait_propagates(monkeypatch):
    async def never(_s: float) -> None:
        await asyncio.sleep(3600)
    started = asyncio.Event()

    async def slow_sleep(seconds: float) -> None:
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(PS.asyncio, "sleep", slow_sleep)
    task = asyncio.create_task(_run(lambda: _error_stream(429, None)))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
# the whole path: a throttling server sees three requests, not nine
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_throttling_endpoint_receives_exactly_one_request_per_attempt(waits):
    provider = _provider()
    with patch("requests.post", return_value=_HttpResp(429, {})) as post:
        out = await _run(lambda: provider.generate_stream_events_async("sys", [{"role": "user", "content": "hi"}]))
    assert post.call_count == 3, f"was 9 (3 transport attempts x 3 stream attempts); got {post.call_count}"
    assert any("after 3 attempts" in str(e.get("message", "")) for e in out)


@pytest.mark.asyncio
async def test_the_whole_path_honours_retry_after_from_the_http_response(waits):
    provider = _provider()
    with patch("requests.post", return_value=_HttpResp(429, {"Retry-After": "4"})) as post:
        await _run(lambda: provider.generate_stream_events_async("sys", [{"role": "user", "content": "hi"}]))
    assert post.call_count == 3 and waits == [5.0, 5.0]
