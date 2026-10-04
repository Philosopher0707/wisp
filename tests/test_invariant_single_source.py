"""Single-source turn-completion invariant (GH#27).

`verification.py` owns the canonical statement; **the gate enforces it and `compose_nudge`
derives from it**, so those two can never drift — that half is unchanged and still pinned here.

The **static prompt** no longer quotes the string verbatim (2026-10-01). It states the same
conditions in its own words, so the prompt can be developed without a byte-exact pin. See
`TestSingleSource.test_prose_names_all_three_gate_conditions` for what is still guaranteed and
why the guarantee moved from spelling to content.
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
    def test_prose_names_all_three_gate_conditions(self):
        """The prose is **free to be edited**; it is not free to be less true.

        This replaced an exact-string pin (`count(INVARIANT_STATEMENT) == 1`) on 2026-10-01, so
        the prompt could be developed without a byte-exact constraint. The guarantee moved from
        **spelling** to **content**.

        The gate blocks iff the turn mutated code **AND** no verification exit-0 postdates the
        mutation **AND** the grind floor is unspent — so the prose must name all three, plus the
        sanctioned verdict for when the floor is spent. That last one is the fact the verbatim
        quote used to carry and the prose did not: without it a model that genuinely cannot get a
        passing run has no legitimate answer, and its only options are to lie or to loop.
        """
        for name, text in (("VERIFICATION_LOOP_RULES", VERIFICATION_LOOP_RULES),
                           ("VERIFICATION_LOOP_RULES_NO_BASH", VERIFICATION_LOOP_RULES_NO_BASH)):
            lowered = text.lower()
            assert "code change" in lowered, (
                f"{name} no longer names the mutation condition the gate tests")
            assert "exit" in lowered and "status 0" in lowered, (
                f"{name} no longer names the exit-0 condition the gate tests")
            assert "grind floor" in lowered, (
                f"{name} no longer names the escape hatch — a model that cannot verify is left "
                "with no sanctioned answer, which is the hole the quote used to fill")
            assert "unverified" in lowered, (
                f"{name} no longer names the UNVERIFIED verdict")

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
