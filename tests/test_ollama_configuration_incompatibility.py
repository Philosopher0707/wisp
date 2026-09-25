"""ADR-0038 R5/R6 — a capacity rejection is a named configuration incompatibility.

The ratified rule: `max_tokens` is an **exact** requested output budget, sent
verbatim. When the provider rejects it because the model's maximum output is
lower, Wisp **classifies and surfaces** that refusal — it does not clamp, omit,
negotiate or retry, and it does not turn the provider's error text into a
capability.

These tests pin both halves of that:

* the diagnostic **names** the configured budget and the provider-reported limit;
* nothing else changes — the request value, the retry policy, `None` semantics,
  and every other error classification stay exactly as they were.

**The double matches the real control flow**, which matters here: Ollama's
client returns a response and `raise_for_status()` raises *inside* the `with`
block, so the body must be read while the response is open. A double that raised
from `post()` itself would never exercise that path — and would have hidden the
defect this test file was written to pin.

Non-vacuity: `test_an_unrelated_400_is_not_this_classification` and
`test_the_limit_is_not_reused_on_a_later_request` fail if the matcher is widened
or the parsed limit is ever fed back into a request.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import requests.exceptions

from wisp.ollama_client import (
    OllamaClient,
    OllamaConfigurationError,
    OllamaError,
)
from wisp.stream_events import StreamError

# The real body, captured from Ollama for a model whose maximum output is 65536.
REAL_BODY = json.dumps({
    "error": "max_tokens (131072) exceeds model's maximum output tokens "
             "(65536) for model nemotron-3-ultra (ref: 5348158a-1407-4608)"
})

CONFIGURED = 131072
PROVIDER_LIMIT = 65536


class FakeConfig:
    ollama_url = "http://localhost:11434"
    model = "nemotron-3-ultra:cloud"
    temperature = 0.2
    max_tokens = CONFIGURED
    max_context_tokens = 128000
    chars_per_token = 4
    auto_approve = True
    show_thinking = False


class FakeResponse:
    """Duck-types a requests Response, including `.text` for the body matcher."""

    def __init__(self, status_code=200, body="", json_data=None):
        self.status_code = status_code
        self.text = body
        self._json = json_data or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)

    def iter_content(self, chunk_size=None):
        return iter(b"")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def _reject(body=REAL_BODY, status=400):
    """A RESPONSE that rejects on raise_for_status(), as the real path does."""
    return FakeResponse(status, body)


def _client(**overrides):
    cfg = FakeConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return OllamaClient(cfg)


def _drive(client, messages=None):
    """Drive the public streaming entry point, which is what BUILDS the payload.

    `_post_stream` receives an already-built payload, so asserting on
    `num_predict` requires going through `generate_stream_events`.

    Note the exception does NOT propagate from here: `generate_stream_events`
    converts every failure into a `StreamError` carrying
    `error_type=type(e).__name__` and `message=str(e)`. That is the real
    observable surface, and it is where the classification has to land.
    """
    return list(client.generate_stream_events(
        "sys", messages or [{"role": "user", "content": "hi"}], None))


def _errors(events):
    return [e for e in events if isinstance(e, StreamError)]


# ══════════════════════════════════════════════════════════════════════════
# R5/R6 — the classification and the diagnostic
# ══════════════════════════════════════════════════════════════════════════


class TestTheCapacityRejectionIsClassified:
    def test_it_raises_the_configuration_error_type(self):
        client = _client()
        with patch.object(client._session, "post", return_value=_reject()):
            with pytest.raises(OllamaConfigurationError):
                list(client._post_stream("chat", {"model": "m"}))

    def test_it_is_still_an_ollama_error(self):
        """Backward compatible: every existing `except OllamaError` still catches it."""
        assert issubclass(OllamaConfigurationError, OllamaError)

    def test_it_carries_the_classification(self):
        assert OllamaConfigurationError.kind == "CONFIGURATION_INCOMPATIBILITY"

    def test_the_diagnostic_names_the_configured_budget(self):
        client = _client()
        with patch.object(client._session, "post", return_value=_reject()):
            with pytest.raises(OllamaConfigurationError) as caught:
                list(client._post_stream("chat", {"model": "m"}))
        assert str(CONFIGURED) in str(caught.value)

    def test_the_diagnostic_names_the_provider_reported_limit(self):
        client = _client()
        with patch.object(client._session, "post", return_value=_reject()):
            with pytest.raises(OllamaConfigurationError) as caught:
                list(client._post_stream("chat", {"model": "m"}))
        assert str(PROVIDER_LIMIT) in str(caught.value)

    def test_the_diagnostic_names_the_class(self):
        client = _client()
        with patch.object(client._session, "post", return_value=_reject()):
            with pytest.raises(OllamaConfigurationError) as caught:
                list(client._post_stream("chat", {"model": "m"}))
        text = str(caught.value).upper()
        assert "CONFIGURATION INCOMPATIBILITY" in text
        assert "NOT A TRANSIENT" in text

    def test_the_diagnostic_offers_the_remedy_without_claiming_adaptation(self):
        client = _client()
        with patch.object(client._session, "post", return_value=_reject()):
            with pytest.raises(OllamaConfigurationError) as caught:
                list(client._post_stream("chat", {"model": "m"}))
        text = str(caught.value).lower()
        assert "max_tokens" in text
        assert "not retried" in text
        assert "was not modified" in text
        # It must not claim to have recorded or reused the limit.
        assert "does not record it" in text

    def test_the_non_streaming_path_classifies_it_too(self):
        """`generate()` goes through `_post_with_retry`; one rule, not two."""
        client = _client()
        with patch.object(client._session, "post", return_value=_reject()):
            with pytest.raises(OllamaConfigurationError):
                client._post_with_retry("chat", {"model": "m"})

    def test_the_body_is_read_before_the_response_closes(self):
        """The regression that made this phase longer than expected.

        `raise_for_status()` raises INSIDE the `with`, so by the time the handler
        runs the response is closed and `response.text` is empty. The body must
        be captured while it is open, or the diagnostic can never be built.
        """
        closed = {"v": False}

        class ClosingResponse(FakeResponse):
            def __exit__(self, *a):
                closed["v"] = True

        client = _client()
        with patch.object(client._session, "post",
                          return_value=ClosingResponse(400, REAL_BODY)):
            with pytest.raises(OllamaConfigurationError) as caught:
                list(client._post_stream("chat", {"model": "m"}))
        assert closed["v"], "the response was never closed — double is wrong"
        assert str(PROVIDER_LIMIT) in str(caught.value)


# ══════════════════════════════════════════════════════════════════════════
# R7 — not retried, and the request is not modified
# ══════════════════════════════════════════════════════════════════════════


class TestItIsPermanentAndUnmodified:
    def test_one_logical_call_is_one_post(self):
        client = _client()
        calls = {"n": 0}

        def _side_effect(*a, **kw):
            calls["n"] += 1
            return _reject()

        with patch.object(client._session, "post", side_effect=_side_effect):
            with pytest.raises(OllamaConfigurationError):
                list(client._post_stream("chat", {"model": "m"}))
        assert calls["n"] == 1, "a capacity rejection must not be retried"

    def test_the_request_still_carries_the_configured_value_verbatim(self):
        client = _client()
        seen = []

        def _side_effect(*a, **kw):
            seen.append(kw.get("json") or {})
            return _reject()

        with patch.object(client._session, "post", side_effect=_side_effect):
            _drive(client)
        assert seen[0]["options"]["num_predict"] == CONFIGURED

    def test_the_limit_is_not_reused_on_a_later_request(self):
        """F4/F5 falsification: the parsed limit must never become state."""
        client = _client()
        seen = []

        def _side_effect(*a, **kw):
            seen.append(kw.get("json") or {})
            if len(seen) == 1:
                return _reject()
            return FakeResponse(200)

        with patch.object(client._session, "post", side_effect=_side_effect):
            _drive(client)
            _drive(client)

        assert seen[1]["options"]["num_predict"] == CONFIGURED, (
            "the provider-reported limit was fed back into a later request")
        assert not hasattr(client, "model_max_output"), (
            "the provider-reported limit became client state")

    def test_no_automatic_fallback_to_omitting_the_budget(self):
        """F6 falsification: `None` must stay the user's explicit choice."""
        client = _client()
        seen = []

        def _side_effect(*a, **kw):
            seen.append(kw.get("json") or {})
            return _reject()

        with patch.object(client._session, "post", side_effect=_side_effect):
            _drive(client)
        assert "num_predict" in seen[0]["options"], (
            "the budget was silently omitted instead of surfaced")

    def test_the_stream_error_event_carries_the_classification(self):
        """The end-to-end surface: `error_type` names the class, `message` the numbers."""
        client = _client()
        with patch.object(client._session, "post", return_value=_reject()):
            events = _drive(client)
        errs = _errors(events)
        assert len(errs) == 1
        assert errs[0].error_type == "OllamaConfigurationError"
        assert str(CONFIGURED) in errs[0].message
        assert str(PROVIDER_LIMIT) in errs[0].message


