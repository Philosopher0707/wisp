"""Deterministic review checks: no model, no network, the same answer for the same diff.

Each check reads the parsed diff (and, for the parse check, the post-image of a changed file) and returns findings that stand on text the harness can
point at. Only established evidence blocks: secrets, conflict markers and files that do not parse. Anything a check could not look at is recorded in
`CheckContext.gaps`, which the engine turns into an `incomplete` verdict: unknown is not clean.

Every quote is scrubbed with the secret gate before it leaves this module, so a review (and any comment built from it) cannot repeat a secret.
"""

from __future__ import annotations

import ast
import json
import posixpath
import re
import tomllib
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from wisp.core.gates import secrets as secret_gate
from wisp.core.gates.deps import CONFIG_NAMES, is_manifest_path
from wisp.review.diff import FileDiff
from wisp.review.types import Finding, Severity

MAX_FILES = 40
MAX_CHANGED_LINES = 1500
MAX_MISSING_TEST_FINDINGS_PER_FILE = 5
MAX_PARSE_BYTES = 1_000_000
MAX_TEST_FINDINGS = 10
QUOTE_CHARS = 200


@dataclass
class CheckContext:
    #: Text of a changed file after the change, or None when it cannot be read (a PR whose head is not fetched).
    read_post_image: Callable[[str], str | None] = lambda path: None
    #: Whether any test already mentions a symbol; None means the tests could not be searched, so nothing is claimed.
    symbol_tested: Callable[[str], bool] | None = None
    #: What no check could look at. A non-empty list makes the verdict `incomplete`.
    gaps: list[str] = field(default_factory=list)
    #: Runs the tests a change affects and returns a summary (failed, errors, total, passed, results, stdout); None means the tests are not run (the default).
    run_tests: Callable[[list[str]], Any] | None = None


Check = Callable[[Sequence[FileDiff], CheckContext], list[Finding]]


def safe_quote(text: str) -> str:
    return secret_gate.scrub(text.strip()).text[:QUOTE_CHARS]


# ── what a path is ───────────────────────────────────────────────────────────

def is_test_path(path: str) -> bool:
    lowered = path.lower()
    base = posixpath.basename(lowered)
    return (
        (base.startswith("test_") and base.endswith(".py"))
        or base.endswith(("_test.py", "_test.go"))
        or ".test." in base
        or ".spec." in base
        or any(part in ("tests", "test", "__tests__") for part in lowered.split("/")[:-1])
    )


def _is_ci_or_config(path: str) -> bool:
    parts = path.rstrip("/").split("/")
    return posixpath.basename(path) in CONFIG_NAMES or ".circleci" in parts or any(parts[i:i + 2] == [".github", "workflows"] for i in range(len(parts) - 1))


def _reviewable(files: Sequence[FileDiff]) -> list[FileDiff]:
    return [f for f in files if not f.binary]


_DOC_SUFFIXES = (".md", ".rst", ".txt")


def _inside_string(line: str, position: int) -> bool:
    """Whether `position` falls inside a quoted string on this line. A pattern that only appears in a string is data (a test about the pattern, a message), not code."""
    quote = ""
    i = 0
    while i < position:
        char = line[i]
        if quote:
            if char == "\\":
                i += 2
                continue
            if char == quote:
                quote = ""
        elif char in "'\"`":
            quote = char
        i += 1
    return bool(quote)


def _found_in_code(pattern: re.Pattern[str], text: str) -> bool:
    return any(not _inside_string(text, match.start()) for match in pattern.finditer(text))


# ── secrets ──────────────────────────────────────────────────────────────────

_TOKEN_SHAPED = re.compile(r"[A-Za-z0-9+/_=.\-]{20,}")


def _certain_kinds(line: str) -> list[str]:
    """Kinds specific enough to block on. The gate merges overlapping spans, so a vendor token after `token:` comes back as a generic assignment or as `entropy`;
    the bare token is checked on its own so its vendor pattern is not hidden by the words around it."""
    kinds = set(secret_gate.high_confidence_kinds(line))
    for token in _TOKEN_SHAPED.findall(line):
        kinds.update(secret_gate.high_confidence_kinds(token))
    return sorted(kinds)


