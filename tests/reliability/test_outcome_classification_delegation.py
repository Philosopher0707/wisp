"""The outcome-classification violation is fixed, and the four non-violations hold.

Context: `PHASE_OUTCOME_CLASSIFICATION_VIOLATION.md`.

`wisp/core/stateless.py::_tool_result_output` compared the executor's envelope
status to the literal `"ok"` in two branches — a second classifier for a
vocabulary this module does not own. The canonical guard
(`test_outcome_classification_authority.py::test_no_module_reimplements_tool_result_status_classification`)
caught it and was **RED at HEAD** from `ade4dc6` (POST-M13 execution semantics)
until this change; it was invisible because the Phase-10 block that would have
run it aborted at collection (`httpx` absent — `PHASE_CORPUS_INTEGRITY.md` §2.1).

The fix is a delegation to `core.events.is_error_outcome`, the binary view of
the one outcome classifier. These tests pin the delegation, and assert the four
non-violations the decision names. Every one is a property read from the AST or
from the public surface — never a scan of the text that describes it
(`CONTEXT.md` §10: a check over Python must parse it).
"""

from __future__ import annotations

import ast
import inspect
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]

STATELESS = REPO / "wisp/core/stateless.py"
RUNTIME = REPO / "wisp/core/runtime.py"
SESSION = REPO / "wisp/core/session.py"
TOOL_EXECUTOR = REPO / "wisp/tool_executor.py"

#: The delegating function, and the classifier it must call.
_DELEGATING_FUNCTION = "_tool_result_output"
_CANONICAL_PREDICATE = "is_error_outcome"

#: The two envelope branches (a dict, and a JSON string that parses to one).
#: Both must delegate; one is the defect half-migrated.
_EXPECTED_DELEGATIONS = 2


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(path: pathlib.Path, name: str) -> ast.AST:
    for node in ast.walk(_tree(path)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path.relative_to(REPO)}")


# ── The fix: the success test is delegated, in both branches ──────────

def test_the_success_test_is_delegated():
    """`_tool_result_output` asks the canonical classifier, twice.

    A count, not a presence check: the defect was that **one** of two
    structurally identical branches still compared the literal, and a
    `"is_error_outcome" in src` string scan would have been satisfied by the
    docstring that names it (`CONTEXT.md` §10 — F77's shape).
    """
    fn = _function(STATELESS, _DELEGATING_FUNCTION)
    calls = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == _CANONICAL_PREDICATE
    ]
    assert len(calls) == _EXPECTED_DELEGATIONS, (
        f"{_DELEGATING_FUNCTION} must delegate the success test in both envelope "
        f"branches; found {len(calls)} call(s) to {_CANONICAL_PREDICATE}()"
    )


def test_no_literal_status_comparison_survives_in_the_engine():
    """The shape the guard exists for, re-asserted at the site of the defect.

    Independent of the repo-wide scan in `test_outcome_classification_authority.py`
    on purpose: that scan's subject is a collection, and a collection scan is the
    check that can pass by reaching nothing. This one names the file.
    """
    offenders: list[int] = []
    for node in ast.walk(_tree(STATELESS)):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        is_status_get = (
            isinstance(left, ast.Call)
            and isinstance(left.func, ast.Attribute)
            and left.func.attr == "get"
            and bool(left.args)
            and isinstance(left.args[0], ast.Constant)
            and left.args[0].value == "status"
        )
        if not is_status_get:
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and comparator.value in ("ok", "error"):
                offenders.append(node.lineno)
    assert not offenders, (
        "wisp/core/stateless.py compares a tool-result envelope status to its "
        f"literals again at line(s) {offenders} — delegate to "
        "wisp.core.events.is_error_outcome instead"
    )


# ── The four non-violations, asserted not merely stated ───────────────

def test_non_violation_1_turn_authorities_are_untouched():
    """`turn_succeeded`, `VerificationFloorGuard` and `goal.PRECEDENCE`.

    `PRECEDENCE` is resolved **by content** (ADR-0049 R1): the ADR-0035 and
    ADR-0047 row numbers are historical, so an index alone is not the property.
    """
    from wisp.core.goal import PRECEDENCE

    assert len(PRECEDENCE) == 8, "the canonical precedence table is eight rows 0-7"
    assert [row[0] for row in PRECEDENCE] == list(range(8))
    outcome_at = {row[0]: row[2] for row in PRECEDENCE}
    assert outcome_at[3] == "GOAL_FAILED"
    assert outcome_at[4] == "GOAL_FAILED" and "no P3 PASS" in PRECEDENCE[4][1], (
        "row 4 is the fatal clause, bounded by the P3 PASS escape (ADR-0047 R1)"
    )
    assert outcome_at[5] == "GOAL_STAGNATED"
    assert outcome_at[6] == "GOAL_MET"
    assert outcome_at[7] == "GOAL_UNVERIFIED"

    from wisp.core.verification import VerificationFloorGuard

    for method in ("note_tool_result", "rejection", "resolved", "reset_turn"):
        assert callable(getattr(VerificationFloorGuard, method, None)), (
            f"VerificationFloorGuard.{method} left the class"
        )

    # `turn_succeeded` is a PROJECTION of terminal evidence, never its own
    # predicate (13-H5). Parse the assignment; a scan would read the comment
    # three lines above it that says the same thing.
    projection = None
    for node in ast.walk(_tree(RUNTIME)):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "turn_succeeded" for t in node.targets):
            continue
        value = node.value
        if (isinstance(value, ast.Compare) and len(value.ops) == 1
                and isinstance(value.ops[0], ast.Is)
                and isinstance(value.comparators[0], ast.Attribute)
                and value.comparators[0].attr == "SUCCEEDED"):
            projection = node
    assert projection is not None, (
        "`turn_succeeded` is no longer `_goal_outcome is TerminalOutcome.SUCCEEDED` "
        "— it is a projection of terminal evidence, not a second predicate"
    )


