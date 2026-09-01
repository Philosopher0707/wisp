"""In-memory tool result pruner — condenses historical payloads before HTTP dispatch.

Prevents unbounded message bloat across high-turn runs (30+ tool calls).
Without pruning, a turn with 30 read_file calls each returning 10KB
produces a 300KB payload that stalls the HTTP write (60s timeout) and
blows the context window.

Design:
  - Retains full content for the most recent N tool results (keep_last_n_full)
    so the model sees fresh context in detail
  - For historical read_file/list_files, retains only status/diff summary:
      * read_file: "✓ Read path (X lines, Y bytes, truncated)" or header
        "FILE: path | LINES: N | SHOWING: a-b" + first 500 chars
      * list_files: "Listed N entries in path (truncated)" or first 10 lines
  - For other historical tools, enforces strict byte ceiling (8KB default)
    with head/tail preservation and truncation marker
  - Enforces total payload ceiling (e.g., 200KB) by further truncating oldest
  - Pure function: takes list[dict], returns new list[dict] — never mutates input
  - Idempotent: safe to call multiple times

Usage:
  from wisp.core.context_pruner import prune_messages, PrunerConfig

  pruned = prune_messages(messages, PrunerConfig(
      keep_last_n_full=3,
      max_bytes_per_historical_result=8192,
      max_bytes_per_recent_result=50000,
      max_total_bytes=200000,
  ))

  # In provider payload builder:
  payload = {
      "model": model,
      "messages": pruned,  # instead of raw messages
  }

Integration points:
  - wisp/core/stateless.py: before _build_system_prompt or before
    _guarded_provider_stream — prune messages list
  - wisp/core/transport.py: before json.dumps(payload) — ensure
    hardened_post receives pruned payload
  - wisp/providers/openai.py: before _build_payload — prune messages
  - wisp/ollama_client.py: before _post_stream — prune messages
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "PrunerConfig",
    "PrunerStats",
    "prune_messages",
    "prune_tool_result",
    "condense_read_file_result",
    "condense_list_files_result",
    "enforce_byte_ceiling",
    "estimate_tokens",
    "is_pruned",
]

# ── Configuration ────────────────────────────────────────────────────


@dataclass(frozen=True)
class PrunerConfig:
    """Tuning knobs for pruning behavior.

    All byte limits are for the tool result's `content` string only,
    not the full message envelope.
    """

    # Keep the most recent N tool results at full fidelity
    keep_last_n_full: int = 3

    # Historical (older than keep_last_n_full) caps
    max_bytes_per_historical_result: int = 8192  # 8KB — enough for status/diff
    max_bytes_per_recent_result: int = 50000  # 50KB — generous for recent context

    # Total payload ceiling — if pruned messages still exceed this,
    # further truncate oldest historical results
    max_total_bytes: int = 200000  # 200KB — well within write timeout budget

    # Per-tool overrides — read_file and list_files get special handling
    read_file_historical_max_bytes: int = 2048  # 2KB for historical reads
    list_files_historical_max_bytes: int = 2048

    # Token ceiling (if tiktoken available, else approx 4 chars/token)
    max_tokens_per_historical_result: int = 2000
    max_tokens_per_recent_result: int = 12000

    # Behavior flags
    preserve_status_line: bool = True  # Always keep first line (often status)
    add_pruned_marker: bool = True  # Add "[pruned ...]" marker


@dataclass
class PrunerStats:
    """Statistics from a prune run — for logging and tests."""

    original_messages: int = 0
    pruned_messages: int = 0
    total_original_bytes: int = 0
    total_pruned_bytes: int = 0
    historical_pruned: int = 0
    recent_kept_full: int = 0
    bytes_saved: int = 0
    was_truncated: bool = False

    @property
    def reduction_pct(self) -> float:
        if self.total_original_bytes == 0:
            return 0.0
        return (self.bytes_saved / self.total_original_bytes) * 100


# ── Helpers ──────────────────────────────────────────────────────────


def estimate_tokens(text: str, chars_per_token: int = 4) -> int:
    """Estimate token count — uses tiktoken if available, else chars/4."""
    if not text:
        return 0
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        return max(1, len(text) // chars_per_token)
    except Exception:
        return max(1, len(text) // chars_per_token)


def is_pruned(content: str) -> bool:
    """Check if content was already pruned (contains marker)."""
    markers = ["[pruned", "[condensed", "[truncated", "... +"]
    content_lower = content.lower()
    return any(m in content_lower for m in markers)


def enforce_byte_ceiling(
    content: str,
    max_bytes: int,
    preserve_start: int = 1024,
    preserve_end: int = 512,
    add_marker: bool = True,
) -> str:
    """Enforce byte ceiling with head/tail preservation.

    Keeps first `preserve_start` bytes and last `preserve_end` bytes,
    with a marker in the middle showing how much was removed.

    Args:
        content: Original content string
        max_bytes: Maximum allowed bytes
        preserve_start: Bytes to keep from start (often contains status)
        preserve_end: Bytes to keep from end (often contains summary)
        add_marker: Whether to add pruning marker

    Returns:
        Truncated content with marker if needed, else original
    """
    if not content or len(content.encode("utf-8", errors="ignore")) <= max_bytes:
        return content

    content_bytes = content.encode("utf-8", errors="ignore")
    if len(content_bytes) <= max_bytes:
        return content

    # Calculate marker overhead
    marker = f"\n[... pruned {len(content_bytes) - max_bytes} bytes ...]\n"
    marker_bytes = marker.encode("utf-8", errors="ignore")
    available = max_bytes - len(marker_bytes) if add_marker else max_bytes

    if available <= 0:
        # Max bytes too small — just truncate hard
        return content_bytes[:max_bytes].decode("utf-8", errors="ignore") + (" [...]" if add_marker else "")

    # Split available between start and end
    if preserve_end <= 0 or available <= preserve_start:
        # Only keep start
        result = content_bytes[:available].decode("utf-8", errors="ignore")
        return result + marker if add_marker else result

    start_bytes = min(preserve_start, available - preserve_end)
    end_bytes = available - start_bytes

    start = content_bytes[:start_bytes].decode("utf-8", errors="ignore")
    end = content_bytes[-end_bytes:].decode("utf-8", errors="ignore") if end_bytes > 0 else ""

    if add_marker:
        return start + marker + end
    else:
        return start + end


def condense_read_file_result(content: str, max_bytes: int = 2048) -> str:
    """Condense a read_file tool result to status/diff summary.

    read_file results typically look like:
      "--- FILE: path | LINES: N | SHOWING: a-b ---\\n<actual content>"

    For historical results, we keep only the header and first 500 chars,
    or a summary like "✓ Read path (N lines, M bytes)".

    Args:
        content: Original read_file result content
        max_bytes: Max bytes for condensed result

    Returns:
        Condensed content
    """
    if not content:
        return content

    # Check if already pruned
    if is_pruned(content):
        return enforce_byte_ceiling(content, max_bytes)

    lines = content.splitlines()
    if not lines:
        return content

    # Try to parse the standard header: "--- FILE: path | LINES: N | SHOWING: a-b ---"
    header = lines[0] if lines and lines[0].startswith("--- FILE:") else ""
    if header:
        # Extract file info from header
        # Example: "--- FILE: src/app.py | LINES: 120 | SHOWING: 1-50 ---"
        try:
            # Keep header plus a small preview
            preview_lines = 5
            preview = "\n".join(lines[1 : 1 + preview_lines])
            # Build condensed version
            # Estimate original size
            original_lines = 0
            if "LINES:" in header:
                try:
                    parts = header.split("LINES:")
                    if len(parts) > 1:
                        num_str = parts[1].split("|")[0].strip().split()[0]
                        original_lines = int(num_str)
                except (ValueError, IndexError):
                    pass

            condensed = header
            if preview.strip():
                # Only show preview if it's meaningful (not just empty)
                preview_truncated = preview[:500] + ("..." if len(preview) > 500 else "")
                condensed += f"\n{preview_truncated}"
            if original_lines > preview_lines:
                condensed += f"\n[... {original_lines - preview_lines} more lines pruned, {len(content)} bytes → {len(condensed)} bytes ...]"
            elif len(content) > len(condensed) + 100:
                condensed += f"\n[... pruned {len(content) - len(condensed)} bytes ...]"

            # Enforce final ceiling
            return enforce_byte_ceiling(condensed, max_bytes, preserve_start=max_bytes)
        except Exception:
            # Fallback to simple truncation
            pass

    # Fallback: not in expected format — just keep first 500 chars + status
    if len(content) <= max_bytes:
        return content

    # Keep header/first line + truncated preview
    keep = content[: min(500, max_bytes - 100)]
    remaining = len(content) - len(keep)
    marker = f"\n[... pruned {remaining} bytes from historical read_file ...]"
    condensed = keep + marker
    return enforce_byte_ceiling(condensed, max_bytes)


def condense_list_files_result(content: str, max_bytes: int = 2048) -> str:
    """Condense a list_files tool result to summary.

    list_files results typically look like:
      "📁 dir/\\n📄 file1.py (1,234 bytes)\\n📄 file2.py ..."

    For historical, we keep count and first 10 entries.

    Args:
        content: Original list_files content
        max_bytes: Max bytes for condensed

    Returns:
        Condensed content
    """
    if not content:
        return content

    if is_pruned(content):
        return enforce_byte_ceiling(content, max_bytes)

    lines = content.splitlines()
    if len(lines) <= 10:
        # Small listing — keep as is if under ceiling
        if len(content.encode("utf-8", errors="ignore")) <= max_bytes:
            return content

    # Count entries
    file_count = sum(1 for line in lines if "📄" in line or "📁" in line)
    dir_count = sum(1 for line in lines if "📁" in line)
    total = len(lines)

    # Keep first 10 lines + summary
    preview = "\n".join(lines[:10])
    if total > 10:
        summary = f"\n[... {total - 10} more entries pruned ({file_count} files, {dir_count} dirs total, {len(content)} bytes → ~{len(preview)} bytes) ...]"
        condensed = preview + summary
    else:
        condensed = preview

    # Also handle the "(no matches...)" or error cases — keep them intact
    if content.startswith("(") or "not a directory" in content.lower() or "no matches" in content.lower():
        # These are already short status messages — keep as is or truncate
        return enforce_byte_ceiling(content, max_bytes)

    return enforce_byte_ceiling(condensed, max_bytes)


def _get_tool_name_for_result(
    tool_result_msg: dict[str, Any],
    id_to_name: dict[str, str],
) -> Optional[str]:
    """Resolve tool name for a tool result message.

    Tries:
      1. Direct "name" field in tool result (if present)
      2. Lookup via tool_call_id -> name map built from assistant messages
      3. Inference from content prefix (e.g., "--- FILE:" => read_file)

    Returns:
        Tool name or None if cannot be determined
    """
    # Direct name field (some providers include it)
    name = tool_result_msg.get("name")
    if name:
        return str(name)

    # Lookup via tool_call_id
    tc_id = tool_result_msg.get("tool_call_id") or tool_result_msg.get("id")
    if tc_id and tc_id in id_to_name:
        return id_to_name[tc_id]

    # Inference from content
    content = str(tool_result_msg.get("content", ""))
    if content.startswith("--- FILE:"):
        return "read_file"
    if "📄" in content and "📁" in content:
        # Likely list_files, but also could be other — check more
        if content.startswith("📁") or content.startswith("📄"):
            return "list_files"
    if content.startswith("(") and "no matches" in content.lower():
        return "list_files"

    return None


def build_tool_call_id_map(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Build tool_call_id -> tool name map from assistant messages.

    Scans all assistant messages with tool_calls and builds the mapping.
    This is needed to resolve tool names for historical tool results which
    only have tool_call_id, not the name directly.

    Args:
        messages: Full messages list

    Returns:
        Dict mapping tool_call_id -> tool name
    """
    id_to_name: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tc_id = tc.get("id", "")
                func = tc.get("function", {})
                name = func.get("name", "") if isinstance(func, dict) else ""
                if tc_id and name:
                    id_to_name[tc_id] = name
                # Also handle OpenAI's older 'name' field directly
                if tc_id and not name and tc.get("name"):
                    id_to_name[tc_id] = tc["name"]
    return id_to_name


