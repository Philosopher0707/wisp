"""SecurityPolicy — unified security decision layer.

Delegates to PriorityRuleEngine for mode-based checks. Keeps the same
public API (check, with_*, audit_log) for backward compatibility.

Design:
  - ONE decision function: check(action, context) → Decision
  - Mode checks delegated to PriorityRuleEngine (composable rules)
  - Workspace trust and hooks evaluated as additional layers
  - Audit: logged to ImmutableAuditTrail (backed by SQLite via UnifiedStore)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Optional

from wisp.infra.policy_engine import (
    Action as EngineAction,
    EvalContext,
    PolicyEngine,
    PriorityRuleEngine,
    Rule,
)


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
    "spawn", "fanout", "plan_task", "mark_step_done", "update_plan",
})

# Tools that require approval in AUTO_EDIT (writes auto-approved, bash blocked)
_AUTO_EDIT_BLOCK_TOOLS = frozenset({
    "run_bash", "git_branch", "git_commit", "git_push", "gh_pr_create",
    "spawn", "fanout",
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
    """Immutable security policy. Use with_* to create variants.

    Delegates mode checks to PriorityRuleEngine. Audit decisions are
    written to an ImmutableAuditTrail backed by the same SQLite store.
    """

    permission_mode: PermissionMode = PermissionMode.AUTO_EDIT
    trusted_workspaces: frozenset[Path] = field(default_factory=frozenset)
    hooks: list[Callable] = field(default_factory=list)
    _engine: PolicyEngine | None = field(default=None, repr=False)
    _audit_trail: Any = field(default=None, repr=False)
    _audit_log: list[dict] = field(default_factory=list, repr=False)  # fallback when no trail

    def __post_init__(self):
        if self._engine is None:
            self._engine = PriorityRuleEngine.with_builtin_rules(
                permission_mode=str(self.permission_mode),
                safe_read_tools=_SAFE_READ_TOOLS,
                ask_all_block_tools=_ASK_ALL_BLOCK_TOOLS,
                auto_edit_block_tools=_AUTO_EDIT_BLOCK_TOOLS,
            )

    @property
    def engine(self) -> PolicyEngine:
        """Access the underlying policy engine for rule management."""
        assert self._engine is not None
        return self._engine

    # ── Immutable builders ──────────────────────────────────────────

    def with_trusted_workspaces(self, workspaces: set[Path]) -> "SecurityPolicy":
        return SecurityPolicy(
            permission_mode=self.permission_mode,
            trusted_workspaces=frozenset(workspaces),
            hooks=list(self.hooks),
            _engine=self._engine,
            _audit_trail=self._audit_trail,
            _audit_log=list(self._audit_log),
        )

    def with_hook(self, hook: Callable) -> "SecurityPolicy":
        new_hooks = list(self.hooks)
        new_hooks.append(hook)
        return SecurityPolicy(
            permission_mode=self.permission_mode,
            trusted_workspaces=self.trusted_workspaces,
            hooks=new_hooks,
            _engine=self._engine,
            _audit_trail=self._audit_trail,
            _audit_log=list(self._audit_log),
        )

    def with_permission_mode(self, mode: PermissionMode) -> "SecurityPolicy":
        """Return a new policy with a different permission mode."""
        return SecurityPolicy(
            permission_mode=mode,
            trusted_workspaces=self.trusted_workspaces,
            hooks=list(self.hooks),
            _engine=PriorityRuleEngine.with_builtin_rules(
                permission_mode=str(mode),
                safe_read_tools=_SAFE_READ_TOOLS,
                ask_all_block_tools=_ASK_ALL_BLOCK_TOOLS,
                auto_edit_block_tools=_AUTO_EDIT_BLOCK_TOOLS,
            ),
            _audit_trail=self._audit_trail,
            _audit_log=list(self._audit_log),
        )

    def add_rule(self, rule: Rule) -> None:
        """Add a custom rule to the underlying engine."""
        self.engine.add_rule(rule)

    # ── Core decision ───────────────────────────────────────────────

    def check(self, action: Action, context: Context) -> Decision:
        # Layer 1: Workspace trust
        trust_result = self._check_trust(context)
        if not trust_result.allowed:
            self._audit(action, context, trust_result)
            return trust_result

        # Layer 2: Permission mode (delegated to engine)
        engine_action = EngineAction(name=action.name, args=dict(action.args))
        engine_ctx = EvalContext(
            workspace=context.workspace,
            permission_mode=str(self.permission_mode),
        )
        result = self.engine.evaluate(engine_action, engine_ctx)

        if not result.allowed:
            decision = Decision(allowed=False, reason=result.reason)
            self._audit(action, context, decision)
            return decision

        # Layer 3: Hooks
        hook_result = self._check_hooks(action, context)
        if not hook_result.allowed:
            self._audit(action, context, hook_result)
            return hook_result

        # Layer 4: Approved
        decision = Decision(
            allowed=True,
            modified_args=result.modified_args or hook_result.modified_args,
        )
        self._audit(action, context, decision)
        return decision

    # ── Layer implementations ───────────────────────────────────────

    def _check_trust(self, context: Context) -> Decision:
        if not self.trusted_workspaces:
            return Decision(allowed=True)
        ws = context.workspace.resolve()
        for trusted in self.trusted_workspaces:
            if ws == trusted.resolve():
                return Decision(allowed=True)
        return Decision(allowed=False, reason=f"Untrusted workspace: {context.workspace}")

    def _check_hooks(self, action: Action, context: Context) -> Decision:
        current_args = dict(action.args)
        for hook in self.hooks:
            result = hook(action, context)
            if result.get("action") == "block":
                return Decision(allowed=False, reason=result.get("reason", "blocked by hook"))
            if result.get("action") == "modify":
                new_args = result.get("args")
                if new_args:
                    current_args.update(new_args)
        if current_args != action.args:
            return Decision(allowed=True, modified_args=current_args)
        return Decision(allowed=True)

    def _audit(self, action: Action, context: Context, decision: Decision) -> None:
        try:
            import json
            args_summary = json.dumps(dict(action.args), default=str)
            if len(args_summary) > 500:
                args_summary = args_summary[:500]
            if self._audit_trail is not None:
                self._audit_trail.record_decision(
                    action=action.name,
                    tool_name=action.name,
                    workspace=str(context.workspace),
                    allowed=decision.allowed,
                    reason=decision.reason,
                    args_summary=args_summary,
                )
            else:
                self._audit_log.append({
                    "action": action.name,
                    "allowed": decision.allowed,
                    "reason": decision.reason,
                    "workspace": str(context.workspace),
                    "timestamp": time.time(),
                })
        except Exception:
            pass  # Audit failure must not break the agent

    def audit_log(self) -> list[dict]:
        """Read recent audit entries from the immutable trail or fallback list."""
        if self._audit_trail is not None:
            try:
                return self._audit_trail.entries(limit=100)
            except Exception:
                pass
        return list(self._audit_log)


# Keys whose values must never be echoed to a human or client in approval
# prompts, logs, or previews. Substring-matched against normalized keys.
SENSITIVE_ARG_PATTERNS = frozenset({
    "api_key", "token", "password", "secret", "credential", "auth",
    "bearer", "authorization", "client_secret", "ssh_key", "private_key",
    "access_token", "refresh_token",
})


def redact_sensitive_tool_args(args: dict) -> dict:
    """Redact known sensitive fields from tool arguments before display.

    Pure function; used by every surface that shows tool arguments to a
    human (approval prompts, event previews).
    """
    if not isinstance(args, dict):
        return args
    redacted: dict = {}
    for key, value in args.items():
        key_lower = str(key).lower().replace("-", "_")
        if any(p in key_lower for p in SENSITIVE_ARG_PATTERNS):
            if isinstance(value, str) and len(value) > 4:
                redacted[key] = value[:4] + "***"
            else:
                redacted[key] = "***"
        else:
            redacted[key] = value
    return redacted