def _weak_is_noise(path: str, text: str, findings: Sequence[Any]) -> bool:
    """Weak shapes (a credential word assigned something, a high-entropy token) are for production code. In tests and docs they are almost always fixtures and prose, and a
    credential word assigned an expression (`token = make_token()`, `KEY = os.environ[...]`) is code that handles a secret, not a secret. A vendor token is never weak."""
    if is_test_path(path) or path.lower().endswith(_DOC_SUFFIXES):
        return True
    for found in findings:
        value = text[found.start:found.end]
        expression = any(c in value for c in "([{") or text[found.end:found.end + 1] in ("(", "[", "{")
        identifier = found.kind == "entropy" and value.replace("_", "").isalpha()  # a long snake_case name next to a credential word, not a token
        if not (expression or identifier):
            return False
    return True


def check_secrets(files: Sequence[FileDiff], ctx: CheckContext) -> list[Finding]:
    found: list[Finding] = []
    for fd in _reviewable(files):
        for number, text in fd.added_lines():
            result = secret_gate.scrub(text)
            if not result.findings:
                continue
            certain = _certain_kinds(text)
            if not certain and _weak_is_noise(fd.path, text, result.findings):
                continue
            kinds = certain or sorted({f.kind for f in result.findings})
            found.append(Finding(
                rule="secret", severity=Severity.BLOCK if certain else Severity.WARN, file=fd.path, line=number,
                quote=result.text.strip()[:QUOTE_CHARS], evidence="gate:secrets",
                message=f"added line looks like it contains a credential ({', '.join(kinds)}); the value is not shown here",
                suggestion="Remove it from the change and from history, and rotate it: a credential that was committed is compromised."))
    return found


# ── conflict markers ─────────────────────────────────────────────────────────

_MARKER = re.compile(r"^(?:<{7}|>{7})(?: .*)?$")


def check_conflict_markers(files: Sequence[FileDiff], ctx: CheckContext) -> list[Finding]:
    return [
        Finding("conflict-marker", Severity.BLOCK, fd.path, number, safe_quote(text), "unresolved merge conflict marker in an added line", "diff pattern",
                "Resolve the conflict and remove the marker.")
        for fd in _reviewable(files) for number, text in fd.added_lines() if _MARKER.match(text)
    ]


# ── files that do not parse ──────────────────────────────────────────────────

def _parse_error(path: str, content: str) -> tuple[int, str] | None:
    """(line, message) when `content` is not valid for the file's type, None when it is (or the type is not one we parse)."""
    if path.endswith(".py"):
        try:
            ast.parse(content, filename=path)
        except SyntaxError as exc:
            return exc.lineno or 0, exc.msg
        return None
    if path.endswith(".json"):
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            return exc.lineno, exc.msg
        return None
    if path.endswith(".toml"):
        try:
            tomllib.loads(content)
        except tomllib.TOMLDecodeError as exc:
            where = re.search(r"line (\d+)", str(exc))
            return int(where.group(1)) if where else 0, str(exc)
        return None
    return None


_PARSEABLE = (".py", ".json", ".toml")


def check_syntax(files: Sequence[FileDiff], ctx: CheckContext) -> list[Finding]:
    found: list[Finding] = []
    unreadable = 0
    for fd in _reviewable(files):
        if fd.status == "deleted" or not fd.path.endswith(_PARSEABLE):
            continue
        content = ctx.read_post_image(fd.path)
        if content is None:
            unreadable += 1
            continue
        if len(content.encode("utf-8", "replace")) > MAX_PARSE_BYTES:
            found.append(Finding("syntax-error", Severity.INFO, fd.path, 0, "", f"file is over {MAX_PARSE_BYTES} bytes and was not parsed", "size bound"))
            continue
        error = _parse_error(fd.path, content)
        if error is None:
            continue
        line, message = error
        lines = content.split("\n")
        quote = safe_quote(lines[line - 1]) if 0 < line <= len(lines) else ""
        found.append(Finding("syntax-error", Severity.BLOCK, fd.path, line, quote, f"the file does not parse after this change: {message}", "post-image parse"))
    if unreadable:
        ctx.gaps.append(f"syntax: {unreadable} changed .py/.json/.toml file(s) could not be read, so they were not parsed")
    return found


# ── tests made weaker ────────────────────────────────────────────────────────

