"""Rendering a review: the verdict first, every finding with its evidence, an honest account of what was not reviewed, JSON for CI and an exit code."""

from __future__ import annotations

import json
from dataclasses import asdict

from wisp.review.engine import ReviewReport
from wisp.review.types import Finding, Severity

MAX_FILES_LISTED = 12
_FAIL_SETS = {"blocked": {"blocked"}, "incomplete": {"blocked", "incomplete"}, "attention": {"blocked", "incomplete", "attention"}}


def exit_code(report: ReviewReport, fail_on: str) -> int:
    if fail_on not in _FAIL_SETS:
        raise ValueError(f"fail_on must be one of {', '.join(_FAIL_SETS)}")
    return 1 if report.verdict in _FAIL_SETS[fail_on] else 0


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _entry(finding: Finding) -> list[str]:
    where = f"{finding.file}:{finding.line}" if finding.line else finding.file or "(whole change)"
    lines = [f"- `{where}` **{finding.rule}**: {finding.message}"]
    if finding.quote:
        lines.append(f"  > {finding.quote}")
    detail = f"  _{finding.evidence}_" + (f" · {finding.suggestion}" if finding.suggestion else "")
    lines.append(detail)
    return lines


def render_markdown(report: ReviewReport) -> str:
    blocking = [f for f in report.findings if f.severity is Severity.BLOCK]
    warnings = [f for f in report.findings if f.severity is Severity.WARN]
    notes = [f for f in report.findings if f.severity is Severity.INFO]
    out = [
        f"# Review: {report.source}",
        "",
        f"**Verdict: {report.verdict.upper()}** — {len(blocking)} blocking, {_plural(len(warnings), 'warning')}, {_plural(len(notes), 'note')} · {_plural(len(report.files), 'file')}",
        "",
    ]
    for title, group in (("Blocking", blocking), ("Warnings", warnings), ("Notes", notes)):
        if group:
            out.append(f"## {title}")
            for finding in group:
                out.extend(_entry(finding))
            out.append("")
    if report.suppressed:
        out.append(f"{report.suppressed} more finding(s) were not shown (the cap keeps every blocker and the highest-severity rest).")
        out.append("")
    if report.reasons:
        out.append("## Not fully reviewed")
        out.extend(f"- {reason}" for reason in report.reasons)
        out.append("")
    if report.verdict == "clean":
        out.append("Clean means the checks that ran found nothing. It is not a statement that the change is correct.")
        out.append("")
    out.append("## Coverage")
    shown = report.files[:MAX_FILES_LISTED]
    out.append(f"- Files ({len(report.files)}): {', '.join(shown)}" + (f" (+{len(report.files) - len(shown)} more)" if len(report.files) > len(shown) else ""))
    out.append(f"- Deterministic checks: {', '.join(report.checks_run)}")
    out.append(f"- Model lenses: {', '.join(report.lenses_run) if report.lenses_run else 'none'}")
    if report.ungrounded:
        out.append(f"- {report.ungrounded} model claim(s) could not be tied to the diff and were dropped.")
    if report.skipped:
        out.append("- Not reviewed by the model: " + "; ".join(f"{path} ({why})" for path, why in report.skipped))
    if report.model_summary:
        out.extend(["", "## Model's summary (advice only; it does not affect the verdict)", report.model_summary])
    return "\n".join(out).rstrip() + "\n"


def render_json(report: ReviewReport) -> str:
    data = asdict(report)
    data["findings"] = [{**asdict(f), "severity": f.severity.value, "fingerprint": f.fingerprint} for f in report.findings]
    data["skipped"] = [{"path": path, "reason": why} for path, why in report.skipped]
    return json.dumps(data, indent=2, ensure_ascii=False)
