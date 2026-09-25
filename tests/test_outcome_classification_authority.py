"""Target B — one semantic authority for outcome classification.

Context (see PHASE_10_AUTHORITY_CLOSURE_AUDIT.md, Target B):

Before Phase 10 there was no single authority answering *"what kind of outcome
is this tool result?"*. Three predicates existed, and the denial detector in
the subagent orchestrator matched **prose** (`"[denied"`, `"not authorized"`)
against the error text — which **never matched a structured status** such as
`POLICY_DENIED`. So a structured denial arriving as text was invisible to the
"denials must never auto-retry" rule.

The authority now lives in `wisp.core.events`, the module that owns the status
taxonomy:

    OutcomeClass            the taxonomy
    OUTCOME_BY_STATUS       status -> class
    classify_result()       the single classifier
    is_error_outcome()      the binary view
    is_terminal_outcome()   retry-terminal view
    is_denial_text()        text-aware denial detection

These tests pin the authority, the equivalence of the migrated predicate, and
the fact that a second classifier cannot appear unnoticed.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from wisp.core import events as ev
from wisp.core.events import (
    DENIAL_APPROVAL_TIMEOUT,
    DENIAL_CANCELLED,
    DENIAL_POLICY_DENIED,
    DENIAL_SCHEMA_INVALID,
    DENIAL_USER_DENIED,
    OUTCOME_BY_STATUS,
    TERMINAL_OUTCOME_CLASSES,
    OutcomeClass,
    classify_result,
    is_denial_outcome,
    is_denial_text,
    is_error_outcome,
    is_terminal_outcome,
)
from wisp.transport.renderer import result_is_error

REPO = pathlib.Path(__file__).resolve().parent.parent

ALL_DENIAL_STATUSES = (
    DENIAL_POLICY_DENIED, DENIAL_USER_DENIED, DENIAL_APPROVAL_TIMEOUT,
    DENIAL_CANCELLED, DENIAL_SCHEMA_INVALID,
)


# ── The taxonomy is complete and self-consistent ─────────────────────

@pytest.mark.parametrize("status", ALL_DENIAL_STATUSES)
def test_every_denial_status_is_classified(status):
    """A denial constant with no class is how a status becomes invisible."""
    assert status in OUTCOME_BY_STATUS, (
        f"{status} is a canonical denial status but has no OutcomeClass"
    )
    assert OUTCOME_BY_STATUS[status] is not OutcomeClass.SUCCESS


def test_every_class_in_the_mapping_is_a_real_class():
    for status, cls in OUTCOME_BY_STATUS.items():
        assert isinstance(cls, OutcomeClass), f"{status} maps to {cls!r}"


def test_ok_is_the_only_success():
    successes = [s for s, c in OUTCOME_BY_STATUS.items() if c is OutcomeClass.SUCCESS]
    assert successes == ["ok"]


def test_terminal_classes_cover_every_denial_status():
    for status in ALL_DENIAL_STATUSES:
        assert OUTCOME_BY_STATUS[status] in TERMINAL_OUTCOME_CLASSES, (
            f"{status} is a verdict and must be terminal for retry"
        )


def test_success_and_plain_error_are_not_terminal_for_retry():
    """A transient ERROR may legitimately be retried."""
    assert OutcomeClass.SUCCESS not in TERMINAL_OUTCOME_CLASSES
    assert OutcomeClass.ERROR not in TERMINAL_OUTCOME_CLASSES


# ── The migrated predicate is behaviourally identical ────────────────

def _original_predicate(result):
    """The exact implementation `result_is_error` had before the migration."""
    if isinstance(result, dict):
        return result.get("status", "ok") != "ok"
    if isinstance(result, str):
        text = result.strip()
        if text.startswith("{"):
            try:
                import json as _json
                parsed = _json.loads(text)
                if isinstance(parsed, dict):
                    return parsed.get("status", "ok") != "ok"
            except (ValueError, TypeError):
                pass
        return text.startswith(("Error", "[Error", "[WEB_FETCH_FAILED]",
                                "[WEB_FETCH_BLOCKED]", "[Denied", "[Blocked",
                                "[Cancelled", "ToolError:", "Unexpected error:"))
    return False


EQUIVALENCE_CORPUS = [
    {"status": "ok"}, {"status": "error"}, {"status": "POLICY_DENIED"},
    {"status": "USER_DENIED"}, {"status": "APPROVAL_TIMEOUT"},
    {"status": "CANCELLED"}, {"status": "SCHEMA_INVALID"},
    {"status": "weird"}, {}, {"no_status": 1},
    "", "ok output", "Error: boom", "[Error] x", "[Denied] nope", "[Blocked] x",
    "[Cancelled] x", "ToolError: x", "Unexpected error: x",
    "[WEB_FETCH_FAILED] x", "[WEB_FETCH_BLOCKED] x",
    '{"status": "ok"}', '{"status": "error"}', '{"status": "POLICY_DENIED"}',
    "{bad json", "   ", None, 42, ["x"],
]


@pytest.mark.parametrize("result", EQUIVALENCE_CORPUS)
def test_migrated_predicate_is_equivalent_to_the_original(result):
    assert result_is_error(result) == _original_predicate(result), (
        "the canonical predicate changed behaviour for "
        f"{result!r} — migration must be behaviour-preserving"
    )


def test_renderer_delegates_rather_than_reimplementing():
    src = (REPO / "wisp/transport/renderer.py").read_text(encoding="utf-8")
    assert "is_error_outcome" in src, "renderer no longer delegates"
    assert 'get("status", "ok") != "ok"' not in src, (
        "renderer re-derived the status comparison instead of delegating"
    )


# ── The denial-detection defect is fixed ─────────────────────────────

@pytest.mark.parametrize("status", ALL_DENIAL_STATUSES)
def test_structured_denial_statuses_are_detected_in_text(status):
    """The regression: none of these matched the old prose markers."""
    assert is_denial_text(f"[{status}] refused") is True
    assert is_denial_text(status) is True


@pytest.mark.parametrize("prose", [
    "[denied] nope", "denied by policy", "approval denied", "not authorized",
])
def test_prose_denials_are_still_detected(prose):
    """Behaviour preserved: the markers that existed still work."""
    assert is_denial_text(prose) is True


def test_a_transient_transport_error_is_not_a_denial():
    """The axes must stay distinct — this is why `_is_transient` remains."""
    for transient in ("connection reset", "429 Too Many Requests", "rate limit"):
        assert is_denial_text(transient) is False


def test_denial_detection_handles_empty_input():
    assert is_denial_text(None) is False
    assert is_denial_text("") is False


def _mentions_name_in_code(path: pathlib.Path, name: str) -> list[int]:
    """Line numbers where `name` appears as an **identifier**.

    AST-based, so a comment or docstring that *discusses* the removed list does
    not trip the guard. That distinction matters and was learned the hard way:
    the original form of this test was a whole-file `not in src` grep, and M12's
    explanatory comment — which names the list to say why it was removed — made
    it fail. A guard that forbids documenting the defect it guards against is
    one people delete.
    """
    hits: list[int] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            hits.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == name:
            hits.append(node.lineno)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == name:
                    hits.append(node.lineno)
    return hits


def test_subagent_denial_detector_delegates():
    src = (REPO / "wisp/multi_agent/subagent_orchestrator.py").read_text(encoding="utf-8")
    assert "is_denial_text" in src, "the orchestrator no longer delegates"
    offenders = _mentions_name_in_code(
        REPO / "wisp/multi_agent/subagent_orchestrator.py", "_DENIAL_MARKERS")
    assert not offenders, (
        f"the orchestrator reintroduced its own prose-only denial markers at "
        f"lines {offenders}"
    )


def test_the_denial_marker_guard_is_not_vacuous():
    """The AST form must still catch a real reintroduction.

    An earlier whole-file grep was replaced because it matched its own
    documentation. That is only an improvement if the replacement still detects
    the thing — so the detector is run against a synthetic offender here.
    """
    probe = REPO / "tests" / "_tmp_denial_marker_probe.py"
    probe.write_text(
        'def f():\n'
        '    # a comment naming _DENIAL_MARKERS must NOT count\n'
        '    _DENIAL_MARKERS = ("nope",)\n'
        '    return _DENIAL_MARKERS\n',
        encoding="utf-8",
    )
    try:
        hits = _mentions_name_in_code(probe, "_DENIAL_MARKERS")
        assert hits, "the AST guard does not detect a real reintroduction"
        assert len(hits) >= 2, f"expected the assign and the use, got {hits}"
    finally:
        probe.unlink(missing_ok=True)


# ── Envelope classification ──────────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    ("ok", OutcomeClass.SUCCESS),
    ("error", OutcomeClass.ERROR),
    (DENIAL_POLICY_DENIED, OutcomeClass.POLICY_DENIAL),
    (DENIAL_USER_DENIED, OutcomeClass.DENIAL),
    (DENIAL_APPROVAL_TIMEOUT, OutcomeClass.TIMEOUT),
    (DENIAL_CANCELLED, OutcomeClass.CANCELLATION),
    (DENIAL_SCHEMA_INVALID, OutcomeClass.INVALID),
])
def test_envelope_classification(status, expected):
    assert classify_result({"status": status}) is expected


def test_unknown_status_is_a_failure_not_a_success():
    """An unrecognised status must still read as failure — fail closed."""
    assert classify_result({"status": "something-new"}) is OutcomeClass.UNKNOWN
    assert is_error_outcome({"status": "something-new"}) is True


def test_error_and_denial_are_both_failures_but_only_denial_is_terminal():
    assert is_error_outcome({"status": "error"}) is True
    assert is_terminal_outcome({"status": "error"}) is False
    assert is_error_outcome({"status": DENIAL_POLICY_DENIED}) is True
    assert is_terminal_outcome({"status": DENIAL_POLICY_DENIED}) is True


def test_denial_outcome_helper_covers_the_denial_set():
    for status in ALL_DENIAL_STATUSES:
        assert is_denial_outcome({"status": status}) is True
    assert is_denial_outcome({"status": "ok"}) is False
    assert is_denial_outcome({"status": "error"}) is False


def test_denial_result_builds_a_classifiable_envelope():
    """The builder and the classifier must agree — they are two halves of one
    contract."""
    for status in ALL_DENIAL_STATUSES:
        event = ev.denial_result("run_bash", status, "because")
        assert classify_result(event.data["result"]) is OUTCOME_BY_STATUS[status]


# ── Enforcement: a second classifier cannot appear unnoticed ─────────

#: Modules allowed to compare a *tool-result* status against the envelope's
#: success/failure literals. Only the taxonomy module may.
_TAXONOMY_OWNER = "wisp/core/events.py"


def _is_status_get(node: ast.AST) -> bool:
    """True for a `<something>.get("status", ...)` call."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "status"
    )


