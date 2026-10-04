"""M12 — the failure path reaches the recovery taxonomy, and refusals are seen.

P6 built a closed 10-class taxonomy and a 7-rung ladder, and recorded the gap as
M12 with ADR-0026:

> *"the turn loop does not consult the ladder. Rewiring it alters the **failure**
> path — the least-covered path — and the plan names the risk as 'ordering and
> budget interaction', exactly what a live rewiring disturbs."*

Reconnaissance found that the gap is not where the ADR put it. The ladder cannot
be consulted because **nothing bridges the runtime's failure signals to the
taxonomy**:

| Side | Has |
|---|---|
| the runtime | `(message, recoverable, code)` on an error event |
| the taxonomy | a `result` (→ `classify_result`) **or** semantic flags |

Neither of those is the other, so there is no adapter — and driving the taxonomy
from real failures for the first time found two defects:

**1. The engine's own refusals are invisible to the denial predicate.** The
engine emits its pre-dispatch refusals as `Blocked: …` error events.
`is_denial_text` checks the five canonical statuses and four prose markers —
`('denied', 'denied by', 'approval denied', 'not authorized')` — and **none of
them matches `Blocked:`**. So the orchestrator's retry loop, which says

    # Don't retry authorization denials or cancellations (§17).
    if self._is_denial(last_error) or "cancell" in last_error.lower():

does exactly that: it retries them, up to `max_retries`, on a call that will be
refused identically. The ladder forbids retrying a `SECURITY` failure; the
orchestrator's own loop does it because it cannot see the refusal.

This is F15's shape again, from the other side. F15: the prose markers matched
nothing real, so structured denials were invisible. Now: the statuses are
checked, and the **engine's** marker is missing.

**2. `OutcomeClass.TIMEOUT` does not mean a turn timeout.** It is reachable only
from `APPROVAL_TIMEOUT` — a *denial* status — and `classify_failure` maps it to
`SECURITY` for that reason. The name invites a caller to route the engine's turn
timeout (`CODE_TURN_TIMEOUT`) through it and get a security failure that forbids
retry. `test_a_turn_timeout_is_not_a_security_failure` pins the trap.

`test_an_engine_refusal_is_a_denial` is the RED-first test.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from wisp.core.events import CODE_ITERATION_BUDGET, CODE_PROVIDER_STREAM, \
    CODE_TURN_TIMEOUT, is_denial_text
from wisp.core.recovery import FailureClass, RecoveryLadder, RecoveryRung

REPO = Path(__file__).resolve().parents[1]

#: The refusals the engine actually emits, taken from `stateless.py`.
ENGINE_REFUSALS = (
    "Blocked: tool 'write_file' is not allowed for this agent's role",
    "Blocked: Schema validation failed for tool 'read_file'",
    "Blocked: workspace is quarantined",
    "Blocked: hook denied the write",
    "Blocked: extension declined",
    "Extension intercept failed: boom. Tool call denied.",
)

#: The failures the engine emits that are NOT refusals.
ENGINE_OTHER_FAILURES = (
    ("Turn timed out after 600s", False, CODE_TURN_TIMEOUT),
    ("Provider stream failed: Connection reset", False, CODE_PROVIDER_STREAM),
    ("Stream error: timeout", True, None),
    ("Incomplete provider round (no tool calls) — stop; no final answer was produced",
     False, None),
    ("Protocol integrity failure, ending turn: bad block", False, None),
    ("Max iterations reached", False, CODE_ITERATION_BUDGET),
    ("Provider temporarily unavailable (circuit open, retry in 12s).", True, None),
)


def _adapter():
    from wisp.core.recovery import classify_failure_signal
    return classify_failure_signal


# ══════════════════════════════════════════════════════════════════════════
# 1. The engine's refusals must be visible as denials
# ══════════════════════════════════════════════════════════════════════════


class TestTheEngineRefusalIsADenial:
    def test_an_engine_refusal_is_a_denial(self):
        """**The RED-first test.** `Blocked: …` is the engine refusing a tool
        call before dispatch. It is a denial by construction and the canonical
        predicate does not see it."""
        assert is_denial_text(
            "Blocked: tool 'write_file' is not allowed for this agent's role")

    @pytest.mark.parametrize("refusal", ENGINE_REFUSALS)
    def test_every_engine_refusal_is_a_denial(self, refusal):
        assert is_denial_text(refusal), refusal

    def test_the_canonical_statuses_still_match(self):
        """The P10 fix must not be disturbed."""
        for status in ("POLICY_DENIED", "USER_DENIED", "SCHEMA_INVALID",
                       "APPROVAL_TIMEOUT", "CANCELLED"):
            assert is_denial_text(status), status
            assert is_denial_text(f"[{status}] refused"), status

    def test_the_legacy_prose_markers_still_match(self):
        for prose in ("[denied by policy]", "approval denied", "not authorized"):
            assert is_denial_text(prose), prose

    def test_a_non_denial_is_still_not_a_denial(self):
        """A transient failure must not be read as a refusal, or the retry path
        stops working."""
        for other in ("Stream error: timeout",
                      "Provider temporarily unavailable (circuit open).",
                      "Max iterations reached"):
            assert not is_denial_text(other), other

    def test_the_marker_is_a_prefix_not_a_substring(self):
        """A sentence that merely *mentions* blocking is not a refusal. The
        engine always emits the marker as a prefix."""
        assert not is_denial_text("the write was not blocked: it succeeded")
        assert not is_denial_text("nothing was blocked: all tools ran")

    def test_every_refusal_prefix_the_engine_emits_is_recognised(self):
        """**The ratchet.** Read the `f"…"` prefixes out of `stateless.py`'s
        error events and require each to be classified as a denial or as a
        known non-refusal — so a new refusal shape cannot arrive invisible.
        """
        tree = ast.parse((REPO / "wisp" / "core" / "stateless.py")
                         .read_text(encoding="utf-8"))
        prefixes: set[str] = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "error_event"
                    and node.args
                    and isinstance(node.args[0], ast.JoinedStr)
                    and node.args[0].values
                    and isinstance(node.args[0].values[0], ast.Constant)):
                literal = node.args[0].values[0].value
                if literal:
                    prefixes.add(literal.strip().lower())

        assert prefixes, "no error-event prefixes found — the AST probe is stale"
        unknown = {p for p in prefixes
                   if not is_denial_text(p)
                   and p not in _KNOWN_NON_REFUSALS}
        assert not unknown, (
            f"engine failure prefixes that are neither recognised refusals nor "
            f"known non-refusals: {sorted(unknown)}. Decide which it is in "
            "`_ENGINE_DENIAL_PREFIXES` or `_KNOWN_NON_REFUSALS`.")


#: Engine failure prefixes that are deliberately NOT refusals. Kept here so the
#: ratchet above forces a decision rather than a silent default.
#:
#: The AST yields the literal text up to the first interpolation, so these are
#: the exact strings the probe produces — `"provider temporarily unavailable
#: (circuit open, retry in"` rather than the whole message. That is deliberate:
#: matching the exact extracted form means a *new* message shape does not
#: silently inherit an existing entry's decision.
_KNOWN_NON_REFUSALS = {
    "turn timed out after",
    "provider stream failed:",
    "stream error:",
    "incomplete provider round (",
    "protocol integrity failure, ending turn:",
    "provider temporarily unavailable (circuit open, retry in",
}


# ══════════════════════════════════════════════════════════════════════════
# 2. The consequence: the retry loop stops retrying refusals
# ══════════════════════════════════════════════════════════════════════════


class TestTheRetryLoopStopsRetryingRefusals:
    def test_the_orchestrator_checks_denial_before_retrying(self):
        """AST: the retry loop consults `_is_denial` — so once the predicate can
        see the engine's refusals, the loop stops retrying them."""
        tree = ast.parse((REPO / "wisp" / "multi_agent" / "subagent_orchestrator.py")
                         .read_text(encoding="utf-8"))
        found = [n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute) and n.attr == "_is_denial"]
        assert found, "the retry loop no longer consults _is_denial"

    def test_the_orchestrator_still_delegates_to_the_authority(self):
        """P10 removed a prose guard that matched nothing; the replacement must
        stay a delegation, not become a second matcher."""
        src = (REPO / "wisp" / "multi_agent" / "subagent_orchestrator.py"
               ).read_text(encoding="utf-8")
        assert "is_denial_text" in src, "the orchestrator no longer delegates"

    def test_an_engine_refusal_is_now_recognised_where_the_loop_asks(self):
        """The behavioural statement of the fix: the exact expression the retry
        loop evaluates now answers `True` for an engine refusal."""
        for refusal in ENGINE_REFUSALS:
            assert is_denial_text(refusal), (
                f"the retry loop would retry this refusal: {refusal!r}")


