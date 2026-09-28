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
    """Immutable decision from a policy evaluation.

    Three-state contract (13F.1): ALLOW (allowed, no approval),
    REQUIRE_APPROVAL (allowed + approval_required — the host must ask),
    DENY (allowed=False — hard, never overridable by user approval).
    """

    allowed: bool
    reason: str = ""
    modified_args: Optional[dict] = None
    rule_name: str = ""
    approval_required: bool = False

    @classmethod
    def allow(cls, rule_name: str = "", reason: str = "") -> "PolicyDecision":
        return cls(allowed=True, reason=reason, rule_name=rule_name)

    @classmethod
    def deny(cls, rule_name: str, reason: str) -> "PolicyDecision":
        return cls(allowed=False, reason=reason, rule_name=rule_name)

    @classmethod
    def require_approval(cls, rule_name: str, reason: str) -> "PolicyDecision":
        return cls(allowed=True, approval_required=True,
                   reason=reason, rule_name=rule_name)

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
            # Fail-closed stickiness (13F.1): a REQUIRE_APPROVAL verdict
            # is never cleared by a later plain allow (e.g. the catch-all).
            # Only a DENY (returned above), an arg-modifying allow, or a
            # newer gated verdict replaces it.
            if (result.modified_args or result.approval_required
                    or last_allow is None
                    or not last_allow.approval_required):
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
            predicate=_make_approval_rule(ask_block, "ASK_ALL mode requires approval for", "ask_all"),
            priority=21,
            description="ASK_ALL mode: blocked tools require approval",
        ))

        # Priority 30: AUTO_EDIT — `_AUTO_EDIT_DENY_TOOLS` are hard DENY
        # (never prompt, never overridable; empty since 2026-09-28); exec,
        # git/gh writes and delegation primitives are REQUIRE_APPROVAL (an
        # operator's yes each time, not a ban).
        engine.add_rule(Rule(
            name="mode.auto_edit_block",
            predicate=_make_block_rule(edit_block - _AUTO_EDIT_APPROVAL_TOOLS,
                                       "AUTO_EDIT mode blocks", "auto_edit",
                                       suffix=f" — {_AUTO_EDIT_DENY_REMEDY}"),
            priority=30,
            description="AUTO_EDIT mode: bash and destructive tools blocked",
        ))
        engine.add_rule(Rule(
            name="mode.auto_edit_require_approval",
            predicate=_make_approval_rule(_AUTO_EDIT_APPROVAL_TOOLS,
                                          "AUTO_EDIT mode requires approval for", "auto_edit"),
            priority=31,
            description="AUTO_EDIT mode: exec, git/gh writes and delegation require approval",
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
    "spawn", "fanout", "plan_task", "mark_step_done", "update_plan",
})

# AUTO_EDIT mode split (13F.1): hard-DENY exec/git writes vs
# REQUIRE_APPROVAL delegation primitives. A fanout/spawn itself performs
# no mutation — children are independently mode-filtered
# (filter_allowed_for_mode) — so it needs an operator's yes, not a ban.
# Single source for the split; the legacy BLOCK union is derived.
# `run_bash` was here. Removed 2026-09-27: it is the one member of this set whose execution is
# CONFINEMENT-ROUTED (`tools/bash.py` -> the tier router: Docker -> isolated PTY -> host), so the
# hard deny was blocking a command path that is already bounded. The four git/gh writes stay —
# they mutate a shared remote, which no local sandbox tier contains.
#
# RESIDUAL, stated because it is not nothing: the PTY tier bounds RESOURCE USE and CREDENTIAL
# EXPOSURE (own session, rlimits, credential-stripped env). It does NOT bound FILESYSTEM REACH —
# a command in AUTO_EDIT can still write anywhere the user can. That is the trade this line makes.
#
# Lifting the deny moves `run_bash` to REQUIRE_APPROVAL, not to allowed: `authorize()` and the
# executor's forced-approval gate already ask for exec in AUTO_EDIT. Dropping it from both sets
# let the REST gate, which reads this engine, run shell unprompted in the default mode.
#
# The four git/gh writes moved to REQUIRE_APPROVAL on 2026-09-28. Their reason for staying — they
# mutate a shared remote, which no local sandbox tier contains — argues for a HUMAN'S YES before
# each one, not for making them impossible: the default mode refused `git_push` outright, so an
# operator could not push at all without switching the whole session to `ask_all` or `full`.
# They are asked every time (the executor's AUTO_EDIT gate names all four, `authorize()` asks for
# exec), never run unprompted, are still blocked where no approval handler exists (headless,
# REST without a bridge), and stay out of subagent children (`filter_allowed_for_mode` drops the
# whole AUTO_EDIT block union). The deny set is kept, empty, as the one place a future hard deny
# goes.
_AUTO_EDIT_DENY_TOOLS: frozenset[str] = frozenset()
_AUTO_EDIT_APPROVAL_TOOLS = frozenset({
    "run_bash", "spawn", "fanout",
    "git_branch", "git_commit", "git_push", "gh_pr_create",
})

