#!/usr/bin/env python3
"""End-to-end subsystem verification for the refactored Wisp CLI stack.

Runs 4 phases against the REAL production wiring (only the LLM network layer
is scripted via ``wisp.providers.mock.MockProvider``):

  Phase 1  Import sanitation + legacy ``agent.*`` namespace audit
           (zero ModuleNotFoundError on wisp-internal imports, zero legacy
           leaks in critical subsystem modules).
  Phase 2  Pre-flight Doctor — ``run_preflight_sync()`` under the 100 ms
           budget must report 5/5 ok across
           path_environment / stream_hygiene / tool_cache /
           autonomous_policy / graph_integrity, and the live ``/doctor``
           REPL command must render the same result.
  Phase 3  In-memory file mutation & diff presentation — a scripted
           edit_file tool call flows through
           CompositionRoot -> AgentRuntime.run_turn -> WispAgentCore ->
           ToolExecutor -> registry with a mock-TTY CLITransport approval
           session answering ``v`` (view diff) then ``y`` (approve).
           Asserts the compact 2-line approval badge, the ANSI Rich diff
           panel, loop continuity (no tool-loop reset on ``v``), and the
           clean on-disk mutation.
  Phase 4  Stream hygiene — provider-stream warnings are rerouted to
           ``.agent/runtime.log`` by the ``agent.logger.BadgeFilter`` and
           never reach user stdout; turn stdout is free of tracebacks.

Usage:
    python scripts/verify_subsystems.py                # fast: targeted checks
    python scripts/verify_subsystems.py --full-suite   # + full pytest run
    python scripts/verify_subsystems.py --skip-pytest  # harness only

Exit code 0 = all phases green, 1 = at least one assertion failed.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import io
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# ── Repo-relative bootstrap (run from anywhere) ─────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)  # .agent/runtime.log and workspace paths resolve here

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LEGACY_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+agent\b", re.MULTILINE)

# Modules whose namespace purity is contract-critical (no agent.* bridging).
CRITICAL_MODULES = (
    "wisp/core/doctor.py",
    "wisp/core/provider_stream.py",
    "wisp/core/stateless.py",
    "wisp/core/runtime.py",
    "wisp/cli/approval.py",
    "wisp/ui/diff_viewer.py",
    "wisp/transport/cli.py",
    "wisp/repl/commands/doctor.py",
)

# composition.py intentionally installs back-compat bridges into the legacy
# agent/ package (logger sink, bash runner sink, batch-reader registration).
# These are the ONLY sanctioned agent.* touchpoints in wisp/.
DOCUMENTED_BRIDGES = ("wisp/composition.py",)

DOCTOR_CHECKS = (
    "path_environment",
    "stream_hygiene",
    "tool_cache",
    "autonomous_policy",
    "graph_integrity",
)

# Targeted pytest files covering the audited subsystems (fast, hermetic).
TARGETED_TESTS = (
    "tests/test_preflight_doctor.py",
    "tests/test_diff_approval.py",
    "tests/test_provider_integration.py",
    "tests/test_core_stateless.py",
    "tests/test_transport_cli.py",
    "tests/test_provider_stream.py",
)


@dataclass
class PhaseResult:
    name: str
    passed: bool = True
    latency_ms: float = 0.0
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, label: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((label, ok, detail))
        if not ok:
            self.passed = False
        return ok


# ════════════════════════════════════════════════════════════════════════
# Phase 1 — Import sanitation & legacy namespace audit
# ════════════════════════════════════════════════════════════════════════

def phase1_import_sanitation(res: PhaseResult) -> None:
    import wisp  # noqa: F401
    import pkgutil

    t0 = time.monotonic()
    mod_failures: list[tuple[str, str]] = []
    third_party_gaps: list[tuple[str, str]] = []

    modules = [m.name for m in pkgutil.walk_packages(wisp.__path__, prefix="wisp.")]
    for mod_name in modules:
        try:
            importlib.import_module(mod_name)
        except ModuleNotFoundError as e:
            missing = getattr(e, "name", "") or str(e)
            if missing.startswith("wisp"):
                mod_failures.append((mod_name, f"ModuleNotFoundError: {missing}"))
            else:
                third_party_gaps.append((mod_name, f"optional dep missing: {missing}"))
        except Exception as e:  # import side-effect crash — still fatal
            mod_failures.append((mod_name, f"{type(e).__name__}: {e}"))

    res.add(
        f"import-walk: {len(modules)} wisp.* modules import cleanly "
        "(0 wisp-internal ModuleNotFoundError)",
        not mod_failures,
        "; ".join(f"{m}: {err}" for m, err in mod_failures[:10]) or "all imports OK",
    )
    if third_party_gaps:
        res.add(
            f"optional third-party gaps (WARN only, {len(third_party_gaps)} module(s) degraded)",
            True,
            "; ".join(f"{m}: {err}" for m, err in third_party_gaps[:5]),
        )

    # ── Legacy `agent.*` namespace leak audit ───────────────────────────
    wisp_pkg_dir = REPO_ROOT / "wisp"
    leaks: dict[str, list[str]] = {}
    for py in sorted(wisp_pkg_dir.rglob("*.py")):
        rel = py.relative_to(REPO_ROOT).as_posix()
        if "__pycache__" in rel:
            continue
        src = py.read_text(encoding="utf-8", errors="replace")
        hits = [
            ln.strip()
            for ln in src.splitlines()
            if LEGACY_IMPORT_RE.match(ln)
        ]
        if hits:
            leaks.setdefault(rel, []).extend(hits)

    critical_leaks = {m: leaks[m] for m in CRITICAL_MODULES if m in leaks}
    res.add(
        "legacy namespace: 0 `agent.*` imports in all critical subsystem modules",
        not critical_leaks,
        f"leaks: {critical_leaks}" if critical_leaks else
        "doctor/provider_stream/stateless/runtime/approval/diff_viewer/cli "
        "all on canonical wisp.* namespace",
    )

    undocumented = {m: v for m, v in leaks.items() if m not in DOCUMENTED_BRIDGES}
    res.add(
        "legacy namespace: no UNdocumented agent.* bridges outside composition.py",
        not undocumented,
        f"undocumented: {undocumented}" if undocumented else
        f"documented bridges only: {sorted(set(leaks) & set(DOCUMENTED_BRIDGES)) or 'none'}",
    )

    res.latency_ms = (time.monotonic() - t0) * 1000


def run_pytest(full_suite: bool) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pytest", "-q", "--no-header", "-x",
           "-p", "no:cacheprovider"]
    if full_suite:
        cmd += sorted(str(p) for p in (REPO_ROOT / "tests").glob("test_*.py"))
    else:
        cmd += [t for t in TARGETED_TESTS if (REPO_ROOT / t).exists()]
    # start_new_session detaches the controlling TTY so getpass()'s /dev/tty
    # open fails (interactive API-key prompts degrade to EOF instead of
    # hanging); stdin=DEVNULL then feeds that EOF deterministically.
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, timeout=1800, stdin=subprocess.DEVNULL,
                          start_new_session=True)
    tail_lines = (proc.stdout + proc.stderr).splitlines()
    keep = [ln for ln in tail_lines
            if ("FAILED" in ln or "ERROR" in ln or " passed" in ln
                or " failed" in ln or "no tests ran" in ln)]
    tail = "\n".join(keep[-15:]) or "\n".join(tail_lines[-10:])
    return proc.returncode == 0, tail


# ════════════════════════════════════════════════════════════════════════
# Phase 2 — Pre-flight Doctor (100 ms budget, 5/5 ok) + live /doctor
# ════════════════════════════════════════════════════════════════════════

def phase2_doctor(res: PhaseResult, workspace: str) -> None:
    from wisp.core.doctor import (
        CHECK_NAMES,
        CheckStatus,
        format_banner,
        format_detailed,
        run_preflight_sync,
    )

    report = run_preflight_sync(workspace=workspace, timeout_s=0.1)
    res.latency_ms = report.total_duration_ms

    res.add(
        f"doctor: total latency {report.total_duration_ms:.0f}ms < 100ms budget",
        report.total_duration_ms < 100.0,
        f"engine-reported {report.total_duration_ms:.1f}ms (asyncio budget enforced)",
    )
    res.add(
        f"doctor: overall status {report.passed}/{report.total} ok (healthy)",
        report.healthy and report.passed == 5,
        format_banner(report),
    )
    for c in report.checks:
        res.add(
            f"doctor check: {c.name}",
            c.status == CheckStatus.OK,
            f"{c.symbol} [{c.commit}] {c.message} ({c.latency_ms:.0f}ms)",
        )
    res.add(
        "doctor: CHECK_NAMES contract matches the 5 canonical subsystems",
        tuple(CHECK_NAMES) == DOCTOR_CHECKS,
        f"{tuple(CHECK_NAMES)}",
    )
    res.add(
        "doctor: detailed formatter names every subsystem + healthy banner",
        all(name in format_detailed(report) for name in CHECK_NAMES)
        and format_banner(report).startswith("✓"),
        "format_detailed + format_banner OK",
    )

    # ── Live /doctor command through the REPL command registry ─────────
    captured = io.StringIO()
    fake_agent = SimpleNamespace(config=SimpleNamespace(workspace=workspace))
    with contextlib.redirect_stdout(captured):
        from wisp.repl.commands.doctor import cmd_doctor

        cmd_doctor(fake_agent, "")
    out = ANSI_RE.sub("", captured.getvalue())
    res.add(
        "/doctor command: prints '5/5 ok' summary without duplicated pre-flight warnings",
        "5/5 ok" in out and "✗" not in out and "⚠" not in out,
        out.splitlines()[0] if out.strip() else "(no output)",
    )


# ════════════════════════════════════════════════════════════════════════
# Phase 3 — In-memory file mutation & diff presentation (mock TTY)
# ════════════════════════════════════════════════════════════════════════

class _NoSpinner:
    """Silent spinner stand-in so approval keystrokes stay hermetic."""

    def start(self, *a: Any, **k: Any) -> None: ...
    def stop(self, *a: Any, **k: Any) -> None: ...
    def update(self, *a: Any, **k: Any) -> None: ...


def phase3_diff_mutation(res: PhaseResult) -> dict[str, Any]:
    from wisp.config import WispConfig
    from wisp.composition import CompositionRoot
    from wisp.providers.mock import MockProvider
    from wisp.transport.cli import CLITransport

    ws = Path(tempfile.mkdtemp(prefix="wisp_verify_"))
    fixture = ws / "test_calc.py"
    fixture.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    provider = MockProvider(
        responses=["", "Refactored add() to use a named total variable."],
        tool_calls=[
            [{"function": {"name": "edit_file",
                           "arguments": {"path": "test_calc.py",
                                         "old_text": ("def add(a, b):\n"
                                                      "    return a + b"),
                                         "new_text": ("def add(a, b):\n"
                                                      "    total = a + b\n"
                                                      "    return total")}}}],
        ],
    )

    # Route the production core factory to the scripted provider (same
    # injection seams tests/test_toolchain_e2e.py pins).
    import wisp.provider_catalog as pc
    from wisp.providers.factory import ProviderFactory

    orig_resolve, orig_from_config = pc.resolve_selection, ProviderFactory.from_config
    pc.resolve_selection = lambda cfg: SimpleNamespace(
        status="ok", suggested=None, provider="mock", detail="",
        model="mock-model", alternatives=[])
    ProviderFactory.from_config = lambda self, cfg: provider

    root: CompositionRoot | None = None
    try:
        config = WispConfig().replace(
            workspace=str(ws), provider="mock", model="mock-model",
            auto_approve=False, show_thinking=False)
        root = CompositionRoot(config)
        transport = CLITransport(root.runtime, config)
        transport._force_approval_mode = False
        transport._get_spinner = lambda: _NoSpinner()  # type: ignore[method-assign]

        # Mock TTY: scripted keystrokes v (view diff) → y (approve)
        answers = iter(["v", "y"])
        read_calls = {"n": 0}

        async def scripted_answer(*a: Any, **k: Any) -> str:
            read_calls["n"] += 1
            return next(answers)

        transport._read_approval_answer_with_reminders = scripted_answer  # type: ignore[method-assign]

        stdout_buf, stderr_buf = io.StringIO(), io.StringIO()

        async def _turn() -> list[dict]:
            session = await root.runtime.get_or_create_session(
                "verify-diff", model="mock-model", workspace=str(ws))
            events = []
            with (contextlib.redirect_stdout(stdout_buf),
                  contextlib.redirect_stderr(stderr_buf)):
                async for ev in root.runtime.run_turn(
                        session,
                        "Refactor add() to store the sum in a variable named total",
                        approval_handler=transport.approve):
                    events.append(ev)
            return events

        events = asyncio.run(_turn())

        by_type: dict[str, list[dict]] = {}
        for ev in events:
            by_type.setdefault(ev.get("type", ""), []).append(ev)

        return _phase3_assertions(res, ws, fixture, stdout_buf, stderr_buf,
                                  read_calls, by_type) | {
            "workspace": str(ws),
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue(),
            "events": events,
        }
    finally:
        pc.resolve_selection = orig_resolve
        ProviderFactory.from_config = orig_from_config
        if root is not None:
            with contextlib.suppress(Exception):
                root.shutdown()


def _phase3_assertions(
    res: PhaseResult,
    ws: Path,
    fixture: Path,
    stdout_buf: io.StringIO,
    stderr_buf: io.StringIO,
    read_calls: dict,
    by_type: dict[str, list[dict]],
) -> dict[str, Any]:
    err_text = stderr_buf.getvalue()

    # ── 1. Badge: compact 2-line form, no escaped raw payload dump ──────
    #    (the badge is re-printed after a [v] view; pair each badge header
    #    with the line that follows it — the Scope line.)
    plain_lines = [ANSI_RE.sub("", ln) for ln in err_text.splitlines()]
    badge_pairs = [
        (plain_lines[i], plain_lines[i + 1])
        for i, ln in enumerate(plain_lines)
        if "edit_file:" in ln and i + 1 < len(plain_lines)
    ]
    badge_ok = False
    badge_detail = f"badge pairs seen: {badge_pairs[:2]!r}"
    if badge_pairs:
        line1, line2 = badge_pairs[0]
        badge_ok = (
            "edit_file" in line1
            and "test_calc.py" in line1
            and "(+2 / -1 lines)" in line1
            and line1.rstrip().endswith("lines)")
            and line2.strip().startswith("Scope:")
            and "in def add()" in line2
            # No raw multiline payload with escaped newlines dumped:
            and "\\n" not in line1 + line2
            and "old_text" not in line1 + line2
        )
        badge_detail = f"line1={line1!r} line2={line2!r} (pairs={len(badge_pairs)})"
    res.add(
        "approval badge: compact 2-line form (path, (+N / -M lines), "
        "Scope: in def …) — no raw escaped payload",
        badge_ok, badge_detail,
    )

    # ── 2. 'v' keystroke: ANSI Rich unified diff panel ──────────────────
    plain_diff = ANSI_RE.sub("", err_text)
    diff_ok = (
        "Diff:" in plain_diff
        and "test_calc.py" in plain_diff
        and "-    return a + b" in plain_diff
        and "+    total = a + b" in plain_diff
        and "+    return total" in plain_diff
        and "\x1b[" in err_text  # syntax-highlighted (ANSI emitted)
    )
    ansi_emitted = "\x1b[" in err_text
    panel_header = "Diff:" in plain_diff
    hunks_present = "-    return a + b" in plain_diff and "+    return total" in plain_diff
    res.add(
        "diff viewer: [v] expands ANSI-highlighted Rich unified diff panel "
        "(+/- lines present)",
        diff_ok,
        f"ansi_emitted={ansi_emitted}; "
        f"panel_header={panel_header}; "
        f"hunks={hunks_present}",
    )
    res.add(
        "tool loop continuity: v → y in ONE approval session, edit executed "
        "exactly once (no loop reset)",
        read_calls["n"] == 2
        and len(by_type.get("tool_result", [])) == 1
        and bool(by_type.get("done")),
        f"reads={read_calls['n']}, "
        f"tool_results={len(by_type.get('tool_result', []))}, "
        f"done={bool(by_type.get('done'))}",
    )

    # ── 3. 'y' keystroke applied the diff cleanly to disk ───────────────
    after = fixture.read_text(encoding="utf-8")
    res.add(
        "file mutation: [y] applied the diff to test_calc.py on disk",
        "total = a + b" in after and "return total" in after,
        repr(after),
    )

    # Approval request event preceded the callback (contract pin)
    approval_reqs = by_type.get("approval_request", [])
    res.add(
        "approval contract: approval_request event emitted before callback",
        len(approval_reqs) == 1 and approval_reqs[0].get("name") == "edit_file",
        f"{[e.get('name') for e in approval_reqs]}",
    )
    return {}


# ════════════════════════════════════════════════════════════════════════
# Phase 4 — Transport resilience & stream hygiene
# ════════════════════════════════════════════════════════════════════════

def phase4_stream_hygiene(res: PhaseResult, phase3_ctx: dict[str, Any]) -> None:
    import agent.logger as alog
    from agent.logger import BadgeFilter

    ws = Path(phase3_ctx["workspace"])
    log_path = ws / ".agent" / "runtime.log"
    orig_log_path = alog.LOG_PATH
    # get_log_path() reads LOG_PATH dynamically — the documented seam tests use.
    alog.LOG_PATH = log_path
    try:
        alog.install()

        noisy = logging.getLogger("wisp.core.provider_stream")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            noisy.warning("Provider stream retry backoff 250ms (attempt 2)")
            logging.getLogger("wisp.tools.registry").warning(
                "File not found: missing_module.py")
        console_text = out.getvalue() + err.getvalue()

        file_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        res.add(
            "log routing: provider-stream warning rerouted to .agent/runtime.log",
            "Provider stream retry backoff" in file_text,
            f"log exists={log_path.exists()}, {len(file_text)} bytes captured",
        )
        res.add(
            "log routing: tool-miss warning rerouted to .agent/runtime.log",
            "File not found: missing_module.py" in file_text,
            "BadgeFilter._TOOL_MISS_RE path",
        )
        res.add(
            "stdout hygiene: rerouted warnings NEVER reach user console",
            "Provider stream" not in console_text
            and "File not found" not in console_text,
            f"console saw {len(console_text)} chars",
        )
        res.add(
            "BadgeFilter contract: filter installed on wisp.core.provider_stream",
            any(isinstance(f, BadgeFilter) for f in noisy.filters),
            "agent.logger._NOISY_LOGGERS wired",
        )
    finally:
        alog.LOG_PATH = orig_log_path
        with contextlib.suppress(Exception):
            alog.install()  # reinstall against the original sink

    # ── Turn stdout was clean of tracebacks / provider pollution ────────
    turn_stdout = phase3_ctx.get("stdout", "")
    polluted = [ln for ln in turn_stdout.splitlines()
                if "wisp.core.provider_stream" in ln
                or "Traceback (most recent call last)" in ln
                or re.search(r"WARNING.+Provider stream", ln)]
    res.add(
        "stream hygiene: live turn stdout free of tracebacks / "
        "provider_stream warnings",
        not polluted,
        "; ".join(polluted[:3]) or "clean",
    )

    # ── Runtime log exists for the REPL session (cwd-relative contract) ──
    cwd_log = REPO_ROOT / ".agent" / "runtime.log"
    res.add(
        "runtime.log sink: .agent/runtime.log present for diagnostics",
        cwd_log.exists() or log_path.exists(),
        f"cwd sink={cwd_log.exists()}, ws sink={log_path.exists()}",
    )


# ════════════════════════════════════════════════════════════════════════
# Reporting
# ════════════════════════════════════════════════════════════════════════

def render_report(results: list[PhaseResult]) -> str:
    lines = ["# Wisp Subsystem Verification Report", ""]
    total_ok = total = 0
    for r in results:
        status = "✅ PASS" if r.passed else "❌ FAIL"
        lines.append(f"## {r.name} — {status} ({r.latency_ms:.0f}ms)")
        lines.append("")
        lines.append("| Check | Status | Detail |")
        lines.append("|-------|--------|--------|")
        for label, ok, detail in r.checks:
            total += 1
            total_ok += ok
            lines.append(f"| {label} | {'✓' if ok else '✗'} | {detail} |")
        lines.append("")
    lines.append(f"**Overall: {total_ok}/{total} checks passed.**")
    lines.append("")
    lines.append("## Verification Checklist")
    lines.append("")
    lines.append("### Doctor (5/5)")
    owner = next((r for r in results if r.name.startswith("Phase 2")), None)
    for name in DOCTOR_CHECKS:
        hit = next((c for c in owner.checks
                    if c[0] == f"doctor check: {name}"), None) if owner else None
        lines.append(f"- [{'x' if hit and hit[1] else ' '}] `{name}` — "
                     f"{hit[2] if hit else 'n/a'}")
    lines.append("")
    lines.append("### ANSI / Diff Viewer")
    for r in results:
        if r.name.startswith("Phase 3"):
            for label, ok, detail in r.checks:
                if any(k in label for k in ("badge", "diff viewer",
                                            "continuity", "mutation")):
                    lines.append(f"- [{'x' if ok else ' '}] {label}")
    lines.append("")
    lines.append("### Edge Cases / Observations")
    lines.append("- Doctor latency is engine-reported against the 100 ms asyncio "
                 "budget; first-run wall-clock includes cold imports and is NOT "
                 "part of the REPL's per-launch budget (entry.py uses the same "
                 "`run_preflight_sync(timeout_s=0.1)` seam).")
    lines.append("- composition.py intentionally bridges agent.logger / "
                 "agent.tools.runner / agent.tools.batch_reader — the only "
                 "sanctioned legacy touchpoints in wisp/.")
    lines.append("- MockProvider scripts only the network layer; approval, "
                 "executor, registry, diff rendering and log routing all run "
                 "production code (same injection seams as "
                 "tests/test_toolchain_e2e.py).")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-suite", action="store_true",
                        help="run the FULL pytest suite (slow) instead of targeted files")
    parser.add_argument("--skip-pytest", action="store_true",
                        help="skip the pytest regression phase entirely")
    parser.add_argument(
        "--report",
        default=str(REPO_ROOT / "scripts" / "verification_report.md"),
        help="where to write the markdown report")
    args = parser.parse_args()

    print("═" * 72)
    print(" WISP SUBSYSTEM VERIFICATION — 4-phase audit")
    print("═" * 72)

    results: list[PhaseResult] = []

    # Phase 1
    r1 = PhaseResult("Phase 1 — Import sanitation & legacy namespace audit")
    print("\n[Phase 1] import sanitation + namespace audit…")
    phase1_import_sanitation(r1)
    if not args.skip_pytest:
        print("[Phase 1] pytest regression run…")
        ok, tail = run_pytest(args.full_suite)
        scope = "FULL suite" if args.full_suite else "targeted subsystem files"
        last = tail.splitlines()[-1] if tail else ""
        r1.add(f"pytest regression: {scope} pass", ok, last)
    results.append(r1)

    # Phase 2
    r2 = PhaseResult("Phase 2 — Pre-flight Doctor & /doctor pipeline")
    print("[Phase 2] doctor pre-flight + /doctor command…")
    with tempfile.TemporaryDirectory(prefix="wisp_doctor_ws_") as ws:
        phase2_doctor(r2, ws)
    results.append(r2)

    # Phase 3
    r3 = PhaseResult("Phase 3 — File mutation & diff presentation (mock TTY)")
    print("[Phase 3] scripted edit_file turn with v/y approval…")
    ctx: dict[str, Any] = {}
    try:
        ctx = phase3_diff_mutation(r3)
    except Exception as e:
        import traceback
        r3.add("phase harness ran without crash", False,
               f"{type(e).__name__}: {e}\n{traceback.format_exc()[-800:]}")
    results.append(r3)

    # Phase 4
    r4 = PhaseResult("Phase 4 — Transport resilience & stream hygiene")
    print("[Phase 4] log routing + stdout hygiene…")
    phase4_stream_hygiene(r4, ctx or {"workspace": tempfile.mkdtemp(prefix="wisp_p4_"),
                                      "stdout": ""})
    results.append(r4)

    # Report
    all_ok = all(r.passed for r in results)
    Path(args.report).write_text(render_report(results), encoding="utf-8")

    print()
    for r in results:
        sym = "✅" if r.passed else "❌"
        print(f"{sym} {r.name}")
        for label, ok, detail in r.checks:
            print(f"   {'✓' if ok else '✗'} {label}")
            if not ok and detail:
                print(f"     ↳ {detail[:300]}")
    print()
    print(f"Report written to {args.report}")
    print("═" * 72)
    print(" RESULT:", "ALL SUBSYSTEMS VERIFIED ✓" if all_ok
          else "FAILURES DETECTED ✗")
    print("═" * 72)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
