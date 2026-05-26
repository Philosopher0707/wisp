"""Error diagnosis — parse stack traces, classify errors, identify root causes.

Analyzes Python tracebacks, test failures, and tool errors to suggest fixes.
No ML model required — pure pattern matching and heuristics.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Diagnosis:
    """Structured diagnosis of an error."""

    error_type: str = "Unknown"
    message: str = ""
    suggestion: str = ""
    likely_cause: str = ""
    failing_file: str = ""
    failing_line: int = 0
    related_files: list[str] = field(default_factory=list)
    severity: str = "error"  # error, warning, info

    def format(self) -> str:
        """Format as a human-readable string."""
        lines = [
            f"🩺 Diagnosis: {self.error_type}",
        ]
        if self.message:
            lines.append(f"   Message: {self.message}")
        if self.failing_file:
            loc = f"{self.failing_file}:{self.failing_line}" if self.failing_line else self.failing_file
            lines.append(f"   Location: {loc}")
        if self.likely_cause:
            lines.append(f"   Cause: {self.likely_cause}")
        if self.suggestion:
            lines.append(f"   Suggestion: {self.suggestion}")
        if self.related_files:
            lines.append(f"   Related: {', '.join(self.related_files)}")
        return "\n".join(lines)


# ── Error Patterns ─────────────────────────────────────────────────────

_ERROR_PATTERNS = {
    "ImportError": {
        "pattern": re.compile(r"ImportError:\s*(?:cannot import name\s+'(\w+)'|No module named\s+'([^']+)')"),
        "suggest": lambda m: f"Check if '{m.group(1) or m.group(2)}' exists and is exported. Verify the module is installed or the name is spelled correctly.",
        "cause": "Missing import or typo in module/name",
    },
    "ModuleNotFoundError": {
        "pattern": re.compile(r"ModuleNotFoundError:\s*No module named\s+'([^']+)'"),
        "suggest": lambda m: f"Install the module with 'pip install {m.group(1)}' or check the import path.",
        "cause": "Missing Python package",
    },
    "AttributeError": {
        "pattern": re.compile(r"AttributeError:\s*(?:'(\w+)'\s*object has no attribute\s+'(\w+)'|module\s+'(\w+)'\s*has no attribute\s+'(\w+)')"),
        "suggest": lambda m: "Check spelling of the attribute or verify the object type. The attribute may have been renamed or removed.",
        "cause": "Typo or API change",
    },
    "SyntaxError": {
        "pattern": re.compile(r"SyntaxError:\s*(.+?)"),
        "suggest": lambda m: "Check for missing brackets, quotes, colons, or incorrect indentation near the reported line.",
        "cause": "Invalid Python syntax",
    },
    "IndentationError": {
        "pattern": re.compile(r"IndentationError:\s*(.+?)"),
        "suggest": lambda m: "Fix indentation — likely mixed tabs/spaces or wrong nesting level. Use 4 spaces per indent.",
        "cause": "Incorrect indentation",
    },
    "TypeError": {
        "pattern": re.compile(r"TypeError:\s*(.+?)"),
        "suggest": lambda m: "Check argument types and counts. Verify function signatures match the call.",
        "cause": "Type mismatch in function call",
    },
    "KeyError": {
        "pattern": re.compile(r"KeyError:\s*'([^']+)'"),
        "suggest": lambda m: f"Key '{m.group(1)}' not found. Use .get() with a default or check if the key exists before accessing.",
        "cause": "Missing dictionary key",
    },
    "IndexError": {
        "pattern": re.compile(r"IndexError:\s*(?:list index out of range|tuple index out of range)"),
        "suggest": lambda m: "Check list length before indexing. The collection may be empty or shorter than expected.",
        "cause": "Index exceeds collection bounds",
    },
    "ValueError": {
        "pattern": re.compile(r"ValueError:\s*(.+?)"),
        "suggest": lambda m: "Check the value being passed — it may be invalid, malformed, or of wrong format.",
        "cause": "Invalid value",
    },
    "FileNotFoundError": {
        "pattern": re.compile(r"FileNotFoundError:\s*\[Errno 2\]\s*No such file or directory:\s*'([^']+)'"),
        "suggest": lambda m: f"File '{m.group(1)}' does not exist. Check the path or create the file/directory first.",
        "cause": "Missing file or directory",
    },
    "AssertionError": {
        "pattern": re.compile(r"AssertionError"),
        "suggest": lambda m: "Test expectation doesn't match actual result. Check both the test assertion and the implementation.",
        "cause": "Test failure — expected vs actual mismatch",
    },
    "NameError": {
        "pattern": re.compile(r"NameError:\s*name\s+'(\w+)'\s*is not defined"),
        "suggest": lambda m: f"Variable or function '{m.group(1)}' is not defined. Check spelling, scope, or import it.",
        "cause": "Undefined name",
    },
    "ZeroDivisionError": {
        "pattern": re.compile(r"ZeroDivisionError:\s*division by zero"),
        "suggest": lambda m: "Add a guard check to ensure the denominator is not zero before dividing.",
        "cause": "Division by zero",
    },
    "RecursionError": {
        "pattern": re.compile(r"RecursionError:\s*maximum recursion depth exceeded"),
        "suggest": lambda m: "Check for infinite recursion — missing base case or circular call. Consider iterative approach.",
        "cause": "Infinite recursion",
    },
    "TimeoutError": {
        "pattern": re.compile(r"TimeoutError|timed out"),
        "suggest": lambda m: "Operation took too long. Check for infinite loops, slow I/O, or increase timeout.",
        "cause": "Operation timeout",
    },
    "ConnectionError": {
        "pattern": re.compile(r"ConnectionError|Connection refused|Connection reset"),
        "suggest": lambda m: "Check if the service is running and accessible. Verify host/port and network connectivity.",
        "cause": "Network connection failure",
    },
    "PermissionError": {
        "pattern": re.compile(r"PermissionError:\s*\[Errno 13\]\s*Permission denied"),
        "suggest": lambda m: "Check file permissions. You may need to run with elevated privileges or change file ownership.",
        "cause": "Insufficient permissions",
    },
    "JSONDecodeError": {
        "pattern": re.compile(r"json\.decoder\.JSONDecodeError|JSONDecodeError"),
        "suggest": lambda m: "The JSON is malformed. Check for trailing commas, unclosed quotes, or invalid syntax.",
        "cause": "Invalid JSON",
    },
    "OSError": {
        "pattern": re.compile(r"OSError:\s*\[Errno (\d+)\]\s*(.+?)"),
        "suggest": lambda m: f"System error {m.group(1)}: {m.group(2)}. Check disk space, file locks, or system limits.",
        "cause": "Operating system error",
    },
}


# ── Stack Trace Parser ─────────────────────────────────────────────────

def parse_traceback(output: str) -> tuple[str, int, str]:
    """Extract (file, line, function) from the last frame of a traceback.

    Returns (file_path, line_number, function_name).
    """
    # Python traceback format:
    #   File "path/to/file.py", line 42, in function_name
    #     some_code()
    pattern = re.compile(
        r'File\s+"([^"]+)"\s*,\s*line\s+(\d+)\s*,\s*in\s+(\w+)',
        re.MULTILINE,
    )
    matches = pattern.findall(output)
    if not matches:
        return "", 0, ""

    # Return the LAST frame (where the error actually occurred)
    file_path, line_str, func_name = matches[-1]
    return file_path, int(line_str), func_name


def extract_error_message(output: str) -> str:
    """Extract the final error message from traceback or test output."""
    lines = output.strip().split("\n")

    # Look for the last line that looks like an exception
    for line in reversed(lines):
        line = line.strip()
        if re.match(r"^[A-Z][a-zA-Z0-9]*Error:\s*", line):
            return line
        if re.match(r"^[A-Z][a-zA-Z0-9]*Exception:\s*", line):
            return line
        if "FAILED" in line and "::" in line:
            return line

    # Fallback: last non-empty line
    for line in reversed(lines):
        if line.strip():
            return line.strip()

    return ""


def identify_changed_files(workspace: str, since_minutes: int = 30) -> list[str]:
    """Identify recently changed files via git."""
    from wisp.git_context import _run_git

    rc, out, _ = _run_git(
        ["diff", "--name-only", f"--since=@{since_minutes} minutes ago"],
        workspace,
    )
    if rc == 0 and out.strip():
        return [line.strip() for line in out.strip().split("\n") if line.strip()]

    # Fallback: check unstaged changes
    rc, out, _ = _run_git(["status", "--porcelain"], workspace)
    if rc == 0:
        files = []
        for line in out.strip().split("\n"):
            if len(line) > 3:
                files.append(line[3:].strip())
        return files

    return []


# ── Main Diagnosis Engine ────────────────────────────────────────────────

def diagnose(output: str, workspace: str = ".") -> Diagnosis:
    """Analyze error output and return a structured diagnosis.

    Args:
        output: The error output (traceback, test failure, etc.)
        workspace: Project directory for correlating with recent changes

    Returns:
        Diagnosis object with error type, suggestion, and likely cause.
    """
    if not output or not output.strip():
        return Diagnosis(error_type="None", suggestion="No error output to analyze.")

    diag = Diagnosis()
    diag.message = extract_error_message(output)

    # Parse traceback location
    failing_file, failing_line, func_name = parse_traceback(output)
    diag.failing_file = failing_file
    diag.failing_line = failing_line

    # Special handling for test failures (check first — takes precedence)
    if "FAILED" in output and "::" in output:
        # Match patterns like: tests/test_app.py::test_login FAILED
        test_match = re.search(r"([\w/_.]+::\w+)\s+FAILED", output)
        if not test_match:
            # Fallback: FAILED tests/test_app.py::test_login
            test_match = re.search(r"FAILED\s+([\w/_.]+::\w+)", output)
        if test_match:
            diag.error_type = "TestFailure"
            diag.likely_cause = f"Test {test_match.group(1)} failed"
            diag.suggestion = "Review the test assertion and the implementation it tests. Check for recent changes to related files."
            # Still try to find underlying error for more detail
            for error_name, config in _ERROR_PATTERNS.items():
                if error_name == "AssertionError":
                    continue
                match = config["pattern"].search(output)
                if match:
                    diag.suggestion += f"\n   Underlying error: {config['cause']}. {config['suggest'](match)}"
                    break
            return diag

    # Special handling for pytest collection errors
    if "ERROR collecting" in output:
        collect_match = re.search(r"ERROR collecting\s+(.+)", output)
        if collect_match:
            diag.error_type = "TestCollectionError"
            diag.likely_cause = f"Cannot collect tests from {collect_match.group(1)}"
            diag.suggestion = "Check for syntax errors or import failures in the test file."
            return diag

    # Classify error type
    for error_name, config in _ERROR_PATTERNS.items():
        match = config["pattern"].search(output)
        if match:
            diag.error_type = error_name
            diag.suggestion = config["suggest"](match)
            diag.likely_cause = config["cause"]
            break

    # Correlate with recent changes
    if failing_file:
        diag.related_files.append(failing_file)

    changed = identify_changed_files(workspace)
    if changed:
        # Find overlap or proximity
        for cf in changed:
            if cf not in diag.related_files:
                diag.related_files.append(cf)

    return diag


def diagnose_tool_error(tool_name: str, args: dict, error: str, workspace: str = ".") -> Diagnosis:
    """Diagnose an error from a specific tool call.

    Args:
        tool_name: Name of the tool that failed (e.g., 'run_bash', 'edit_file')
        args: Arguments passed to the tool
        error: The error message/exception string
        workspace: Project directory

    Returns:
        Diagnosis with tool-specific context.
    """
    diag = diagnose(error, workspace)

    # Add tool-specific context
    if tool_name == "edit_file":
        diag.suggestion += "\n   The edit_file tool requires old_text to match exactly. Check for whitespace, line endings, or the text may have already been changed."
        diag.likely_cause += " (edit_file mismatch)"
    elif tool_name == "write_file":
        diag.suggestion += "\n   Ensure the directory exists and you have write permissions."
    elif tool_name == "run_bash":
        cmd = args.get("command", "")
        if "pytest" in cmd or "python -m pytest" in cmd:
            diag.severity = "error"
            diag.suggestion += "\n   Run 'pytest -xvs' for more verbose output, or check the specific failing test."

    return diag


def format_diagnosis_block(diagnoses: list[Diagnosis]) -> str:
    """Format multiple diagnoses as a system prompt block."""
    if not diagnoses:
        return ""

    lines = ["## Error Analysis"]
    for d in diagnoses:
        lines.append(f"\n{d.format()}")

    lines.append("")
    lines.append("Focus on fixing the root cause before proceeding.")
    return "\n".join(lines)