# ── Main Pruner ───────────────────────────────────────────────────────


def prune_tool_result(
    content: str,
    tool_name: Optional[str],
    is_recent: bool,
    config: PrunerConfig,
) -> str:
    """Prune a single tool result content based on tool type and recency.

    Args:
        content: Original tool result content
        tool_name: Tool name (read_file, list_files, etc.) or None
        is_recent: Whether this is among the keep_last_n_full most recent
        config: Pruner configuration

    Returns:
        Pruned content
    """
    if not content:
        return content

    if is_recent:
        # Recent results get generous ceiling
        max_bytes = config.max_bytes_per_recent_result
        # Still enforce ceiling for huge recent results (e.g., 50KB file)
        if len(content.encode("utf-8", errors="ignore")) <= max_bytes:
            return content
        # Even recent large results get truncated, but preserve more
        return enforce_byte_ceiling(
            content,
            max_bytes,
            preserve_start=max_bytes // 2,
            preserve_end=max_bytes // 4,
        )
    else:
        # Historical — stricter limits and special handling per tool
        if tool_name == "read_file":
            return condense_read_file_result(content, config.read_file_historical_max_bytes)
        elif tool_name == "list_files":
            return condense_list_files_result(content, config.list_files_historical_max_bytes)
        else:
            # Generic historical — byte + token ceiling
            max_bytes = config.max_bytes_per_historical_result
            # Also check token ceiling
            tokens = estimate_tokens(content)
            if tokens > config.max_tokens_per_historical_result:
                # Convert token ceiling to bytes (approx)
                token_bytes = config.max_tokens_per_historical_result * 4
                max_bytes = min(max_bytes, token_bytes)

            if len(content.encode("utf-8", errors="ignore")) <= max_bytes:
                return content

            return enforce_byte_ceiling(content, max_bytes)


