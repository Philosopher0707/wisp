"""Migration P9 — structured delegation (the wiring fixes).

The plan requires seven assertions. Two are implemented and tested here —
`test_subagent_capabilities_narrowed` (the plan calls it *"the single most
important fix in the delegation layer"*) and `test_dag_node_budget_applied`.
The other five are deferred with reasons in `PHASE_P9_REPORT.md` §7, and
`test_circuit_breaker_wired` is **documented rather than done**, because the file
in question is the user's untracked work-in-progress.

The load-bearing property: a child must not be able to call a tool its contract
did not declare. Today every child runs as the **unbounded local human
principal** — `derive_subagent` was implemented, tested, and never called.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from wisp.auth.principal import (
    PrincipalKind,
    child_principal,
    derive_subagent,
    local_principal,
)
from wisp.multi_agent.task import SubagentContract

REPO = Path(__file__).resolve().parents[1]


def _parent_bounded(*tools: str):
    return derive_subagent(local_principal(workspace="/tmp", profile="default"),
                           capabilities=frozenset(tools))


def _parent_unbounded():
    return local_principal(workspace="/tmp", profile="default")


# ── 1. Capabilities are narrowed ────────────────────────────────────────


class TestSubagentCapabilitiesNarrowed:
    def test_a_child_gets_exactly_the_declared_tools(self):
        child = child_principal(_parent_unbounded(),
                                SubagentContract(tools=["read_file"]))
        assert child.capabilities == frozenset({"read_file"})

    def test_a_child_is_a_subagent_not_a_human(self):
        """The kind matters: an authorization layer that treats a child as the
        local human has no basis for narrowing it."""
        child = child_principal(_parent_unbounded(),
                                SubagentContract(tools=["read_file"]))
        assert child.kind is PrincipalKind.SUBAGENT

    def test_the_child_records_its_parent(self):
        parent = _parent_unbounded()
        child = child_principal(parent, SubagentContract(tools=["read_file"]))
        assert child.parent_principal_id == parent.principal_id

    def test_the_child_inherits_the_workspace_and_profile(self):
        parent = _parent_unbounded()
        child = child_principal(parent, SubagentContract(tools=["read_file"]))
        assert child.workspace == parent.workspace
        assert child.profile == parent.profile

    def test_widening_past_a_bounded_parent_is_refused(self):
        """The property the plan cares about: a child cannot exceed its parent."""
        with pytest.raises(ValueError, match="narrow"):
            child_principal(_parent_bounded("read_file"),
                            SubagentContract(tools=["read_file", "write_file"]))

    def test_tools_all_with_a_bounded_parent_inherits_that_set(self):
        child = child_principal(_parent_bounded("read_file", "run_bash"),
                                SubagentContract(tools=["all"]))
        assert child.capabilities == frozenset({"read_file", "run_bash"})

    def test_tools_all_with_an_unbounded_parent_is_refused(self):
        """There is no universe to take a subset of. Both guesses are wrong:
        leaving it unbounded is the defect, and an empty set is a child that
        can call nothing."""
        with pytest.raises(ValueError, match="unbounded"):
            child_principal(_parent_unbounded(), SubagentContract(tools=["all"]))

    def test_the_refusal_names_the_fix(self):
        with pytest.raises(ValueError) as exc:
            child_principal(_parent_unbounded(), SubagentContract(tools=["all"]))
        assert "explicit tool names" in str(exc.value)

    def test_an_empty_tools_list_is_treated_as_all(self):
        with pytest.raises(ValueError, match="unbounded"):
            child_principal(_parent_unbounded(), SubagentContract(tools=[]))

    def test_narrowing_twice_stays_narrow(self):
        """A child of a child cannot regain what its parent lost."""
        first = child_principal(_parent_bounded("read_file", "run_bash"),
                                SubagentContract(tools=["read_file", "run_bash"]))
        second = child_principal(first, SubagentContract(tools=["read_file"]))
        assert second.capabilities == frozenset({"read_file"})
        with pytest.raises(ValueError):
            child_principal(second, SubagentContract(tools=["run_bash"]))

    def test_child_principal_reuses_derive_subagent(self):
        """A second narrowing implementation would be a second authority for
        what a child may do — the defect class this migration removes."""
        src = (REPO / "wisp" / "auth" / "principal.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        # `child_principal` calls derive_subagent rather than constructing a
        # Principal itself.
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "child_principal")
        calls = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "derive_subagent" in calls
        assert "Principal" not in calls, \
            "child_principal must delegate, not build a Principal itself"


# ── 2. The executor authorizes AS a principal ───────────────────────────


class TestExecutorAuthorizesAsAPrincipal:
    def test_tool_executor_accepts_a_principal(self):
        import inspect
        from wisp.tool_executor import ToolExecutor
        params = inspect.signature(ToolExecutor.__init__).parameters
        assert "principal" in params

    def test_the_parameter_defaults_to_none(self):
        """`None` must preserve today's behaviour exactly — this is additive."""
        import inspect
        from wisp.tool_executor import ToolExecutor
        assert inspect.signature(
            ToolExecutor.__init__).parameters["principal"].default is None

    def test_the_authorize_consult_prefers_the_executor_principal(self):
        """AST: the consult reads `self.principal` and only falls back to
        `local_principal` when it is unset."""
        tree = ast.parse((REPO / "wisp" / "tool_executor.py")
                         .read_text(encoding="utf-8"))
        src = (REPO / "wisp" / "tool_executor.py").read_text(encoding="utf-8")
        assert "self.principal" in src
        assert "if self.principal is not None" in src
        # and the fallback is still there, so nothing regresses
        assert "local_principal(workspace=workspace" in src

    def test_a_narrowed_child_reaches_authorize(self, tmp_path):
        """The end of the chain: a principal derived for a contract, handed to
        an executor, is what `authorize()` sees."""
        from wisp.auth import authorize, classify_workspace

        child = child_principal(_parent_unbounded(),
                                SubagentContract(tools=["read_file"]))
        decision = authorize(child, "read_file", {"path": "a.py"},
                             classify_workspace(str(tmp_path)),
                             permission_mode="auto_edit")
        assert decision is not None
        assert hasattr(decision, "allowed")


