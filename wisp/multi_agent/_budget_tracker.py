"""BudgetTracker — token accounting for subagent execution."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class BudgetTracker:
    """Track token consumption across subagent runs.

    Simple, focused class with no side effects.
    """

    def __init__(self):
        self._tokens_consumed: int = 0
        self._global_budget: Optional[int] = None

    def set_budget(self, budget: Optional[int]) -> None:
        """Set global token budget. None = unlimited."""
        self._global_budget = budget
        logger.info("Global token budget set to %s", budget if budget else "unlimited")

    def get_consumed(self) -> int:
        """Total tokens consumed so far."""
        return self._tokens_consumed

    def get_remaining(self) -> Optional[int]:
        """Remaining budget, or None if unlimited."""
        if self._global_budget is None:
            return None
        return max(0, self._global_budget - self._tokens_consumed)

    def check(self) -> Optional[str]:
        """Check if budget is exhausted. Returns error message or None."""
        remaining = self.get_remaining()
        if remaining is not None and remaining <= 0:
            return f"Global token budget exhausted ({self._global_budget} tokens)"
        return None

    def record(self, tokens: int) -> None:
        """Record token consumption."""
        self._tokens_consumed += tokens

    def remove_budget(self) -> None:
        """Remove the global budget."""
        self._global_budget = None