def prune_messages(
    messages: list[dict[str, Any]],
    config: Optional[PrunerConfig] = None,
    *,
    return_stats: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], PrunerStats]:
    """Prune historical tool results in messages list before HTTP dispatch.

    This is the main entry point — call it before building the provider
    payload (e.g., in stateless.py before _guarded_provider_stream or
    in provider's _build_payload).

    Args:
        messages: List of message dicts (role, content, tool_calls, etc.)
        config: Pruner configuration (uses defaults if None)
        return_stats: If True, return (pruned_messages, stats) tuple

    Returns:
        Pruned messages list (new list, never mutates input), or
        (pruned_messages, stats) if return_stats=True
    """
    config = config or PrunerConfig()
    stats = PrunerStats(
        original_messages=len(messages),
        total_original_bytes=sum(len(str(m.get("content", "")).encode("utf-8", errors="ignore")) for m in messages),
    )

    if not messages:
        result: list[dict[str, Any]] = []
        if return_stats:
            return result, stats
        return result

    # Build tool_call_id -> name map for resolving historical tool results
    id_to_name = build_tool_call_id_map(messages)

    # Identify tool result messages and their indices
    tool_result_indices: list[int] = []
    for idx, msg in enumerate(messages):
        if msg.get("role") == "tool":
            tool_result_indices.append(idx)

    # Determine which tool results are "recent" (keep full) vs historical
    # Recent = last keep_last_n_full tool results by position in messages
    recent_ids: set[int] = set()
    if tool_result_indices:
        # Take the last N indices as recent
        recent_start = max(0, len(tool_result_indices) - config.keep_last_n_full)
        recent_indices = tool_result_indices[recent_start:]
        recent_ids = set(recent_indices)

    # Build pruned messages
    pruned: list[dict[str, Any]] = []
    historical_pruned = 0
    recent_kept = 0

    for idx, msg in enumerate(messages):
        # Only prune tool results, leave other roles (system, user, assistant) intact
        if msg.get("role") != "tool":
            pruned.append(dict(msg))  # shallow copy
            continue

        # This is a tool result — determine if recent or historical
        is_recent = idx in recent_ids
        content = str(msg.get("content", ""))

        # Resolve tool name
        tool_name = _get_tool_name_for_result(msg, id_to_name)

        # Prune based on recency and tool type
        pruned_content = prune_tool_result(content, tool_name, is_recent, config)

        if is_recent:
            if pruned_content != content:
                # Even recent was truncated due to ceiling
                historical_pruned += 1
            else:
                recent_kept += 1
        else:
            if pruned_content != content:
                historical_pruned += 1

        # Build new message with pruned content, preserving other fields
        new_msg = dict(msg)
        new_msg["content"] = pruned_content
        # Optionally add a flag for debugging
        if pruned_content != content and config.add_pruned_marker:
            # Add metadata if not already present
            # We don't add a separate field to avoid breaking providers,
            # but we could log
            pass

        pruned.append(new_msg)

    # Enforce total payload ceiling — if still too large, further truncate
    # oldest historical results first
    total_bytes = sum(len(str(m.get("content", "")).encode("utf-8", errors="ignore")) for m in pruned)
    stats.total_pruned_bytes = total_bytes
    stats.pruned_messages = len(pruned)
    stats.historical_pruned = historical_pruned
    stats.recent_kept_full = recent_kept
    stats.bytes_saved = stats.total_original_bytes - total_bytes
    stats.was_truncated = historical_pruned > 0

    if total_bytes > config.max_total_bytes:
        # Need to further prune — calculate per-tool budget and enforce
        # This handles cases like 10 tool results with 50KB each and 20KB total ceiling
        # We need to be aggressive: budget per tool = max_total // num_tools
        num_tools = len(tool_result_indices)
        if num_tools > 0:
            budget_per_tool = max(500, config.max_total_bytes // num_tools)
            # First pass: truncate historical to budget_per_tool
            excess = total_bytes - config.max_total_bytes
            for idx in tool_result_indices:
                if excess <= 0:
                    break
                if idx in recent_ids:
                    continue
                msg = pruned[idx]
                content = str(msg.get("content", ""))
                # Use budget_per_tool or historical limit, whichever is smaller
                new_max = min(budget_per_tool, config.max_bytes_per_historical_result // 2)
                if len(content.encode("utf-8", errors="ignore")) <= new_max:
                    continue
                pruned_content = enforce_byte_ceiling(content, new_max)
                old_bytes = len(content.encode("utf-8", errors="ignore"))
                new_bytes = len(pruned_content.encode("utf-8", errors="ignore"))
                saved = old_bytes - new_bytes
                if saved > 0:
                    pruned[idx] = {**msg, "content": pruned_content}
                    excess -= saved
                    total_bytes -= saved

            # Second pass: if still over, truncate recent as well (emergency)
            # Use budget_per_tool for recent too, but allow slightly more
            if excess > 0:
                recent_budget = min(budget_per_tool * 2, config.max_bytes_per_recent_result // 2)
                for idx in reversed(tool_result_indices):
                    if excess <= 0:
                        break
                    msg = pruned[idx]
                    content = str(msg.get("content", ""))
                    if len(content.encode("utf-8", errors="ignore")) <= recent_budget:
                        continue
                    pruned_content = enforce_byte_ceiling(content, recent_budget)
                    old_bytes = len(content.encode("utf-8", errors="ignore"))
                    new_bytes = len(pruned_content.encode("utf-8", errors="ignore"))
                    saved = old_bytes - new_bytes
                    if saved > 0:
                        pruned[idx] = {**msg, "content": pruned_content}
                        excess -= saved
                        total_bytes -= saved

            # Final emergency: if still over, truncate all to budget_per_tool regardless of recency
            if excess > 0:
                for idx in tool_result_indices:
                    if excess <= 0:
                        break
                    msg = pruned[idx]
                    content = str(msg.get("content", ""))
                    if len(content.encode("utf-8", errors="ignore")) <= budget_per_tool:
                        continue
                    pruned_content = enforce_byte_ceiling(content, budget_per_tool)
                    old_bytes = len(content.encode("utf-8", errors="ignore"))
                    new_bytes = len(pruned_content.encode("utf-8", errors="ignore"))
                    saved = old_bytes - new_bytes
                    if saved > 0:
                        pruned[idx] = {**msg, "content": pruned_content}
                        excess -= saved
                        total_bytes -= saved

        stats.total_pruned_bytes = sum(len(str(m.get("content", "")).encode("utf-8", errors="ignore")) for m in pruned)
        stats.bytes_saved = stats.total_original_bytes - stats.total_pruned_bytes

    if logger.isEnabledFor(logging.DEBUG) and stats.bytes_saved > 0:
        logger.debug(
            "Pruned %d historical tool results, kept %d recent full, saved %d bytes (%.1f%% reduction, %d -> %d bytes)",
            historical_pruned,
            recent_kept,
            stats.bytes_saved,
            stats.reduction_pct,
            stats.total_original_bytes,
            stats.total_pruned_bytes,
        )

    if return_stats:
        return pruned, stats
    return pruned


# ── Legacy alias for backwards compat ─────────────────────────────────

# Some older code may import from agent.context_pruner — keep alias
def prune_history(*args, **kwargs):
    """Alias for prune_messages for backwards compat."""
    return prune_messages(*args, **kwargs)
