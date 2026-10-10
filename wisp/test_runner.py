"""Smart test runner with import-graph-based test selection.

Provides functions to discover tests, select those affected by file
changes, execute them via pytest, and format results for LLM consumption.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from wisp.core.workspace_walk import is_home_directory
from wisp.import_graph import ImportGraphTooLarge, build_import_graph, find_affected_tests
from wisp.test_distill import distill_traceback

_FALLBACK_TAIL_CHARS = 3000

logger = logging.getLogger(__name__)


@dataclass
class UnitTestResult:
    """Result of a single test execution."""
    test_id: str
    outcome: str  # passed, failed, error, skipped
    duration: float
    stdout: str = ""
    stderr: str = ""
    traceback: str = ""


@dataclass
class UnitTestRunSummary:
    """Summary of a test run."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    duration: float = 0.0
    results: list[UnitTestResult] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""

    @property
    def success(self) -> bool:
        return self.failed == 0 and self.errors == 0

    def format_for_llm(self, max_results: int = 20) -> str:
        """Format summary as a concise block for the LLM system prompt."""
        lines = [
            f"## Test Results ({self.passed}/{self.total} passed)",
            f"- Duration: {self.duration:.2f}s",
            f"- Failed: {self.failed}, Errors: {self.errors}, Skipped: {self.skipped}",
        ]

        if self.failed or self.errors:
            lines.append("\n### Failures")
            shown = 0
            for r in self.results:
                if r.outcome in ("failed", "error"):
                    lines.append(f"\n**{r.test_id}** — {r.outcome}")
                    if r.traceback:
                        tb = distill_traceback(r.traceback)
                        lines.append(f"```\n{tb}\n```")
                    shown += 1
                    if shown >= max_results:
                        lines.append(f"\n... and {self.failed + self.errors - shown} more")
                        break
            if not any(r.traceback for r in self.results if r.outcome in ("failed", "error")) and (self.stdout or self.stderr):
                # Nothing was parsed into a per-test reason: show the end of the output, where pytest prints it, rather than a bare "failed".
                tail = (self.stdout + ("\n" + self.stderr if self.stderr else ""))[-_FALLBACK_TAIL_CHARS:]
                lines.append(f"\n### Output (end)\n```\n{tail.strip()}\n```")

        return "\n".join(lines)


def discover_tests(workspace: str | Path) -> list[str]:
    """Discover all test files under *workspace* using pytest --collect-only.

    Returns a list of test node IDs (e.g. ``tests/test_tools.py::TestReadFile``).
    """
    ws = Path(workspace).resolve()
    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", str(ws)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        logger.warning("pytest --collect-only timed out")
        return []
    except FileNotFoundError:
        logger.warning("pytest not found")
        return []

    tests: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if "::" in line and not line.startswith("="):
            # pytest -q output: "tests/test_tools.py::TestReadFile::test_read"
            tests.append(line.split(" ")[0])
    return tests


def run_tests(
    test_paths: list[str | Path],
    workspace: Optional[str | Path] = None,
    timeout: int = 120,
    verbose: bool = False,
) -> UnitTestRunSummary:
    """Run the specified tests via pytest and return a structured summary.

    Parameters
    ----------
    test_paths:
        List of test files or node IDs to run.
    workspace:
        Working directory for the test run.
    timeout:
        Maximum seconds to wait for pytest.
    verbose:
        If True, include stdout/stderr in the summary.

    Returns
    -------
    :class:`UnitTestRunSummary` with parsed results.
    """
    summary = UnitTestRunSummary()
    if not test_paths:
        return summary

    cmd = [
        sys.executable, "-m", "pytest",
        "-v",
        "--tb=short",
        "--json-report",
        "--json-report-file=-",  # stdout
    ]
    # Only add --json-report if pytest-json-report is available
    # Fallback: parse plain pytest output
    has_json_report = _has_plugin("pytest_jsonreport")
    if not has_json_report:
        cmd = [sys.executable, "-m", "pytest", "-v", "--tb=short"]

    for tp in test_paths:
        cmd.append(str(tp))

    cwd = str(workspace) if workspace else None
    start = time.monotonic()

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        summary.duration = time.monotonic() - start
        summary.errors += 1
        summary.total += 1
        summary.results.append(UnitTestResult(
            test_id="pytest",
            outcome="error",
            duration=summary.duration,
            stderr="pytest timed out",
        ))
        return summary
    except FileNotFoundError:
        summary.errors += 1
        summary.results.append(UnitTestResult(
            test_id="pytest",
            outcome="error",
            duration=0.0,
            stderr="pytest not found",
        ))
        return summary

    summary.duration = time.monotonic() - start
    summary.stdout = proc.stdout
    summary.stderr = proc.stderr

    if has_json_report and proc.stdout:
        try:
            # Find JSON report in stdout (pytest may print other stuff)
            json_start = proc.stdout.rfind('{"')
            if json_start >= 0:
                report = json.loads(proc.stdout[json_start:])
                _parse_json_report(report, summary)
                return summary
        except (json.JSONDecodeError, KeyError):
            pass

    # Fallback: parse plain pytest output
    _parse_pytest_output(proc.stdout, proc.stderr, summary)
    return summary


