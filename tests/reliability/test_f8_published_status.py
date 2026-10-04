"""ADR-0052 — the published status of a capability failure is a system failure, not a denial.

`PHASE_F8_ERROR_CLASSIFICATION.md` §4 fixed the attribution **where the failure is produced**
(`ValidationFailure.kind == CAPABILITY_MISSING`) and reported that the published **status** was
still `SCHEMA_INVALID` — a claim about the *arguments*, made about a host that never examined them.

ADR-0052 decides the other half, and this file drives the **whole path**: the failure the engine
produces, the stamping rule the two dry-run sites apply, the envelope `_refusal_result_event`
publishes, and the class `classify_result` derives from it.

The decision is **Option B** — a system-error envelope, the published denial taxonomy unchanged —
because:

* `SCHEMA_INVALID` maps to `OutcomeClass.INVALID`, *"schema/argument rejection"*, and
  `denial_result()`'s payload asserts `authorized=False, executed=False, retryable=False` with the
  hint *"Denial is final for these arguments"*. All of that is a claim about the arguments.
* Option A (a new denial status) would add a member to a published taxonomy consumed by the M12
  classifier **and** require the prompt's DENIALS-ARE-FINAL list to name it — a behavioural change
  (what the model is told) needing the flag-and-measure treatment. Option B leaves both untouched.
* The third option — keep `SCHEMA_INVALID` and add a note to the message — is rejected: that is the
  shape F8 *was*, a system failure wearing a data failure's name.

Non-vacuity: every assertion in `TestTheCapabilityRefusalIsPublishedAsASystemFailure` fails against
the unmodified tree (the envelope was a `SCHEMA_INVALID` denial).
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from wisp.core.events import (
    CAPABILITY_FAILURE_STATUS,
    CAPABILITY_KIND_KEY,
    _DENIAL_STATUSES,
    OutcomeClass,
    classify_result,
)
from wisp.core.stateless import (
    VALIDATION_CAPABILITY_MISSING,
    VALIDATION_SCHEMA_INVALID,
    WispAgentCore,
    _is_capability_failure,
)
from wisp.infra.security import SecurityPolicy

REPO = pathlib.Path(__file__).resolve().parents[2]

#: The denial statuses as they stood before this ADR. Option B must not change this set.
DENIAL_STATUSES_BEFORE = frozenset({
    "POLICY_DENIED", "USER_DENIED", "APPROVAL_TIMEOUT", "CANCELLED", "SCHEMA_INVALID",
})

#: Added since, each by a recorded decision (ADR-0074), never by drift. A new denial status is a
#: published-taxonomy change: it needs an entry here, an ADR naming it, and a terminal class.
DENIAL_STATUSES_ADDED_SINCE = frozenset({"NO_APPROVER", "BUDGET_EXCEEDED"})


@pytest.fixture
def validator_absent():
    """Hide `jsonschema` from `sys.modules` — the F8-provisioning technique."""
    real = sys.modules.get("jsonschema")
    sys.modules["jsonschema"] = None
    try:
        yield
    finally:
        if real is None:
            sys.modules.pop("jsonschema", None)
        else:
            sys.modules["jsonschema"] = real


def _core() -> WispAgentCore:
    return WispAgentCore(config=None, provider=None,
                         security=SecurityPolicy(), tool_executor=None)


def _publish(core: WispAgentCore, failure) -> dict:
    """Apply the **real** stamping rule the two dry-run sites use, then publish.

    Deliberately NOT parameterised on "is this a capability failure": an earlier
    version of this helper took that as an argument and set `_capability` itself, so
    the predicate was never exercised and the non-vacuity probe that broke
    `_is_capability_failure` did not falsify. The rule is applied here exactly as the
    sites apply it.
    """
    tc = {"id": "call-1", "name": "read_file", "arguments": {"path": "a.txt"},
          "_blocked": failure}
    if _is_capability_failure(failure):
        tc["_capability"] = True
    else:
        tc["_denial"] = "SCHEMA_INVALID"
    return core._refusal_result_event(tc, ".")


def _payload(event: dict) -> dict:
    """The tool-result envelope, as the transcript carries it (`event["result"]`)."""
    for key in ("result", "data"):
        value = event.get(key)
        if isinstance(value, dict):
            return value
    return {}


# ── 1. The capability failure is published as a system failure ──────────────


class TestTheCapabilityRefusalIsPublishedAsASystemFailure:
    """Every test here fails against the unmodified tree."""

    def test_the_envelope_status_is_error(self, validator_absent):
        core = _core()
        failure = core._validate_tool_args("read_file", {"path": "a.txt"}, _dry_run=True)
        assert failure is not None and failure.kind == VALIDATION_CAPABILITY_MISSING
        data = _payload(_publish(core, failure))
        assert data.get("status") == CAPABILITY_FAILURE_STATUS == "error", (
            f"the capability refusal is published as {data.get('status')!r} — a denial, "
            f"i.e. a claim about the arguments")

    def test_it_carries_no_denial_status(self, validator_absent):
        core = _core()
        failure = core._validate_tool_args("read_file", {"path": "a.txt"}, _dry_run=True)
        data = _payload(_publish(core, failure))
        assert data.get("status") not in _DENIAL_STATUSES, (
            "the envelope still carries a denial status")

    def test_the_kind_travels_in_the_envelope(self, validator_absent):
        """The brief's requirement: a caller at the published boundary can tell a
        system failure from a data failure without parsing prose."""
        core = _core()
        failure = core._validate_tool_args("read_file", {"path": "a.txt"}, _dry_run=True)
        data = _payload(_publish(core, failure))
        assert data.get(CAPABILITY_KIND_KEY) == VALIDATION_CAPABILITY_MISSING
        assert data.get("capability") == "tool_argument_validation"

    def test_authorized_is_none_not_false(self, validator_absent):
        """No authorization decision was reached. Recording `False` would be a claim
        nothing made — and `denial_result()` records exactly that."""
        core = _core()
        failure = core._validate_tool_args("read_file", {"path": "a.txt"}, _dry_run=True)
        data = _payload(_publish(core, failure))
        assert data.get("authorized") is None, (
            f"authorized={data.get('authorized')!r} claims a decision that was never made")
        assert data.get("executed") is False

    def test_the_classifier_reads_it_as_a_system_failure_not_an_argument_verdict(
            self, validator_absent):
        """The end of the path: the canonical classifier."""
        core = _core()
        failure = core._validate_tool_args("read_file", {"path": "a.txt"}, _dry_run=True)
        event = _publish(core, failure)
        assert classify_result(_payload(event)) is OutcomeClass.ERROR, (
            "the published class is still INVALID — the attribution is still wrong at "
            "the boundary ADR-0052 exists to fix")

    def test_it_does_not_invite_a_retry(self, validator_absent):
        core = _core()
        failure = core._validate_tool_args("read_file", {"path": "a.txt"}, _dry_run=True)
        data = _payload(_publish(core, failure))
        assert data.get("retryable") is False
        assert "identical" in str(data.get("hint", "")).lower()


# ── 2. The genuine schema rejection is unchanged ───────────────────────────


class TestTheGenuineSchemaRejectionIsUnchanged:
    """Option B must not touch the data failure — the one case where the denial
    envelope is exactly right."""

    def test_a_real_rejection_is_still_a_schema_denial(self):
        core = _core()
        failure = core._validate_tool_args("read_file", {})
        assert failure is not None and failure.kind == VALIDATION_SCHEMA_INVALID
        assert not _is_capability_failure(failure)
        data = _payload(_publish(core, failure))
        assert data.get("status") == "SCHEMA_INVALID"
        assert data.get("status") in _DENIAL_STATUSES
        assert classify_result(_payload(_publish(core, failure))) \
            is OutcomeClass.INVALID

    def test_a_real_rejection_is_never_published_as_a_system_failure(self):
        core = _core()
        failure = core._validate_tool_args("read_file", {})
        data = _payload(_publish(core, failure))
        assert data.get(CAPABILITY_KIND_KEY) != VALIDATION_CAPABILITY_MISSING

    def test_the_predicate_defaults_to_a_data_failure(self):
        """A plain `str` — an older caller, or a future one that forgets — must not be
        read as a capability failure. The safe default keeps today's envelope."""
        assert _is_capability_failure("Schema validation failed") is False
        assert _is_capability_failure(None) is False