def test_non_violation_2_the_taxonomy_public_surface_is_unchanged():
    """`core.events`'s classifier signature, vocabulary and classification table.

    **The status table now has ONE owner.** This test used to re-list the seven keys, so
    the taxonomy had two records — and when ADR-0074 added `NO_APPROVER` and
    `BUDGET_EXCEEDED` (R2: *"the taxonomy grows by decision, not by drift"*), the pin in
    `test_f8_published_status.py` was updated and this one went red. It now **reads** that
    pin rather than restating it — the same move the migration made for `_VERIFY_TOOLS`
    (*"imported from the authority rather than re-listed"*) — so a taxonomy change cannot
    update one record and miss the other.

    What stays here is the part *this* file is about: the classifier's signature, the
    class vocabulary the delegation leans on, and the two structural properties
    (totality, and `ok` being the only success).
    """
    from wisp.core import events as ev

    from tests.reliability.test_f8_published_status import (
        DENIAL_STATUSES_ADDED_SINCE,
        DENIAL_STATUSES_BEFORE,
    )

    assert list(inspect.signature(ev.classify_result).parameters) == ["result"], (
        "classify_result's signature moved"
    )
    assert set(ev.OutcomeClass.__members__) == {
        "SUCCESS", "ERROR", "DENIAL", "POLICY_DENIAL",
        "TIMEOUT", "CANCELLATION", "INVALID", "UNKNOWN",
    }, "the OutcomeClass vocabulary moved"

    assert set(ev.OUTCOME_BY_STATUS) == (
        DENIAL_STATUSES_BEFORE | DENIAL_STATUSES_ADDED_SINCE | {"ok", "error"}
    ), (
        "the status -> class table disagrees with its owner in "
        "test_f8_published_status.py. The taxonomy may grow, but only by ADR-0074 R4's "
        "route: add the status to DENIAL_STATUSES_ADDED_SINCE and write an ADR that names "
        "it — never by editing one of the two records."
    )

    # Total by test: every class is either a mapped value or the fallback. A
    # class with no route to it is a class the classifier can never return.
    mapped = set(ev.OUTCOME_BY_STATUS.values())
    assert mapped | {ev.OutcomeClass.UNKNOWN} == set(ev.OutcomeClass)

    # And `ok` is still the only success — the property the delegation leans on.
    assert [s for s, c in ev.OUTCOME_BY_STATUS.items() if c is ev.OutcomeClass.SUCCESS] == ["ok"]


def test_non_violation_3_the_engine_gate_chain_is_unchanged():
    """`policy_hard_deny` -> `authorize()` -> the approval test, **parsed**.

    Mirrors ADR-0055 R8's pin. Duplicated here rather than imported so that this
    file fails on its own if the chain moves — the defect this mission fixed was
    in the engine, so the engine's own invariants belong in its neighbourhood.
    """
    fn = _function(TOOL_EXECUTOR, "execute")
    first: dict[str, int] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name in ("policy_hard_deny", "authorize", "_get_write_tools"):
            first.setdefault(name, node.lineno)

    assert set(first) == {"policy_hard_deny", "authorize", "_get_write_tools"}, (
        f"a gate left ToolExecutor.execute: {sorted(first)}"
    )
    assert first["policy_hard_deny"] < first["authorize"] < first["_get_write_tools"], (
        f"the gate chain was re-ordered: {first}"
    )


def test_non_violation_4_the_tool_reply_shape_is_unchanged():
    """`Session.apply`'s TOOL_RESULT reply — the live transcript shape (ADR-0029).

    `{role, content}` plus a **conditional** `tool_call_id`. A literal third key
    is the drift the M9 note describes: the journal would stop reproducing
    `messages` exactly, and `reconstruct()` would depend on which source it read.
    """
    fn = _function(SESSION, "apply")

    # `reply: dict[str, Any] = {...}` is an AnnAssign, not an Assign.
    reply: ast.Dict | None = None
    for node in ast.walk(fn):
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign):
            target, value = (node.targets[0] if len(node.targets) == 1 else None), node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id == "reply" and isinstance(value, ast.Dict):
            reply = value

    assert reply is not None, "Session.apply no longer builds a `reply` dict literal"
    literal_keys = {k.value for k in reply.keys if isinstance(k, ast.Constant)}
    assert literal_keys == {"role", "content"}, (
        f"the live tool-reply shape changed: {sorted(literal_keys)}"
    )

    conditional = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Subscript)
            and isinstance(t.value, ast.Name)
            and t.value.id == "reply"
            and getattr(t.slice, "value", None) == "tool_call_id"
            for t in node.targets
        )
    ]
    assert len(conditional) == 1, (
        "the conditional `tool_call_id` pairing key moved — it is added only when "
        "the writer supplied one, never as a literal key"
    )
