"""Harness invariants — the checks that came out of the 2026-10 harness review, runnable on demand.

`wisp.core.doctor.run_preflight` runs at REPL launch under a 100 ms budget, so it can only hold checks that cost
nothing. These probe behaviour (a budget meter, a prompt fit, a skill round trip), import heavier modules and
touch the disk, so they run when asked: ``wisp doctor`` and ``/doctor --deep``.

Every probe is hermetic: it builds its own temp workspace and audit log and never writes to the user's real
``~/.config/wisp`` or workspace. The one read of real state (the live audit log) is read-only and reports a
historical break as a warning, because a broken chain is evidence and must not be "repaired".

Each check returns a ``CheckResult``; none raises. A module this checkout does not have is a WARN, like the
preflight, so a partial checkout still boots.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from wisp.core.doctor import CheckResult, CheckStatus, DoctorReport

__all__ = ["HARNESS_CHECK_NAMES", "run_harness_checks"]

#: A user with more global skills than this pays for them on every request (measured: 56 skills ≈ 10k tokens).
_GLOBAL_SKILL_WARN_COUNT: Final = 30


def _result(name: str, commit: str, t0: float, status: CheckStatus, message: str, **details: Any) -> CheckResult:
    return CheckResult(name, commit, status, message, (time.monotonic() - t0) * 1000, details)


def _check_tool_profile() -> CheckResult:
    """The default tool surface: core profile, every core tool implemented and schema'd."""
    t0, name, commit = time.monotonic(), "tool_profile", "tool-surface"
    try:
        from wisp.config import get_setting
        from wisp.tools.profile import CORE_TOOLS, parse_profile
        from wisp.tools.registry import TOOL_IMPLS, TOOL_SCHEMAS
    except ImportError as exc:
        return _result(name, commit, t0, CheckStatus.WARN, f"not in this checkout: {exc}")

    def schema_name(schema: dict[str, Any]) -> str:
        return str(schema.get("function", {}).get("name") or schema.get("name") or "")

    profile = parse_profile(get_setting("tool_profile", None))
    by_name = {schema_name(s): s for s in TOOL_SCHEMAS}
    unimplemented = [n for n in CORE_TOOLS if n not in TOOL_IMPLS]
    unschemad = [n for n in CORE_TOOLS if n not in by_name]
    core_tokens = sum(len(json.dumps(by_name[n])) for n in CORE_TOOLS if n in by_name) // 4
    full_tokens = sum(len(json.dumps(s)) for s in TOOL_SCHEMAS) // 4
    details = {"profile": profile, "core_tools": len(CORE_TOOLS), "core_schema_tokens": core_tokens,
               "all_builtin_schema_tokens": full_tokens}
    if unimplemented or unschemad:
        return _result(name, commit, t0, CheckStatus.FAIL,
                       f"core tools missing: impl={unimplemented} schema={unschemad}", **details)
    if profile != "core":
        return _result(name, commit, t0, CheckStatus.WARN,
                       f"profile={profile}: all {len(TOOL_SCHEMAS)} built-ins offered (~{full_tokens} tokens/call "
                       f"vs ~{core_tokens} for core)", **details)
    return _result(name, commit, t0, CheckStatus.OK,
                   f"core: {len(CORE_TOOLS)} tools, ~{core_tokens} schema tokens/call", **details)


def _check_subagent_surface() -> CheckResult:
    """Subagents never inherit the parent's `skill__*` tools (53 schemas ≈ 10k tokens per child call)."""
    t0, name, commit = time.monotonic(), "subagent_surface", "subagent-tools"
    try:
        from wisp.core.stateless import _SUBAGENT_EXCLUDED_TOOL_PREFIXES
    except ImportError as exc:
        return _result(name, commit, t0, CheckStatus.WARN, f"not in this checkout: {exc}")
    if "skill__" not in _SUBAGENT_EXCLUDED_TOOL_PREFIXES:
        return _result(name, commit, t0, CheckStatus.FAIL, "skill__ tools are offered to subagents",
                       excluded=list(_SUBAGENT_EXCLUDED_TOOL_PREFIXES))
    return _result(name, commit, t0, CheckStatus.OK, "skill__* excluded from subagents",
                   excluded=list(_SUBAGENT_EXCLUDED_TOOL_PREFIXES))


