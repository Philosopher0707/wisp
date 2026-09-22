"""Migration P2 — structural no-bypass invariant for the proposal boundary.

`WISP_MIGRATION_PLAN.md` requires `test_proposal_boundary_no_bypass.py` to
assert that **no code path reaches a tool effect without a proposal**.

`tests/test_no_bypass.py` already covers this *behaviourally* (drive a call,
observe a denial). This file covers it *structurally*, with AST analysis, for
a reason the behavioural tests cannot: a behavioural test only exercises the
paths someone thought to write. The audit that motivated this migration found
**eight** complete-but-unreachable subsystems, and the sibling failure mode —
a reachable path that skips a gate — is invisible to any test that does not
happen to call it.

What is pinned:

1. The set of modules that consult `authorize()` is exactly the expected set.
   A new module consulting it is fine; a module that stops is a regression.
2. `ToolExecutor.execute` consults authority exactly **once** and records
   exactly **one** verdict. Two consults would mean a second decision
   procedure; zero would mean a bypass.
3. No module outside the sanctioned set imports a tool implementation table
   directly to invoke it.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WISP = REPO / "wisp"


def _parsed(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _call_names(tree: ast.Module) -> list[str]:
    """Every called function name in a module, dotted where obvious."""
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                out.append(fn.id)
            elif isinstance(fn, ast.Attribute):
                out.append(fn.attr)
    return out


def _py_files() -> list[Path]:
    return sorted(p for p in WISP.rglob("*.py") if "__pycache__" not in p.parts)


# ── 1. Who consults the authority layer ─────────────────────────────────


#: Modules that call `authorize()` and must keep doing so. Both are real
#: enforcement points: the agent's executor and the direct registry entry.
AUTHORITY_CONSUMERS = {
    "wisp/tool_executor.py",
    "wisp/tools/registry.py",
}


class TestAuthorityConsumers:
    def test_expected_modules_consult_authorize(self):
        actual = set()
        for path in _py_files():
            if "authorize" in _call_names(_parsed(path)):
                actual.add(str(path.relative_to(REPO)))
        missing = AUTHORITY_CONSUMERS - actual
        assert not missing, (
            f"these modules no longer consult the authority layer: {sorted(missing)}")

    def test_no_unexpected_module_consults_authorize(self):
        """A NEW consumer is not automatically wrong — but it is a second
        opinion about authority unless it is deliberate, and a second opinion
        is the defect class this migration exists to remove. Update the set
        consciously if you add one."""
        actual = set()
        for path in _py_files():
            if "authorize" in _call_names(_parsed(path)):
                actual.add(str(path.relative_to(REPO)))
        extra = actual - AUTHORITY_CONSUMERS
        assert not extra, (
            "unexpected modules consult authorize() — confirm this is a "
            f"deliberate enforcement point, then add it to AUTHORITY_CONSUMERS: {sorted(extra)}")


# ── 2. The executor's consult/record arity ──────────────────────────────


class TestExecutorConsultArity:
    """Pins that P2 added a *record*, not a decision procedure."""

    @staticmethod
    def _execute_fn() -> ast.AsyncFunctionDef:
        tree = _parsed(WISP / "tool_executor.py")
        for node in ast.walk(tree):
            if (isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "execute"):
                return node
        raise AssertionError("ToolExecutor.execute not found")

    def test_execute_consults_authorize_exactly_once(self):
        calls = [n for n in ast.walk(self._execute_fn())
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)
                 and n.func.id == "authorize"]
        assert len(calls) == 1, (
            f"execute() consults authorize() {len(calls)} times; exactly one "
            "authority consult is the invariant (P2 adds a record, not a "
            "second decision procedure)")

    def test_execute_records_exactly_one_verdict_per_path(self):
        """One recorder call on the allow path. The deny path records through
        `_audit_denial`, which predates P2."""
        calls = [n for n in ast.walk(self._execute_fn())
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "_audit_authorization"]
        assert len(calls) == 1, (
            f"execute() records {len(calls)} authorization verdicts; exactly "
            "one is expected (the deny path records via _audit_denial)")

    def test_verdict_record_follows_the_allow_deny_fork(self):
        """Recording after the fork is what makes it exactly-once: a denied
        call returns before reaching the recorder."""
        src = (WISP / "tool_executor.py").read_text(encoding="utf-8")
        deny_idx = src.index("if not _decision.allowed:")
        record_idx = src.index("self._audit_authorization(")
        assert deny_idx < record_idx, (
            "the verdict recorder must sit AFTER the allow/deny fork, or a "
            "denied call would be recorded twice (deny + allow)")


# ── 3. No module invokes tool implementations behind the executor ───────


#: Modules allowed to reach a tool implementation directly.
#: `registry.py` is the sanctioned fallback entry point and consults
#: authority itself (see AUTHORITY_CONSUMERS).
DIRECT_IMPL_ALLOWED = {
    "wisp/tools/registry.py",
    "wisp/tool_executor.py",
}


class TestNoDirectImplementationReach:
    def test_no_module_bypasses_the_authority_entry_points(self):
        """A module that pulls `TOOL_IMPLS` and calls into it reaches an
        effect without passing either authority entry point."""
        offenders: list[str] = []
        for path in _py_files():
            rel = str(path.relative_to(REPO))
            if rel in DIRECT_IMPL_ALLOWED:
                continue
            tree = _parsed(path)
            imports_impls = any(
                isinstance(n, ast.ImportFrom)
                and any(a.name == "TOOL_IMPLS" for a in n.names)
                for n in ast.walk(tree)
            )
            if not imports_impls:
                continue
            # Importing is fine (introspection, CLI listing). INVOKING is not.
            invokes = any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "TOOL_IMPLS"
                for n in ast.walk(tree)
            )
            if invokes:
                offenders.append(rel)
        assert not offenders, (
            f"these modules invoke TOOL_IMPLS directly, bypassing the "
            f"authority entry points: {sorted(offenders)}")


# ── 4. The P2 additions are reachable (RULE 11) ─────────────────────────


class TestReachability:
    def test_verdict_recorder_is_reachable_from_production(self):
        """`_audit_authorization` must be called from `execute()`, which is on
        the production turn path — not merely defined."""
        tree = _parsed(WISP / "tool_executor.py")
        called = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_audit_authorization"
            for n in ast.walk(tree)
        )
        assert called, (
            "_audit_authorization is defined but never called — the exact "
            "'written but unwired' pathology this migration exists to remove")

    def test_execute_is_reachable_from_the_core(self):
        """`ToolExecutor.execute` must be called from `core/stateless.py`;
        otherwise the whole gate chain is dead code."""
        tree = _parsed(WISP / "core" / "stateless.py")
        assert any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "execute"
            for n in ast.walk(tree)
        ), "the core no longer calls ToolExecutor.execute"
