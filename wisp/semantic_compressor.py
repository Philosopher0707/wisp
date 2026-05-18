"""Semantic context compressor for Wisp sessions.

Three-tier compression:
  Tier 1 – Semantic deduplication (fast, no LLM)
  Tier 2 – Content-aware truncation (fast, no LLM)
  Tier 3 – LLM abstractive summary (fallback when 1+2 insufficient)

Replaces the purely extractive heuristic summarizer with structured
conversation understanding while keeping ExtractiveSummarizer as a
fast fallback when the LLM is unavailable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from wisp.core.message_format import extract_text
from wisp.summarizer import (
    ExtractiveSummarizer,
    SessionSummary,
    _get_content,
)

logger = logging.getLogger(__name__)


# ── Message classification ─────────────────────────────────────────────

class MessageType(Enum):
    INTENT = "intent"               # user goal / question
    REASONING = "reasoning"         # assistant planning / thinking
    TOOL_CALL = "tool_call"         # assistant requesting tool execution
    TOOL_RESULT = "tool_result"     # raw tool output
    SYNTHESIS = "synthesis"         # assistant final answer to user
    SYSTEM = "system"               # system prompt / compacted context
    CORRECTION = "correction"       # user correcting assistant
    CONTINUATION = "continuation"   # "continue"/"go on" expansion
    UNKNOWN = "unknown"


@dataclass
class MessageNode:
    """A single message with semantic metadata."""
    index: int                      # position in original messages list
    role: str                       # user | assistant | tool | system
    mtype: MessageType
    content: str                    # normalized text content
    raw: dict                       # original message dict
    importance: float = 0.0         # 0.0–1.0 computed score
    thread_id: Optional[str] = None
    turn_idx: Optional[int] = None
    content_hash: str = ""

    def __post_init__(self):
        if not self.content_hash:
            self.content_hash = _hash_text(self.content)


@dataclass
class Turn:
    """One logical user↔assistant exchange, possibly with tool calls."""
    index: int
    user_idx: int                   # index into nodes of user message
    assistant_idx: Optional[int] = None
    tool_call_indices: list[int] = field(default_factory=list)
    tool_result_indices: list[int] = field(default_factory=list)
    thread_id: Optional[str] = None
    complete: bool = True


@dataclass
class Thread:
    """A tracked conversation topic."""
    id: str
    start_turn: int
    end_turn: Optional[int] = None
    status: str = "COMPLETE"        # COMPLETE | INCOMPLETE
    topic: str = ""


@dataclass
class ConversationGraph:
    """Structured representation of a message history."""
    nodes: list[MessageNode] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    threads: list[Thread] = field(default_factory=list)


@dataclass
class CompressionResult:
    """Output of the semantic compressor."""
    summary: str
    key_decisions: list[str] = field(default_factory=list)
    user_preferences: list[str] = field(default_factory=list)
    open_tasks: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    thread_stack: list[dict] = field(default_factory=list)
    compression_stats: dict = field(default_factory=dict)

    def to_session_summary(self, session_id: str = "", workspace: str = "") -> SessionSummary:
        """Convert to the legacy SessionSummary dataclass."""
        return SessionSummary(
            session_id=session_id,
            timestamp="",
            workspace=workspace,
            summary=self.summary,
            key_decisions=list(self.key_decisions),
            user_preferences=list(self.user_preferences),
            open_tasks=list(self.open_tasks),
            files_touched=list(self.files_touched),
            thread_stack=list(self.thread_stack),
        )


# ── Constants ────────────────────────────────────────────────────────

# Tool types and their truncation strategies
_TOOL_TRUNCATION: dict[str, tuple[int, str]] = {
    "read_file": (0, ""),               # never truncate
    "git_status": (0, ""),               # never truncate
    "list_files": (50, " files"),        # keep first N lines, suffix describes items
    "git_diff": (100, " lines"),
    "run_bash": (80, " lines"),
    "web_search": (5, " results"),       # keep top 5 result blocks
    "lsp_diagnostics": (20, " diagnostics"),
    "web_fetch": (5000, " chars"),       # truncate by character count
    "search_codebase": (5, " results"),
    "search_symbols": (10, " results"),
    "recall": (0, ""),
    "remember": (0, ""),
}

# Markers that indicate an assistant message is reasoning rather than synthesis
_REASONING_MARKERS = [
    "let me think", "i'll start by", "first i need", "let me check",
    "i need to", "i should", "i will", "i'm going to", "plan:",
    "step 1", "step 2", "step 3", "approach:", "strategy:",
]

# Markers that indicate a message is a synthesis / final answer
_SYNTHESIS_MARKERS = [
    "done.", "completed.", "finished.", "summary:", "here's the result",
    "to summarize", "in conclusion", "final answer", "the fix is",
]

# Incomplete thread signals
_INCOMPLETE_MARKERS = [
    "i'll do that now", "let me do that", "working on it",
    "in progress", "not yet", "still need", "pending",
]

# ── Helpers ──────────────────────────────────────────────────────────

def _hash_text(text: str) -> str:
    """Stable hash for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _normalize_tool_args(args: dict | str) -> str:
    """Normalize tool arguments for deduplication hashing."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return _hash_text(args)
    if not isinstance(args, dict):
        return _hash_text(str(args))
    # Sort keys, drop empty values for stable comparison
    clean = {k: v for k, v in sorted(args.items()) if v not in (None, "", [], {})}
    return json.dumps(clean, sort_keys=True, ensure_ascii=False)


def _classify_message(msg: dict, idx: int) -> MessageType:
    """Classify a single message by its semantic type."""
    role = msg.get("role", "")
    content = _get_content(msg)
    lowered = content.lower()

    if role == "system":
        return MessageType.SYSTEM

    if role == "user":
        if any(m in lowered for m in ("continue", "go on", "keep going", "proceed")):
            if len(content.strip()) < 30:
                return MessageType.CONTINUATION
        if any(m in lowered for m in ("no", "wrong", "incorrect", "fix", "not what", "that's not")):
            if len(content.strip()) < 100:
                return MessageType.CORRECTION
        return MessageType.INTENT

    if role == "assistant":
        if msg.get("tool_calls"):
            return MessageType.TOOL_CALL
        if any(m in lowered for m in _SYNTHESIS_MARKERS):
            return MessageType.SYNTHESIS
        if any(m in lowered for m in _REASONING_MARKERS):
            return MessageType.REASONING
        # Default: if the message is short and the previous was a tool_result, it's synthesis
        if len(content) < 500 and idx > 0:
            # heuristic: assistant messages right after tool results are usually synthesis
            pass
        return MessageType.REASONING

    if role == "tool":
        return MessageType.TOOL_RESULT

    return MessageType.UNKNOWN


def _score_importance(node: MessageNode, total_nodes: int) -> float:
    """Score a message's importance (0.0–1.0)."""
    score = 0.0

    # Position: later messages are more important (+0.3 for last third)
    if total_nodes > 0:
        ratio = node.index / total_nodes
        if ratio > 0.66:
            score += 0.3
        elif ratio > 0.33:
            score += 0.1

    # Type bonuses
    type_bonus = {
        MessageType.INTENT: 0.2,
        MessageType.SYNTHESIS: 0.3,
        MessageType.CORRECTION: 0.25,
        MessageType.TOOL_CALL: 0.15,
        MessageType.TOOL_RESULT: 0.1,
        MessageType.REASONING: 0.05,
        MessageType.CONTINUATION: 0.2,
        MessageType.SYSTEM: 0.0,
    }
    score += type_bonus.get(node.mtype, 0.0)

    # Content signals
    content = node.content
    if any(p in content.lower() for p in _INCOMPLETE_MARKERS):
        score += 0.3  # incomplete work is very important
    if "error" in content.lower() or "failed" in content.lower() or "exception" in content.lower():
        score += 0.2
    if re.search(r"\b(decided|chosen|settled on|opted for)\b", content, re.I):
        score += 0.15
    if re.search(r"\b(TODO|FIXME|HACK|NOTE:|WARNING:)\b", content):
        score += 0.1

    return min(score, 1.0)


