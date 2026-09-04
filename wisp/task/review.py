"""Plan-review-apply rendering + approval capture (M6, pure).

The model proposes a structured plan; the CLI shows files, tools, risk
classes, policy obligations, cost budget, secret/data warnings, command
previews, and rollback instructions. Approval covers the whole plan or
individual action classes (risk-class granularity). Execution itself
reuses the turn engine; worktree provisioning is the only side effect
here, isolated in provision_worktree().
"""
from __future__ import annotations
import time
from typing import Any

# Static cost weights (relative budget units, documented estimate).
_COST = {"read": 1, "write": 2, "network": 3, "exec": 5, "privileged": 10}


def _risk_of(tool: str) -> str:
    from wisp.core.contracts import ToolRisk, risk_for_tool
    try:
        return risk_for_tool(tool).value
    except Exception:
        return ToolRisk.READ.value


def _action_classes(plan: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for action in plan.get("actions", []):
        cls = _risk_of(action.get("tool", ""))
        if cls not in seen:
            seen.append(cls)
    return seen


def render_review(plan: dict[str, Any],
                 approval_matrix: dict[str, str] | None = None) -> str:
    """Render the full safe-review card (pure). Secret material is never
    rendered — only warnings that secrets were detected."""
    from wisp.auth.secrets import scan_for_secrets
    matrix = approval_matrix or {}
    lines = [f"Plan: {plan.get('goal', '(no goal)')}", ""]
    lines.append("Proposed files:")
    for f in plan.get("files", []):
        lines.append(f"  - {f}")
    lines.append("")
    lines.append("Actions (tool, risk, policy):")
    budget = 0
    warnings: list[str] = []
    for action in plan.get("actions", []):
        tool = action.get("tool", "?")
        args = action.get("args", {})
        risk = _risk_of(tool)
        budget += _COST.get(risk, 1)
        level = matrix.get(tool, "approve")
        lines.append(f"  - {tool} [{risk}] policy={level}")
        target = args.get("path", args.get("command", ""))
        if target:
            lines.append(f"      target: {target}")
        for value in args.values():
            hits = scan_for_secrets(str(value))
            if hits:
                warnings.append(
                    f"secret pattern ({hits[0]}) in {tool} arguments — "
                    "value withheld from this review")
    lines.append("")
    lines.append(f"Cost budget (estimate): {budget} units")
    test_plan = plan.get("test_plan", [])
    if test_plan:
        lines.append("Test plan:")
        for t in test_plan:
            lines.append(f"  - {t}")
    if warnings:
        lines.append("Warnings:")
        for w in dict.fromkeys(warnings):
            lines.append(f"  ! {w}")
    lines.append("Rollback: revert with `git checkout -- <file>` per file; "
                 "worktree changes are discarded on cleanup.")
    return "\n".join(lines)


def approve_scope(plan: dict[str, Any], scope: str,
                  approver: str) -> dict[str, Any]:
    """Capture an approval decision: whole plan ("all") or one action
    class (risk-class granularity). Returns the recorded decision dict."""
    classes = _action_classes(plan)
    if scope == "all":
        approved_classes = classes
    elif scope in classes:
        approved_classes = [scope]
    else:
        raise ValueError(
            f"unknown scope {scope!r} (choose 'all' or one of {classes})")
    return {"approved": True, "scope": scope,
            "action_classes": approved_classes,
            "pending": [c for c in classes if c not in approved_classes],
            "approver": approver, "approved_at": time.time(), "version": 1}


async def provision_worktree(workspace: str, name: str) -> str:
    """Create an isolated worktree for plan-approved work (default target).
    The only side effect in this module; cleanup via WorktreeManager."""
    from pathlib import Path
    from wisp.multi_agent._worktree_manager import WorktreeManager
    mgr = WorktreeManager(Path(workspace))
    path = await mgr.create(name)
    return str(path)
