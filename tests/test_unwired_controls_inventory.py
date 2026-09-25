"""The unwired-controls inventory, pinned (Phase 10).

`docs/audit-2026-08-24.md:270` named "written-but-unwired controls" as the
dominant pattern and listed 12 instances. Re-verified in Phase 10:
**5 are now wired, 6 remain unwired, 1 is in an unreachable module** — and
nothing in the repository marked which was which. The list had decayed in
exactly the way it was warning about.

`PHASE_10_UNWIRED_CONTROLS_INVENTORY.md` records the evidence. This file keeps
it true: a control that becomes wired fails here (the document would be stale),
and so does a control that silently stops being wired.

The probes deliberately separate a **definition-only** reference from a real
consumer — a symbol whose only reference is its own `def` line is unwired no
matter how often its name appears.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _production_files() -> list[pathlib.Path]:
    return [p for p in (REPO / "wisp").rglob("*.py") if "__pycache__" not in p.parts]


def _files_referencing(name: str) -> set[str]:
    """Production files that *use* `name`.

    AST-based, not textual: a mention in a comment or docstring is not a
    reference. That distinction matters here — an explanatory note about a
    symbol must not make the symbol look wired (the first draft of this helper
    was textual and did exactly that, caught by this file's own tests).
    """
    out = set()
    for py in _production_files():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            hit = False
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and node.name == name:
                hit = True                      # the definition site
            elif isinstance(node, ast.Name) and node.id == name:
                hit = True                      # a module-level constant, or a use
            elif isinstance(node, ast.Attribute) and node.attr == name:
                hit = True
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if (alias.asname or alias.name.split(".")[-1]) == name:
                        hit = True
            if hit:
                out.add(str(py.relative_to(REPO)))
                break
    return out


def _kwarg_callers(kwarg: str) -> list[str]:
    """Call sites that pass `kwarg=` — the real test of whether a param is used."""
    out = []
    for py in _production_files():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(k.arg == kwarg for k in node.keywords):
                out.append(f"{py.relative_to(REPO)}:{node.lineno}")
    return out


# ── The five that are wired — they must stay wired ───────────────────

WIRED = {
    # control -> production files that define or use it.
    # AST-derived: a mention in a comment or docstring does not count.
    # (`core/context/boot.py` names DockerSandbox in a docstring explaining why
    # it does NOT probe the daemon — prose, not a reference.)
    "scrub_sensitive_env": {"wisp/infra/hook_types.py", "wisp/tools/_utils_env.py"},
    "DockerSandbox": {"wisp/sandbox/__init__.py", "wisp/sandbox/router.py"},
    "log_blocked": {"wisp/tools/audit.py", "wisp/tool_executor.py",
                    "wisp/core/stateless.py"},
    "redact_sensitive_tool_args": {"wisp/infra/security.py", "wisp/transport/cli.py",
                                   "wisp/transport/tui.py", "wisp/cli/approval.py"},
}


@pytest.mark.parametrize("name", sorted(WIRED))
def test_a_remediated_control_stays_wired(name):
    """Each of these was unwired at the audit and is now consumed.

    If one regresses to definition-only, it is invisible again.
    """
    actual = _files_referencing(name)
    assert actual == WIRED[name], (
        f"{name} referrers changed — it may have regressed to unwired, or the "
        f"inventory is stale.\n  expected: {sorted(WIRED[name])}\n"
        f"  actual:   {sorted(actual)}"
    )


def test_chain_patch_apply_stays_wired():
    """#12 — the worktree patch is applied by the orchestrator."""
    src = (REPO / "wisp/multi_agent/subagent_orchestrator.py").read_text(encoding="utf-8")
    assert "apply_patch(" in src, "chain patch apply is no longer invoked"
    src2 = (REPO / "wisp/multi_agent/_worktree_manager.py").read_text(encoding="utf-8")
    assert "async def apply_patch" in src2


def test_the_auto_edit_default_holds_at_both_points():
    """#10 — the schema default AND the resolution default are both AUTO_EDIT.

    The audit's finding was that the safe default existed in the schema and
    *lost at resolution time*. Both sites must keep agreeing.
    """
    src = (REPO / "wisp/config.py").read_text(encoding="utf-8")
    assert '"default": PermissionMode.AUTO_EDIT,' in src
    assert 'get_setting("permission_mode", PermissionMode.AUTO_EDIT.value)' in src


# ── The six that are unwired — tripwires ─────────────────────────────

def test_the_superseded_env_list_is_gone():
    """#2 — resolved by deletion, not by wiring.

    `_SENSITIVE_ENV_KEYS` was a static deny-list with no consumer. The live
    implementation (`tools/_utils_env.py`) is stricter and more nuanced: an
    allow-list for hooks, a deny-list *plus* `_CREDENTIAL_ENV_PATTERN` for
    bash/sandbox/MCP. Its pattern covers every key the static list named.

    Keeping the dead constant was a trap — wiring it instead of the pattern
    would be a narrower control wearing a similar name.
    """
    assert _files_referencing("_SENSITIVE_ENV_KEYS") == set(), (
        "`_SENSITIVE_ENV_KEYS` came back — reconcile it with `_utils_env.py` "
        "rather than reintroducing a second env list"
    )


def test_the_live_env_scrubbers_are_wired():
    """The three real env controls must stay consumed."""
    for name in ("scrub_sensitive_env", "credential_free_env",
                 "minimal_process_env"):
        files = _files_referencing(name)
        assert len(files) >= 2, (
            f"{name} has no consumer beyond its definition — env scrubbing "
            f"regressed: {sorted(files)}"
        )


def test_the_dag_budget_is_now_honored():
    """#7 — RESOLVED. The runner applies a node's declared budget.

    `docs/audit-2026-08-24.md:112` prescribed three fixes for the DAG
    scheduler; two landed (`_block_descendants`, `metadata["_dep_results"]`)
    and the third — *"honor metadata budget"* — did not. The orchestrator
    built a `ResourceBudget`, attached it to `contract.metadata["_budget"]`,
    and the runner built its own from contract fields, dropping it.
    """
    import time
    from types import SimpleNamespace

    from wisp.multi_agent._runner import _budget_from_contract
    from wisp.multi_agent.resource_budget import ResourceBudget

    declared = ResourceBudget(max_tokens=100, max_tool_calls=3, max_wall_time=5.0)
    contract = SimpleNamespace(max_tokens=1000, max_input_tokens=None,
                               metadata={"_budget": declared})
    budget = _budget_from_contract(contract, time.monotonic() + 60)

    assert budget.max_tokens == 100, "a declared token cap was not applied"
    assert budget.max_tool_calls == 3, (
        "a declared tool-call cap was not applied — the contract has no such "
        "field, so this is the only way to bound tool calls per node"
    )
    assert budget.max_wall_time <= 5.0, "a declared wall-time cap was not applied"


def test_the_dag_budget_is_narrow_only():
    """A graph author may bound a node, never widen it past the contract."""
    import time
    from types import SimpleNamespace

    from wisp.multi_agent._runner import _budget_from_contract
    from wisp.multi_agent.resource_budget import ResourceBudget

    wide = ResourceBudget(max_tokens=99999, max_wall_time=9999.0)
    contract = SimpleNamespace(max_tokens=1000, max_input_tokens=None,
                               metadata={"_budget": wide})
    budget = _budget_from_contract(contract, time.monotonic() + 60)

    assert budget.max_tokens == 1000, "a widening declaration took effect"
    assert budget.max_wall_time <= 61.0, "a widening declaration extended the deadline"


def test_a_contract_without_a_declared_budget_is_unchanged():
    """The common path — no metadata budget — must behave exactly as before."""
    import time
    from types import SimpleNamespace

    from wisp.multi_agent._runner import _budget_from_contract

    contract = SimpleNamespace(max_tokens=1000, max_input_tokens=None, metadata={})
    budget = _budget_from_contract(contract, time.monotonic() + 60)
    assert budget.max_tokens == 1000
    assert budget.max_tool_calls is None

    # And a contract with no `metadata` attribute at all must not raise.
    bare = SimpleNamespace(max_tokens=500, max_input_tokens=None)
    assert _budget_from_contract(bare, time.monotonic() + 30).max_tokens == 500


def test_all_three_prescribed_dag_fixes_are_present():
    """The audit's item 11 fix had three parts; all must stay in."""
    dag = (REPO / "wisp/multi_agent/dag.py").read_text(encoding="utf-8")
    orch = (REPO / "wisp/multi_agent/subagent_orchestrator.py").read_text(encoding="utf-8")
    runner = (REPO / "wisp/multi_agent/_runner.py").read_text(encoding="utf-8")

    assert "_block_descendants" in dag, "fix 1 (skip descendants of failed nodes) regressed"
    assert "_dep_results" in orch, "fix 2 (inject dependency outputs) regressed"
    assert '"budget"' in runner or "_budget" in runner, "fix 3 (honor metadata budget) regressed"
    # The runner must read it, not merely mention it.
    assert 'metadata' in runner and '_budget' in runner
    assert runner.count("_budget_from_contract(") >= 3, (
        "both budget construction sites must route through the helper"
    )


def test_no_budget_is_constructed_bare_at_a_call_site():
    """Both sites built `ResourceBudget()` inline before; neither may again.

    Exactly one bare construction may remain — inside `_budget_from_contract`,
    which is the narrowing helper itself.
    """
    runner = (REPO / "wisp/multi_agent/_runner.py").read_text(encoding="utf-8")
    inline = [ln for ln in runner.splitlines()
              if "budget = ResourceBudget()" in ln]
    assert len(inline) == 1, (
        "expected exactly one bare construction (inside the helper), found "
        f"{len(inline)} — a call site may be bypassing the narrowing: {inline}"
    )
    # And that one must live in the helper.
    tree = ast.parse(runner)
    helper = next((n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == "_budget_from_contract"), None)
    assert helper is not None, "the narrowing helper is gone"
    lines = runner.splitlines()
    bare_line = next(i for i, ln in enumerate(lines, 1)
                     if "budget = ResourceBudget()" in ln)
    assert helper.lineno <= bare_line <= (helper.end_lineno or 0), (
        "the remaining bare construction is outside the narrowing helper"
    )


def test_security_policy_kwarg_is_still_never_passed():
    """#8 — the registry-level check exists and no caller reaches it."""
    callers = _kwarg_callers("security_policy")
    assert not callers, (
        f"something now passes `security_policy=`: {callers}. If the registry "
        "check is wired, update the inventory"
    )


def test_tool_registry_execute_is_still_unused_in_production():
    """#9 — a parallel implementation without truncation or security."""
    hits = []
    for py in _production_files():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "execute":
                val = node.value
                name = getattr(val, "id", None) or getattr(val, "attr", None) or ""
                if "registry" in name.lower():
                    hits.append(f"{py.relative_to(REPO)}:{node.lineno}")
    assert not hits, (
        f"`ToolRegistry.execute` is now called in production: {hits}. It lacks "
        "truncation and security — update the inventory before relying on it"
    )


def test_the_dead_spawn_variant_is_still_dead_but_its_guards_live():
    """#6 — a superseded duplicate. The *guards* are live; the function is not.

    The distinction matters: "unwired guard" and "dead copy of a live guard"
    warrant different responses.
    """
    src = (REPO / "wisp/multi_agent/subagent_orchestrator.py").read_text(encoding="utf-8")
    assert "async def spawn_with_guards(" in src, "the dead variant was deleted — update the inventory"
    assert src.count("spawn_with_guards") == 1, (
        "`spawn_with_guards` now has a caller — it is no longer dead code"
    )
    # And the guards it duplicates are still enforced in the live path.
    assert "if contract._subagent_depth >= self._max_depth" in src
    assert "if branch_count >= self._max_branching" in src


def test_semantic_compressor_is_reachable_via_a_deferred_import():
    """#11 — a correction to this inventory's first draft.

    The draft called `semantic_compressor` unreachable on the strength of a
    grep that matched nothing (BSD `grep` ignores `--include` when it follows
    the path — the third time that produced a false "unreferenced" verdict in
    this engagement). It is reachable: `infra/session_dto.py` imports
    `SemanticCompressor` **inside a function**, and `session_dto` is imported
    by `__main__.py` and `repl/commands/core.py`.

    Pinned so the wrong verdict cannot come back.
    """
    dto = (REPO / "wisp/infra/session_dto.py").read_text(encoding="utf-8")
    assert "from wisp.semantic_compressor import SemanticCompressor" in dto, (
        "the deferred import is gone — re-verify whether the module is now dead"
    )
    # And the deferred import is inside a function, not at module level.
    tree = ast.parse(dto)
    module_level = {
        n.module for n in tree.body
        if isinstance(n, ast.ImportFrom) and n.module
    }
    assert "wisp.semantic_compressor" not in module_level, (
        "the import moved to module level — the reachability note is stale"
    )


def test_the_event_replay_referent_stays_identified():
    """#11 remains an *unidentified* audit entry — recorded, not guessed at.

    The two candidates are both wired; the document says so rather than
    substituting a confident guess (which the first draft did, and got wrong).
    """
    text = (REPO / "PHASE_10_UNWIRED_CONTROLS_INVENTORY.md").read_text(encoding="utf-8")
    assert "REFERENT NOT IDENTIFIED" in text
    # The candidate that IS wired must stay wired, or the note needs revising.
    export = (REPO / "wisp/trace/export.py").read_text(encoding="utf-8")
    assert 'span.kind != "tool_call"' in export, (
        "replay_plan no longer filters tool_call spans — re-examine #11"
    )


# ── The document itself ──────────────────────────────────────────────

def test_the_inventory_document_exists_and_states_the_tally():
    doc = REPO / "PHASE_10_UNWIRED_CONTROLS_INVENTORY.md"
    assert doc.exists(), "the inventory document was removed"
    text = doc.read_text(encoding="utf-8")
    assert "7 wired since the audit · 1 deleted as superseded · 3 still unwired · 1 whose referent I could not identify" in text, (
        "the inventory's tally changed — re-verify all twelve and update it"
    )


def test_the_audit_that_produced_the_list_is_still_cited():
    """The list came from a specific audit; the inventory must stay traceable."""
    text = (REPO / "PHASE_10_UNWIRED_CONTROLS_INVENTORY.md").read_text(encoding="utf-8")
    assert "docs/audit-2026-08-24.md:270" in text