def _build_graph(messages: list[dict]) -> ConversationGraph:
    """Build a structured graph from raw message list."""
    nodes: list[MessageNode] = []
    for i, msg in enumerate(messages):
        mtype = _classify_message(msg, i)
        content = _get_content(msg)
        node = MessageNode(
            index=i,
            role=msg.get("role", ""),
            mtype=mtype,
            content=content,
            raw=dict(msg),
        )
        node.importance = _score_importance(node, len(messages))
        nodes.append(node)

    # Build turns
    turns: list[Turn] = []
    current_turn: Optional[Turn] = None
    for node in nodes:
        if node.role == "user":
            if current_turn is not None:
                turns.append(current_turn)
            current_turn = Turn(index=len(turns), user_idx=node.index)
        elif node.role == "assistant" and current_turn is not None:
            if current_turn.assistant_idx is None and node.mtype != MessageType.TOOL_CALL:
                current_turn.assistant_idx = node.index
            elif node.mtype == MessageType.TOOL_CALL:
                current_turn.tool_call_indices.append(node.index)
        elif node.role == "tool" and current_turn is not None:
            current_turn.tool_result_indices.append(node.index)
    if current_turn is not None:
        turns.append(current_turn)

    # Mark completeness
    for turn in turns:
        if turn.assistant_idx is None:
            turn.complete = False
        else:
            last_assistant = nodes[turn.assistant_idx]
            content = last_assistant.content
            # Complete if ends with terminal punctuation or code block close
            if content:
                tail = content.strip()[-10:].lower()
                turn.complete = any(tail.endswith(c) for c in ".!?;`})")
            # Incomplete if there are pending tool calls
            if nodes[turn.assistant_idx].raw.get("tool_calls"):
                turn.complete = False

    # Build threads
    threads: list[Thread] = []
    current_thread: Optional[Thread] = None
    for turn in turns:
        if current_thread is None:
            current_thread = Thread(
                id=f"t{len(threads)}",
                start_turn=turn.index,
                topic=nodes[turn.user_idx].content[:60],
            )
        turn.thread_id = current_thread.id
        current_thread.end_turn = turn.index
        # Check if this turn completes the thread
        if turn.complete:
            # Does the next turn start a new topic? (heuristic: user message differs significantly)
            current_thread.status = "COMPLETE"
            threads.append(current_thread)
            current_thread = None
    if current_thread is not None:
        current_thread.status = "INCOMPLETE"
        threads.append(current_thread)

    # Update nodes with thread info
    for turn in turns:
        for idx in [turn.user_idx, turn.assistant_idx] + turn.tool_call_indices + turn.tool_result_indices:
            if idx is not None and idx < len(nodes):
                nodes[idx].thread_id = turn.thread_id
                nodes[idx].turn_idx = turn.index

    return ConversationGraph(nodes=nodes, turns=turns, threads=threads)


