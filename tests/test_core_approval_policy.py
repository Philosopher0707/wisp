"""The approval policy lives in core, once; `tool_executor` and the REST gate consume it.

Until now the rule "which tools need an approver, and does this mode or setting waive it" was ten names inside the
2,400-line `tool_executor.py`, and `server/deps.py` imported that whole executor just to ask the predicate. The parity
tests (`test_no_approver_means_no_on_every_surface`) existed only to stop two surfaces drifting apart. A rule consumed by
more than one surface, and security-relevant, belongs in core where one small module can be read and audited.

This is a pure move: behaviour is held by the existing suites, unchanged. These tests hold the move itself.
"""
from __future__ import annotations

import ast
from pathlib import Path

import wisp.core.approval_policy as policy
import wisp.tool_executor as te

REPO = Path(__file__).resolve().parent.parent

MOVED = {
    "approval_needed": "approval_needed",
    "standing_grant_applies": "standing_grant_applies",
    "_get_write_tools": "get_write_tools",
    "_forced_by_mode": "forced_by_mode",
}


def _defined_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_the_policy_is_implemented_in_core():
    for name in MOVED.values():
        assert callable(getattr(policy, name)), name


def test_tool_executor_re_exports_the_same_objects_not_copies():
    """Tests and the AST pins on `execute()` still reach these names on `tool_executor`; they must be the core ones."""
    for old, new in MOVED.items():
        assert getattr(te, old) is getattr(policy, new), f"tool_executor.{old} is not core's {new}"


def test_the_default_write_set_under_its_old_name_is_the_core_set():
    assert te._DEFAULT_WRITE_TOOLS is policy.DEFAULT_WRITE_TOOLS


def test_tool_executor_no_longer_defines_the_policy():
    defined = _defined_functions(REPO / "wisp" / "tool_executor.py")
    assert not defined & set(MOVED), f"still defined in tool_executor: {sorted(defined & set(MOVED))}"


def test_the_rest_gate_depends_on_core_not_on_the_executor():
    src = (REPO / "wisp" / "server" / "deps.py").read_text(encoding="utf-8")
    assert "from wisp.core.approval_policy import approval_needed" in src
    assert "from wisp.tool_executor import approval_needed" not in src


def test_the_module_does_not_reach_up_into_the_executor_or_presentation():
    tree = ast.parse(Path(policy.__file__).read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    bad = {m for m in mods if m.startswith(("wisp.tool_executor", "wisp.cli", "wisp.repl", "wisp.server",
                                           "wisp.transport", "wisp.tui"))}
    assert not bad, f"core approval policy imports {sorted(bad)}"


def test_the_write_tool_set_is_unchanged_in_content():
    """Pins what is gated, by name, so a move that drops or adds a tool is caught here and not in production."""
    tools = policy.get_write_tools(None)
    for expected in ("write_file", "edit_file", "edit_file_multi", "run_bash", "git_commit", "git_push", "gh_pr_merge",
                     "spawn", "fanout", "rewind", "exec_sandbox", "fs_mutate", "capture_skill"):
        assert expected in tools, expected
    assert "read_file" not in tools and "grep" not in tools and "glob" not in tools