# ══════════════════════════════════════════════════════════════════════════
# 3. The adapter: the runtime's signals → the taxonomy
# ══════════════════════════════════════════════════════════════════════════


class TestTheAdapter:
    def test_the_adapter_exists_and_takes_the_runtime_signals(self):
        import inspect
        sig = inspect.signature(_adapter())
        assert "message" in sig.parameters
        assert "recoverable" in sig.parameters
        assert "code" in sig.parameters

    @pytest.mark.parametrize("refusal", ENGINE_REFUSALS)
    def test_an_engine_refusal_is_a_security_failure(self, refusal):
        assert _adapter()(refusal) is FailureClass.SECURITY

    def test_a_turn_timeout_is_not_a_security_failure(self):
        """**The naming trap.** `OutcomeClass.TIMEOUT` is reachable only from
        `APPROVAL_TIMEOUT`, a *denial* status, which is why the taxonomy maps it
        to `SECURITY`. Routing the engine's turn timeout through that name would
        make a slow model a security failure and forbid retrying it."""
        cls = _adapter()("Turn timed out after 600s", False, CODE_TURN_TIMEOUT)
        assert cls is not FailureClass.SECURITY, (
            "a turn timeout must not be classified as a denial")

    def test_a_provider_stream_failure_is_not_a_security_failure(self):
        cls = _adapter()("Provider stream failed: reset", False,
                         CODE_PROVIDER_STREAM)
        assert cls is not FailureClass.SECURITY

    def test_a_transient_signal_is_transient(self):
        cls = _adapter()("Provider temporarily unavailable (circuit open).", True,
                         None)
        assert cls is FailureClass.TRANSIENT

    def test_a_cancellation_is_a_security_failure(self):
        """A cancelled call is an authorization outcome — the recovery answer is
        'do not retry', the same as a denial."""
        assert _adapter()("Turn aborted: cancelled by user", False,
                          None) is FailureClass.SECURITY

    def test_the_adapter_is_total(self):
        """Every real failure maps to a class — no `None`, no raise."""
        for msg, rec, code in ENGINE_OTHER_FAILURES:
            cls = _adapter()(msg, rec, code)
            assert isinstance(cls, FailureClass), (msg, cls)

    def test_the_adapter_defaults_to_implementation(self):
        """An unrecognised failure is the agent's own, not a denial and not
        transient — the conservative default."""
        assert _adapter()("something entirely new went wrong", False,
                          None) is FailureClass.IMPLEMENTATION

    def test_every_error_code_has_a_classification(self):
        """**The ratchet.** Every `CODE_*` the engine can emit must have an
        explicit class, so a new code cannot silently take the default."""
        from wisp.core import events as E
        from wisp.core.recovery import CODE_FAILURE_CLASS

        codes = {v for k, v in vars(E).items()
                 if k.startswith("CODE_") and isinstance(v, str)
                 and re.fullmatch(r"E\d+", v)}
        assert codes, "no error codes found — the probe is stale"
        missing = sorted(codes - set(CODE_FAILURE_CLASS))
        assert not missing, (
            f"error codes with no classification: {missing}. Add them to "
            "CODE_FAILURE_CLASS — an unclassified code takes the default "
            "silently.")

    def test_the_code_table_and_the_adapter_agree(self):
        from wisp.core.recovery import CODE_FAILURE_CLASS
        for code, cls in CODE_FAILURE_CLASS.items():
            assert isinstance(cls, FailureClass), (code, cls)
        # the adapter must route a code through the table, not around it
        assert _adapter()("anything", False,
                          CODE_TURN_TIMEOUT) is CODE_FAILURE_CLASS[CODE_TURN_TIMEOUT]


# ══════════════════════════════════════════════════════════════════════════
# 4. The ladder decides, and the decision is recorded — not enforced
# ══════════════════════════════════════════════════════════════════════════


class TestTheLadderDecisionIsRecorded:
    def test_a_refusal_escalates_rather_than_retrying(self):
        cls = _adapter()("Blocked: workspace is quarantined", True, None)
        ladder = RecoveryLadder()
        decision = ladder.decide(cls, ["engine refused the tool call"])
        assert decision.rung is not RecoveryRung.RETRY
        assert decision.rung is RecoveryRung.HUMAN, (
            "a refusal has no legal rung but escalation")

    def test_a_transient_failure_retries(self):
        cls = _adapter()("Provider temporarily unavailable.", True, None)
        decision = RecoveryLadder().decide(cls, ["circuit open"])
        assert decision.rung is RecoveryRung.RETRY

    def test_the_decision_cites_evidence(self):
        cls = _adapter()("Blocked: quarantined", True, None)
        decision = RecoveryLadder().decide(cls, ["the gate refused it"])
        assert decision.evidence
        assert decision.failure_class is cls
