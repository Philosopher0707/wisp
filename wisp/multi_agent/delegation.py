"""Auto-delegation triggers — detect when a task should be delegated to subagents.

This module provides capability mismatch detection for the agent loop.
When the main agent encounters a task that is too complex, multi-faceted,
or outside its current context, it can automatically delegate to specialized
subagents.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DelegationSignal:
    """Result of delegation analysis."""
    should_delegate: bool
    reason: str = ""
    suggested_contracts: list[dict] = None
    confidence: float = 0.0  # 0.0 to 1.0

    def __post_init__(self):
        if self.suggested_contracts is None:
            self.suggested_contracts = []


class DelegationAnalyzer:
    """Analyzes prompts to detect capability mismatch and suggest delegation."""

    # Keywords that suggest multi-faceted tasks
    COMPLEXITY_INDICATORS = [
        "implement", "build", "create", "design", "refactor",
        "architecture", "system", "framework", "library",
        "multi-step", "complex", "complicated", "sophisticated",
        "end-to-end", "full-stack", "integration",
    ]

    # Keywords that suggest research/investigation
    RESEARCH_INDICATORS = [
        "research", "investigate", "analyze", "compare", "survey",
        "evaluate", "benchmark", "study", "explore",
    ]

    # Keywords that suggest multiple files/modules
    MULTI_FILE_INDICATORS = [
        "across", "throughout", "all files", "multiple", "every",
        "entire codebase", "whole project", "global",
    ]

    # Keywords that suggest specialized knowledge
    SPECIALIZED_INDICATORS = [
        "security", "performance", "optimization", "memory",
        "concurrency", "async", "threading", "crypto",
        "authentication", "authorization", "database",
        "frontend", "backend", "api", "microservice",
    ]

    def __init__(self, max_prompt_length: int = 100):
        self.max_prompt_length = max_prompt_length

    def analyze(self, prompt: str, current_iteration: int = 0,
                max_iterations: int = 10) -> DelegationSignal:
        """Analyze a prompt and return delegation recommendation.

        Args:
            prompt: The user's prompt
            current_iteration: Current iteration in the agent loop
            max_iterations: Maximum iterations allowed

        Returns:
            DelegationSignal with should_delegate=True if delegation is recommended
        """
        prompt_lower = prompt.lower()
        score = 0.0
        reasons = []

        # Check 1: Prompt complexity (length + keywords)
        complexity_score = self._score_complexity(prompt_lower)
        if complexity_score > 0.5:
            score += complexity_score * 0.3
            reasons.append(f"complexity({complexity_score:.2f})")

        # Check 2: Research orientation
        research_score = self._score_research(prompt_lower)
        if research_score > 0.3:
            score += research_score * 0.25
            reasons.append(f"research({research_score:.2f})")

        # Check 3: Multi-file scope
        multi_file_score = self._score_multi_file(prompt_lower)
        if multi_file_score > 0.3:
            score += multi_file_score * 0.2
            reasons.append(f"multi-file({multi_file_score:.2f})")

        # Check 4: Specialized knowledge
        specialized_score = self._score_specialized(prompt_lower)
        if specialized_score > 0.3:
            score += specialized_score * 0.15
            reasons.append(f"specialized({specialized_score:.2f})")

        # Check 5: Iteration pressure (running out of iterations)
        if current_iteration > max_iterations * 0.7:
            score += 0.2
            reasons.append(f"iteration_pressure({current_iteration}/{max_iterations})")

        # Check 6: Explicit delegation request
        if any(kw in prompt_lower for kw in ["delegate", "spawn agent", "parallel"]):
            score += 0.5
            reasons.append("explicit_request")

        # Determine if we should delegate
        should_delegate = score >= 0.6

        if should_delegate:
            contracts = self._suggest_contracts(prompt, reasons)
            return DelegationSignal(
                should_delegate=True,
                reason="; ".join(reasons),
                suggested_contracts=contracts,
                confidence=min(score, 1.0),
            )

        return DelegationSignal(should_delegate=False, confidence=score)

    def _score_complexity(self, prompt: str) -> float:
        """Score prompt complexity (0.0 to 1.0)."""
        score = 0.0
        words = prompt.split()

        # Length factor
        if len(prompt) > 200:
            score += 0.3
        elif len(prompt) > 100:
            score += 0.15

        # Keyword matches
        matches = sum(1 for kw in self.COMPLEXITY_INDICATORS if kw in prompt)
        score += min(matches * 0.15, 0.5)

        # Word count
        if len(words) > 30:
            score += 0.2

        return min(score, 1.0)

    def _score_research(self, prompt: str) -> float:
        """Score research orientation (0.0 to 1.0)."""
        matches = sum(1 for kw in self.RESEARCH_INDICATORS if kw in prompt)
        return min(matches * 0.3, 1.0)

    def _score_multi_file(self, prompt: str) -> float:
        """Score multi-file scope (0.0 to 1.0)."""
        matches = sum(1 for kw in self.MULTI_FILE_INDICATORS if kw in prompt)
        return min(matches * 0.4, 1.0)

    def _score_specialized(self, prompt: str) -> float:
        """Score specialized knowledge requirement (0.0 to 1.0)."""
        matches = sum(1 for kw in self.SPECIALIZED_INDICATORS if kw in prompt)
        return min(matches * 0.2, 1.0)

    def _suggest_contracts(self, prompt: str, reasons: list[str]) -> list[dict]:
        """Generate suggested subagent contracts based on the prompt."""
        contracts = []
        prompt_lower = prompt.lower()

        # Research contract
        if any(kw in prompt_lower for kw in self.RESEARCH_INDICATORS):
            contracts.append({
                "name": "researcher",
                "role": "researcher",
                "task": f"Research and analyze: {prompt[:200]}",
                "timeout_seconds": 60,
                "max_iterations": 5,
            })

        # Implementation contract
        if any(kw in prompt_lower for kw in self.COMPLEXITY_INDICATORS):
            contracts.append({
                "name": "implementer",
                "role": "coder",
                "task": f"Implement the solution for: {prompt[:200]}",
                "timeout_seconds": 120,
                "max_iterations": 10,
            })

        # Review contract
        if len(contracts) >= 2:
            contracts.append({
                "name": "reviewer",
                "role": "reviewer",
                "task": f"Review the approach for: {prompt[:200]}",
                "timeout_seconds": 60,
                "max_iterations": 5,
            })

        return contracts


# Global analyzer instance
_default_analyzer: Optional[DelegationAnalyzer] = None


def get_delegation_analyzer() -> DelegationAnalyzer:
    """Get the default delegation analyzer."""
    global _default_analyzer
    if _default_analyzer is None:
        _default_analyzer = DelegationAnalyzer()
    return _default_analyzer