def _compares_tool_result_status(path: pathlib.Path) -> list[int]:
    """Find comparisons of a *tool-result envelope status* to its literals.

    The signature is `<x>.get("status", ...) == "ok" | "error"`. Merely
    comparing to the string "ok"/"error" is not enough — the codebase does
    that for event types, plan status, and health status, which are different
    vocabularies sharing an overloaded key name.
    """
    hits: list[int] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not _is_status_get(node.left):
            continue
        for comparator in node.comparators:
            if (isinstance(comparator, ast.Constant)
                    and comparator.value in ("ok", "error")):
                hits.append(node.lineno)
    return hits


def test_no_module_reimplements_tool_result_status_classification():
    scanned = 0
    offenders: dict[str, list[int]] = {}
    for py in (REPO / "wisp").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        scanned += 1
        rel = str(py.relative_to(REPO))
        if rel == _TAXONOMY_OWNER:
            continue
        hits = _compares_tool_result_status(py)
        if hits:
            offenders[rel] = hits
    # A check whose subject is a **collection** needs a non-empty floor: a scan
    # that reaches nothing passes vacuously and reports nothing (CONTEXT.md §10).
    # 380 modules under `wisp/` at HEAD; the floor is far below that and far
    # above zero, so it fires on a path change and not on a legitimate addition.
    assert scanned >= 100, (
        f"the scan reached only {scanned} module(s) — it is no longer scanning "
        "`wisp/`, so its silence is not evidence of anything"
    )
    assert not offenders, (
        "a module classifies a tool-result status by comparing "
        ".get(\"status\") to \"ok\"/\"error\" directly — classify through "
        f"wisp.core.events instead: {offenders}"
    )


