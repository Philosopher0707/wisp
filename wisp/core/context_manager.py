"""Lean live-context engine: bound in-memory tool payload bytes per session.

Problem: every turn appends full tool payloads (``read_file`` dumps,
``list_files`` trees) to ``session["messages"]``. Provider dispatch then
re-serializes ALL of it — per-turn cost grows with history, and the
process RSS follows. ``maybe_compact`` only fires at 50 messages and
summarizes whole turns; between compactions nothing bounds payload bytes.

This module enforces a byte budget on the LIVE session list (persisted
history is untouched — the store keeps full fidelity):
  - The most recent ``keep_last_n_full`` tool results stay verbatim
    (fresh context for the next provider call).
  - Older ``read_file``/``list_files`` results condense to status headers
    via the established ``context_pruner`` helpers (same idiom as the
    pre-dispatch pruner, applied once at rest instead of every dispatch).
  - Anything else historical is head/tail-truncated to the per-result cap.
  - A total ceiling sheds the oldest payloads first when still over budget.

The gate is cheap: one O(n) byte walk; sessions under budget return
immediately with no mutation (idempotent — condensed markers are never
re-condensed). Runtime calls this pre-turn inside the session lock.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextBudget:
    """Ceilings for live in-memory tool payload bytes."""

    keep_last_n_full: int = 3
    max_bytes_per_historical_result: int = 8192
    max_bytes_per_recent_result: int = 50000
    max_total_bytes: int = 200000
    read_file_historical_max_bytes: int = 2048
    list_files_historical_max_bytes: int = 2048

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.keep_last_n_full < 1:
            errors.append("keep_last_n_full must be >= 1")
        if self.max_total_bytes < self.max_bytes_per_recent_result:
            errors.append("max_total_bytes must cover one recent result")
        return errors


@dataclass
class PruneReport:
    """Observable outcome of one live-prune pass."""

    pruned_results: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    at_budget: bool = True


def _content_of(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    return content if isinstance(content, str) else ""


def live_tool_bytes(messages: list[dict[str, Any]]) -> int:
    """Sum of UTF-8 bytes across tool-role message contents (O(n))."""
    total = 0
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "tool":
            total += len(_content_of(message).encode("utf-8", errors="ignore"))
    return total


def _tool_name(message: dict[str, Any]) -> str:
    name = message.get("name", "")
    return name if isinstance(name, str) else ""


def _condense_one(
    content: str,
    tool_name: str,
    is_recent: bool,
    budget: ContextBudget,
) -> str:
    """Condense a single historical payload (never touches recent ones)."""
    from wisp.core.context_pruner import (
        condense_list_files_result,
        condense_read_file_result,
        enforce_byte_ceiling,
    )

    if is_recent:
        if len(content.encode("utf-8", errors="ignore")) <= budget.max_bytes_per_recent_result:
            return content
        return enforce_byte_ceiling(content, budget.max_bytes_per_recent_result)
    if tool_name == "read_file":
        return condense_read_file_result(content, budget.read_file_historical_max_bytes)
    if tool_name == "list_files":
        return condense_list_files_result(content, budget.list_files_historical_max_bytes)
    if len(content.encode("utf-8", errors="ignore")) <= budget.max_bytes_per_historical_result:
        return content
    return enforce_byte_ceiling(content, budget.max_bytes_per_historical_result)


def prune_live_session(
    session: dict[str, Any],
    budget: ContextBudget | None = None,
) -> PruneReport:
    """Condense historical tool payloads in the live session list, in place.

    Only tool-role messages older than the most recent ``keep_last_n_full``
    are touched; user/assistant messages are never modified. Returns a
    report; when the session is already under budget nothing is mutated.
    Idempotent: re-running over condensed output is a no-op (markers and
    short contents pass through unchanged).
    """
    budget = budget or ContextBudget()
    messages = session.get("messages", [])
    if not isinstance(messages, list):
        return PruneReport(at_budget=True)

    before = live_tool_bytes(messages)
    if before <= budget.max_total_bytes:
        return PruneReport(bytes_before=before, bytes_after=before, at_budget=True)

    tool_indices = [i for i, m in enumerate(messages)
                    if isinstance(m, dict) and m.get("role") == "tool"]
    recent = set(tool_indices[-budget.keep_last_n_full:]) if tool_indices else set()

    pruned = 0
    for i in tool_indices:
        if i in recent:
            continue
        message = messages[i]
        content = _content_of(message)
        if not content:
            continue
        condensed = _condense_one(content, _tool_name(message), False, budget)
        if condensed != content:
            message["content"] = condensed
            pruned += 1

    # Total ceiling: shed oldest payloads first if still over budget.
    if live_tool_bytes(messages) > budget.max_total_bytes:
        from wisp.core.context_pruner import enforce_byte_ceiling

        for i in tool_indices:
            if i in recent:
                continue
            message = messages[i]
            content = _content_of(message)
            if not content:
                continue
            smaller = enforce_byte_ceiling(content, budget.max_bytes_per_historical_result)
            if smaller != content:
                message["content"] = smaller
                pruned += 1
            if live_tool_bytes(messages) <= budget.max_total_bytes:
                break

    after = live_tool_bytes(messages)
    return PruneReport(pruned_results=pruned, bytes_before=before,
                       bytes_after=after, at_budget=after <= budget.max_total_bytes)
