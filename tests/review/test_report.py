"""Rendering: the verdict first, every finding with its evidence, an honest account of what was not reviewed, JSON for CI, and an exit code a pipeline can use."""

from __future__ import annotations

import json

import pytest

from wisp.review.report import exit_code, render_json, render_markdown
from wisp.review.types import Finding, Severity
from wisp.review.engine import ReviewReport


def make(verdict="attention", findings=(), reasons=(), skipped=(), **kw):
    base = dict(source="range main...HEAD", files=["a.py", "b.py"], findings=list(findings), suppressed=0, verdict=verdict, reasons=list(reasons), skipped=list(skipped),
                checks_run=["check_secrets"], lenses_run=["security"], ungrounded=0, model_summary="", errors=[])
    base.update(kw)
    return ReviewReport(**base)


BLOCK = Finding("secret", Severity.BLOCK, "c.py", 3, "KEY = [REDACTED:x]", "credential in an added line", "gate:secrets", "rotate it")
WARN = Finding("no-test", Severity.WARN, "a.py", 10, "def f():", "no test mentions f", "diff pattern")
NOTE = Finding("manifest-changed", Severity.INFO, "package.json", 0, "", "manifest changed", "path class")


def test_the_verdict_and_counts_come_first():
    text = render_markdown(make("blocked", [BLOCK, WARN, NOTE]))
    first = text.splitlines()[0:4]
    assert "BLOCKED" in "\n".join(first) and "1 blocking" in text and "1 warning" in text and "1 note" in text


def test_findings_are_grouped_and_show_location_quote_evidence_and_suggestion():
    text = render_markdown(make("blocked", [BLOCK, WARN]))
    assert "c.py:3" in text and "KEY = [REDACTED:x]" in text and "gate:secrets" in text and "rotate it" in text
    assert text.index("Blocking") < text.index("Warnings")


def test_a_file_level_finding_has_no_line_number():
    assert "package.json" in render_markdown(make("clean", [NOTE])) and "package.json:0" not in render_markdown(make("clean", [NOTE]))


def test_what_was_not_reviewed_is_listed_with_reasons():
    text = render_markdown(make("incomplete", reasons=["security: timed out"], skipped=[("big.py", "over the review budget"), ("img.png", "binary")]))
    assert "Not fully reviewed" in text and "security: timed out" in text and "big.py" in text and "over the review budget" in text


def test_a_clean_verdict_says_clean_means_nothing_found_not_correct():
    assert "not a statement that the change is correct" in render_markdown(make("clean"))


def test_dropped_model_claims_and_the_summary_are_reported_as_advice():
    text = render_markdown(make(ungrounded=4, model_summary="looks ok"))
    assert "4 model claim(s)" in text and "advice only" in text.lower() and "looks ok" in text


def test_suppressed_findings_are_counted():
    assert "12 more finding(s) were not shown" in render_markdown(make(suppressed=12))


def test_json_has_the_stable_fields_ci_reads():
    data = json.loads(render_json(make("blocked", [BLOCK])))
    assert data["verdict"] == "blocked" and data["findings"][0]["fingerprint"] == BLOCK.fingerprint and data["findings"][0]["severity"] == "block"
    assert set(data) >= {"source", "files", "verdict", "reasons", "findings", "skipped", "checks_run", "lenses_run", "ungrounded", "suppressed"}


@pytest.mark.parametrize("verdict,fail_on,expected", [
    ("blocked", "blocked", 1), ("attention", "blocked", 0), ("clean", "blocked", 0), ("incomplete", "blocked", 0),
    ("attention", "attention", 1), ("incomplete", "attention", 1), ("clean", "attention", 0),
    ("incomplete", "incomplete", 1), ("attention", "incomplete", 0), ("blocked", "incomplete", 1),
])
def test_the_exit_code_follows_the_chosen_threshold(verdict, fail_on, expected):
    assert exit_code(make(verdict), fail_on) == expected


def test_an_unknown_threshold_is_an_error():
    with pytest.raises(ValueError):
        exit_code(make(), "sometimes")


def test_the_files_that_were_part_of_the_change_are_listed_with_a_cap():
    few = render_markdown(make(files=["a.py", "b.py"]))
    assert "Files (2): a.py, b.py" in few
    many = render_markdown(make(files=[f"f{i}.py" for i in range(30)]))
    assert "Files (30):" in many and "f0.py" in many and "f11.py" in many and "f12.py" not in many and "+18 more" in many
