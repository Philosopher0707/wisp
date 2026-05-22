"""Policy engine — composable rule-based security decisions.

Replaces the 4-mode if-else chain in SecurityPolicy with a PriorityRuleEngine
that composes named, ordered predicates. Rules are evaluated in priority order:
first deny wins, last allow wins.

Design:
  - PolicyEngine (ABC) — contract for any policy decision system
  - PriorityRuleEngine — ordered rule evaluation with deny/allow semantics
  - Rule — named, self-contained predicate
  - PolicyDecision — immutable result with reason and optional modified args

Extensible via config: users can add custom rules that run before/after built-ins.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Optional


class RuleEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyDecision:
    """Immutable decision from a policy evaluation."""

    allowed: bool
    reason: str = ""
    modified_args: Optional[dict] = None
    rule_name: str = ""

    @classmethod
    def allow(cls, rule_name: str = "", reason: str = "") -> "PolicyDecision":
        return cls(allowed=True, reason=reason, rule_name=rule_name)

    @classmethod
    def deny(cls, rule_name: str, reason: str) -> "PolicyDecision":
        return cls(allowed=False, reason=reason, rule_name=rule_name)

    @classmethod
    def allow_modified(cls, rule_name: str, modified_args: dict) -> "PolicyDecision":
        return cls(allowed=True, reason="args modified by rule", modified_args=modified_args, rule_name=rule_name)


@dataclass(frozen=True)
class Action:
    """A tool invocation to evaluate."""

    name: str
    args: dict = field(default_factory=dict)

    @classmethod
    def from_security_action(cls, action: Any) -> "Action":
        """Adapt from wisp.infra.security.Action."""
        return cls(name=action.name, args=dict(action.args))


@dataclass(frozen=True)
class EvalContext:
    """Context for policy evaluation."""

    workspace: Path
    permission_mode: str = "full"
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_security_context(cls, ctx: Any, permission_mode: str = "full") -> "EvalContext":
        """Adapt from wisp.infra.security.Context."""
        return cls(workspace=ctx.workspace, permission_mode=permission_mode)


RulePredicate = Callable[[Action, EvalContext], Optional[PolicyDecision]]
"""A rule predicate inspects an action+context and returns:
- PolicyDecision.deny(...) to block
- PolicyDecision.allow(...) to explicitly allow
- None to pass (defer to next rule)
"""


@dataclass(frozen=True)
class Rule:
    """A named security rule with a priority.

    Priority determines evaluation order (lower = earlier).
    Built-in rules use priority 0-99. User rules use 100+.
    """

    name: str
    predicate: RulePredicate
    priority: int = 0
    description: str = ""


class PolicyEngine(ABC):
    """Abstract contract for policy decision systems."""

    @abstractmethod
    def evaluate(self, action: Action, context: EvalContext) -> PolicyDecision:
        """Evaluate an action in context and return a decision."""
        ...

    @abstractmethod
    def add_rule(self, rule: Rule) -> None:
        """Register a new rule."""
        ...

    @abstractmethod
    def remove_rule(self, name: str) -> None:
        """Remove a rule by name."""
        ...


@dataclass
class PriorityRuleEngine(PolicyEngine):
    """Ordered rule evaluation engine.

    Rules are evaluated in priority order (lower = first).
    First DENY wins — once a rule denies, evaluation stops.
    Last ALLOW wins — explicit allow overrides implicit pass.
    If no rule matches, denies by default.
    """

    rules: list[Rule] = field(default_factory=list)
    default_deny: bool = True

    def evaluate(self, action: Action, context: EvalContext) -> PolicyDecision:
        last_allow: Optional[PolicyDecision] = None

        for rule in sorted(self.rules, key=lambda r: r.priority):
            try:
                result = rule.predicate(action, context)
            except Exception:
                # Faulty rule is skipped — don't crash the agent
                continue

            if result is None:
                continue

            if not result.allowed:
                return PolicyDecision(
                    allowed=False,
                    reason=result.reason,
                    rule_name=result.rule_name or rule.name,
                )

            if result.modified_args:
                last_allow = result
            else:
                last_allow = result

        if last_allow is not None:
            return last_allow

        if self.default_deny:
            return PolicyDecision.deny("default", "no matching rule — denied by default")

        return PolicyDecision.allow("default", "no matching rule")

    def add_rule(self, rule: Rule) -> None:
        if any(r.name == rule.name for r in self.rules):
            raise ValueError(f"Rule '{rule.name}' already registered")
        self.rules.append(rule)

    def remove_rule(self, name: str) -> None:
        self.rules = [r for r in self.rules if r.name != name]

    # ── Built-in rule factory ─────────────────────────────────────────

    @classmethod
    def with_builtin_rules(
        cls,
        permission_mode: str,
        safe_read_tools: frozenset[str] | None = None,
        ask_all_block_tools: frozenset[str] | None = None,
        auto_edit_block_tools: frozenset[str] | None = None,
    ) -> "PriorityRuleEngine":
        """Create engine pre-loaded with standard permission-mode rules.

        These implement the same semantics as the original 4-mode if-else chain.
        Custom rules can be added afterward at higher priority numbers.
        """
        engine = cls()

        safe = safe_read_tools or _DEFAULT_SAFE_READ_TOOLS
        ask_block = ask_all_block_tools or _DEFAULT_ASK_ALL_BLOCK
        edit_block = auto_edit_block_tools or _DEFAULT_AUTO_EDIT_BLOCK

        # Priority 0: FULL mode — allow everything
        engine.add_rule(Rule(
            name="mode.full",
            predicate=_make_mode_rule("full", allow_all=True),
            priority=0,
            description="FULL mode: all tools allowed",
        ))

        # Priority 10: READ_ONLY — only safe reads
        engine.add_rule(Rule(
            name="mode.read_only",
            predicate=_make_readonly_rule(safe),
            priority=10,
            description="READ_ONLY mode: only safe read tools",
        ))

        # Priority 20: ASK_ALL — safe reads allowed, blocked tools denied, rest allowed
        engine.add_rule(Rule(
            name="mode.ask_all_safe",
            predicate=_make_safe_read_rule(safe),
            priority=20,
            description="ASK_ALL mode: safe reads allowed",
        ))
        engine.add_rule(Rule(
            name="mode.ask_all_block",
            predicate=_make_block_rule(ask_block, "ASK_ALL mode requires approval", "ask_all"),
            priority=21,
            description="ASK_ALL mode: blocked tools require approval",
        ))

        # Priority 30: AUTO_EDIT — blocked tools denied, rest allowed
        engine.add_rule(Rule(
            name="mode.auto_edit_block",
            predicate=_make_block_rule(edit_block, "AUTO_EDIT mode blocks", "auto_edit"),
            priority=30,
            description="AUTO_EDIT mode: bash and destructive tools blocked",
        ))

        # Priority 1000: catch-all — allow if mode matched, deny otherwise
        engine.add_rule(Rule(
            name="catch_all",
            predicate=_make_catch_all(permission_mode),
            priority=1000,
            description="Catch-all: allow if mode matched",
        ))

        return engine


# ── Default tool classification sets ──────────────────────────────────

_DEFAULT_SAFE_READ_TOOLS = frozenset({
    "read_file", "list_files", "search_codebase", "search_symbols",
    "git_status", "git_diff", "lsp_diagnostics", "lsp_definition",
    "lsp_references", "lsp_hover", "lsp_symbols", "web_fetch",
    "web_search", "recall",
})

_DEFAULT_ASK_ALL_BLOCK = frozenset({
    "write_file", "edit_file", "edit_file_multi", "run_bash",
    "git_branch", "git_commit", "git_push", "gh_pr_create",
    "spawn_subagent", "plan_task", "mark_step_done", "update_plan",
})

_DEFAULT_AUTO_EDIT_BLOCK = frozenset({
    "run_bash", "git_branch", "git_commit", "git_push", "gh_pr_create",
    "spawn_subagent",
})


# ── Rule predicate factories ──────────────────────────────────────────

def _make_mode_rule(mode_name: str, allow_all: bool) -> RulePredicate:
    def predicate(action: Action, ctx: EvalContext) -> Optional[PolicyDecision]:
        if ctx.permission_mode == mode_name:
            return PolicyDecision.allow(f"mode.{mode_name}", f"{mode_name} mode — all allowed")
        return None
    return predicate


def _make_readonly_rule(safe_tools: frozenset[str]) -> RulePredicate:
    def predicate(action: Action, ctx: EvalContext) -> Optional[PolicyDecision]:
        if ctx.permission_mode != "read_only":
            return None
        if action.name in safe_tools:
            return PolicyDecision.allow("mode.read_only", f"safe read: {action.name}")
        return PolicyDecision.deny("mode.read_only", f"READ_ONLY mode blocks {action.name}")
    return predicate


def _make_safe_read_rule(safe_tools: frozenset[str]) -> RulePredicate:
    def predicate(action: Action, ctx: EvalContext) -> Optional[PolicyDecision]:
        if ctx.permission_mode != "ask_all":
            return None
        if action.name in safe_tools:
            return PolicyDecision.allow("mode.ask_all_safe", f"safe read: {action.name}")
        return None  # defer to next rule
    return predicate


def _make_block_rule(blocked: frozenset[str], reason_template: str, mode_name: str) -> RulePredicate:
    def predicate(action: Action, ctx: EvalContext) -> Optional[PolicyDecision]:
        if ctx.permission_mode != mode_name:
            return None
        if action.name in blocked:
            return PolicyDecision.deny(
                "mode.block",
                f"{reason_template} {action.name}",
            )
        return None
    return predicate


def _make_catch_all(mode_name: str) -> RulePredicate:
    def predicate(action: Action, ctx: EvalContext) -> Optional[PolicyDecision]:
        # Only fires for known modes — new/unknown modes fall through to default deny
        if mode_name in ("full", "read_only", "ask_all", "auto_edit"):
            return PolicyDecision.allow("catch_all", f"mode '{mode_name}' — allowed")
        return None
    return predicate
