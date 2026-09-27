"""Redaction is on the TRACE, never the PROMPT — and the trace is not replayable.

The decision (and its cost) as tests. Two things are load-bearing:

1. The **refusal** is on the prompt side now. An earlier version refused the trace
   side; the inversion is deliberate and this file is where it is visible.
2. The **cost is mechanical, not a caveat**: a redacted trace genuinely cannot be
   replayed, and the test drives that through `wisp/core/replay_digest.py` rather
   than asserting it in prose.
"""
from __future__ import annotations

import pytest

from wisp.core.replay_digest import ReplayDivergence, projection_digest, verify
from wisp.runtime.redaction import (
    REPLAY_UNAVAILABLE_REASON,
    RedactionPoint,
    RedactionPointError,
    is_replayable,
    prompt_value,
    recorded_value,
    redact,
)

SECRET = "sk-live-0123456789"
MASK = "[REDACTED]"


def _redactor(value):
    """Stands in for `redact_sensitive_tool_args`, the real authority for *which*
    fields are sensitive."""
    if isinstance(value, str):
        return value.replace(SECRET, MASK)
    if isinstance(value, dict):
        return {k: _redactor(v) for k, v in value.items()}
    return value


def _transcript(value):
    return [{"role": "tool", "content": value, "tool_call_id": "c1"}]


# ── The refused point ───────────────────────────────────────────────────


class TestThePromptPointIsRefused:
    def test_redacting_the_prompt_raises(self):
        with pytest.raises(RedactionPointError):
            redact(SECRET, point=RedactionPoint.PROMPT, redactor=_redactor)

    def test_the_refusal_names_the_alternative(self):
        with pytest.raises(RedactionPointError) as excinfo:
            redact(SECRET, point=RedactionPoint.PROMPT, redactor=_redactor)
        text = str(excinfo.value)
        assert "TRACE" in text, "the refusal does not say where to redact instead"
        assert "credential reference" in text, (
            "the refusal does not name the option for a value that must not reach "
            "the provider")
        assert excinfo.value.code == "redaction_point_invalid"

    def test_both_points_are_spelled_out_so_the_refusal_is_readable(self):
        assert {p.value for p in RedactionPoint} == {"prompt", "trace"}


# ── The live point ──────────────────────────────────────────────────────


class TestTheTracePointIsTheLiveOne:
    def test_the_model_sees_the_real_value(self):
        """The reason for this side of the exclusion: a secret the agent must USE
        cannot be redacted out of the prompt without disabling the tool."""
        assert prompt_value(SECRET) == SECRET
        assert prompt_value({"token": SECRET}) == {"token": SECRET}

    def test_the_trace_holds_the_mask(self):
        assert recorded_value({"token": SECRET}, redactor=_redactor) == {"token": MASK}

    def test_the_trace_point_is_accepted_directly(self):
        assert redact(SECRET, point=RedactionPoint.TRACE, redactor=_redactor) == MASK

    def test_with_no_redactor_the_trace_is_the_identity(self):
        """The core is generic and the policy is configuration: wiring the point
        must not require a policy."""
        assert recorded_value(SECRET) == SECRET


# ── The cost, made mechanical ───────────────────────────────────────────


class TestTheCostIsReal:
    def test_a_redacted_trace_is_not_replayable(self):
        assert is_replayable(redacted=False) is True
        assert is_replayable(redacted=True) is False

    def test_a_redacted_trace_genuinely_diverges_from_the_run(self):
        """**The proof that the cost is real, not a caveat.** The run's transcript
        held the secret; the redacted trace holds the mask; replay cannot reconcile
        them. That is why the trace must declare itself unrunnable rather than let
        replay discover it as a divergence."""
        live = _transcript(SECRET)                    # what the run actually used
        recorded = projection_digest(live)
        redacted = _transcript(recorded_value(SECRET, redactor=_redactor))
        with pytest.raises(ReplayDivergence):
            verify(redacted, recorded)

    def test_the_unrunnable_reason_is_named_not_improvised(self):
        """A named constant, so replay refuses by name instead of raising a
        divergence that reads like a corruption bug."""
        assert "redacted at rest" in REPLAY_UNAVAILABLE_REASON
        assert "cannot be replayed" in REPLAY_UNAVAILABLE_REASON

    def test_an_unredacted_trace_still_replays(self):
        """The decision must not break replay for the runs that did not redact."""
        live = _transcript("ordinary tool output")
        verify(live, projection_digest(live))          # must not raise


# ── The floor ───────────────────────────────────────────────────────────


class TestTheFloor:
    def test_there_is_exactly_one_redaction_site(self):
        """If `recorded_value` were bypassed by a second inline `redactor(...)`
        call, a trace could acquire an UNREDACTED value by the back door — the
        failure this decision exists to prevent."""
        import inspect

        from wisp.runtime import redaction

        source = inspect.getsource(redaction)
        assert source.count("redactor(value)") == 1, (
            "more than one place applies the redactor; the trace's value must be "
            "produced in exactly one")