def _has_plugin(name: str) -> bool:
    """Check if a pytest plugin is installed."""
    try:
        import importlib
        importlib.import_module(name)
        return True
    except ImportError:
        return False


def _parse_json_report(report: dict, summary: UnitTestRunSummary) -> None:
    """Parse pytest-json-report output."""
    summary.total = report.get("summary", {}).get("total", 0)
    summary.passed = report.get("summary", {}).get("passed", 0)
    summary.failed = report.get("summary", {}).get("failed", 0)
    summary.skipped = report.get("summary", {}).get("skipped", 0)
    summary.errors = report.get("summary", {}).get("error", 0)

    for test in report.get("tests", []):
        summary.results.append(UnitTestResult(
            test_id=test.get("nodeid", "unknown"),
            outcome=test.get("outcome", "unknown"),
            duration=test.get("duration", 0.0),
            stdout=test.get("setup", {}).get("longrepr", "")
            + test.get("call", {}).get("longrepr", ""),
            traceback=test.get("call", {}).get("longrepr", ""),
        ))


_SUMMARY_LINE = re.compile(r"^[=\s]*(\d+ \w+(?:\s*\([^)]*\))?(?:, \d+ \w+(?:\s*\([^)]*\))?)*) in \d+(?:\.\d+)?s\b")
_COUNT = re.compile(r"(\d+) (passed|failed|errors?|skipped)")
_BANNER = re.compile(r"^=+ (.+?) =+$")
_BLOCK_HEADER = re.compile(r"^_{2,} (.+?) _{2,}$")
_SHORT_SUMMARY = re.compile(r"^(FAILED|ERROR) (\S+)(?: - (.*))?$")
_VERBOSE_LINE = re.compile(r"(\S+)::(\S+) (PASSED|FAILED|ERROR|SKIPPED)")


def _failure_blocks(lines: list[str]) -> dict[str, str]:
    """The per-test sections of pytest's `FAILURES` and `ERRORS` output, by their header (`test_add`, `TestK.test_m`, `ERROR collecting x.py`)."""
    blocks: dict[str, list[str]] = {}
    inside, current = False, ""
    for line in lines:
        banner = _BANNER.match(line)
        if banner:
            inside, current = banner.group(1).strip() in ("FAILURES", "ERRORS"), ""
            continue
        header = _BLOCK_HEADER.match(line) if inside else None
        if header:
            current = header.group(1).strip()
            blocks.setdefault(current, [])
        elif inside and current:
            blocks[current].append(line)
    return {k: "\n".join(v).strip() for k, v in blocks.items()}


def _block_for(test_id: str, blocks: dict[str, str]) -> str:
    _, _, name = test_id.partition("::")
    wanted = name.replace("::", ".")
    for header, text in blocks.items():
        if wanted and header == wanted:
            return text
    for header, text in blocks.items():  # a collection error has no `::` in its id: its header names the file
        if not wanted and test_id in header:
            return text
    return ""


