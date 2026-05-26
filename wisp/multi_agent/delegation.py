"""Auto-delegation triggers — detect when a task should be delegated to subagents.

This module provides capability mismatch detection for the agent loop.
When the main agent encounters a task that is too complex, multi-faceted,
or outside its current context, it can automatically delegate to specialized
subagents.

Delegation classification uses a hybrid approach:
1. LLM-based classification (primary) — single-call prompt, 5s timeout
2. Keyword-based scoring (fallback) — when LLM unavailable or times out
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

_LLM_CLASSIFY_PROMPT = (
    "Analyze this task and determine if it should be delegated to specialized "
    "subagents. A task benefits from delegation if it is multi-faceted, requires "
    "parallel research, or spans many files.\n\n"
    "Task: {task}\n\n"
    'Respond with JSON only: {{"delegate": true/false, "confidence": 0.0-1.0, '
    '"reason": "short reason", "subagents": ["role1", "role2"]}}'
)


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
    """Analyzes prompts to detect capability mismatch and suggest delegation.

    Hybrid: LLM classification (primary) → keyword scoring (fallback).
    """

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

    # ── Public API ──────────────────────────────────────────────────────

    async def analyze_with_llm(
        self,
        prompt: str,
        llm_call: Callable[[str], Awaitable[str]],
        current_iteration: int = 0,
        max_iterations: int = 10,
    ) -> DelegationSignal:
        """Analyze prompt using LLM classification with keyword fallback.

        Args:
            prompt: The user's prompt.
            llm_call: Async callable that takes a prompt string and returns
                      the LLM's text response. Called with a classification
                      prompt; expects a JSON response.
            current_iteration: Current iteration in the agent loop.
            max_iterations: Maximum iterations allowed.

        Returns:
            DelegationSignal with should_delegate and suggested contracts.
        """
        try:
            classify_prompt = _LLM_CLASSIFY_PROMPT.format(task=prompt[:800])
            response = await asyncio.wait_for(
                llm_call(classify_prompt), timeout=5.0,
            )
            return self._parse_llm_response(prompt, response)
        except asyncio.TimeoutError:
            logger.debug("LLM delegation classification timed out — using keyword fallback")
        except Exception:
            logger.debug("LLM delegation classification failed — using keyword fallback", exc_info=True)

        return self.analyze(prompt, current_iteration, max_iterations)

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
        if complexity_score >= 0.05:
            score += complexity_score * 0.4
            reasons.append(f"complexity({complexity_score:.2f})")

        # Check 2: Research orientation
        research_score = self._score_research(prompt_lower)
        if research_score >= 0.05:
            score += research_score * 0.35
            reasons.append(f"research({research_score:.2f})")

        # Check 3: Multi-file scope
        multi_file_score = self._score_multi_file(prompt_lower)
        if multi_file_score >= 0.05:
            score += multi_file_score * 0.3
            reasons.append(f"multi-file({multi_file_score:.2f})")

        # Check 4: Specialized knowledge
        specialized_score = self._score_specialized(prompt_lower)
        if specialized_score >= 0.05:
            score += specialized_score * 0.3
            reasons.append(f"specialized({specialized_score:.2f})")

        # Check 5: Iteration pressure (running out of iterations)
        if current_iteration > max_iterations * 0.6:
            score += 0.5
            reasons.append(f"iteration_pressure({current_iteration}/{max_iterations})")

        # Check 6: Explicit delegation request
        if any(kw in prompt_lower for kw in ["delegate", "spawn agent", "parallel"]):
            score += 0.5
            reasons.append("explicit_request")

        # Determine if we should delegate
        should_delegate = score >= 0.18

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
        elif len(prompt) > 80:
            score += 0.2
        elif len(prompt) > 40:
            score += 0.1

        # Keyword matches
        matches = sum(1 for kw in self.COMPLEXITY_INDICATORS if kw in prompt)
        score += min(matches * 0.2, 0.6)

        # Word count
        if len(words) > 20:
            score += 0.15

        return min(score, 1.0)

    def _score_research(self, prompt: str) -> float:
        """Score research orientation (0.0 to 1.0)."""
        matches = sum(1 for kw in self.RESEARCH_INDICATORS if kw in prompt)
        return min(matches * 0.4, 1.0)

    def _score_multi_file(self, prompt: str) -> float:
        """Score multi-file scope (0.0 to 1.0)."""
        matches = sum(1 for kw in self.MULTI_FILE_INDICATORS if kw in prompt)
        return min(matches * 0.5, 1.0)

    def _score_specialized(self, prompt: str) -> float:
        """Score specialized knowledge requirement (0.0 to 1.0)."""
        matches = sum(1 for kw in self.SPECIALIZED_INDICATORS if kw in prompt)
        return min(matches * 0.25, 1.0)

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
                "timeout_seconds": 180,
                "max_iterations": 15,
            })

        # Implementation contract
        if any(kw in prompt_lower for kw in self.COMPLEXITY_INDICATORS):
            contracts.append({
                "name": "implementer",
                "role": "coder",
                "task": f"Implement the solution for: {prompt[:200]}",
                "timeout_seconds": 300,
                "max_iterations": 20,
            })

        # Review contract
        if len(contracts) >= 2:
            contracts.append({
                "name": "reviewer",
                "role": "reviewer",
                "task": f"Review the approach for: {prompt[:200]}",
                "timeout_seconds": 180,
                "max_iterations": 15,
            })

        return contracts

    def _parse_llm_response(self, prompt: str, response: str) -> DelegationSignal:
        """Parse LLM classification response into a DelegationSignal."""
        try:
            # Extract JSON from response (may have markdown fences)
            json_str = response.strip()
            if json_str.startswith("```"):
                lines = json_str.split("\n")
                json_str = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            logger.debug("Failed to parse LLM delegation response, falling back to keyword")
            return self.analyze(prompt)

        should_delegate = data.get("delegate", False)
        confidence = float(data.get("confidence", 0.0))
        reason = data.get("reason", "LLM classified")
        roles = data.get("subagents", [])

        contracts = []
        role_to_config = {
            "researcher": {"role": "researcher", "timeout_seconds": 180, "max_iterations": 15},
            "coder": {"role": "coder", "timeout_seconds": 300, "max_iterations": 20},
            "reviewer": {"role": "reviewer", "timeout_seconds": 180, "max_iterations": 15},
            "architect": {"role": "architect", "timeout_seconds": 240, "max_iterations": 20},
            "generalist": {"role": "generalist", "timeout_seconds": 180, "max_iterations": 15},
        }
        for i, role in enumerate(roles):
            cfg = role_to_config.get(role, {"role": "generalist", "timeout_seconds": 180, "max_iterations": 15})
            contracts.append({
                "name": f"{role}-{i}",
                "role": cfg["role"],
                "task": f"[{role}] {prompt[:200]}",
                "timeout_seconds": cfg["timeout_seconds"],
                "max_iterations": cfg["max_iterations"],
            })

        return DelegationSignal(
            should_delegate=should_delegate,
            reason=reason,
            suggested_contracts=contracts if should_delegate else [],
            confidence=confidence,
        )


# Global analyzer instance
_default_analyzer: Optional[DelegationAnalyzer] = None


def get_delegation_analyzer() -> DelegationAnalyzer:
    """Get the default delegation analyzer."""
    global _default_analyzer
    if _default_analyzer is None:
        _default_analyzer = DelegationAnalyzer()
    return _default_analyzer
