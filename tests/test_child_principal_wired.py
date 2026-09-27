"""M15 — a subagent authorizes as a narrowed child, not as the local human.

P9 wired the *plumbing* — `auth.principal.child_principal()` and
`ToolExecutor(principal=...)` — and recorded the spawn site as the remaining gap
(M15), with a tripwire asserting it was still unwired.

The gap, verified: `_runner._run_agent` builds the child core with
`tool_executor=self._tool_executor` — the **parent's** executor, whose
`principal` is `None`. So `ToolExecutor.execute` falls back to
`local_principal(...)`: a `HUMAN` principal with `capabilities=None`, i.e.
**unbounded**. Every child's tool call was authorized as the local human.

The child's *tool list* was already narrowed (`session_dict["allowed_tools"]`,
enforced by the core). What was missing is the **authorization identity**: L1 of
`authorize()` denies a tool the principal lacks, and the principal was always the
unbounded human.

`test_a_child_is_denied_a_tool_it_was_not_given` is the RED-first test.

**Why the principal is per call, not per executor.** `ToolExecutor.__init__`
creates two `ThreadPoolExecutor`s whose shutdown the composition root owns. A
per-child executor would leak two pools per subagent, and `fanout` spawns many.
So the executor stays shared and the principal travels with the call.
`test_the_runner_does_not_construct_a_tool_executor` is the ratchet.
"""

from __future__ import annotations

import asyncio
import ast
import inspect
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


# ── helpers ─────────────────────────────────────────────────────────────


def _collect(agen):
    return asyncio.run(_collect_async(agen))


async def _collect_async(agen):
    return [e async for e in agen]


def _payloads(events) -> list[str]:
    """Every event's human-readable result text."""
    out = []
    for e in events:
        data = getattr(e, "data", e) if not isinstance(e, dict) else e.get("data", e)
        if isinstance(data, dict):
            out.append(str(data.get("result", data.get("data", data))))
        else:
            out.append(str(data))
    return out


def _executor(tmp_path, **kw):
    from unittest.mock import AsyncMock, MagicMock

    from wisp.config import WispConfig
    from wisp.tool_executor import ToolExecutor

    hooks = MagicMock()
    hooks.arun_hooks = AsyncMock(return_value=[])
    hooks.maybe_reload_hooks = MagicMock()
    hooks.load_project_hooks = MagicMock()
    config = WispConfig().replace(workspace=str(tmp_path))
    return ToolExecutor(config=config, hook_manager=hooks, **kw)


def _unbounded_parent():
    return local_principal(workspace="/tmp", profile="default")


def _bounded_parent(*tools: str):
    return derive_subagent(_unbounded_parent(), capabilities=frozenset(tools))


# ══════════════════════════════════════════════════════════════════════════
# The executor accepts a per-call principal
# ══════════════════════════════════════════════════════════════════════════


class TestTheExecutorAcceptsAPerCallPrincipal:
    def test_execute_takes_a_principal_parameter(self):
        params = inspect.signature(
            __import__("wisp.tool_executor", fromlist=["ToolExecutor"])
            .ToolExecutor.execute).parameters
        assert "principal" in params

    def test_the_parameter_defaults_to_none(self):
        """Additive: every existing caller keeps today's behaviour."""
        from wisp.tool_executor import ToolExecutor
        assert inspect.signature(
            ToolExecutor.execute).parameters["principal"].default is None

    def test_a_per_call_principal_is_what_authorize_sees(self, tmp_path):
        """The end of the chain, behaviourally: a child principal handed to a
        call it has no capability for is denied by the principal layer.

        `write_file` and not `run_bash`, because the **policy gate denies
        `run_bash` in `auto_edit` before the authorize consult runs** — the same
        ordering fact P2 recorded as F15. The principal layer is the *second*
        narrowing, so a test of it must use a tool the first one permits.
        """
        child = child_principal(_unbounded_parent(),
                                SubagentContract(tools=["read_file"]))
        te = _executor(tmp_path)
        events = _collect(te.execute(
            "write_file", {"path": "x.py", "content": "y"}, str(tmp_path),
            tool_call_id="t1", principal=child))
        texts = _payloads(events)
        assert any("Denied by principal" in t for t in texts), texts

    def test_the_override_wins_over_the_executor_principal(self, tmp_path):
        """One executor, many children: the per-call principal must take
        precedence, or `fanout` could not share an executor at all."""
        executor_principal = _bounded_parent("read_file", "write_file")
        child = child_principal(_unbounded_parent(),
                                SubagentContract(tools=["read_file"]))
        te = _executor(tmp_path, principal=executor_principal)
        events = _collect(te.execute(
            "write_file", {"path": "x.py", "content": "y"}, str(tmp_path),
            tool_call_id="t1", principal=child))
        assert any("Denied by principal" in t for t in _payloads(events)), \
            _payloads(events)

    def test_the_executor_principal_still_applies_without_an_override(self, tmp_path):
        te = _executor(tmp_path, principal=_bounded_parent("read_file"))
        events = _collect(te.execute(
            "write_file", {"path": "x.py", "content": "y"}, str(tmp_path),
            tool_call_id="t1"))
        assert any("Denied by principal" in t for t in _payloads(events)), \
            _payloads(events)

    def test_no_principal_anywhere_still_falls_back_to_the_local_human(self, tmp_path):
        """ADR-0004's additive default, unchanged: `principal=None` means the
        unbounded local human, and the call is not denied at L1."""
        te = _executor(tmp_path)
        events = _collect(te.execute(
            "read_file", {"path": "nope.txt"}, str(tmp_path), tool_call_id="t1"))
        assert not any("principal" in t for t in _payloads(events)), _payloads(events)


