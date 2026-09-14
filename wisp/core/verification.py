"""Verification floor & grind budget (harness > model on completion calls).

Models surrender early: one syntax error and they summarize, or they claim
success without ever running the suite. This guard owns the completion
invariant so ``_turn_inner`` doesn't re-derive it inline:

  - a turn that mutated code may NOT finish until a verification command
    exited 0 *after* the last mutation, OR the grind floor is exhausted;
  - each interception consumes one nudge (bounded — a turn can always end);
  - a verified finish marks the turn RESOLVED (auto-skill capture trigger).

Pure state machine — no provider, tool, or transport imports — so the
whole policy is unit-testable without a core.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

HARNESS_REJECTION = (
    "[HARNESS REJECTION] You have not executed the test suite to verify "
    "your changes. You must run the tests and inspect the output before "
    "finishing."
)

# Single source of the turn-completion invariant (GH#27). The gate below
# enforces it, the prompt prose quotes it, and compose_nudge() derives
# the intervention text from it — change the policy here and all three
# expressions follow. Wording is gate-faithful: the guard blocks iff the
# turn mutated code AND no verification exit-0 postdates the mutation AND
# the grind floor is unspent.
INVARIANT_STATEMENT = (
    "Harness rule: a turn that changed code may finish only after a "
    "verification command exits 0 on the current code — or after the "
    "grind floor is spent, in which case report the work UNVERIFIED, "
    "never success."
)


def compose_nudge(reason: str) -> str:
    """Build the harness intervention message for a premature finish.

    *reason* is the run-context detail (no verification run yet vs the
    latest run failed); the invariant, the rejection, and the recovery
    instruction all come from this module so prose can never drift from
    the gate.
    """
    return (
        "[SYSTEM] Verification loop: you changed code this turn, "
        f"but {reason}. {HARNESS_REJECTION} {INVARIANT_STATEMENT} "
        "Run the project's test/lint command, fix any failures, and only "
        "then summarize. If you genuinely cannot verify, say the work is "
        "UNVERIFIED instead of claiming success."
    )


# Short pointer for an identical repeat block (no new tool outcome since
# the last nudge). Deliberately NOT the full compose_nudge text: re-appending
# the invariant + rejection verbatim doubles context for zero new signal.
# Keeps the "Verification loop" marker so existing nudge filters still count
# it as an intervention.
SHORT_REPEAT_NUDGE = (
    "[SYSTEM] Verification loop (repeat): same block as above — "
    "run the tests/linter and only then summarize. If you genuinely "
    "cannot verify, say the work is UNVERIFIED instead of claiming success."
)

# Tool names that mutate code (legacy 42-tool surface + thin primitives).
_MUTATING_TOOLS = frozenset({"write_file", "edit_file", "edit_file_multi", "fs_mutate"})
# Tool names whose exit status counts as verification evidence.
_VERIFY_TOOLS = frozenset({"run_bash", "exec_sandbox"})

# run_tests summary shape (test_runner.format_for_llm). A vacuous
# 0/0 run is NOT evidence — otherwise auto_edit turns (run_bash blocked)
# could "verify" without executing a single test.
_TEST_RESULTS_RE = re.compile(r"## Test Results \((\d+)/(\d+) passed\)")


def _run_tests_is_evidence(result_text: str) -> bool:
    """True when result_text reports a real green suite (≥1 collected, 0 failed)."""
    m = _TEST_RESULTS_RE.search(result_text or "")
    if not m or int(m.group(2)) < 1:
        return False
    return "- Failed: 0, Errors: 0" in result_text


@dataclass
class VerificationFloorGuard:
    """Per-turn completion gate. One instance per turn, single-threaded use."""

    min_turns: int = 5
    max_nudges: int = 2
    enabled: bool = True
    wrote_code: bool = False
    verify_ok_after_edit: bool | None = None
    nudges_used: int = 0
    turns_used: int = 0
    # (tool, args-digest) trail for auto-skill capture on RESOLVED.
    steps: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    # Dedupe key of the last blocked state + consecutive identical repeats.
    # A repeat means zero new tool outcomes since the last nudge, so the
    # full text would be a verbatim duplicate.
    last_block_key: tuple[bool, bool | None, int] | None = None
    repeat_count: int = 0

    def note_tool_result(self, name: str, result_text: str,
                         args: dict[str, str] | None = None) -> None:
        """Fold one tool outcome into the gate. Call for every tool_result."""
        self.turns_used += 1
        if args is not None:
            self.steps.append((name, dict(args)))
        if name in _MUTATING_TOOLS:
            # fs_mutate reads must not count as mutations.
            if name == "fs_mutate" and (args or {}).get("op") == "read":
                return
            self.wrote_code = True
            self.verify_ok_after_edit = None  # prior evidence is stale now
        elif name in _VERIFY_TOOLS:
            self.verify_ok_after_edit = not result_text.startswith("[exit code:")
        elif name == "run_tests":
            if _run_tests_is_evidence(result_text):
                self.verify_ok_after_edit = True
            # A red or vacuous suite leaves prior evidence untouched:
            # absence of proof is not proof of breakage (a later green run
            # still counts), but it never manufactures verification.

    def rejection(self) -> str | None:
        """HARNESS REJECTION text when finishing now is premature, else None.

        A mutated turn is blocked until verification succeeds. The guard gives
        the model a bounded number of self-correction nudges; once that budget is
        spent, it allows the turn to finish honestly rather than looping forever.

        Identical repeats (no new tool outcome since the last nudge) return
        SHORT_REPEAT_NUDGE without consuming budget the first time, then None
        to break the spin — a verbatim full nudge twice carries zero new signal.
        """
        if not self.enabled or not self.wrote_code:
            return None
        if self.verify_ok_after_edit is True:
            self.last_block_key = None
            self.repeat_count = 0
            return None
        if self.turns_used >= self.min_turns and self.nudges_used >= self.max_nudges:
            logger.info("Verification floor exhausted (%d turns, %d nudges) — "
                        "allowing unverified finish", self.turns_used, self.nudges_used)
            return None
        key = (self.wrote_code, self.verify_ok_after_edit, self.turns_used)
        if key == self.last_block_key:
            if self.repeat_count >= 1:
                logger.info("Verification repeat with no new evidence — "
                            "allowing unverified finish")
                return None
            self.repeat_count += 1
            return SHORT_REPEAT_NUDGE
        self.nudges_used += 1
        self.last_block_key = key
        self.repeat_count = 0
        return HARNESS_REJECTION

    def resolved(self) -> bool:
        """True when the turn earned its completion (verified, not surrendered)."""
        return bool(self.enabled and self.wrote_code
                    and self.verify_ok_after_edit is True)

    def reset_turn(self) -> None:
        """Clear per-turn state (the guard instance may be reused)."""
        self.wrote_code = False
        self.verify_ok_after_edit = None
        self.nudges_used = 0
        self.turns_used = 0
        self.steps.clear()
        self.last_block_key = None
        self.repeat_count = 0
