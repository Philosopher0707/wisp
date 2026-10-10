"""The review engine: deterministic checks, repo rules, optional model lenses with grounding, then a verdict the harness derives.

Pure orchestration. The model is an injected async callable (prompt in, text out); the diff comes from `source.py`. A lens that fails, times out or returns
something unusable is recorded and makes the verdict `incomplete`; it never reads as "no findings".
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping

from wisp.review.checks import ALL_CHECKS, CheckContext, check_affected_tests, run_checks
from wisp.review.diff import FileDiff
from wisp.review.grounding import Claim, clean_text, ground, merge_duplicates
from wisp.review.lens import LENSES, MAX_CHUNK_CHARS, MAX_TOTAL_CHARS, Parsed, build_prompt, chunk_files, parse_response
from wisp.review.rules import Rules, check_rules
from wisp.review.types import Finding, Severity
from wisp.review.verdict import derive_verdict

ModelRunner = Callable[[str], Awaitable[str]]


@dataclass
class ReviewOptions:
    lenses: tuple[str, ...] = tuple(LENSES)
    use_model: bool = True
    max_findings: int = 30
    max_concurrency: int = 3
    lens_timeout_s: float = 180.0
    max_chunk_chars: int = MAX_CHUNK_CHARS
    max_total_chars: int = MAX_TOTAL_CHARS


@dataclass
class ReviewReport:
    source: str
    files: list[str]
    findings: list[Finding]
    suppressed: int
    verdict: str
    reasons: list[str]
    skipped: list[tuple[str, str]]
    checks_run: list[str]
    lenses_run: list[str]
    ungrounded: int
    model_summary: str
    errors: list[str] = field(default_factory=list)
    malformed: int = 0


async def review(
    files: list[FileDiff], residue: int, ctx: CheckContext, rules: Rules, options: ReviewOptions, runner: ModelRunner | None, source: str = "",
) -> ReviewReport:
    unknown = [lens for lens in options.lenses if lens not in LENSES]
    if unknown:
        raise ValueError(f"unknown lens: {', '.join(unknown)}")
    active_checks = ALL_CHECKS + ((check_affected_tests,) if ctx.run_tests is not None else ())
    findings, check_errors = run_checks(files, ctx, active_checks)
    findings.extend(check_rules(files, rules))

    skipped: list[tuple[str, str]] = []
    lens_errors: list[str] = []
    claims_grounded: list[Finding] = []
    ungrounded = malformed = 0
    summaries: list[str] = []
    model_available = runner is not None
    if options.use_model and runner is not None:
        chunks, skipped = chunk_files(files, options.max_chunk_chars, options.max_total_chars)
        by_path: Mapping[str, FileDiff] = {fd.path: fd for fd in files}
        for lens, parsed, error in await _run_lenses(chunks, rules, options, runner):
            if error:
                if error not in lens_errors:
                    lens_errors.append(error)
                continue
            assert parsed is not None
            malformed += parsed.malformed
            if parsed.summary and parsed.summary not in summaries:
                summaries.append(parsed.summary)
            for claim in parsed.claims:
                grounded = ground(claim, by_path, rules)
                if grounded is None:
                    ungrounded += 1
                else:
                    claims_grounded.append(grounded)
    deterministic_keys = {(f.rule, f.file, f.line) for f in findings}
    findings.extend(f for f in merge_duplicates(claims_grounded) if (f.rule, f.file, f.line) not in deterministic_keys)
    findings.sort(key=lambda f: (f.severity.rank, f.file, f.line, f.rule))

    blockers = [f for f in findings if f.severity is Severity.BLOCK]
    others = [f for f in findings if f.severity is not Severity.BLOCK]
    kept = blockers + others[:max(options.max_findings - len(blockers), 0)]
    budget_skips = [path for path, why in skipped if "budget" in why]
    verdict, reasons = derive_verdict(
        kept, gaps=ctx.gaps, check_errors=check_errors, lens_errors=lens_errors, skipped_for_budget=budget_skips, residue=residue,
        truncated_files=[fd.path for fd in files if fd.truncated], model_requested=options.use_model, model_available=model_available)
    return ReviewReport(
        source=source, files=[fd.path for fd in files], findings=kept, suppressed=len(findings) - len(kept), verdict=verdict, reasons=reasons, skipped=skipped,
        checks_run=[check.__name__ for check in active_checks], lenses_run=list(options.lenses) if options.use_model and runner is not None else [], ungrounded=ungrounded,
        model_summary=clean_text(" ".join(summaries), 500), errors=check_errors + lens_errors, malformed=malformed)


async def _run_lenses(chunks, rules: Rules, options: ReviewOptions, runner: ModelRunner) -> list[tuple[str, Parsed | None, str]]:
    gate = asyncio.Semaphore(max(1, options.max_concurrency))
    rules_text = rules.model_text()

    async def one(lens: str, chunk) -> tuple[str, Parsed | None, str]:
        async with gate:
            try:
                text = await asyncio.wait_for(runner(build_prompt(lens, chunk.text, rules_text)), options.lens_timeout_s)
            except asyncio.TimeoutError:
                return lens, None, f"{lens}: timed out after {options.lens_timeout_s:g}s"
            except Exception as exc:  # noqa: BLE001 — a provider failure is a coverage gap, not a crash and not a clean review
                return lens, None, f"{lens}: {type(exc).__name__}: {clean_text(str(exc), 200)}"
        parsed = parse_response(text, lens)
        return lens, parsed, f"{lens}: {parsed.error}" if parsed.error else ""

    return list(await asyncio.gather(*(one(lens, chunk) for lens in options.lenses for chunk in chunks)))


__all__ = ["Claim", "ModelRunner", "ReviewOptions", "ReviewReport", "review"]
