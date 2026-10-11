"""The verdict is the harness's: blocked > incomplete > attention > clean. Unknown is never clean."""

from __future__ import annotations

import pytest

from wisp.review.types import Finding, Severity
from wisp.review.verdict import derive_verdict


def finding(severity):
    return Finding("r", severity, "a.py", 1, "q", "m", "e")


def verdict(findings=(), **kw):
    base = dict(gaps=[], check_errors=[], lens_errors=[], skipped_for_budget=[], residue=0, truncated_files=[], model_requested=False, model_available=True)
    base.update(kw)
    return derive_verdict(list(findings), **base)


def test_nothing_found_and_nothing_unseen_is_clean():
    assert verdict() == ("clean", [])


def test_notes_alone_are_clean():
    assert verdict([finding(Severity.INFO)])[0] == "clean"


def test_a_warning_is_attention():
    assert verdict([finding(Severity.WARN)])[0] == "attention"


def test_a_blocker_blocks_and_outranks_everything_else():
    state, reasons = verdict([finding(Severity.BLOCK), finding(Severity.WARN)], gaps=["x"])
    assert state == "blocked" and any("x" in r for r in reasons)


@pytest.mark.parametrize("kw,needle", [
    ({"gaps": ["syntax: 2 file(s) could not be read"]}, "syntax"),
    ({"check_errors": ["check_x: RuntimeError: boom"]}, "check_x"),
    ({"residue": 3}, "3 line"),
    ({"truncated_files": ["a.py"]}, "a.py"),
    ({"model_requested": True, "lens_errors": ["security: timed out"]}, "security"),
    ({"model_requested": True, "skipped_for_budget": ["big.py"]}, "big.py"),
    ({"model_requested": True, "model_available": False}, "no model"),
])
def test_each_thing_the_review_could_not_see_makes_it_incomplete(kw, needle):
    state, reasons = verdict(**kw)
    assert state == "incomplete" and any(needle in r for r in reasons)


def test_incomplete_outranks_a_warning():
    assert verdict([finding(Severity.WARN)], gaps=["g"])[0] == "incomplete"


def test_the_model_not_being_asked_means_its_coverage_is_not_a_gap():
    assert verdict(model_requested=False, skipped_for_budget=["big.py"], lens_errors=["x"], model_available=False)[0] == "clean"