# ══════════════════════════════════════════════════════════════════════════
# The matcher must stay narrow
# ══════════════════════════════════════════════════════════════════════════


class TestTheMatcherIsNarrow:
    @pytest.mark.parametrize("body", [
        "",                                                    # empty body
        "not json at all",                                     # unparseable
        json.dumps({"error": "model 'x' not found"}),          # unrelated 400
        json.dumps({"error": "invalid options"}),              # unrelated 400
        json.dumps({"error": "maximum context length exceeded"}),
        json.dumps({"error": "too many tokens in the prompt"}),  # keywords only
        json.dumps({"error": "max_tokens is not a valid option"}),
    ])
    def test_an_unrelated_400_is_not_this_classification(self, body):
        """F1/F8 falsification: a generic 400 keeps the generic error."""
        client = _client()
        with patch.object(client._session, "post", return_value=_reject(body)):
            with pytest.raises(OllamaError) as caught:
                list(client._post_stream("chat", {"model": "m"}))
        assert not isinstance(caught.value, OllamaConfigurationError)
        assert "Ollama HTTP error" in str(caught.value)

    def test_no_budget_configured_is_not_this_classification(self):
        """With `None` there is no budget to exceed, so no such claim is made."""
        client = _client(max_tokens=None)
        with patch.object(client._session, "post", return_value=_reject()):
            with pytest.raises(OllamaError) as caught:
                list(client._post_stream("chat", {"model": "m"}))
        assert not isinstance(caught.value, OllamaConfigurationError)