def _function_node(path: pathlib.Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path.name}")


def _calls(path: pathlib.Path, func: str, callee: str) -> bool:
    """True when `func`'s body calls `callee` (AST, so docstrings don't count)."""
    node = _function_node(path, func)
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name) and f.id == callee:
                return True
            if isinstance(f, ast.Attribute) and f.attr == callee:
                return True
    return False


def _substring_status_test(path: pathlib.Path, func: str) -> bool:
    """True when `func` decides by substring-matching a status literal.

    Detects `'"status": "ok"' in x` / `'"status": "error"' not in x` — the
    truncation- and whitespace-sensitive pattern Phase 10 removed.
    """
    node = _function_node(path, func)
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Compare):
            continue
        ops = [type(o) for o in sub.ops]
        if not any(o in (ast.In, ast.NotIn) for o in ops):
            continue
        for operand in [sub.left, *sub.comparators]:
            if (isinstance(operand, ast.Constant) and isinstance(operand.value, str)
                    and '"status"' in operand.value):
                return True
    return False


def test_tool_executor_metrics_delegates():
    """The third classifier found in Phase 10."""
    path = REPO / "wisp/tool_executor.py"
    assert _calls(path, "_record_metrics", "is_error_outcome"), (
        "_record_metrics no longer delegates to the canonical classifier"
    )
    assert not _substring_status_test(path, "_record_metrics"), (
        "the substring-based success test returned to _record_metrics"
    )