_WEAKENING = (
    re.compile(r"^\s*@pytest\.mark\.(?:skip|xfail)\b"),
    re.compile(r"^\s*(?:pytest\.skip|unittest\.skip\w*|self\.skipTest)\("),
    re.compile(r"^\s*@unittest\.skip"),
    re.compile(r"^\s*assert\s+(?:True|1)\s*(?:#.*)?$"),
    re.compile(r"\b(?:it|test|describe)\.(?:skip|todo)\b|\bx(?:it|describe|test)\("),
    re.compile(r"\bt\.Skip(?:f|Now)?\("),
    re.compile(r"\bexpect\(\s*true\s*\)\.toBe\(\s*true\s*\)"),
)
_ASSERTION = re.compile(r"\bassert\b|\bself\.assert\w*\(|\bexpect\(|\.should\b|\bassert_(?:eq|ne)!|\bassert!\(|\brequire\.\w+\(|\bassert\.\w+\(")


def check_weakened_tests(files: Sequence[FileDiff], ctx: CheckContext) -> list[Finding]:
    found: list[Finding] = []
    for fd in _reviewable(files):
        if not is_test_path(fd.path):
            continue
        if fd.status == "deleted":
            found.append(Finding("test-removed", Severity.WARN, fd.path, 0, "", "a test file was deleted", "diff status", "Say why the tests are no longer needed, or move them."))
            continue
        for number, text in fd.added_lines():
            if any(_found_in_code(pattern, text) for pattern in _WEAKENING):
                found.append(Finding("test-weakened", Severity.WARN, fd.path, number, safe_quote(text), "this added line skips a test or makes an assertion vacuous", "diff pattern",
                                     "A skipped or tautological test hides a failure instead of fixing it."))
        removed = [text for _n, text in fd.removed_lines() if _ASSERTION.search(text)]
        added = [text for _n, text in fd.added_lines() if _ASSERTION.search(text)]
        if len(removed) > len(added):
            found.append(Finding("test-weakened", Severity.WARN, fd.path, 0, safe_quote(removed[0]),
                                 f"{len(removed) - len(added)} assertion(s) removed net ({len(removed)} removed, {len(added)} added)", "diff count",
                                 "Fewer assertions means the tests now accept more behaviour; confirm that is intended."))
    return found


# ── new public code that no test mentions ────────────────────────────────────

_PUBLIC = {
    ".py": re.compile(r"^(?:async\s+def|def|class)\s+([A-Za-z][A-Za-z0-9_]*)\b"),
    ".ts": re.compile(r"^export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let)\s+([A-Za-z_$][\w$]*)"),
    ".go": re.compile(r"^func\s+(?:\([^)]*\)\s+)?([A-Z][A-Za-z0-9_]*)\s*\("),
    ".rs": re.compile(r"^\s*pub\s+(?:async\s+)?(?:fn|struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)"),
}
_PUBLIC[".tsx"] = _PUBLIC[".js"] = _PUBLIC[".jsx"] = _PUBLIC[".mjs"] = _PUBLIC[".ts"]


def check_missing_tests(files: Sequence[FileDiff], ctx: CheckContext) -> list[Finding]:
    if any(is_test_path(fd.path) and fd.status != "deleted" for fd in files):
        return []
    if ctx.symbol_tested is None:
        ctx.gaps.append("missing-tests: the tests could not be searched, so new public symbols were not checked against them")
        return []
    found: list[Finding] = []
    for fd in _reviewable(files):
        pattern = _PUBLIC.get(posixpath.splitext(fd.path)[1])
        if pattern is None or is_test_path(fd.path) or fd.status == "deleted":
            continue
        reported = 0
        for number, text in fd.added_lines():
            match = pattern.match(text)
            if match is None or ctx.symbol_tested(match.group(1)):
                continue
            found.append(Finding("no-test", Severity.WARN, fd.path, number, safe_quote(text), f"new public symbol '{match.group(1)}' is not mentioned by any test, and the change adds none",
                                 "diff pattern and a search of the tests", "Add a test that exercises it."))
            reported += 1
            if reported >= MAX_MISSING_TEST_FINDINGS_PER_FILE:
                break
    return found


# ── CI, project config and dependencies ──────────────────────────────────────

def check_config_and_dependencies(files: Sequence[FileDiff], ctx: CheckContext) -> list[Finding]:
    found: list[Finding] = []
    for fd in files:
        if _is_ci_or_config(fd.path):
            found.append(Finding("ci-config-changed", Severity.WARN, fd.path, 0, "", "CI or project configuration changed; it decides what runs with the repository's credentials and what is committed", "path class",
                                 "Review this file's change on its own."))
        elif is_manifest_path(fd.path):
            found.append(Finding("manifest-changed", Severity.INFO, fd.path, 0, "", "a dependency manifest or lockfile changed", "path class"))
    return found


