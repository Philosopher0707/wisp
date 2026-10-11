"""The setup wizard's validation handshake must work for providers that only stream.

`_probe` called `provider.generate(...)`, but only the mock and Ollama providers define it; OpenAI, OpenRouter and NVIDIA
(the keyed ones) expose `generate_stream_events` only. So for every keyed provider the handshake died with
`AttributeError: ... object has no attribute 'generate'` and the wizard fell through to "save anyway", never validating.
"""

from __future__ import annotations

import pytest

from wisp.cli import setup as setup_mod


class _StreamOnly:
    """Shaped like OpenAIProvider/NVIDIAProvider: no `generate`, only a streaming generator."""

    def __init__(self, events):
        self._events = events
        self.closed = False
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
        self.calls += 1
        try:
            yield from self._events
        finally:
            self.closed = True


class _WithGenerate:
    def __init__(self, result=None, raises=None):
        self.result, self.raises = result, raises

    def generate(self, system_prompt, messages, tools=None):
        if self.raises:
            raise self.raises
        return self.result


@pytest.fixture
def use_provider(monkeypatch):
    def _use(provider):
        from wisp.providers.factory import ProviderFactory

        monkeypatch.setattr(ProviderFactory, "from_config", lambda self, cfg: provider)
        return provider

    return _use


def _probe():
    return setup_mod._probe("nvidia", "m", "k", "", timeout_s=5)


def test_a_streaming_only_provider_that_answers_validates(use_provider):
    p = use_provider(_StreamOnly([{"type": "content", "text": "ok"}, {"type": "done", "done_reason": "stop"}]))
    ok, detail = _probe()
    assert ok is True, detail
    assert p.calls == 1


def test_the_stream_is_closed_after_the_first_answer(use_provider):
    p = use_provider(_StreamOnly([{"type": "content", "text": "ok"}] + [{"type": "content", "text": "x"}] * 1000))
    assert _probe()[0] is True
    assert p.closed is True  # the handshake must not read a whole completion


def test_an_error_event_is_a_failed_handshake_with_the_provider_s_message(use_provider):
    use_provider(_StreamOnly([{"type": "error", "message": "API error 402: out of credit"}]))
    ok, detail = _probe()
    assert ok is False
    assert "402" in detail and "credit" in detail


def test_a_402_is_a_billing_message_not_an_unreachable_endpoint(use_provider):
    # The provider layer words a 402 as "the provider refused this request on billing"; "refused" must not read as a network failure.
    use_provider(_StreamOnly([{"type": "error", "message": "API error 402: more credits required — the provider refused this request on billing"}]))
    ok, detail = _probe()
    assert ok is False and detail.startswith("out of credit") and "unreachable" not in detail


def test_a_401_error_event_is_classified_as_a_bad_key(use_provider):
    use_provider(_StreamOnly([{"type": "error", "message": "API error 401: invalid api key"}]))
    ok, detail = _probe()
    assert ok is False and detail.startswith("unauthorized")


def test_a_stream_that_ends_with_nothing_is_not_a_validated_handshake(use_provider):
    use_provider(_StreamOnly([]))
    ok, detail = _probe()
    assert ok is False and "no response" in detail.lower()


def test_an_exception_while_streaming_is_reported_not_raised(use_provider):
    class Boom(_StreamOnly):
        def generate_stream_events(self, *a, **k):
            raise ConnectionError("connection refused")
            yield  # pragma: no cover

    use_provider(Boom([]))
    ok, detail = _probe()
    assert ok is False and detail.startswith("unreachable")


def test_typed_stream_events_are_understood_too(use_provider):
    class Typed:
        def __init__(self, type, **kw):
            self.type = type
            self.__dict__.update(kw)

    use_provider(_StreamOnly([Typed("content", text="ok")]))
    assert _probe()[0] is True


def test_providers_with_generate_keep_working(use_provider):
    use_provider(_WithGenerate(result={"content": "ok"}))
    assert _probe()[0] is True
    use_provider(_WithGenerate(raises=RuntimeError("API error 403 forbidden")))
    ok, detail = _probe()
    assert ok is False and detail.startswith("forbidden")
