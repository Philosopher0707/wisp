"""SecurityPolicy — unified security decision layer.

Replaces: permission_mode checks, WorkspaceTrustManager, hook execution,
and ad-hoc audit logging.

Design:
  - ONE decision function: check(action, context) → Decision
  - FOUR layers evaluated in order:
    1. Permission mode (coarse: FULL, ASK_ALL, AUTO_EDIT, READ_ONLY)
    2. Workspace trust (workspace must be in trusted set)
    3. Hooks (user-defined interception)
    4. Audit (always logged)
  - Immutable: with_* methods return new instances
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Callable, Optional


class PermissionMode(StrEnum):
    FULL = "full"
    ASK_ALL = "ask_all"
    AUTO_EDIT = "auto_edit"
    READ_ONLY = "read_only"


# Tools that are "safe reads" — allowed in READ_ONLY and ASK_ALL
_SAFE_READ_TOOLS = frozenset({
    "read_file", "list_files", "search_codebase", "search_symbols",
    "git_status", "git_diff", "lsp_diagnostics", "lsp_definition",
    "lsp_references", "lsp_hover", "lsp_symbols", "web_fetch",
    "web_search", "recall",
})

# Tools that require approval in ASK_ALL
_ASK_ALL_BLOCK_TOOLS = frozenset({
    "write_file", "edit_file", "edit_file_multi", "run_bash",
    "git_branch", "git_commit", "git_push", "gh_pr_create",
    "spawn_subagent", "plan_task", "mark_step_done", "update_plan",
})

# Tools that require approval in AUTO_EDIT (writes auto-approved, bash blocked)
_AUTO_EDIT_BLOCK_TOOLS = frozenset({
    "run_bash", "git_branch", "git_commit", "git_push", "gh_pr_create",
    "spawn_subagent",
})


@dataclass(frozen=True)
class Action:
    name: str
    args: dict


@dataclass(frozen=True)
class Context:
    workspace: Path


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    modified_args: Optional[dict] = None


@dataclass
class SecurityPolicy:
    """Immutable security policy. Use with_* to create variants."""

    permission_mode: PermissionMode = PermissionMode.FULL
    trusted_workspaces: frozenset[Path] = field(default_factory=frozenset)
    hooks: list[Callable] = field(default_factory=list)
    _audit_log: list[dict] = field(default_factory=list, repr=False)

    # ── Immutable builders ──────────────────────────────────────────

    def with_trusted_workspaces(self, workspaces: set[Path]) -> "SecurityPolicy":
        return SecurityPolicy(
            permission_mode=self.permission_mode,
            trusted_workspaces=frozenset(workspaces),
            hooks=list(self.hooks),
            _audit_log=list(self._audit_log),
        )

    def with_hook(self, hook: Callable) -> "SecurityPolicy":
        new_hooks = list(self.hooks)
        new_hooks.append(hook)
        return SecurityPolicy(
            permission_mode=self.permission_mode,
            trusted_workspaces=self.trusted_workspaces,
            hooks=new_hooks,
            _audit_log=list(self._audit_log),
        )

    # ── Core decision ───────────────────────────────────────────────

    def check(self, action: Action, context: Context) -> Decision:
        # Layer 1: Permission mode
        mode_result = self._check_mode(action)
        if not mode_result.allowed:
            self._audit(action, context, mode_result)
            return mode_result

        # Layer 2: Workspace trust
        trust_result = self._check_trust(context)
        if not trust_result.allowed:
            self._audit(action, context, trust_result)
            return trust_result

        # Layer 3: Hooks
        hook_result = self._check_hooks(action, context)
        if not hook_result.allowed:
            self._audit(action, context, hook_result)
            return hook_result

        # Layer 4: Audit (approved)
        decision = Decision(allowed=True)
        self._audit(action, context, decision)
        return decision

    # ── Layer implementations ───────────────────────────────────────

    def _check_mode(self, action: Action) -> Decision:
        mode = self.permission_mode

        if mode == PermissionMode.FULL:
            return Decision(allowed=True)

        if mode == PermissionMode.READ_ONLY:
            if action.name in _SAFE_READ_TOOLS:
                return Decision(allowed=True)
            return Decision(allowed=False, reason=f"READ_ONLY mode blocks {action.name}")

        if mode == PermissionMode.ASK_ALL:
            if action.name in _SAFE_READ_TOOLS:
                return Decision(allowed=True)
            if action.name in _ASK_ALL_BLOCK_TOOLS:
                return Decision(allowed=False, reason=f"ASK_ALL mode requires approval for {action.name}")
            return Decision(allowed=True)

        if mode == PermissionMode.AUTO_EDIT:
            if action.name in _AUTO_EDIT_BLOCK_TOOLS:
                return Decision(allowed=False, reason=f"AUTO_EDIT mode blocks {action.name}")
            return Decision(allowed=True)

        return Decision(allowed=True)

    def _check_trust(self, context: Context) -> Decision:
        if not self.trusted_workspaces:
            return Decision(allowed=True)  # no trust restriction
        ws = context.workspace.resolve()
        for trusted in self.trusted_workspaces:
            if ws == trusted.resolve():
                return Decision(allowed=True)
        return Decision(allowed=False, reason=f"Untrusted workspace: {context.workspace}")

    def _check_hooks(self, action: Action, context: Context) -> Decision:
        for hook in self.hooks:
            result = hook(action, context)
            if result.get("action") == "block":
                return Decision(allowed=False, reason=result.get("reason", "blocked by hook"))
            if result.get("action") == "modify":
                new_args = result.get("args")
                if new_args:
                    action.args.update(new_args)
        return Decision(allowed=True)

    def _audit(self, action: Action, context: Context, decision: Decision) -> None:
        self._audit_log.append({
            "action": action.name,
            "args": dict(action.args),
            "workspace": str(context.workspace),
            "allowed": decision.allowed,
            "reason": decision.reason,
        })

    def audit_log(self) -> list[dict]:
        return list(self._audit_log)
