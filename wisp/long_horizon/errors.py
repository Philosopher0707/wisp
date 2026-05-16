"""Exceptions for long-horizon task execution."""

from __future__ import annotations


class TaskError(Exception):
    """Base exception for long-horizon task errors."""
    pass


class StepTimeoutError(TaskError):
    """Raised when a step exceeds its timeout."""
    pass


class MaxIterationsError(TaskError):
    """Raised when the task exceeds max_iterations without completion."""
    pass


class MaxReplansError(TaskError):
    """Raised when the task exceeds max_replans."""
    pass


class EscalationError(TaskError):
    """Raised when a task requires human intervention."""
    pass


class ReplanError(TaskError):
    """Raised when replanning fails (e.g., model error, invalid plan)."""
    pass


class DeadlockError(TaskError):
    """Raised when parallel execution encounters a dependency deadlock."""
    pass