def _check_spend_accounting() -> CheckResult:
    """The subagent meter counts streamed text (with the remainder carried) and stops at its limit."""
    t0, name, commit = time.monotonic(), "spend_accounting", "subagent-budget"
    try:
        from wisp.config import get_setting
        from wisp.multi_agent.resource_budget import CHARS_PER_TOKEN, ResourceBudget
    except ImportError as exc:
        return _result(name, commit, t0, CheckStatus.WARN, f"not in this checkout: {exc}")
    if not hasattr(ResourceBudget, "record_text"):
        return _result(name, commit, t0, CheckStatus.FAIL, "ResourceBudget.record_text is missing: streamed output "
                       "is not metered")
    meter = ResourceBudget(max_tokens=10)
    meter.start()
    for _ in range(CHARS_PER_TOKEN * 3):  # 12 deltas of one character: flooring each would count zero
        meter.record_text("x")
    if meter._tokens_used != 3:
        return _result(name, commit, t0, CheckStatus.FAIL,
                       f"{CHARS_PER_TOKEN * 3} one-char deltas counted {meter._tokens_used} tokens, want 3")
    meter.record_text("y" * (CHARS_PER_TOKEN * 8))
    if not meter.check():
        return _result(name, commit, t0, CheckStatus.FAIL, "meter past max_tokens did not report exhaustion")
    try:
        ceiling = int(get_setting("subagent_token_budget", "2000000"))
    except (TypeError, ValueError):
        ceiling = 0
    if ceiling <= 0:
        return _result(name, commit, t0, CheckStatus.WARN, "subagent_token_budget is not a positive integer: the "
                       "global admission gate is effectively off", subagent_token_budget=ceiling)
    return _result(name, commit, t0, CheckStatus.OK, f"meter exact; global ceiling {ceiling:,} tokens",
                   subagent_token_budget=ceiling)


def _check_context_budget() -> CheckResult:
    """The system-prompt fit honours its budget and names what it cut (it used to overshoot 1.5x, silently)."""
    t0, name, commit = time.monotonic(), "context_budget", "context-assembly"
    try:
        from wisp.context_assembler import ContextAssembler
    except ImportError as exc:
        return _result(name, commit, t0, CheckStatus.WARN, f"not in this checkout: {exc}")
    sections = [("core rules", 0, "r" * 900), ("memory", 3, "m" * 900), ("extra tools", 6, "e" * 900)]
    budget = 200
    text, tokens = ContextAssembler()._fit_sections(sections, budget)
    details = {"budget": budget, "estimated": tokens, "chars": len(text)}
    if tokens > budget or len(text) // 3 > budget:
        return _result(name, commit, t0, CheckStatus.FAIL,
                       f"fit overshot: {tokens} estimated / {len(text) // 3} actual vs budget {budget}", **details)
    if "extra tools" not in text:  # the lowest-priority section was dropped: the note must say so
        return _result(name, commit, t0, CheckStatus.FAIL, "a section was dropped without being named", **details)
    return _result(name, commit, t0, CheckStatus.OK, f"fit {tokens}/{budget} tokens; cuts are named", **details)


def _check_global_skills() -> CheckResult:
    """Global skills come from ~/.agents/skills only, and their count is a per-request cost."""
    t0, name, commit = time.monotonic(), "global_skills", "skills"
    try:
        from wisp.skills import GLOBAL_SKILL_DIRS, SKILL_DIR_NAMES, discover_skills
    except ImportError as exc:
        return _result(name, commit, t0, CheckStatus.WARN, f"not in this checkout: {exc}")
    if [Path(p).parts[-2:] for p in GLOBAL_SKILL_DIRS] != [(".agents", "skills")]:
        return _result(name, commit, t0, CheckStatus.FAIL,
                       f"global skill dirs are {[str(p) for p in GLOBAL_SKILL_DIRS]}, want only ~/.agents/skills")
    if ".wisp/skills/auto" not in SKILL_DIR_NAMES:
        return _result(name, commit, t0, CheckStatus.FAIL, "captured skills are written to .wisp/skills/auto but "
                       "never loaded back")
    with tempfile.TemporaryDirectory(prefix="wisp-doctor-skills-") as empty:
        skills = discover_skills(empty)  # an empty workspace: whatever comes back is global
    menu_tokens = sum(len(s.name) + len(s.description) + 8 for s in skills) // 3
    details = {"global_skills": len(skills), "menu_tokens": menu_tokens}
    if len(skills) > _GLOBAL_SKILL_WARN_COUNT:
        return _result(name, commit, t0, CheckStatus.WARN,
                       f"{len(skills)} global skills ≈ {menu_tokens} menu tokens on every request; trim "
                       f"~/.agents/skills", **details)
    return _result(name, commit, t0, CheckStatus.OK, f"{len(skills)} global skills ≈ {menu_tokens} menu tokens",
                   **details)


