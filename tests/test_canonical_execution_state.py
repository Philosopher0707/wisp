"""Canonical execution state: the invariant that made F28 drift is now tested.

Context (see PHASE_CANONICAL_AUTHORITY_MAP.md / PHASE_CANONICAL_CONTRACT_FREEZE.md C1):

Wisp had four independent statements of "what state is this run in":

  1. wisp/runs/record.py          -- prose in the module docstring
  2. wisp/runs/store.py           -- _LEGACY_STATUS_IN (executable, but private)
  3. wisp/multi_agent/background.py -- _STATUS_TO_RUN_STATE (a second executable copy)
  4. wisp/multi_agent/background.py -- an inline display dict that rendered the
                                       same constant as "completed" again

and consumers compared raw strings against whichever vocabulary they learned:
`tools/subagent_tools.py` against "completed", `graph/cli.py` and `coding.py`
against "succeeded". No test asserted the vocabularies agreed, which is why
they didn't.

These tests make the canonical authority executable. They fail if a second
authority reappears.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from wisp.runs.record import (
    LEGACY_STATE_ALIASES,
    TERMINAL_STATES,
    RunRecord,
    RunState,
    coerce_state,
    is_legal,
    is_terminal,
)

REPO = pathlib.Path(__file__).resolve().parent.parent


# ── The translation is complete and correct ──────────────────────────

def test_canonical_values_coerce_to_themselves():
    for state in RunState:
        assert coerce_state(state) is state
        assert coerce_state(state.value) is state


def test_produced_vocabulary_maps_into_canonical():
    """The M1a produced vocabulary (background agents) is the historical
    source of the divergence: it calls terminal success "completed"."""
    assert coerce_state("completed") is RunState.SUCCEEDED
    assert coerce_state("running") is RunState.RUNNING
    assert coerce_state("failed") is RunState.FAILED
    assert coerce_state("cancelled") is RunState.CANCELLED


def test_pre_m3_vocabulary_maps_into_canonical():
    assert coerce_state("pending") is RunState.QUEUED


def test_alias_table_contents_are_explicit():
    """Pin the mapping itself so an edit that changes semantics is caught."""
    assert LEGACY_STATE_ALIASES == {
        "pending": RunState.QUEUED,
        "completed": RunState.SUCCEEDED,
    }


def test_unknown_state_fails_loud():
    """Fail loud, not silent — a producer drifting to a new vocabulary must
    surface, not be mis-classified as non-terminal."""
    with pytest.raises(ValueError, match="unknown run status"):
        coerce_state("finished")
    with pytest.raises(ValueError):
        coerce_state("")


# ── Every producer's terminal-success normalizes to ONE value ────────

def test_all_terminal_success_spellings_converge():
    """The invariant the codebase was missing: however a producer spells
    "it worked", it resolves to exactly one canonical state."""
    spellings = ["succeeded", "completed"]
    resolved = {coerce_state(s) for s in spellings}
    assert resolved == {RunState.SUCCEEDED}


def test_is_terminal_agrees_across_vocabularies():
    assert is_terminal("succeeded") is True
    assert is_terminal("completed") is True
    assert is_terminal("failed") is True
    assert is_terminal("cancelled") is True
    assert is_terminal("running") is False
    assert is_terminal("queued") is False
    assert is_terminal("awaiting_approval") is False


def test_terminal_set_is_exactly_three_states():
    assert TERMINAL_STATES == frozenset(
        {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED})


# ── Transitions are evaluated in the canonical vocabulary ────────────

def test_legality_accepts_produced_vocabulary():
    """A caller holding a produced-vocabulary value must still get a correct
    legality answer rather than a KeyError."""
    assert is_legal("completed", "running") is False   # terminal -> anything
    assert is_legal("running", "completed") is True
    assert is_legal("running", "failed") is True
    assert is_legal("succeeded", "running") is False


# ── The latent crash path is closed ──────────────────────────────────

def test_from_dict_accepts_legacy_status():
    """`RunRecord.from_dict` previously did a bare RunState(...) and would
    raise on a stored "completed" row (which supervisor.py writes)."""
    rec = RunRecord.from_dict({"run_id": "r1", "status": "completed"})
    assert rec.status is RunState.SUCCEEDED

    rec = RunRecord.from_dict({"run_id": "r2", "status": "pending"})
    assert rec.status is RunState.QUEUED


def test_from_dict_rejects_unknown_status():
    with pytest.raises(ValueError, match="unknown run status"):
        RunRecord.from_dict({"run_id": "r3", "status": "not-a-state"})


# ── Structural: no second authority may reappear ─────────────────────

def _source(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def test_store_no_longer_defines_its_own_mapping():
    """The store must delegate, not re-derive. This is the assertion that
    prevents the duplicate from silently returning."""
    src = _source("wisp/runs/store.py")
    assert "_LEGACY_STATUS_IN" not in src, (
        "wisp/runs/store.py re-introduced a private legacy-state mapping; "
        "the canonical authority is wisp.runs.record.LEGACY_STATE_ALIASES"
    )
    assert "def _coerce_status" not in src, (
        "wisp/runs/store.py re-introduced a private coercion function; "
        "use wisp.runs.record.coerce_state()"
    )


def test_background_manager_no_longer_keeps_a_status_map():
    src = _source("wisp/multi_agent/background.py")
    assert "_STATUS_TO_RUN_STATE" not in src, (
        "wisp/multi_agent/background.py re-derived the state mapping; "
        "use wisp.runs.record.coerce_state()"
    )


def test_legacy_alias_map_has_exactly_one_definition():
    """Exactly one module may define the legacy alias table."""
    definers = []
    for py in (REPO / "wisp").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == "LEGACY_STATE_ALIASES":
                    definers.append(str(py.relative_to(REPO)))
    assert definers == ["wisp/runs/record.py"], (
        f"LEGACY_STATE_ALIASES must be defined exactly once; found {definers}"
    )


def test_run_state_enum_is_defined_exactly_once():
    """Two enums named RunState is how the ambiguity started."""
    definers = []
    for py in (REPO / "wisp").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "RunState":
                definers.append(str(py.relative_to(REPO)))
    assert definers == ["wisp/runs/record.py"], (
        f"RunState must be defined exactly once; found {definers}"
    )


# Known, tracked, NOT-YET-REMOVED duplication. These two enums predate this
# work and are now safe because their values all pass through coerce_state().
# This test is a ratchet: it fails if a THIRD appears, and must be tightened
# to an equality on a single entry when the remaining two are folded into
# RunState (sequenced as a follow-up in PHASE_FINDINGS_NORMALIZATION.md).
_KNOWN_RUNSTATUS_DEFINERS = {
    "wisp/contracts/run.py",
    "wisp/graph/types.py",
}


def test_runstatus_duplication_does_not_grow():
    definers = set()
    for py in (REPO / "wisp").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "RunStatus":
                definers.add(str(py.relative_to(REPO)))
    assert definers <= _KNOWN_RUNSTATUS_DEFINERS, (
        "a new RunStatus enum appeared; every run-state vocabulary must route "
        f"through wisp.runs.record.coerce_state(). Found: {sorted(definers)}"
    )


def test_every_runstatus_value_coerces_without_error():
    """The duplicate enums are tolerable only while their values resolve.
    This is what makes the duplication safe rather than merely present."""
    import importlib

    for mod_name, cls_name in (("wisp.contracts.run", "RunStatus"),
                               ("wisp.graph.types", "RunStatus")):
        mod = importlib.import_module(mod_name)
        enum_cls = getattr(mod, cls_name)
        for member in enum_cls:
            resolved = coerce_state(member.value)
            assert isinstance(resolved, RunState), (
                f"{mod_name}.{cls_name}.{member.name} = {member.value!r} "
                f"does not resolve to a canonical RunState"
            )