# ── 3. The published taxonomy and the prompt are unchanged ─────────────────


class TestThePublishedTaxonomyIsUnchanged:
    """The reason Option B was chosen over Option A."""

    def test_no_new_denial_status(self):
        assert _DENIAL_STATUSES == DENIAL_STATUSES_BEFORE | DENIAL_STATUSES_ADDED_SINCE, (
            "the published denial taxonomy changed without a recorded decision — ADR-0052 chose the "
            "envelope route precisely to avoid silent changes; add the status to "
            "DENIAL_STATUSES_ADDED_SINCE and write the ADR (ADR-0074 is the model)")

    def test_every_addition_is_recorded_in_an_adr_that_names_it(self):
        text = (REPO / "WISP_ARCHITECTURE_DECISIONS.md").read_text()
        adr = text[text.index("## ADR-0074"):]
        adr = adr[:adr.index("\n## ADR-", 10)] if "\n## ADR-" in adr[10:] else adr
        for status in DENIAL_STATUSES_ADDED_SINCE:
            assert status in adr, f"ADR-0074 does not name {status}"

    @pytest.mark.parametrize("status", sorted(DENIAL_STATUSES_BEFORE | DENIAL_STATUSES_ADDED_SINCE))
    def test_every_denial_status_is_classified_and_final(self, status):
        """`UNKNOWN` is not terminal, so an unclassified denial could be auto-retried (it was: NO_APPROVER and
        BUDGET_EXCEEDED classified as UNKNOWN until ADR-0074). The map says every status `denial_result()`
        can emit appears in it."""
        from wisp.core.events import OUTCOME_BY_STATUS, TERMINAL_OUTCOME_CLASSES, denial_result

        assert status in OUTCOME_BY_STATUS, f"{status} is not in OUTCOME_BY_STATUS"
        assert OUTCOME_BY_STATUS[status] in TERMINAL_OUTCOME_CLASSES
        envelope = denial_result("write_file", status, "x", tool_call_id="c")
        payload = envelope.data["result"] if hasattr(envelope, "data") else envelope["result"]
        assert classify_result(payload) in TERMINAL_OUTCOME_CLASSES

    def test_the_capability_status_is_not_a_denial(self):
        assert CAPABILITY_FAILURE_STATUS not in _DENIAL_STATUSES

    def test_denial_result_still_refuses_an_unknown_status(self):
        """The guard that makes Option A a real decision: `denial_result` raises for a
        status outside the taxonomy, so Option A could not be taken by accident."""
        from wisp.core.events import denial_result

        with pytest.raises(ValueError):
            denial_result("read_file", VALIDATION_CAPABILITY_MISSING, "x")

    def test_the_prompt_denial_list_is_unchanged(self):
        """ADR-0052 is not a prompt change: the model is told exactly what it was told
        before, because no denial status was added."""
        prompt = (REPO / "wisp/context_assembler.py").read_text()
        line = next(ln for ln in prompt.splitlines() if "DENIALS ARE FINAL" in ln)
        for status in DENIAL_STATUSES_BEFORE:
            assert status in line, f"{status} left the DENIALS-ARE-FINAL list"
        assert VALIDATION_CAPABILITY_MISSING not in line, (
            "a capability kind entered the prompt's denial list — that IS a behavioural "
            "change (what the model is told) and needs the flag-and-measure treatment")