def _check_skill_capture() -> CheckResult:
    """A captured skill is written, parses, and is found again by discovery (the loop was dead once)."""
    t0, name, commit = time.monotonic(), "skill_capture", "skill-capture"
    try:
        from wisp.skill_capture import capture_resolved_skill
        from wisp.skills import discover_skills
    except ImportError as exc:
        return _result(name, commit, t0, CheckStatus.WARN, f"not in this checkout: {exc}")
    with tempfile.TemporaryDirectory(prefix="wisp-doctor-capture-") as ws:
        written = capture_resolved_skill("doctor probe task", [("edit_file", {"path": "a.py"}), ("run_tests", {})], ws)
        if written is None:
            return _result(name, commit, t0, CheckStatus.FAIL, "capture wrote nothing for a two-step trail")
        found = [s for s in discover_skills(ws) if s.file_path.resolve().is_relative_to(Path(ws).resolve())]
    if not found:
        return _result(name, commit, t0, CheckStatus.FAIL, "a captured skill is written but discovery does not "
                       "load it back")
    return _result(name, commit, t0, CheckStatus.OK, "capture → discover round trip works", skills=len(found))


def _check_audit_chain() -> CheckResult:
    """Appends chain under concurrent writers (probe, in a temp log); the live log is verified read-only."""
    t0, name, commit = time.monotonic(), "audit_chain", "audit"
    try:
        from wisp.infra.audit import DEFAULT_AUDIT_PATH, AuditTrail
    except ImportError as exc:
        return _result(name, commit, t0, CheckStatus.WARN, f"not in this checkout: {exc}")
    with tempfile.TemporaryDirectory(prefix="wisp-doctor-audit-") as tmp:
        log = Path(tmp) / "audit.jsonl"
        first, second = AuditTrail(log), AuditTrail(log)  # two writers, each holding a stale in-memory head
        for i in range(3):
            first.record("doctor_probe", actor="doctor", metadata={"n": f"a{i}"})
            second.record("doctor_probe", actor="doctor", metadata={"n": f"b{i}"})
        broken = AuditTrail(log).verify()
    if broken is not None:
        return _result(name, commit, t0, CheckStatus.FAIL, f"two writers forked the chain at entry {broken}")
    live = Path(os.environ.get("WISP_AUDIT_LOG", str(DEFAULT_AUDIT_PATH)))
    if not live.exists():
        return _result(name, commit, t0, CheckStatus.OK, "writers chain correctly; no live log yet")
    live_break = AuditTrail(live).verify()
    if live_break is not None:
        return _result(name, commit, t0, CheckStatus.WARN,
                       f"writers chain correctly; live log has a historical break at entry {live_break} (evidence: "
                       f"do not rewrite)", live_break=live_break)
    return _result(name, commit, t0, CheckStatus.OK, "writers chain correctly; live log intact")


_CHECKS: Final[tuple[tuple[str, Callable[[], CheckResult]], ...]] = (
    ("tool_profile", _check_tool_profile),
    ("subagent_surface", _check_subagent_surface),
    ("spend_accounting", _check_spend_accounting),
    ("context_budget", _check_context_budget),
    ("global_skills", _check_global_skills),
    ("skill_capture", _check_skill_capture),
    ("audit_chain", _check_audit_chain),
)

HARNESS_CHECK_NAMES: Final[tuple[str, ...]] = tuple(n for n, _ in _CHECKS)


def run_harness_checks() -> DoctorReport:
    """Run every harness check once, in order. Never raises: a probe that crashes is a FAIL for that check."""
    start = time.monotonic()
    results: list[CheckResult] = []
    for name, check in _CHECKS:
        t0 = time.monotonic()
        try:
            results.append(check())
        except Exception as exc:  # a broken probe is itself a finding, not a reason to hide the others
            results.append(_result(name, "harness", t0, CheckStatus.FAIL, f"probe raised: {exc!r}"))
    return DoctorReport(checks=tuple(results), total_duration_ms=(time.monotonic() - start) * 1000)


def main(argv: list[str] | None = None) -> int:
    """``wisp doctor [--json]``: print the harness report. Exit 1 on any FAIL; a WARN is advice, not a failure."""
    from wisp.core.doctor import format_detailed

    args = argv or []
    report = run_harness_checks()
    print(json.dumps(report.to_dict(), indent=2) if "--json" in args else format_detailed(report))
    return 1 if report.failed else 0
