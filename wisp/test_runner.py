"""Smart test runner with import-graph-based test selection.

Provides functions to discover tests, select those affected by file
changes, execute them via pytest, and format results for LLM consumption.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from wisp.import_graph import build_import_graph, find_affected_tests

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
                        tb = r.traceback[:500]
                        if len(r.traceback) > 500:
                            tb += "\n... (truncated)"
                        lines.append(f"```\n{tb}\n```")
                    shown += 1
                    if shown >= max_results:
                        lines.append(f"\n... and {self.failed + self.errors - shown} more")
                        break

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


def _parse_pytest_output(stdout: str, stderr: str, summary: UnitTestRunSummary) -> None:
    """Parse plain pytest -v output as a fallback."""
    # Count outcomes from summary line
    # Example: "1 passed, 2 failed, 3 skipped in 0.5s"
    import re
    summary_line = ""
    for line in stdout.splitlines():
        # Pytest summary line: "==== 147 passed in 3.78s ====" or "1 passed, 2 failed in 0.5s"
        if re.search(r"^=+\s+\d+\s+(passed|failed|error)", line):
            summary_line = line
            break

    if summary_line:
        summary.passed = len(re.findall(r"(\d+) passed", summary_line)) and int(
            re.findall(r"(\d+) passed", summary_line)[0]
        ) or 0
        summary.failed = len(re.findall(r"(\d+) failed", summary_line)) and int(
            re.findall(r"(\d+) failed", summary_line)[0]
        ) or 0
        summary.skipped = len(re.findall(r"(\d+) skipped", summary_line)) and int(
            re.findall(r"(\d+) skipped", summary_line)[0]
        ) or 0
        summary.errors = len(re.findall(r"(\d+) error", summary_line)) and int(
            re.findall(r"(\d+) error", summary_line)[0]
        ) or 0
        summary.total = summary.passed + summary.failed + summary.skipped + summary.errors

    # Parse individual test results
    for line in stdout.splitlines():
        m = re.match(r"(\S+)::(\S+) (PASSED|FAILED|ERROR|SKIPPED)", line)
        if m:
            outcome = m.group(3).lower()
            summary.results.append(UnitTestResult(
                test_id=f"{m.group(1)}::{m.group(2)}",
                outcome=outcome,
                duration=0.0,
            ))


def run_affected_tests(
    changed_files: list[str | Path],
    workspace: str | Path,
    timeout: int = 120,
) -> UnitTestRunSummary:
    """Build import graph, find affected tests, and run them.

    This is the main entry point for auto-test on change.
    """
    ws = Path(workspace).resolve()
    logger.info("Building import graph for %s", ws)
    graph = build_import_graph(ws)

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