# ══════════════════════════════════════════════════════════════════════════
# The load-bearing property
# ══════════════════════════════════════════════════════════════════════════


class TestTheChildIsDeniedOutsideItsContract:
    def test_a_child_is_denied_a_tool_it_was_not_given(self, tmp_path):
        """**The RED-first test.** A child declared `read_file` only. It tries
        `run_bash`. Before M15 the call was authorized as the unbounded local
        human and permitted; it must be denied by the principal layer."""
        child = child_principal(_unbounded_parent(),
                                SubagentContract(tools=["read_file"]))
        assert not child.allows_tool("write_file")
        assert child.allows_tool("read_file")

        te = _executor(tmp_path)
        events = _collect(te.execute(
            "write_file", {"path": "x.py", "content": "y"}, str(tmp_path),
            tool_call_id="t1", principal=child))
        texts = _payloads(events)
        assert any("Denied by principal" in t for t in texts), texts

    def test_the_denial_is_attributed_to_the_principal_layer(self, tmp_path):
        """Not just refused — refused *by the layer that refused it*, so the
        audit record says which authority decided."""
        from wisp.auth import authorize, classify_workspace

        child = child_principal(_unbounded_parent(),
                                SubagentContract(tools=["read_file"]))
        decision = authorize(child, "write_file", {"path": "x.py"},
                             classify_workspace(str(tmp_path)),
                             permission_mode="full")
        assert decision.allowed is False
        assert decision.controlling_layer == "principal"

    def test_the_child_identity_is_recorded(self, tmp_path):
        """`SUBAGENT` with a parent pointer, so a decision can be traced to the
        delegation that caused it rather than to the machine's user."""
        parent = _unbounded_parent()
        child = child_principal(parent, SubagentContract(tools=["read_file"]))
        assert child.kind is PrincipalKind.SUBAGENT
        assert child.parent_principal_id == parent.principal_id
        assert child.principal_id != parent.principal_id

    def test_the_mode_gate_denies_before_the_principal_consult(self, tmp_path):
        """**The ordering fact**, pinned so a future reader does not conclude
        the principal layer is redundant.

        A hard-denied tool is refused by the policy gate in `auto_edit` —
        *before* `authorize()` runs — so that denial names no controlling layer.
        The principal layer therefore catches what the mode permits but the
        **contract** excludes. Two different questions, two different gates; the
        child inherits the parent's *mode* but not the parent's *contract*.

        **The witness moved on 2026-09-27.** This used `run_bash`, which left
        `_AUTO_EDIT_DENY_TOOLS` when it was routed through the sandbox tier
        router — so the mode gate no longer denies it and the principal layer
        correctly decides. That changed the ORDER this test observes, not the
        fact it asserts. `git_push` is still hard-denied in `auto_edit` (it
        mutates a shared remote, which no local sandbox tier contains), so it
        witnesses the same ordering.
        """
        child = child_principal(_unbounded_parent(),
                                SubagentContract(tools=["read_file"]))
        te = _executor(tmp_path)
        events = _collect(te.execute(
            "git_push", {}, str(tmp_path),
            tool_call_id="t1", principal=child))
        texts = _payloads(events)
        assert any("denied" in t.lower() for t in texts), texts
        assert not any("principal layer" in t.lower() for t in texts), (
            "the principal layer now decides run_bash in auto_edit; the policy "
            "gate no longer runs first, which changes the ordering P2 recorded")

    def test_a_child_is_not_denied_its_own_tool(self, tmp_path):
        """The narrowing must not be so broad that the child cannot work."""
        child = child_principal(_unbounded_parent(),
                                SubagentContract(tools=["read_file"]))
        te = _executor(tmp_path)
        events = _collect(te.execute(
            "read_file", {"path": "missing.txt"}, str(tmp_path),
            tool_call_id="t1", principal=child))
        assert not any("Denied by principal" in t for t in _payloads(events)), \
            _payloads(events)


# ══════════════════════════════════════════════════════════════════════════
# The wiring: core → executor, and runner → core
# ══════════════════════════════════════════════════════════════════════════


def _calls_named(tree: ast.AST, names: set[str]) -> list[int]:
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id in names]