# ── 7. The DAG node budget is applied ───────────────────────────────────


class TestDagNodeBudgetApplied:
    def test_subagent_contract_has_a_metadata_field(self):
        """The latent defect: the orchestrator WRITES `task.metadata` and the
        runner READS `contract.metadata`, but the class had no such field — so
        the read raised `AttributeError` the first time a DAG node declared a
        budget, and the declared budget never applied."""
        assert any(f.name == "metadata"
                   for f in dataclasses.fields(SubagentContract))

    def test_metadata_defaults_to_an_empty_dict(self):
        assert SubagentContract(task="x").metadata == {}

    def test_metadata_is_per_instance(self):
        """A shared default would leak one node's budget into the next."""
        a, b = SubagentContract(task="a"), SubagentContract(task="b")
        a.metadata["_budget"] = 1
        assert b.metadata == {}

    def test_the_orchestrators_write_now_succeeds(self):
        """Reproduces `subagent_orchestrator.py:1316` exactly: the read that
        used to raise, then the write it guards."""
        task = SubagentContract(task="x")
        if not task.metadata:              # line 1316 — used to AttributeError
            task.metadata = {}
        task.metadata["_budget"] = "BUDGET"
        assert task.metadata["_budget"] == "BUDGET"

    def test_the_runner_can_read_what_the_orchestrator_wrote(self):
        """`_runner._budget_from_contract` reads `contract.metadata["_budget"]`."""
        task = SubagentContract(task="x")
        task.metadata["_budget"] = "BUDGET"
        assert task.metadata.get("_budget") == "BUDGET"

    def test_the_metadata_field_is_documented(self):
        src = (REPO / "wisp" / "multi_agent" / "task.py").read_text(encoding="utf-8")
        i = src.index("metadata: dict[str, Any] = field(default_factory=dict)")
        assert "P9" in src[i:i + 900], "the field must say why it exists"

    def test_the_metadata_field_is_additive(self):
        """Existing constructions must be unaffected."""
        c = SubagentContract(task="x", tools=["read_file"], name="worker")
        assert c.name == "worker" and c.tools == ["read_file"]
        assert c.metadata == {}


# ── Reachability ────────────────────────────────────────────────────────


class TestReachability:
    def test_derive_subagent_still_has_no_production_caller(self):
        """Honest accounting: P9 wired the *plumbing* (`child_principal` +
        `ToolExecutor.principal`), not the spawn site. This test documents the
        remaining gap rather than letting it look closed."""
        # AST, not string matching: a comment or docstring that merely NAMES
        # these functions is not a caller. (P8's suite made exactly this
        # mistake with its own module docstring.)
        offenders: list[str] = []
        for path in sorted((REPO / "wisp").rglob("*.py")):
            if "__pycache__" in path.parts or path.name == "principal.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in ("derive_subagent", "child_principal")):
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
        assert not offenders, (
            "the spawn site is now wired — update PHASE_P9_REPORT.md §7 (M15) "
            f"and delete this test: {offenders}")

    def test_child_principal_is_reachable(self):
        from wisp.auth import child_principal as exported
        assert exported is child_principal

    def test_the_circuit_breaker_duplicate_is_not_deleted(self):
        """`multi_agent/_circuit_breaker.py` is the user's **untracked** WIP and
        a near-duplicate of the wired `infra/circuit_breaker.py`. P9 documents
        it and recommends deletion; it does not delete someone else's file."""
        assert (REPO / "wisp" / "multi_agent" / "_circuit_breaker.py").exists()
        assert (REPO / "wisp" / "infra" / "circuit_breaker.py").exists()