# ══════════════════════════════════════════════════════════════════════════
# Everything else is untouched
# ══════════════════════════════════════════════════════════════════════════


class TestNothingElseChanged:
    def test_none_still_omits_num_predict(self):
        client = _client(max_tokens=None)
        seen = []

        def _side_effect(*a, **kw):
            seen.append(kw.get("json") or {})
            return FakeResponse(200)

        with patch.object(client._session, "post", side_effect=_side_effect):
            _drive(client)
        assert "num_predict" not in seen[0]["options"]

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    def test_a_500_still_retries(self):
        """F2-adjacent: the retry policy for server errors is unchanged."""
        client = _client()
        calls = {"n": 0}

        def _side_effect(*a, **kw):
            calls["n"] += 1
            return _reject(body="upstream boom", status=503)

        with patch.object(client._session, "post", side_effect=_side_effect):
            with pytest.raises(OllamaError):
                list(client._post_stream("chat", {"model": "m"}))
        assert calls["n"] == 3, "a 5xx must still exhaust the retry budget"

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    def test_a_connection_failure_is_unchanged(self):
        client = _client()
        exc = requests.exceptions.ConnectionError("refused")
        with patch.object(client._session, "post", side_effect=exc):
            with pytest.raises(OllamaError, match="Cannot connect"):
                list(client._post_stream("chat", {"model": "m"}))