# ── Tier 1: Semantic deduplication ───────────────────────────────────

def _dedup_tool_results(graph: ConversationGraph) -> list[dict]:
    """Remove repeated tool calls with identical/similar results.
    Returns the filtered message list."""
    seen: dict[str, tuple[int, str]] = {}  # hash -> (last_index, note)
    keep_indices: set[int] = set()
    dedup_notes: dict[int, str] = {}

    # Temporal guard: never deduplicate the most recent tool invocations.
    # This preserves the user's immediate context — critical for things like
    # checking git status, listing files, or reading docs in the current turn.
    # The value 15 = roughly 5 tool-call rounds (user + assistant tool call +
    # tool result + assistant response + user correction).
    _RECENT_WINDOW = 15
    total_nodes = len(graph.nodes)

    for node in graph.nodes:
        if node.mtype != MessageType.TOOL_RESULT:
            keep_indices.add(node.index)
            continue

        # Temporal guard: always preserve recent tool results
        if node.index >= max(0, total_nodes - _RECENT_WINDOW):
            keep_indices.add(node.index)
            continue

        # Extract tool name and args from the corresponding tool_call
        tool_name = ""
        args_hash = ""
        for prev in reversed(graph.nodes[:node.index]):
            if prev.mtype == MessageType.TOOL_CALL:
                for tc in prev.raw.get("tool_calls", []):
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    args = func.get("arguments", {})
                    args_hash = _normalize_tool_args(args)
                break

        if not tool_name:
            keep_indices.add(node.index)
            continue

        # Build a dedup key
        result_hash = node.content_hash
        similar_key = f"{tool_name}:{args_hash}"

        if similar_key in seen:
            last_idx, _ = seen[similar_key]
            if tool_name in ("git_status", "list_files", "read_file"):
                # Only dedup if the older result is *not* the first of its kind.
                # If the user checked status, moved around, then checked again 20
                # turns later, we keep both as landmarks.
                seen[similar_key] = (node.index, "")
                keep_indices.add(node.index)
                if last_idx in keep_indices:
                    keep_indices.discard(last_idx)
                    dedup_notes[last_idx] = f"[Replaced by newer {tool_name} result]"
            else:
                keep_indices.add(node.index)
                seen[similar_key] = (node.index, "")
        else:
            seen[similar_key] = (node.index, "")
            keep_indices.add(node.index)

    # Build filtered messages
    filtered: list[dict] = []
    for node in graph.nodes:
        if node.index in keep_indices:
            msg = dict(node.raw)
            if node.index in dedup_notes:
                content = _get_content(msg)
                msg["content"] = f"{content}\n{dedup_notes[node.index]}"
            filtered.append(msg)

    return filtered