#: What to do about an AUTO_EDIT denial. One string, both denial sites (`_make_block_rule` here and
#: `auth/decision.py`), because a remedy stated twice drifts.
#:
#: `core/stateless.py`'s `_capability_missing` already holds the principle: *"It also states that
#: re-issuing the call unchanged will fail identically, because the model is the reader and 'retry'
#: is otherwise the obvious response to a refusal."* This denial did not say that. A live session
#: burned a turn discovering it had to fall back to read-only tools, and a human reading
#: *"AUTO_EDIT mode blocks run_bash"* has no idea what to change.
_AUTO_EDIT_DENY_REMEDY = (
    "execution is a hard deny in this mode and is never prompted — set "
    "WISP_PERMISSION_MODE=ask_all (prompt before each command) or =full (unprompted) to run it. "
    "`run_tests` IS allowed here. Re-issuing unchanged will fail identically."
)

_DEFAULT_AUTO_EDIT_BLOCK = _AUTO_EDIT_DENY_TOOLS | _AUTO_EDIT_APPROVAL_TOOLS


def filter_allowed_for_mode(mode: str, tool_names) -> list[str]:
    """Tool names this permission mode can actually execute.

    Subagent children inherit the parent's mode and have no approval
    handler of their own; advertising a blocked tool only burns a child
    turn producing a guaranteed '[Blocked: ...]' result. FULL allows
    everything; READ_ONLY reduces to the safe-read set; ASK_ALL and
    AUTO_EDIT drop their deny-listed tools.
    """
    requested = [t for t in tool_names if t]
    m = (mode or "").strip().lower()
    if m == "full":
        return requested
    if m == "read_only":
        return [t for t in requested if t in _DEFAULT_SAFE_READ_TOOLS]
    blocked = (
        _DEFAULT_ASK_ALL_BLOCK if m == "ask_all" else _DEFAULT_AUTO_EDIT_BLOCK
    )
    return [t for t in requested if t not in blocked]


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


def _make_block_rule(blocked: frozenset[str], reason_template: str, mode_name: str,
                     suffix: str = "") -> RulePredicate:
    def predicate(action: Action, ctx: EvalContext) -> Optional[PolicyDecision]:
        if ctx.permission_mode != mode_name:
            return None
        if action.name in blocked:
            return PolicyDecision.deny(
                "mode.block",
                f"{reason_template} {action.name}{suffix}",
            )
        return None
    return predicate


def _make_approval_rule(gated: frozenset[str], reason_template: str, mode_name: str) -> RulePredicate:
    """REQUIRE_APPROVAL rule: the tool may run only after user approval.

    Unlike _make_block_rule, the decision stays allowed=True with
    approval_required set — the gate prompts instead of rejecting, and a
    missing/declining handler fails closed. Hard DENY must never route
    through here.
    """
    def predicate(action: Action, ctx: EvalContext) -> Optional[PolicyDecision]:
        if ctx.permission_mode != mode_name:
            return None
        if action.name in gated:
            return PolicyDecision.require_approval(
                "mode.require_approval",
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
