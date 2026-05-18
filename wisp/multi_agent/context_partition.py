"""Context partitioning — filter relevant messages for subagent context.

Instead of passing the full conversation history to subagents (which wastes
tokens and confuses the model), this module extracts only the messages
relevant to the current task.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ContextPartitioner:
    """Partitions conversation history into relevant subsets for subagents."""

    def __init__(self, max_messages: int = 10, max_tokens: int = 4000):
        self.max_messages = max_messages
        self.max_tokens = max_tokens

    def partition(self, messages: list[dict], task: str,
                  include_system: bool = True) -> list[dict]:
        """Extract relevant messages for a subagent task.

        Strategy:
        1. Always include system messages
        2. Include the most recent user-assistant exchange
        3. Include messages that mention files/tools relevant to the task
        4. Truncate to max_messages

        Args:
            messages: Full conversation history
            task: The subagent's task description
            include_system: Whether to include system messages

        Returns:
            Filtered list of messages
        """
        if not messages:
            return []

        task_lower = task.lower()
        result = []

        # Extract file/tool mentions from task
        task_files = self._extract_file_mentions(task)
        task_tools = self._extract_tool_mentions(task)

        # Score each message for relevance
        scored = []
        for i, msg in enumerate(messages):
            score = self._score_message(msg, task_lower, task_files, task_tools)
            scored.append((score, i, msg))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        # Take top messages up to max_messages
        selected = scored[:self.max_messages]

        # Sort back by original index to preserve order
        selected.sort(key=lambda x: x[1])

        # Build result
        for score, idx, msg in selected:
            if msg.get("role") == "system" and not include_system:
                continue
            result.append(msg)

        # Always ensure we have at least the last user message
        if messages and messages[-1].get("role") == "user":
            if messages[-1] not in result:
                result.append(messages[-1])

        return result

    def _score_message(self, msg: dict, task_lower: str,
                       task_files: set, task_tools: set) -> float:
        """Score a message's relevance to the task (0.0 to 1.0)."""
        score = 0.0
        role = msg.get("role", "")
        content = str(msg.get("content", "")).lower()

        # System messages are always relevant
        if role == "system":
            score += 1.0

        # Recent messages are more relevant
        # (handled by caller ordering, but boost last user message)

        # File mention overlap
        msg_files = self._extract_file_mentions(content)
        if msg_files & task_files:
            score += 0.5 * len(msg_files & task_files)

        # Tool mention overlap
        msg_tools = self._extract_tool_mentions(content)
        if msg_tools & task_tools:
            score += 0.3 * len(msg_tools & task_tools)

        # Content similarity (simple keyword overlap)
        task_words = set(task_lower.split())
        content_words = set(content.split())
        if task_words and content_words:
            overlap = len(task_words & content_words) / len(task_words)
            score += overlap * 0.3

        # User messages are slightly more relevant than assistant
        if role == "user":
            score += 0.1

        return min(score, 1.0)

    def _extract_file_mentions(self, text: str) -> set[str]:
        """Extract file path mentions from text."""
        import re
        # Match common file patterns
        patterns = [
            r'[\w\-./]+\.(?:py|js|ts|rs|go|java|cpp|c|h|rb|php|swift|kt|scala|yaml|yml|toml|md|json|sh)',
            r'[\w\-./]+/(?:[\w\-]+/)*[\w\-]+\.\w+',
        ]
        files = set()
        for pattern in patterns:
            matches = re.findall(pattern, text)
            files.update(matches)
        return files

    def _extract_tool_mentions(self, text: str) -> set[str]:
        """Extract tool name mentions from text."""
        tools = {
            "read_file", "write_file", "edit_file", "edit_file_multi",
            "run_bash", "list_files", "web_fetch", "web_search",
            "search_symbols", "lsp_diagnostics", "remember",
        }
        found = set()
        text_lower = text.lower()
        for tool in tools:
            if tool in text_lower:
                found.add(tool)
        return found


def partition_context(messages: list[dict], task: str,
                     max_messages: int = 10,
                     include_system: bool = True) -> list[dict]:
    """Convenience function to partition context.

    Args:
        messages: Full conversation history
        task: The subagent's task description
        max_messages: Maximum messages to include
        include_system: Whether to include system messages

    Returns:
        Filtered list of messages
    """
    partitioner = ContextPartitioner(max_messages=max_messages)
    return partitioner.partition(messages, task, include_system)