def _dedup_exact_duplicates(messages: list[dict]) -> list[dict]:
    """Remove messages with identical content hashes."""
    seen: set[str] = set()
    filtered: list[dict] = []
    for msg in messages:
        h = _hash_text(_get_content(msg))
        if h in seen and msg.get("role") in ("system", "tool"):
            # Only dedup system and tool messages (not user/assistant)
            continue
        seen.add(h)
        filtered.append(msg)
    return filtered


# ── Tier 2: Content-aware truncation ─────────────────────────────────

def _truncate_tool_result(content: str, tool_name: str) -> str:
    """Apply tool-specific truncation rules."""
    limit, unit = _TOOL_TRUNCATION.get(tool_name, (0, ""))
    if limit == 0:
        return content  # never truncate

    if unit == " chars":
        if len(content) > limit:
            return content[:limit] + f"\n... ({len(content) - limit} more characters)"
        return content

    if unit in (" lines", " files", " results", " diagnostics"):
        lines = content.splitlines()
        if len(lines) > limit:
            # For results, try to keep complete result blocks
            if unit == " results":
                # Split by blank lines to preserve result blocks
                blocks = content.split("\n\n")
                if len(blocks) > limit:
                    kept = "\n\n".join(blocks[:limit])
                    return kept + f"\n\n... ({len(blocks) - limit} more results)"
                return content
            kept = "\n".join(lines[:limit])
            return kept + f"\n... ({len(lines) - limit} more{unit})"
        return content

    return content


def _truncate_assistant_message(content: str, node: MessageNode) -> str:
    """Intelligently truncate assistant messages."""
    # Never truncate synthesis messages
    if node.mtype == MessageType.SYNTHESIS:
        return content

    # Never truncate messages with code blocks by splitting inside them
    if "```" in content:
        # If it's very long, truncate after the last complete code block
        if len(content) > 3000:
            blocks = content.split("```")
            # Keep first N complete code blocks + surrounding text
            result_parts: list[str] = []
            char_count = 0
            for i, part in enumerate(blocks):
                result_parts.append(part)
                if i > 0 and i % 2 == 1:  # inside a code block
                    result_parts.append("```")
                char_count += len(part)
                if char_count > 2500 and i % 2 == 0:
                    # Truncate after a complete block
                    break
            truncated = "".join(result_parts)
            if len(truncated) < len(content):
                return truncated + f"\n... ({len(content) - len(truncated)} more characters)"
        return content

    # For reasoning messages, truncate long ones
    if node.mtype == MessageType.REASONING and len(content) > 2000:
        # Keep first 1500 chars, try to end at a sentence boundary
        trunc = content[:1500]
        # Find last sentence end
        for end in ".!?\n":
            last = trunc.rfind(end)
            if last > 1200:
                trunc = trunc[: last + 1]
                break
        return trunc + f"\n... ({len(content) - len(trunc)} more characters of reasoning)"

    return content


