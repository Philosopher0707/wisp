"""Provider conformance gauntlet — every protocol provider passes, identically.

One parametrized suite defining the observable contract of ``Provider``
(wisp/providers/protocol.py):

- every streamed event is a dict whose ``type`` is in the known vocabulary
- content events carry string ``text``; tool_call events carry ``name`` +
  ``arguments``; done events carry ``done_reason``
- terminal discipline: a stream ends after ``done`` or ``error`` and never
  continues past one
- errors arrive as ``{"type": "error"}`` events, never as raised exceptions
  (connection failures included)
- the async stream yields exactly what the sync stream yields
- cancelling the async stream early closes promptly (bridge threads joined,
  nothing hangs)
- metadata endpoints honor their shapes: health_check status vocabulary,
  list_models entries keyed by id, get_model_info with positive int context

Adding a provider means adding a spec to PROVIDER_SPECS and passing the same
gauntlet — no bespoke test file, no negotiated exceptions.

MockProvider deliberately does NOT appear here: it models the legacy
BaseProvider vocabulary (TokenBatch/ToolCallBatch/StreamComplete) which
``normalize_event`` bridges at the core boundary. Unifying that vocabulary
is tracked in ROADMAP.md Theme 1.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import requests

# ═══════════════════════════════════════════════════════════════════
# Wire-format fixtures: one canned conversation per provider dialect,
# encoding content("hello") followed by a clean stop.
# ═══════════════════════════════════════════════════════════════════

OPENAI_SSE_LINES = [
    'data: {"choices": [{"delta": {"content": "hello"}}]}',
    'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}',
    "data: [DONE]",
]

OLLAMA_NDJSON_LINES = [
    json.dumps({"message": {"content": "hello"}}),
    json.dumps({"done": True, "done_reason": "stop"}),
]


class _FakeStreamResponse:
    """Stand-in for requests.Response in streaming mode."""

    status_code = 200

    def __init__(self, lines: list[str]):
        self._lines = lines

    def iter_lines(self):
        return iter([line.encode("utf-8") for line in self._lines])


class _FakeJSONResponse:
    """Stand-in for requests.Response in JSON mode."""

    def __init__(self, payload: dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._payload


def _routed(method_map: dict[str, Any]):
    """Build a requests callable answering per URL substring."""

    def _call(url: str = "", *args: Any, **kwargs: Any):
        for fragment, response in method_map.items():
            if fragment in url:
                return response
        raise AssertionError(f"Unexpected URL in test stub: {url}")

    return _call


# ═══════════════════════════════════════════════════════════════════
# Provider specs: how to build each provider and stub its transport.
# ═══════════════════════════════════════════════════════════════════


class ProviderSpec:
    def __init__(self, name: str, make_provider, apply_transport_stubs):
        self.name = name
        self.make_provider = make_provider
        self.apply_transport_stubs = apply_transport_stubs


def _openai_like_spec(name: str, cls):
    def make():
        return cls(base_url="http://stub:1/v1", model="test-model", api_key="sk-test")

    def stub(monkeypatch, *, stream_lines=None, fail=False):
        stream_resp = (
            _FakeStreamResponse(OPENAI_SSE_LINES)
            if stream_lines is None
            else _FakeStreamResponse(stream_lines)
        )
        models_resp = _FakeJSONResponse({"data": [{"id": "test-model"}]})
        if fail:
            monkeypatch.setattr(
                requests, "post", _routed({"/chat/completions": ConnectionError("refused")})
            )
        else:
            monkeypatch.setattr(
                requests,
                "post",
                _routed({"/chat/completions": stream_resp}),
            )
        monkeypatch.setattr(requests, "get", _routed({"/models": models_resp}))

    return ProviderSpec(name, make, stub)


def _make_ollama():
    from wisp.providers.ollama import OllamaProvider

    return OllamaProvider(base_url="http://stub:11434", model="test-model")


def _ollama_stub(monkeypatch, *, stream_lines=None, fail=False):
    stream_resp = (
        _FakeStreamResponse(OLLAMA_NDJSON_LINES)
        if stream_lines is None
        else _FakeStreamResponse(stream_lines)
    )
    tags_resp = _FakeJSONResponse({"models": [{"name": "test-model"}]})
    show_resp = _FakeJSONResponse({"context_length": 4096})
    if fail:
        monkeypatch.setattr(
            requests, "post", _routed({"/api/chat": ConnectionError("refused")})
        )
    else:
        monkeypatch.setattr(
            requests,
            "post",
            _routed({"/api/chat": stream_resp, "/api/show": show_resp}),
        )
    monkeypatch.setattr(requests, "get", _routed({"/api/tags": tags_resp}))


SPECS = [
    _openai_like_spec("openai", __import__("wisp.providers.openai", fromlist=["OpenAIProvider"]).OpenAIProvider),
    _openai_like_spec("nvidia", __import__("wisp.providers.nvidia", fromlist=["NVIDIAProvider"]).NVIDIAProvider),
    ProviderSpec("ollama", _make_ollama, _ollama_stub),
]


@pytest.fixture(params=SPECS, ids=[s.name for s in SPECS])
def provider(request, monkeypatch):
    spec: ProviderSpec = request.param
    return spec, spec.make_provider(), monkeypatch


# ═══════════════════════════════════════════════════════════════════
# The gauntlet.
# ═══════════════════════════════════════════════════════════════════

TERMINAL_TYPES = {"done", "error"}
# stream_stats: internal bookkeeping (per-stream counters for empty-close
# forensics); the engine's guarded stream consumes it and never surfaces it
# as meaningful output.
KNOWN_TYPES = (
    {"content", "tool_call", "tool_calls", "thinking", "stream_stats"}
    | TERMINAL_TYPES
)


def _check_terminal_discipline(provider_name: str, types_seen: list[str]) -> None:
    """Assert exactly one terminal event, last, and nothing after it.

    Extracted so TestGauntletSensitivity can prove the tripwire fires.
    """
    terminals = [t for t in types_seen if t in TERMINAL_TYPES]
    assert len(terminals) == 1, (
        f"{provider_name}: expected exactly one terminal event, saw {terminals}"
    )
    assert types_seen[-1] in TERMINAL_TYPES, (
        f"{provider_name}: stream ended without a terminal event"
    )


class TestConformsToProtocolABC:
    def test_is_provider_subclass(self, provider):
        spec, prov, _ = provider
        from wisp.providers.protocol import Provider

        assert isinstance(prov, Provider)


class TestStreamEventContract:
    def test_events_are_dicts_in_known_vocabulary(self, provider):
        spec, prov, mp = provider
        spec.apply_transport_stubs(mp)
        for event in prov.generate_stream_events("sys", []):
            assert isinstance(event, dict), f"{spec.name}: non-dict event {event!r}"
            assert event.get("type") in KNOWN_TYPES, f"{spec.name}: unknown type {event!r}"

    def test_content_events_carry_string_text(self, provider):
        spec, prov, mp = provider
        spec.apply_transport_stubs(mp)
        texts = [
            e["text"]
            for e in prov.generate_stream_events("sys", [])
            if e.get("type") == "content"
        ]
        assert texts and all(isinstance(t, str) for t in texts)

    def test_clean_stream_ends_with_single_done(self, provider):
        spec, prov, mp = provider
        spec.apply_transport_stubs(mp)
        types_seen = [e["type"] for e in prov.generate_stream_events("sys", [])]
        _check_terminal_discipline(spec.name, types_seen)
        assert types_seen[-1] == "done"

    def test_done_carries_done_reason(self, provider):
        spec, prov, mp = provider
        spec.apply_transport_stubs(mp)
        dones = [
            e for e in prov.generate_stream_events("sys", []) if e.get("type") == "done"
        ]
        assert len(dones) == 1 and isinstance(dones[0].get("done_reason"), str)


class TestGauntletSensitivity:
    """The gauntlet must be able to fail — prove each tripwire fires."""

    def test_discipline_checker_rejects_event_after_terminal(self):
        with pytest.raises(AssertionError):
            _check_terminal_discipline("mutant", ["content", "done", "content"])

    def test_discipline_checker_rejects_double_terminal(self):
        with pytest.raises(AssertionError):
            _check_terminal_discipline("mutant", ["done", "error"])

    def test_discipline_checker_rejects_missing_terminal(self):
        with pytest.raises(AssertionError):
            _check_terminal_discipline("mutant", ["content", "thinking"])


class TestErrorDiscipline:
    def test_connection_failure_arrives_as_error_event_not_exception(self, provider):
        spec, prov, mp = provider
        spec.apply_transport_stubs(mp, fail=True)
        try:
            events = list(prov.generate_stream_events("sys", []))
        except Exception as exc:  # pragma: no cover - failure mode under test
            pytest.fail(f"{spec.name}: raised {exc!r} instead of yielding an error event")
        assert events, f"{spec.name}: silent death — no events at all"
        assert any(e.get("type") == "error" for e in events)
        assert all(isinstance(e.get("message", ""), str) for e in events if e["type"] == "error")


class TestAsyncParity:
    def test_async_stream_equals_sync_stream(self, provider):
        spec, prov, mp = provider
        spec.apply_transport_stubs(mp)
        sync_events = list(prov.generate_stream_events("sys", []))

        async def collect() -> list[dict]:
            return [
                e
                async for e in prov.generate_stream_events_async("sys", [])
            ]

        async_events = asyncio.run(asyncio.wait_for(collect(), timeout=10))
        assert async_events == sync_events

    def test_early_cancellation_closes_promptly(self, provider):
        spec, prov, mp = provider
        spec.apply_transport_stubs(mp)

        async def consume_one_then_close():
            agen = prov.generate_stream_events_async("sys", [])
            first = await agen.__anext__()
            await agen.aclose()
            return first

        # The bridge joins its producer thread on close (≤5s budget);
        # anything beyond this bound means cancellation hygiene is broken.
        first = asyncio.run(asyncio.wait_for(consume_one_then_close(), timeout=8))
        assert first.get("type") in KNOWN_TYPES


class TestMetadataEndpoints:
    def test_health_check_status_vocabulary(self, provider):
        spec, prov, mp = provider
        spec.apply_transport_stubs(mp)
        result = prov.health_check()
        assert isinstance(result, dict)
        assert result.get("status") in {"healthy", "unhealthy"}

    def test_list_models_entries_keyed_by_id(self, provider):
        spec, prov, mp = provider
        spec.apply_transport_stubs(mp)
        models = prov.list_models()
        assert isinstance(models, list) and models
        assert all(isinstance(m, dict) and isinstance(m.get("id"), str) for m in models)

    def test_get_model_info_shape(self, provider):
        spec, prov, mp = provider
        spec.apply_transport_stubs(mp)
        info = prov.get_model_info("test-model")
        assert isinstance(info, dict)
        assert info.get("id") == "test-model"
        ctx = info.get("context_length")
        assert isinstance(ctx, int) and ctx > 0