# ── 4. The stamping rule is shared, and cannot drift ───────────────────────


class TestTheStampingRuleIsShared:
    """Both dry-run sites must apply the same rule. A site that stamped `_denial`
    unconditionally would publish a capability failure as a denial again."""

    def test_both_dry_run_sites_use_the_predicate(self):
        """c285e87 merged the batched and the lone-call paths into one
        `_gate_tool_call`, so the rule now has ONE site that both paths call."""
        src = (REPO / "wisp/core/stateless.py").read_text()
        assert src.count("await self._gate_tool_call(") == 2, (
            "the batched and the lone-call paths no longer share the gate")
        assert src.count("_is_capability_failure(schema_error)") == 1, (
            "the dry-run stamping site no longer applies the predicate — a site "
            "that always stamps `_denial` re-publishes a host failure as an "
            "argument verdict")
        stamp = 'tc_event["_denial"] = "SCHEMA_INVALID"'
        assert src.count(stamp) == 1, "a second SCHEMA_INVALID stamp appeared"
        predicate_at = src.index("_is_capability_failure(schema_error)")
        assert predicate_at < src.index(stamp) < predicate_at + 200, (
            "the SCHEMA_INVALID stamp is no longer the predicate's else-branch")

    def test_the_refusal_helper_reads_the_capability_flag(self):
        src = (REPO / "wisp/core/stateless.py").read_text()
        assert 'tc.get("_capability")' in src
        assert "capability_failure_result" in src
