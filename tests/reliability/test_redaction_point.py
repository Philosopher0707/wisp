"""Prompt vs trace redaction — mutually exclusive, proven rather than asserted.

The claim: they are the same edit at two different moments, and only one of them
leaves the record true. This file drives both and shows that the trace-side one is
**caught by the replay digest** (`wisp/core/replay_digest.py`) rather than merely
disapproved of.

That is the difference between a rule and a preference. A rule can be broken and
noticed; a preference is a comment.
"""
from __future__ import annotations

import pytest

from wisp.core.replay_digest import ReplayDivergence, projection_digest, verify
from wisp.runtime.redaction import (
    RedactionPoint,
    RedactionPointError,
    recorded_value,
    redact,
)

SECRET = "sk-live-0123456789"
MASK = "[REDACTED]"


def _redactor(value):
    """Stands in for `redact_sensitive_tool_args`, which is the real authority."""
    if isinstance(value, str):
        return value.replace(SECRET, MASK)
    if isinstance(value, dict):
        return {k: _redactor(v) for k, v in value.items()}
    return value


def _transcript(value: str) -> list[dict]:
    return [{"role": "tool", "content": value, "tool_call_id": "c1"}]


# ── The refused point ───────────────────────────────────────────────────


class TestTheTracePointIsRefused:
    def test_redacting_the_trace_raises(self):
        with pytest.raises(RedactionPointError):
            redact(SECRET, point=RedactionPoint.TRACE, redactor=_redactor)

    def test_the_refusal_explains_itself(self):
        with pytest.raises(RedactionPointError) as excinfo:
            redact(SECRET, point=RedactionPoint.TRACE, redactor=_redactor)
        text = str(excinfo.value)
        assert "ReplayDivergence" in text, "the failure does not name the consequence"
        assert excinfo.value.code == "redaction_point_invalid"

    def test_the_prompt_point_is_allowed(self):
        assert redact(SECRET, point=RedactionPoint.PROMPT,
                      redactor=_redactor) == MASK

    def test_both_points_are_spelled_out_so_the_refusal_is_readable(self):
        """`TRACE` exists only to be refused, and it is in the enum so the reason is
        visible where a caller would reach for it — not only in a commit message."""
        assert {p.value for p in RedactionPoint} == {"prompt", "trace"}


# ── The mutual exclusion, proven ────────────────────────────────────────


class TestTheExclusionIsMechanical:
    def test_prompt_redaction_keeps_the_record_true(self):
        """The legal shape. The model sees the masked value, the trace records the
        masked value, and replay agrees — because there is only one value."""
        seen = redact(SECRET, point=RedactionPoint.PROMPT, redactor=_redactor)
        transcript = _transcript(seen)
        recorded = projection_digest(transcript)
        verify(transcript, recorded)              # must not raise
        assert SECRET not in str(transcript)

    def test_trace_only_redaction_is_caught_by_the_replay_digest(self):
        """**The proof.** The model saw the real value; the trace holds the mask.

        Nothing about this looks wrong at the call site — it looks like a redaction
        that worked. It is caught because the transcript no longer digests to what
        the run recorded.
        """
        live = _transcript(SECRET)                # what the model actually saw
        recorded = projection_digest(live)        # what the run recorded

        tampered = _transcript(MASK)              # what a trace-side redaction stores
        with pytest.raises(ReplayDivergence):
            verify(tampered, recorded)

    def test_the_two_points_differ_exactly_when_the_record_is_involved(self):
        """The exclusion, stated as a property rather than a story: the *returned*
        value is the same either way — what differs is whether the record can still
        be trusted. So the choice cannot be made by looking at the return value."""
        legal = redact(SECRET, point=RedactionPoint.PROMPT, redactor=_redactor)
        assert legal == MASK
        with pytest.raises(RedactionPointError):
            redact(SECRET, point=RedactionPoint.TRACE, redactor=_redactor)


# ── The one place the record's value is produced ────────────────────────


class TestRecordedValue:
    def test_it_redacts_before_the_record(self):
        assert recorded_value({"token": SECRET}, redactor=_redactor) == {"token": MASK}

    def test_with_no_redactor_it_is_the_identity(self):
        """The core is generic and the policy is configuration: wiring the point
        must not require a policy."""
        assert recorded_value(SECRET) == SECRET
        assert redact(SECRET, point=RedactionPoint.PROMPT) == SECRET

    def test_it_is_the_only_legal_route_to_a_recorded_value(self):
        """A floor. If `recorded_value` were bypassed by a second inline
        `redactor(...)` call, a trace would acquire a value the model never saw —
        which is the defect, arriving by the back door."""
        import inspect

        from wisp.runtime import redaction

        source = inspect.getsource(redaction)
        assert source.count("redactor(value)") == 1, (
            "more than one place applies the redactor; the record's value must be "
            "produced in exactly one")