def test_fetch_breaker_delegates():
    """The fourth classifier found in Phase 10."""
    path = REPO / "wisp/tool_executor.py"
    assert _calls(path, "_note_fetch_outcome", "is_error_outcome"), (
        "_note_fetch_outcome no longer delegates to the canonical classifier"
    )
    assert not _substring_status_test(path, "_note_fetch_outcome"), (
        "the truncated substring status test returned to _note_fetch_outcome"
    )


def test_metrics_success_agrees_with_the_canonical_classifier():
    """A successful plain-text result must not be recorded as an error.

    This was the live-metrics defect: most tools return plain text, and the
    old substring test called every one of them a failure.
    """
    from wisp.core.events import is_error_outcome
    for successful in ("def f(): return 1", "[exit code: 0]\n3 passed",
                       '{"status":"ok"}', {"status": "ok"}):
        assert is_error_outcome(successful) is False, (
            f"a successful result was classified as an error: {successful!r}"
        )
    for failed in ("Error: boom", "[Denied] nope", {"status": "POLICY_DENIED"},
                   '{"status": "error"}'):
        assert is_error_outcome(failed) is True


def test_the_taxonomy_is_defined_once():
    definers = []
    for py in (REPO / "wisp").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "OutcomeClass":
                definers.append(str(py.relative_to(REPO)))
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id == "OUTCOME_BY_STATUS":
                        definers.append(str(py.relative_to(REPO)))
    assert definers == [_TAXONOMY_OWNER, _TAXONOMY_OWNER], (
        f"OutcomeClass/OUTCOME_BY_STATUS must be defined exactly once: {definers}"
    )