def _parse_pytest_output(stdout: str, stderr: str, summary: UnitTestRunSummary) -> None:
    """Parse plain pytest output when the json-report plugin is absent (the usual case).

    Whatever the verbosity: a project whose `addopts` holds `-q` cancels our `-v`, so there are no per-test lines and no `====` banner, but the
    short-summary lines and the `FAILURES` sections are always printed, and those are what say WHY a test failed.
    """
    lines = stdout.splitlines()
    for line in lines:
        m = _SUMMARY_LINE.match(line)
        if m and _COUNT.search(m.group(1)):
            counts = {k.rstrip("s") if k.startswith("error") else k: int(n) for n, k in _COUNT.findall(m.group(1))}
            summary.passed = counts.get("passed", 0)
            summary.failed = counts.get("failed", 0)
            summary.skipped = counts.get("skipped", 0)
            summary.errors = counts.get("error", 0)
            summary.total = summary.passed + summary.failed + summary.skipped + summary.errors

    by_id: dict[str, UnitTestResult] = {}
    for line in lines:
        v = _VERBOSE_LINE.match(line)
        if v:
            tid = f"{v.group(1)}::{v.group(2)}"
            by_id[tid] = UnitTestResult(test_id=tid, outcome=v.group(3).lower(), duration=0.0)
    blocks = _failure_blocks(lines)
    for line in lines:
        short = _SHORT_SUMMARY.match(line.strip())
        if not short:
            continue
        outcome, tid, message = short.group(1).lower(), short.group(2), short.group(3) or ""
        result = by_id.setdefault(tid, UnitTestResult(test_id=tid, outcome=outcome, duration=0.0))
        result.outcome = outcome
        result.traceback = _block_for(tid, blocks) or message
    summary.results.extend(by_id.values())
    if not summary.total:
        summary.total = len(summary.results)
        summary.failed = sum(1 for r in summary.results if r.outcome == "failed")
        summary.errors = sum(1 for r in summary.results if r.outcome == "error")
        summary.passed = sum(1 for r in summary.results if r.outcome == "passed")


#: Workspaces whose import graph was refused, by monotonic time. Every write and edit asks for affected tests, and
#: re-walking a workspace that was too large a second ago would charge each of them the full budget.
_TOO_LARGE: dict[Path, float] = {}
_TOO_LARGE_TTL_SECONDS = 600.0


def _lookup_skipped(ws: Path) -> str:
    """Why affected-test analysis must not run for ``ws``, or "" when it may."""
    if is_home_directory(ws):
        return f"{ws} is the home directory, not a project"
    seen = _TOO_LARGE.get(ws)
    if seen is not None:
        if time.monotonic() - seen < _TOO_LARGE_TTL_SECONDS:
            return f"{ws} was too large to analyse a moment ago"
        del _TOO_LARGE[ws]
    return ""


def run_affected_tests(
    changed_files: list[str | Path],
    workspace: str | Path,
    timeout: int = 120,
) -> UnitTestRunSummary:
    """Build import graph, find affected tests, and run them.

    This is the main entry point for auto-test on change.
    """
    ws = Path(workspace).resolve()
    skipped = _lookup_skipped(ws)
    if skipped:
        return UnitTestRunSummary(stdout=f"Affected-test lookup skipped: {skipped}")
    logger.info("Building import graph for %s", ws)
    try:
        graph = build_import_graph(ws)
    except ImportGraphTooLarge as exc:
        # Not a project-sized workspace (a monorepo root, a data directory): finding affected tests would cost
        # more than the edit it follows. Remember it, say so, run nothing; `run_tests` on a path still works.
        _TOO_LARGE[ws] = time.monotonic()
        logger.warning("Affected-test lookup skipped: %s", exc)
        return UnitTestRunSummary(stdout=f"Affected-test lookup skipped: {exc}")

    # Resolve changed files relative to workspace if needed
    resolved_changes = []
    for f in changed_files:
        p = Path(f)
        if not p.is_absolute():
            p = ws / p
        resolved_changes.append(p.resolve())

    test_files = find_affected_tests(resolved_changes, graph)
    if not test_files:
        logger.info("No tests affected by changes")
        return UnitTestRunSummary()

    logger.info("Running %d affected test files", len(test_files))
    return run_tests([str(t) for t in test_files], workspace=ws, timeout=timeout)


def run_all_tests(
    workspace: str | Path,
    timeout: int = 300,
) -> UnitTestRunSummary:
    """Run the entire test suite."""
    ws = Path(workspace).resolve()
    return run_tests([str(ws)], workspace=ws, timeout=timeout)
