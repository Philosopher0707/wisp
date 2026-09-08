"""Single-source turn-completion invariant (GH#27).

verification.py owns the canonical statement; the prompt prose quotes it
and the nudge composer derives from the same source. These pins fail on
any prose<->gate drift.
"""

from wisp.context_assembler import (
    VERIFICATION_LOOP_RULES,
    VERIFICATION_LOOP_RULES_NO_BASH,
)
from wisp.core.verification import (
    HARNESS_REJECTION,
    INVARIANT_STATEMENT,
    VerificationFloorGuard,
    compose_nudge,
)


class TestSingleSource:
    def test_prose_quotes_canonical_statement(self):
        assert VERIFICATION_LOOP_RULES.count(INVARIANT_STATEMENT) == 1
        assert VERIFICATION_LOOP_RULES_NO_BASH.count(INVARIANT_STATEMENT) == 1

    def test_statement_is_gate_faithful(self):
        # Gate blocks iff: mutated AND no post-mutation exit-0 AND floor
        # unspent. The statement must name all three conditions.
        lowered = INVARIANT_STATEMENT.lower()
        assert "changed code" in lowered or "mutat" in lowered
        assert "exit" in lowered and "0" in INVARIANT_STATEMENT
        assert "grind floor" in lowered
        assert "unverified" in lowered

    def test_nudge_derives_from_same_source(self):
        nudge = compose_nudge("no verification command has been run")
        assert nudge.startswith("[SYSTEM]")
        assert "no verification command has been run" in nudge
        assert HARNESS_REJECTION in nudge
        assert INVARIANT_STATEMENT in nudge
        assert "UNVERIFIED" in nudge

    def test_nudge_covers_failed_verification_reason(self):
        nudge = compose_nudge("the most recent verification command FAILED")
        assert "FAILED" in nudge and INVARIANT_STATEMENT in nudge

    def test_gate_and_nudge_agree(self):
        g = VerificationFloorGuard(min_turns=5, max_nudges=2)
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        assert g.rejection() == HARNESS_REJECTION  # contract unchanged
        assert HARNESS_REJECTION in compose_nudge("x")
