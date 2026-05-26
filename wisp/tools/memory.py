"""Memory tools for Wisp — cross-session fact storage and retrieval.

Uses wisp.memory for persistent facts and wisp.agent_memory for session summaries.
"""

import logging
from typing import Optional

from wisp.tools._utils import (
    _validate_string,
    _relevance_score,
)

logger = logging.getLogger(__name__)


def tool_remember(fact: str, workspace: Optional[str] = None) -> str:
    """Store a fact in cross-session memory.

    The fact will be remembered across ALL conversations and injected into
    the system prompt in future sessions regardless of which directory you are in.
    """
    _validate_string(fact, "fact", 500)

    from wisp.memory import add_fact

    added = add_fact(fact, workspace=workspace)
    if added:
        return f"✓ Remembered: {fact}"
    else:
        return f"(Already remembered: {fact})"


def tool_recall(query: str, workspace: Optional[str] = None, limit: int = 10) -> str:
    """Search cross-session memory and past session summaries for relevant facts.

    Use this when you need to actively recall something you may have learned
    in previous conversations, rather than relying only on what's in the
    current context window. Searches across ALL workspaces and ALL past sessions.
    """
    _validate_string(query, "query", 200)
    if limit < 1 or limit > 50:
        limit = 10

    from wisp.memory import list_all_facts
    from wisp.agent_memory import get_agent_memory

    query_lower = query.lower()
    query_words = [w for w in query_lower.split() if len(w) > 2]

    results: list[tuple[float, str]] = []

    # ── Search ALL memory facts across every workspace ──
    all_facts = list_all_facts()
    for fact in all_facts:
        content = fact["content"] if isinstance(fact, dict) else fact
        score = _relevance_score(content, query_lower, query_words)
        if score > 0:
            results.append((score, f"[Memory] {content}"))

    # ── Search ALL session summaries across every workspace ──
    agent_mem = get_agent_memory()
    all_summaries = agent_mem.load_all()
    # Sort newest first so recent sessions rank higher
    all_summaries.sort(key=lambda s: s.timestamp, reverse=True)
    for summary in all_summaries[:50]:  # cap to avoid overload
        texts = [
            (summary.summary, 1.0),
            (" ".join(summary.key_decisions), 1.5),
            (" ".join(summary.user_preferences), 1.5),
            (" ".join(summary.open_tasks), 1.2),
            (" ".join(summary.files_touched), 1.0),
        ]
        for text, field_boost in texts:
            if text:
                score = _relevance_score(text, query_lower, query_words)
                # Session summaries need higher bar to avoid noise
                if score >= 2.0:
                    score *= field_boost
                    results.append((score, f"[Session {summary.session_id[:20]}] {text[:200]}"))

    if not results:
        return "No relevant memories found for this query."

    # Sort by score descending, deduplicate, limit
    results.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    deduped: list[str] = []
    for score, text in results:
        key = text.lower()[:80]
        if key not in seen:
            seen.add(key)
            deduped.append(f"({score:.1f}) {text}")
            if len(deduped) >= limit:
                break

    return "\n".join(deduped)