def _apply_truncation(graph: ConversationGraph, messages: list[dict]) -> list[dict]:
    """Apply content-aware truncation to all messages."""
    result: list[dict] = []

    for msg in messages:
        role = msg.get("role", "")
        content = _get_content(msg)
        if not content:
            result.append(msg)
            continue

        # Find the corresponding node for metadata
        node = None
        for n in graph.nodes:
            if n.raw == msg or n.index == len(result):
                node = n
                break

        truncated = content

        if role == "tool":
            # Try to find tool name from preceding assistant message
            tool_name = ""
            for prev in reversed(result):
                if prev.get("role") == "assistant":
                    for tc in prev.get("tool_calls", []):
                        func = tc.get("function", {})
                        tool_name = func.get("name", "")
                    break
                if prev.get("role") == "tool":
                    continue
                break
            if tool_name:
                truncated = _truncate_tool_result(content, tool_name)

        elif role == "assistant" and node:
            truncated = _truncate_assistant_message(content, node)

        if truncated != content:
            msg = dict(msg)
            msg["content"] = truncated

        result.append(msg)

    return result


# ── Tier 3: LLM abstractive summary ──────────────────────────────────

def _build_llm_summary_prompt(messages: list[dict]) -> str:
    """Build a prompt for the LLM to generate a structured summary."""
    parts = [
        "You are a conversation compression assistant. Summarize the following "
        "conversation into a structured summary that preserves all critical information.",
        "",
        "Rules:",
        "1. Preserve ALL key decisions and their rationale",
        "2. Preserve ALL open tasks and their status",
        "3. Preserve ALL files that were created, modified, or read",
        "4. Preserve ALL user preferences or constraints stated",
        "5. Note any INCOMPLETE threads that need to be resumed",
        "6. Preserve error messages and how they were resolved",
        "7. Drop: repetitive tool output, successful test runs, redundant explanations",
        "",
        'Output format (strict JSON):\n{\n  "summary": "narrative overview",'
        '\n  "key_decisions": ["decision 1"],\n  "open_tasks": ["task 1 (status)"],'
        '\n  "files_touched": ["file1 (action)"],\n  "user_preferences": ["pref 1"],'
        '\n  "incomplete_threads": ["thread description"],'
        '\n  "errors_encountered": ["error and resolution"]\n}',
        "",
        "Conversation:",
    ]

    # Include only the most important messages to stay within token budget
    # Priority: user intents, assistant syntheses, tool calls with errors, corrections
    selected: list[dict] = []
    for msg in messages:
        role = msg.get("role", "")
        content = _get_content(msg)
        if role == "user":
            selected.append({"role": "user", "content": content[:300]})
        elif role == "assistant":
            selected.append({"role": "assistant", "content": content[:400]})
        elif role == "tool":
            # Keep error messages, truncate others
            if any(e in content.lower() for e in ("error", "failed", "exception", "traceback")):
                selected.append({"role": "tool", "content": content[:300]})
            else:
                selected.append({"role": "tool", "content": content[:100]})

    # Limit total characters to ~6000 (roughly 1500 tokens)
    total_chars = 0
    final_selected: list[str] = []
    for msg in selected:
        line = f"{msg['role']}: {msg['content'][:200]}"
        if total_chars + len(line) > 6000:
            break
        total_chars += len(line)
        final_selected.append(line)

    parts.extend(final_selected)
    return "\n".join(parts)


def _parse_llm_summary(response_text: str) -> CompressionResult:
    """Parse the LLM's JSON summary response."""
    # Try to extract JSON from the response
    text = response_text.strip()
    # Find JSON block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Fallback: treat the whole response as a plain summary
        return CompressionResult(
            summary=text[:2000],
            compression_stats={"tier": 3, "parse_error": True},
        )

    def _get_list(key: str) -> list[str]:
        val = data.get(key, [])
        if isinstance(val, list):
            return [str(v) for v in val if v]
        if isinstance(val, str):
            return [val] if val else []
        return []

    summary = data.get("summary", "")
    if not summary and data:
        # Build a summary from available fields
        parts = []
        for k in ("key_decisions", "open_tasks", "files_touched", "incomplete_threads"):
            items = _get_list(k)
            if items:
                parts.append(f"{k}: {', '.join(items[:3])}")
        summary = "; ".join(parts) if parts else "Conversation summary unavailable."

    return CompressionResult(
        summary=summary,
        key_decisions=_get_list("key_decisions"),
        user_preferences=_get_list("user_preferences"),
        open_tasks=_get_list("open_tasks"),
        files_touched=_get_list("files_touched"),
        thread_stack=[{"topic": t, "status": "INCOMPLETE"} for t in _get_list("incomplete_threads")],
        compression_stats={"tier": 3, "parse_ok": True},
    )