class TestTheCorePassesTheSessionPrincipal:
    def test_the_core_reads_the_session_principal(self):
        """The session dict is how the runner already passes `allowed_tools`
        into the child core; the principal travels the same way."""
        src = (REPO / "wisp" / "core" / "stateless.py").read_text(encoding="utf-8")
        assert 'session.get("principal")' in src, (
            "the core does not read a per-session principal, so a child's "
            "principal cannot reach the executor")

    def test_the_core_forwards_it_to_execute(self):
        """AST: the value reaches `tool_executor.execute(...)` as `principal=`."""
        tree = ast.parse((REPO / "wisp" / "core" / "stateless.py")
                         .read_text(encoding="utf-8"))
        found = False
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "execute"
                    and any(k.arg == "principal" for k in node.keywords)):
                found = True
        assert found, "the core never passes a principal to execute()"


class TestTheRunnerStampsAChildPrincipal:
    def test_both_child_paths_stamp_the_principal(self):
        """**The completeness ratchet.** The runner has two child-execution
        paths — `_run_agent` (stateless core) and `_run_via_runtime`
        (`AgentRuntime`). Wiring one and not the other is the half-fix this
        migration keeps finding, so both are asserted together."""
        tree = ast.parse((REPO / "wisp" / "multi_agent" / "_runner.py")
                         .read_text(encoding="utf-8"))
        stamped: list[str] = []
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Subscript)
                        and isinstance(node.slice, ast.Constant)
                        and node.slice.value == "principal"):
                    stamped.append(fn.name)
        assert set(stamped) >= {"_run_agent", "_run_via_runtime"}, (
            f"only {sorted(set(stamped))} stamp a principal; both child paths "
            "must, or the unwired one is a bypass")

    def test_the_runner_derives_the_child_from_the_parent_executor(self):
        """The parent's identity is whatever the parent's executor authorizes
        as — one rule, so a child can never be derived from a *different*
        principal than its parent's own calls use."""
        src = (REPO / "wisp" / "multi_agent" / "_runner.py").read_text(encoding="utf-8")
        assert "executor_principal(" in src, (
            "the runner does not derive the parent principal from the executor, "
            "so the child's parent pointer could name the wrong principal")

    def test_an_unbounded_parent_still_bounds_the_child(self):
        """The default executor has `principal=None`, i.e. the unbounded local
        human. `derive_subagent` permits an unbounded parent to bound a child
        arbitrarily, so the child is bounded even in the default case — which is
        the case that matters, because it is the one production uses."""
        child = child_principal(_unbounded_parent(),
                                SubagentContract(tools=["read_file", "grep"]))
        assert child.capabilities == frozenset({"read_file", "grep"})
        assert not child.allows_tool("write_file")

    def test_the_child_may_call_what_its_contract_resolved_to(self):
        """`tools == ["all"]` is resolved to a concrete list by
        `_effective_child_tools` before the principal is derived, so an explicit
        capability set is passed rather than the literal string "all" — which
        `child_principal` correctly refuses to guess from."""
        child = child_principal(_unbounded_parent(),
                                SubagentContract(tools=["all"]),
                                capabilities=["read_file", "grep"])
        assert child.capabilities == frozenset({"read_file", "grep"})

    def test_explicit_capabilities_are_still_narrowing_only(self):
        """The override must not become a widening escape hatch."""
        parent = _bounded_parent("read_file")
        with pytest.raises(ValueError, match="narrow"):
            child_principal(parent, SubagentContract(tools=["read_file"]),
                            capabilities=["read_file", "run_bash"])


class TestNoPerChildExecutor:
    def test_the_runner_does_not_construct_a_tool_executor(self):
        """**The pool-leak ratchet.** `ToolExecutor.__init__` creates two
        `ThreadPoolExecutor`s whose shutdown the composition root owns. A
        per-child executor would leak two pools per subagent, and `fanout`
        spawns many. The principal travels with the call instead."""
        src = (REPO / "wisp" / "multi_agent" / "_runner.py").read_text(encoding="utf-8")
        assert "ToolExecutor(" not in src, (
            "the runner constructs a ToolExecutor; that leaks two thread pools "
            "per subagent. Pass a principal to execute() instead.")

    def test_the_child_shares_the_parents_executor(self):
        src = (REPO / "wisp" / "multi_agent" / "_runner.py").read_text(encoding="utf-8")
        assert "tool_executor=self._tool_executor" in src, (
            "the child core no longer shares the parent's executor")


# ══════════════════════════════════════════════════════════════════════════
# Reachability — the P9 tripwire, inverted
# ══════════════════════════════════════════════════════════════════════════


class TestReachability:
    def test_child_principal_now_has_a_production_caller(self):
        """P9 shipped a tripwire asserting this was unwired. M15 wires it, so
        the tripwire is replaced by its inverse: the caller must exist."""
        offenders: list[str] = []
        for path in sorted((REPO / "wisp").rglob("*.py")):
            if "__pycache__" in path.parts or path.name == "principal.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in ("child_principal", "derive_subagent")):
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
        assert offenders, (
            "no production caller of child_principal/derive_subagent — M15 has "
            "regressed and every subagent is authorizing as the local human again")

    def test_the_caller_is_the_child_runner(self):
        tree = ast.parse((REPO / "wisp" / "multi_agent" / "_runner.py")
                         .read_text(encoding="utf-8"))
        assert _calls_named(tree, {"child_principal"}), (
            "the child runner is not the caller")
