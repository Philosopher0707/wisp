"""Doctor command — re-run pre-flight verification on demand.

Exposes `/doctor` (alias `/check`) in the REPL. Re-executes the same 5
subsystem checks that run automatically at REPL startup (wisp/entry.py),
then prints a detailed report. Isolated from the turn loop — failures
never mutate session state.

Usage:
  /doctor          — human-readable table + banner
  /doctor --json   — machine-readable JSON (for CI / scripting)
  /check           — alias for /doctor
"""

from __future__ import annotations

import json
import logging

from wisp.colors import dim, success, warning, error
from wisp.repl.commands import register

logger = logging.getLogger(__name__)


@register("doctor", "Run pre-flight health checks (5 subsystems)", aliases=("check", "health"), usage="/doctor [--json]")
def cmd_doctor(agent, args: str):
    """Re-run the pre-flight suite and print the report.

    Args:
        agent: AgentAdapter (holds config, session, loop).
        args: Raw slash-args — may contain `--json`.
    """
    raw = (args or "").strip()
    as_json = "--json" in raw or "-j" in raw

    # Resolve workspace/config from adapter (live values, not bootstrap)
    workspace = None
    config = None
    try:
        workspace = getattr(getattr(agent, "config", None), "workspace", None)
    except Exception:
        pass
    try:
        config = getattr(agent, "config", None)
    except Exception:
        pass
    # Fallback to safe_getcwd if workspace missing
    if not workspace:
        try:
            from wisp.config import safe_getcwd

            workspace = safe_getcwd()
        except Exception:
            workspace = "."

    # Run checks — sync wrapper handles existing loop via thread pool
    try:
        from wisp.core.doctor import run_preflight_sync, format_detailed

        report = run_preflight_sync(workspace=workspace, config=config, timeout_s=0.1)
        # Stash for future /doctor calls and telemetry
        try:
            import wisp.core.doctor as _doctor_mod

            _doctor_mod._LAST_REPORT = report  # type: ignore[attr-defined]
        except Exception:
            pass
    except Exception as e:
        logger.exception("doctor pre-flight failed")
        print(error(f"✗ Doctor failed: {e}"))
        return

    if as_json:
        try:
            print(json.dumps(report.to_dict(), indent=2))
        except Exception as e:
            print(error(f"✗ JSON serialization failed: {e}"))
            # Fallback to detailed
            from wisp.core.doctor import format_detailed as _fmt

            print(_fmt(report))
        return

    # Human-readable
    try:
        from wisp.core.doctor import format_detailed

        detailed = format_detailed(report)
        # Colorize banner line based on health
        if report.healthy:
            # Success banner already in detailed; highlight
            print(success(f"✓ Doctor: {report.passed}/{report.total} ok — {report.total_duration_ms:.0f}ms"))
        else:
            # Warning banner
            if report.failed:
                print(warning(f"⚠ Doctor: {report.failed} failed, {report.warnings} warning(s) — {report.total_duration_ms:.0f}ms"))
            else:
                print(warning(f"⚠ Doctor: {report.warnings} warning(s) — {report.total_duration_ms:.0f}ms"))
            print(dim("  Hint: check .agent/runtime.log for degraded subsystems"))
        print()
        print(detailed)
        # Also print banner line for copy-paste
        print()
        print(dim(report.banner))
    except Exception as e:
        logger.exception("doctor formatting failed")
        print(error(f"✗ Formatting failed: {e}"))
        # Fallback raw
        try:
            print(json.dumps(report.to_dict(), indent=2))
        except Exception:
            pass