def _llm_summarize(
    messages: list[dict],
    model: str = "",
    base_url: str = "",
    client=None,
    timeout: int = 30,
) -> Optional[CompressionResult]:
    """Call the LLM to generate an abstractive summary. Returns None on failure.

    Args:
        messages: Message history to summarize.
        model: Override model name (defaults to the client's configured model).
        base_url: Override Ollama base URL.
        client: Reusable OllamaClient instance.  When None, a *new* client
                is created from WispConfig (useful for tests, wasteful in prod).
        timeout: Seconds to wait for the model to respond.  Tier-3
                 compaction should NOT block for minutes — 30 s is plenty
                 for a short summary.
    """
    try:
        from wisp.ollama_client import OllamaClient, OllamaError
        from wisp.config import WispConfig

        # Reuse an injected client (e.g., the agent's own client) so we do not
        # spin up a fresh connection pool per compaction.
        if client is None:
            config = WispConfig()
            client = OllamaClient(config)

        summary_model = model or getattr(client, "model", "") or WispConfig().model

        # Limit max_tokens for the summary — we want a summary, not an essay.
        old_max = getattr(client, "max_tokens", None)
        try:
            client.max_tokens = 512
            prompt = _build_llm_summary_prompt(messages)
            response = client.generate(
                system_prompt=(
                    "Summarize the conversation below. Produce a SHORT summary,"
                    " key decisions, and a task list. Be concise."
                ),
                messages=[{"role": "user", "content": prompt}],
            )
        finally:
            client.max_tokens = old_max

        text = response.get("message", {}).get("content", "")
        if not text:
            text = response.get("response", "")

        if text:
            result = _parse_llm_summary(text)
            result.compression_stats["model_used"] = summary_model
            return result
    except Exception as e:
        logger.warning("LLM summary failed: %s", e)

    return None


# ── Public API ───────────────────────────────────────────────────────

