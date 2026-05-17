"""Auto-detection for long-horizon tasks.

Determines whether a user prompt should be treated as a multi-step,
persistent long-horizon task or handled as a single-turn interaction.

Usage:
    from wisp.long_horizon.trigger import detect_long_task

    should_use, reason = detect_long_task("Migrate Flask to FastAPI")
    # → (True, "Action keyword: migrate")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Keyword sets ───────────────────────────────────────────────────

# Strong indicators: almost always multi-step
LONG_TASK_KEYWORDS: set[str] = {
    "migrate", "refactor", "rewrite", "restructure", "modernize",
    "implement", "build", "create", "integrate", "upgrade",
    "convert", "port", "transition", "overhaul", "redesign",
    "rearchitect", "consolidate", "split", "extract",
    "add authentication", "add auth", "add authorization",
    "setup ci/cd", "setup pipeline", "dockerize", "containerize",
    "deploy", "provision", "orchestrate",
}

# Medium indicators: often multi-step depending on scope
MEDIUM_KEYWORDS: set[str] = {
    "update", "modify", "change", "fix", "improve", "enhance",
    "optimize", "clean up", "organize", "standardize",
    "add tests", "add documentation", "add logging",
    "error handling", "validation", "caching",
}

# Scope amplifiers: make medium keywords become long-task
SCOPE_AMPLIFIERS: set[str] = {
    "entire", "whole", "all", "complete", "full",
    "system", "module", "architecture", "framework",
    "project", "codebase", "application", "app",
    "every", "each", "across", "throughout",
    "multiple", "many", "several", "various",
}

# Single-turn indicators: never long-task
SHORT_TASK_INDICATORS: set[str] = {
    "what is", "how to", "explain", "show me", "tell me",
    "quick", "one line", "simple", "brief", "short",
    "syntax", "example", "snippet", "pattern",
}

# ── Thresholds ─────────────────────────────────────────────────────

MIN_PROMPT_LENGTH = 80          # Characters below this → likely short
COMPLEX_PROMPT_LENGTH = 200    # Characters above this → likely long
MAX_SHORT_WORDS = 12             # Words below this → likely short
MIN_LONG_WORDS = 25            # Words above this → likely long

# ── Public API ─────────────────────────────────────────────────────

def detect_long_task(prompt: str, workspace: str = ".") -> tuple[bool, str]:
    """Detect if a prompt should be treated as a long-horizon task.

    Returns:
        (should_use_long_task, reason_string)

    The detection uses a scoring system:
      - Strong keywords: +3 points each
      - Medium keywords with scope amplifiers: +2 points
      - Complex prompt length: +1 point
      - Short-task indicators: -3 points (override)

    Threshold: score >= 3 → long task
    """
    if not prompt or not prompt.strip():
        return False, "Empty prompt"

    prompt_lower = prompt.lower().strip()
    words = prompt_lower.split()
    score = 0
    reasons: list[str] = []

    # ── 1. Short-task override ──
    # Only override if the score is low (simple questions that happen to contain keywords)
    for indicator in SHORT_TASK_INDICATORS:
        if indicator in prompt_lower:
            # Quick pre-check: if we already have strong signals, don't override
            pre_score = 0
            for kw in LONG_TASK_KEYWORDS:
                if kw in prompt_lower:
                    pre_score += 3
            if pre_score >= 3:
                break  # Don't override strong signals
            return False, f"Short-task indicator: '{indicator}'"

    # ── 2. Strong keyword detection ──
    for keyword in LONG_TASK_KEYWORDS:
        if keyword in prompt_lower:
            score += 3
            reasons.append(f"action keyword: '{keyword}'")

    # ── 3. Medium keyword + scope amplifier ──
    has_medium = any(kw in prompt_lower for kw in MEDIUM_KEYWORDS)
    has_scope = any(amp in prompt_lower for amp in SCOPE_AMPLIFIERS)
    if has_medium and has_scope:
        score += 2
        reasons.append("medium keyword with broad scope")

    # ── 4. Prompt length heuristics ──
    char_count = len(prompt)
    word_count = len(words)

    # Only use brief prompt check if no strong keywords found
    if not reasons and char_count < MIN_PROMPT_LENGTH and word_count < MAX_SHORT_WORDS:
        return False, "Brief prompt — likely single-turn"

    if char_count > COMPLEX_PROMPT_LENGTH:
        score += 2
        reasons.append("detailed requirements")
    elif char_count > MIN_PROMPT_LENGTH:
        score += 1
        reasons.append("substantial request")

    # ── 5. File impact estimation ──
    file_estimate = _estimate_file_impact(prompt_lower, workspace)
    if file_estimate > 3:
        score += 2
        reasons.append(f"estimated {file_estimate}+ files affected")
    elif file_estimate > 1:
        score += 1
        reasons.append(f"estimated {file_estimate} files affected")

    # ── 6. Step-like structure ──
    step_indicators = ["step", "first", "then", "next", "after", "finally",
                       "1.", "2.", "3.", "(1)", "(2)", "(3)",
                       "- ", "* ", "→", "=>"]
    step_count = sum(1 for ind in step_indicators if ind in prompt_lower)
    if step_count >= 3:
        score += 3
        reasons.append("explicit multi-step structure")
    elif step_count >= 2:
        score += 2
        reasons.append("sequential instructions")
    elif step_count >= 1:
        score += 1
        reasons.append("step-like language")

    # ── Decision ──
    if score >= 3:
        reason_str = "; ".join(reasons[:3])  # Cap at 3 reasons
        logger.debug("Long-task detected (score=%d): %s", score, reason_str)
        return True, reason_str

    return False, f"Score {score} below threshold — single-turn task"


def detect_long_task_with_confidence(prompt: str, workspace: str = ".") -> dict:
    """Extended detection with full scoring breakdown.

    Returns a dict with:
        - should_use: bool
        - reason: str
        - score: int
        - breakdown: dict of individual scores
    """
    should_use, reason = detect_long_task(prompt, workspace)
    # Re-run internal scoring for breakdown
    breakdown = _score_breakdown(prompt)
    return {
        "should_use": should_use,
        "reason": reason,
        "score": breakdown["total"],
        "breakdown": breakdown,
    }


# ── Internal helpers ───────────────────────────────────────────────

def _estimate_file_impact(prompt_lower: str, workspace: str) -> int:
    """Estimate how many files might be affected.

    Uses simple heuristics:
      - Mention of specific file extensions → +1 per extension
      - Mention of directories → +2
      - "all files" / "every file" → +5
    """
    count = 0

    # File extensions mentioned
    extensions = [".py", ".js", ".ts", ".go", ".rs", ".java", ".cpp",
                  ".json", ".yaml", ".yml", ".toml", ".sql"]
    for ext in extensions:
        if ext in prompt_lower:
            count += 1

    # Directory references
    dir_indicators = ["folder", "directory", "path", "src/", "lib/",
                      "app/", "tests/", "config/"]
    for ind in dir_indicators:
        if ind in prompt_lower:
            count += 1

    # Broad scope
    if "all files" in prompt_lower or "every file" in prompt_lower:
        count += 5

    # Try to count actual files in workspace if accessible
    try:
        ws = Path(workspace)
        if ws.exists() and "entire" in prompt_lower or "whole" in prompt_lower:
            py_files = list(ws.rglob("*.py"))
            if len(py_files) > 10:
                count += 3
    except Exception:
        pass

    return min(count, 10)  # Cap at 10


def _score_breakdown(prompt: str) -> dict:
    """Return detailed scoring breakdown for debugging."""
    prompt_lower = prompt.lower().strip()
    words = prompt_lower.split()

    breakdown = {
        "strong_keywords": 0,
        "medium_with_scope": 0,
        "length": 0,
        "file_impact": 0,
        "step_structure": 0,
        "total": 0,
    }

    for keyword in LONG_TASK_KEYWORDS:
        if keyword in prompt_lower:
            breakdown["strong_keywords"] += 3

    has_medium = any(kw in prompt_lower for kw in MEDIUM_KEYWORDS)
    has_scope = any(amp in prompt_lower for amp in SCOPE_AMPLIFIERS)
    if has_medium and has_scope:
        breakdown["medium_with_scope"] = 2

    if len(prompt) > COMPLEX_PROMPT_LENGTH:
        breakdown["length"] = 1
    if len(words) > MIN_LONG_WORDS:
        breakdown["length"] += 1

    file_estimate = _estimate_file_impact(prompt_lower, ".")
    if file_estimate > 3:
        breakdown["file_impact"] = 2
    elif file_estimate > 1:
        breakdown["file_impact"] = 1

    step_indicators = ["step", "first", "then", "next", "after", "finally",
                       "1.", "2.", "3.", "(1)", "(2)", "(3)",
                       "- ", "* ", "→", "=>"]
    step_count = sum(1 for ind in step_indicators if ind in prompt_lower)
    if step_count >= 3:
        breakdown["step_structure"] = 2
    elif step_count >= 2:
        breakdown["step_structure"] = 1

    breakdown["total"] = (
        breakdown["strong_keywords"] +
        breakdown["medium_with_scope"] +
        breakdown["length"] +
        breakdown["file_impact"] +
        breakdown["step_structure"]
    )
    return breakdown