# ── debug leftovers ──────────────────────────────────────────────────────────

_DEBUG = re.compile(r"\bbreakpoint\(\)|\bpdb\.set_trace\(\)|^\s*import\s+i?pdb\b|^\s*debugger\s*;?\s*$")
_CODE = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs")


def check_debug_leftovers(files: Sequence[FileDiff], ctx: CheckContext) -> list[Finding]:
    return [
        Finding("debug-leftover", Severity.WARN, fd.path, number, safe_quote(text), "a debugger hook was added", "diff pattern", "Remove it before merging.")
        for fd in _reviewable(files) if fd.path.endswith(_CODE) for number, text in fd.added_lines() if _found_in_code(_DEBUG, text)
    ]


# ── size ─────────────────────────────────────────────────────────────────────

def check_size(files: Sequence[FileDiff], ctx: CheckContext) -> list[Finding]:
    changed = sum(fd.added + fd.removed for fd in files)
    if len(files) <= MAX_FILES and changed <= MAX_CHANGED_LINES:
        return []
    return [Finding("large-change", Severity.INFO, "", 0, "", f"{len(files)} files and {changed} changed lines; a change this size is reviewed badly by people and by tools", "diff size",
                    "Consider splitting it.")]


# ── the tests the change affects, run by the harness (opt-in) ────────────────

def check_affected_tests(files: Sequence[FileDiff], ctx: CheckContext) -> list[Finding]:
    """A failing test is established evidence, so it blocks. Runs only when the caller supplied `ctx.run_tests`."""
    if ctx.run_tests is None:
        return []
    changed = [fd.path for fd in files if fd.status != "deleted" and not fd.binary]
    if not changed:
        return []
    summary = ctx.run_tests(changed)
    note = str(getattr(summary, "stdout", "") or "").strip()
    if note.startswith("Affected-test lookup skipped"):
        ctx.gaps.append(f"tests: {note[:200]}")
        return []
    results = list(getattr(summary, "results", []) or [])
    failing = [r for r in results if getattr(r, "outcome", "") in ("failed", "error")]
    failed_count = int(getattr(summary, "failed", 0) or 0) + int(getattr(summary, "errors", 0) or 0)
    if failing or failed_count:
        if not failing:
            return [Finding("tests-failing", Severity.BLOCK, "", 0, "", f"{failed_count} affected test(s) failed when run by the harness", "tests run by the harness")]
        return [
            Finding("tests-failing", Severity.BLOCK, "", 0, safe_quote((str(getattr(r, "traceback", "") or "").strip().splitlines() or [""])[-1]),
                    f"{r.test_id} {r.outcome} when run by the harness", "tests run by the harness", "Fix the change or the test; do not skip it.")
            for r in failing[:MAX_TEST_FINDINGS]
        ]
    total = int(getattr(summary, "total", 0) or 0)
    if total == 0:
        return [Finding("tests-none", Severity.INFO, "", 0, "", "no test is affected by this change, by the import graph", "tests run by the harness")]
    return [Finding("tests-passed", Severity.INFO, "", 0, "", f"{total} affected test(s) ran and passed", "tests run by the harness")]


ALL_CHECKS: tuple[Check, ...] = (
    check_secrets, check_conflict_markers, check_syntax, check_weakened_tests, check_missing_tests, check_config_and_dependencies, check_debug_leftovers, check_size,
)


def run_checks(files: Sequence[FileDiff], ctx: CheckContext, checks: Sequence[Check] = ALL_CHECKS) -> tuple[list[Finding], list[str]]:
    """Run every check; one that raises is reported and does not stop the others (the verdict then cannot be `clean`)."""
    findings: list[Finding] = []
    errors: list[str] = []
    for check in checks:
        try:
            findings.extend(check(files, ctx))
        except Exception as exc:  # noqa: BLE001 — a broken check must not hide the others, and must not look like a pass
            errors.append(f"{check.__name__}: {type(exc).__name__}: {exc}")
    findings.sort(key=lambda f: (f.severity.rank, f.file, f.line, f.rule))
    return findings, errors
