"""The verdict, derived by the harness from evidence and coverage. The model's opinion is never an input.

blocked > incomplete > attention > clean. A blocker is established evidence and wins. After that, anything the review could not see makes it `incomplete`
rather than letting warnings or silence read as a pass: unknown is not clean. `clean` means "nothing found by the checks that ran", never "correct".
"""

from __future__ import annotations

from typing import Sequence

from wisp.review.types import Finding, Severity

VERDICTS = ("blocked", "incomplete", "attention", "clean")


def derive_verdict(
    findings: Sequence[Finding], *, gaps: Sequence[str], check_errors: Sequence[str], lens_errors: Sequence[str], skipped_for_budget: Sequence[str], residue: int,
    truncated_files: Sequence[str], model_requested: bool, model_available: bool,
) -> tuple[str, list[str]]:
    reasons: list[str] = list(gaps)
    reasons.extend(f"a check failed to run: {error}" for error in check_errors)
    if residue:
        reasons.append(f"{residue} line(s) of the diff could not be parsed")
    if truncated_files:
        reasons.append("hunk(s) were cut short in: " + ", ".join(truncated_files[:5]))
    if model_requested:
        if not model_available:
            reasons.append("a model review was requested but no model was available")
        reasons.extend(f"model lens failed: {error}" for error in lens_errors)
        if skipped_for_budget:
            reasons.append("not reviewed by the model (over the budget): " + ", ".join(skipped_for_budget[:5]) + (" ..." if len(skipped_for_budget) > 5 else ""))
    if any(f.severity is Severity.BLOCK for f in findings):
        return "blocked", reasons
    if reasons:
        return "incomplete", reasons
    if any(f.severity is Severity.WARN for f in findings):
        return "attention", reasons
    return "clean", reasons
