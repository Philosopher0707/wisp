"""Capability mismatch detection and auto-delegation triggers.

This module analyzes tasks and agent capabilities to detect when the current
agent cannot handle a task and should delegate to a subagent with the right
role, tools, or expertise.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from .roles import ROLE_CONFIGS, AgentRole
from .task import SubagentContract

logger = logging.getLogger(__name__)


@dataclass
class CapabilityMismatch:
    """Describes a detected capability mismatch."""

    reason: str
    """Human-readable reason for the mismatch."""

    required_role: str
    """Suggested role that can handle this task."""

    required_tools: list[str]
    """Tools required but not available to the current agent."""

    confidence: float = 0.5
    """Confidence score 0.0–1.0 that delegation is needed."""

    def should_delegate(self, threshold: float = 0.6) -> bool:
        """Return True if confidence exceeds the delegation threshold."""
        return self.confidence >= threshold


class CapabilityMatcher:
    """Detects capability mismatches and suggests delegation targets.

    Usage::

        matcher = CapabilityMatcher()
        mismatch = matcher.detect_mismatch(
            current_role="reviewer",
            task="Write unit tests for auth.py",
            available_tools=["read_file", "edit_file"],
        )
        if mismatch and mismatch.should_delegate():
            contract = matcher.build_delegation_contract(mismatch, task)
    """

    # ── Task → role heuristic keywords ───────────────────────────────

    ROLE_KEYWORDS: dict[str, list[str]] = {
        AgentRole.CODER: [
            "write", "implement", "create", "add", "build", "refactor",
            "extract", "migrate", "port", "generate code", "script",
            "class", "function", "method", "module", "api",
        ],
        AgentRole.REVIEWER: [
            "review", "audit", "check", "inspect", "critique",
            "approve", "reject", "sign-off", "code review",
            "style", "lint", "correctness", "security audit",
        ],
        AgentRole.TESTER: [
            "test", "tests", "testing", "unit test", "integration test",
            "e2e", "coverage", "pytest", "jest", "verify behavior",
            "reproduce", "regression", "benchmark",
        ],
        AgentRole.RESEARCHER: [
            "research", "investigate", "find", "lookup", "search",
            "document", "explain", "what is", "how to", "why does",
            "compare", "evaluate", "survey", "gather context",
        ],
        AgentRole.DEBUGGER: [
            "debug", "fix", "bug", "crash", "error", "traceback",
            "exception", "breakpoint", "diagnose", "root cause",
            "stack trace", "null pointer", "segmentation fault",
        ],
        AgentRole.PLANNER: [
            "plan", "design", "architecture", "break down", "decompose",
            "roadmap", "milestone", "schedule", "estimate", "strategy",
        ],
    }

    # ── Tool → role mapping ──────────────────────────────────────────

    ROLE_REQUIRED_TOOLS: dict[str, list[str]] = {
        AgentRole.CODER: ["write_file", "edit_file", "edit_file_multi"],
        AgentRole.REVIEWER: ["read_file", "git_diff", "git_status"],
        AgentRole.TESTER: ["run_bash", "write_file"],
        AgentRole.RESEARCHER: ["web_fetch", "web_search", "search_symbols"],
        AgentRole.DEBUGGER: ["run_bash", "read_file", "edit_file"],
        AgentRole.PLANNER: ["read_file", "list_files", "search_symbols"],
    }

    def __init__(self, delegation_threshold: float = 0.2):
        self.delegation_threshold = delegation_threshold

    def detect_mismatch(
        self,
        current_role: str,
        task: str,
        available_tools: Optional[list[str]] = None,
    ) -> Optional[CapabilityMismatch]:
        """Analyze a task and detect if the current agent is mismatched.

        Returns ``None`` if no mismatch is detected (or confidence is too low).
        """
        task_lower = task.lower()
        suggested = self.suggest_role(task)

        # If the suggested role matches current role, no mismatch
        if suggested == current_role:
            return None

        # Calculate confidence based on keyword overlap
        confidence = self._score_role_fit(task_lower, suggested)

        # Boost confidence if the current role explicitly lacks required tools
        missing_tools: list[str] = []
        if available_tools is not None:
            missing_tools = self._missing_tools_for_task(task_lower, available_tools)
            if missing_tools:
                confidence = min(1.0, confidence + 0.2)

        # Only flag if confidence exceeds threshold
        if confidence < self.delegation_threshold:
            return None

        reason = (
            f"Task '{task[:60]}...' requires {suggested} capabilities, "
            f"but current agent is a {current_role}."
        )
        if missing_tools:
            reason += f" Missing tools: {', '.join(missing_tools)}."

        return CapabilityMismatch(
            reason=reason,
            required_role=suggested,
            required_tools=missing_tools,
            confidence=confidence,
        )

    def suggest_role(self, task: str) -> str:
        """Suggest the best role for a given task based on keyword matching."""
        task_lower = task.lower()
        scores: dict[str, float] = {}

        for role in self.ROLE_KEYWORDS:
            scores[role] = self._score_role_fit(task_lower, role)

        # Default to generalist if no strong match
        if not scores or max(scores.values()) == 0:
            return AgentRole.CODER  # Default fallback

        return max(scores, key=scores.get)

    def build_delegation_contract(
        self,
        mismatch: CapabilityMismatch,
        task: str,
        parent_context: str = "",
    ) -> SubagentContract:
        """Build a SubagentContract for delegating a mismatched task."""
        role_cfg = ROLE_CONFIGS.get(mismatch.required_role)
        tools = ["all"]
        if role_cfg and role_cfg.allowed_tools:
            tools = list(role_cfg.allowed_tools)

        contract = SubagentContract(
            name=f"delegate-{mismatch.required_role}",
            role=mismatch.required_role,
            task=task,
            tools=tools,
            max_iterations=role_cfg.max_iterations if role_cfg else 15,
            timeout_seconds=role_cfg.timeout_seconds if role_cfg else 120.0,
            output_format="text",
        )

        if parent_context:
            contract.system_prompt_extra = (
                f"\n## Parent Context\n{parent_context}\n"
            )

        return contract

    def _score_role_fit(self, task_lower: str, role: str) -> float:
        """Score how well a role fits a task (0.0–1.0)."""
        keywords = self.ROLE_KEYWORDS.get(role, [])
        if not keywords:
            return 0.0

        score = 0.0
        hits = 0
        for kw in keywords:
            pattern = r"\b" + re.escape(kw.lower()) + r"\b"
            matches = len(re.findall(pattern, task_lower))
            if matches:
                hits += 1
                weight = 1.0 + len(kw) * 0.05
                score += matches * weight

        # Base score: fraction of keywords matched (capped)
        base = min(hits / max(len(keywords) * 0.3, 3.0), 1.0)

        # Boost for strong signals (multiple hits)
        if hits >= 2:
            base = min(1.0, base + 0.15)
        if hits >= 3:
            base = min(1.0, base + 0.15)

        return max(base, min(score * 0.15, 1.0))

    def _missing_tools_for_task(
        self, task_lower: str, available_tools: list[str]
    ) -> list[str]:
        """Detect tools that are likely needed but not available."""
        missing: list[str] = []
        available_set = set(t.lower() for t in available_tools)

        # Heuristic: if task mentions testing but run_bash is missing
        if any(k in task_lower for k in ("test", "pytest", "jest", "coverage")):
            if "run_bash" not in available_set:
                missing.append("run_bash")

        # Heuristic: if task mentions web/research but web_fetch is missing
        if any(k in task_lower for k in ("web", "url", "fetch", "search online", "online", "internet")):
            if "web_fetch" not in available_set and "web_search" not in available_set:
                missing.append("web_fetch")

        # Heuristic: if task mentions writing files but write_file is missing
        if any(k in task_lower for k in ("write", "create file", "generate")):
            if "write_file" not in available_set:
                missing.append("write_file")

        # Heuristic: if task mentions editing but edit_file is missing
        if any(k in task_lower for k in ("edit", "modify", "fix", "update")):
            if "edit_file" not in available_set and "edit_file_multi" not in available_set:
                missing.append("edit_file")

        return missing


# ── Singleton for convenient import ────────────────────────────────────

_default_matcher = CapabilityMatcher()


def detect_mismatch(
    current_role: str,
    task: str,
    available_tools: Optional[list[str]] = None,
) -> Optional[CapabilityMismatch]:
    """Convenience function using the default matcher."""
    return _default_matcher.detect_mismatch(current_role, task, available_tools)


def suggest_role(task: str) -> str:
    """Convenience function using the default matcher."""
    return _default_matcher.suggest_role(task)