class SemanticCompressor:
    """Three-tier semantic context compressor.

    Usage:
        compressor = SemanticCompressor()
        result = compressor.compress(messages, config)
        # result.summary, result.key_decisions, etc.
    """

    def __init__(self):
        self.extractive = ExtractiveSummarizer()

    def analyze(self, messages: list[dict]) -> ConversationGraph:
        """Build a semantic graph from raw messages."""
        return _build_graph(messages)

    def compress(
        self,
        messages: list[dict],
        chars_per_token: int = 4,
        max_context_tokens: int = 256000,
        tier3_trigger_tokens: int = 0,
        use_llm: bool = True,
        client=None,
    ) -> CompressionResult:
        """Compress a message history using all three tiers.

        Args:
            messages: Raw message list to compress.
            chars_per_token: Rough characters per token estimate.
            max_context_tokens: Overall model context window (for stats/reporting).
            tier3_trigger_tokens: Absolute token threshold at which to invoke
                Tier 3 LLM abstractive summary. If 0 (default), uses
                ``max_context_tokens // 4``.
            use_llm: Whether Tier 3 LLM fallback is allowed.
            client: Reusable OllamaClient instance for Tier 3. Prevents spinning
                    up a fresh HTTP session on every compaction.

        Returns:
            A CompressionResult with summary, decisions, tasks, etc.
        """
        if not messages:
            return CompressionResult(
                summary="",
                compression_stats={"tier": 0, "before_messages": 0, "after_messages": 0},
            )

        before_count = len(messages)
        before_chars = sum(len(_get_content(m)) for m in messages)

        # Tier 1: Semantic deduplication
        graph = self.analyze(messages)
        messages = _dedup_tool_results(graph)
        messages = _dedup_exact_duplicates(messages)

        tier1_chars = sum(len(_get_content(m)) for m in messages)

        # Rebuild graph after dedup
        graph = self.analyze(messages)

        # Tier 2: Content-aware truncation
        messages = _apply_truncation(graph, messages)

        tier2_chars = sum(len(_get_content(m)) for m in messages)
        estimated_tokens = tier2_chars // chars_per_token

        tier3_threshold = tier3_trigger_tokens if tier3_trigger_tokens > 0 else max_context_tokens // 4

        # Tier 3: LLM abstractive summary (only if still over budget)
        if use_llm and estimated_tokens > tier3_threshold:
            llm_result = _llm_summarize(messages, client=client)
            if llm_result:
                llm_result.compression_stats.update({
                    "tier": 3,
                    "before_messages": before_count,
                    "after_messages": len(messages),
                    "before_chars": before_chars,
                    "after_chars": tier2_chars,
                    "tier1_saved_chars": before_chars - tier1_chars,
                    "tier2_saved_chars": tier1_chars - tier2_chars,
                    "tier3_threshold": tier3_threshold,
                })
                return llm_result

        # Build structured result from the compressed messages
        # Use extractive heuristics for the structured fields
        result = self._build_result_from_graph(graph, messages)
        result.compression_stats = {
            "tier": 3 if (use_llm and estimated_tokens > tier3_threshold) else 2,
            "before_messages": before_count,
            "after_messages": len(messages),
            "before_chars": before_chars,
            "after_chars": tier2_chars,
            "tier1_saved_chars": before_chars - tier1_chars,
            "tier2_saved_chars": tier1_chars - tier2_chars,
            "estimated_tokens": estimated_tokens,
            "tier3_threshold": tier3_threshold,
        }
        return result

    def summarize(
        self,
        messages: list[dict],
        session_id: str = "",
        workspace: str = "",
    ) -> Optional[SessionSummary]:
        """Generate a SessionSummary from messages.

        This is the drop-in replacement for ExtractiveSummarizer.summarize().
        """
        result = self.compress(messages)
        summary = result.to_session_summary(session_id, workspace)
        # Preserve timestamp
        from datetime import datetime, timezone
        summary.timestamp = datetime.now(timezone.utc).isoformat()
        return summary

    def _build_result_from_graph(
        self, graph: ConversationGraph, messages: list[dict]
    ) -> CompressionResult:
        """Build a CompressionResult by analyzing the conversation graph."""
        # Use existing extractive patterns as a base
        extractive_summary = self.extractive.summarize(
            messages=[n.raw for n in graph.nodes],
            session_id="",
            workspace="",
        )

        # Build thread stack with our better thread detection
        thread_stack = [
            {"topic": thread.topic, "status": thread.status}
            for thread in graph.threads
        ]

        if extractive_summary and extractive_summary.summary:
            return CompressionResult(
                summary=extractive_summary.summary,
                key_decisions=list(extractive_summary.key_decisions),
                user_preferences=list(extractive_summary.user_preferences),
                open_tasks=list(extractive_summary.open_tasks),
                files_touched=list(extractive_summary.files_touched),
                thread_stack=thread_stack,
            )

        # Fallback: build a minimal summary from graph structure
        if graph.threads:
            last_topic = graph.threads[-1].topic
            parts = [f"Conversation about: {last_topic}"]
            if any(t.status == "INCOMPLETE" for t in graph.threads):
                parts.append("(has incomplete work)")
            summary = " ".join(parts)
        else:
            summary = f"Conversation with {len(graph.turns)} turns."

        if extractive_summary:
            # Use structured data from extractive even if summary was empty
            return CompressionResult(
                summary=summary,
                key_decisions=list(extractive_summary.key_decisions),
                user_preferences=list(extractive_summary.user_preferences),
                open_tasks=list(extractive_summary.open_tasks),
                files_touched=list(extractive_summary.files_touched),
                thread_stack=thread_stack,
            )

        return CompressionResult(
            summary=summary,
            thread_stack=thread_stack,
        )
