"""3-tier context compaction policy (pure transforms + explicit triggers).

  - Static prefix (system instructions, tool schemas, RepoMap) is assembled
    once per session by the caller and never mutated here — immutability is
    what earns provider prompt-cache hits.
  - Tier 1 (MicroCompact): tool payload bodies older than ``keep_turns``
    turns become ``[Output cleared]``; ``tool_use_id`` and the
    call/reply pair schema are preserved.
  - Tier 2 (Session Memory): :class:`RollingSummary` accumulates decisions,
    touched files, and hypotheses as turns land; when usage passes 70% of
    the window the caller swaps historical turns for the rendered summary.
  - Tier 3 (Full AutoCompact): at ``window - reserve`` tokens, build the
    9-section condensation prompt (LLM call injected by the caller), then
    re-inject raw text of the 5 most recently touched files.

No LLM calls and no disk I/O happen in this module — the caller supplies
the summarizer callable and file reader, keeping every tier unit-testable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

MICRO_KEEP_TURNS = 3
SESSION_MEMORY_FRACTION = 0.70
FULL_COMPACT_RESERVE = 20_000
FULL_TAIL_FILES = 5
CLEARED_MARKER = "[Output cleared]"

CONDENSE_SECTIONS = (
    "goal",
    "decisions",
    "hypotheses",
    "touched_files",
    "open_threads",
    "tool_ledger",
    "errors",
    "next_steps",
    "tail_files",
)


@dataclass
class TierReport:
    """What one tier pass changed."""

    tier: str
    changed: bool = False
    detail: str = ""


@dataclass
class RollingSummary:
    """Tier-2 background state: decisions, files, hypotheses per turn."""

    decisions: list[str] = field(default_factory=list)
    touched_files: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    turns_folded: int = 0

    def fold_turn(self, decision: str = "", files: list[str] | None = None,
                  hypothesis: str = "") -> None:
        """Fold one turn's takeaways (idempotent per caller-supplied content)."""
        if decision:
            self.decisions.append(decision)
        for path in files or []:
            if path not in self.touched_files:
                self.touched_files.append(path)
        if hypothesis:
            self.hypotheses.append(hypothesis)
        self.turns_folded += 1

    def render(self) -> str:
        """Markdown summary replacing historical turns at 70% capacity."""
        lines = [f"## Session memory ({self.turns_folded} turns folded)"]
        if self.decisions:
            lines.append("Decisions: " + "; ".join(self.decisions[-10:]))
        if self.touched_files:
            lines.append("Touched files: " + ", ".join(self.touched_files[-15:]))
        if self.hypotheses:
            lines.append("Hypotheses: " + "; ".join(self.hypotheses[-10:]))
        return "\n".join(lines)


def _is_tool_message(message: Any) -> bool:
    return isinstance(message, dict) and message.get("role") == "tool"


def micro_compact(messages: list[dict[str, Any]], keep_turns: int = MICRO_KEEP_TURNS) -> TierReport:
    """Tier 1: strip tool bodies older than ``keep_turns`` turns in place.

    Only the ``content`` field is replaced — ids, names, and the
    call/reply pairing are preserved so provider schemas still validate.
    Already-cleared markers are never rewritten (idempotent).
    """
    if keep_turns < 1:
        raise ValueError("keep_turns must be >= 1")
    tool_indices = [i for i, m in enumerate(messages) if _is_tool_message(m)]
    keep = set(tool_indices[-keep_turns:]) if tool_indices else set()
    cleared = 0
    for i in tool_indices:
        if i in keep:
            continue
        message = messages[i]
        content = message.get("content", "")
        if isinstance(content, str) and content and content != CLEARED_MARKER:
            message["content"] = CLEARED_MARKER
            cleared += 1
    return TierReport(tier="micro", changed=cleared > 0,
                      detail=f"cleared {cleared} payload(s), kept {len(keep)}")


def session_memory_threshold(context_tokens: int, window_tokens: int,
                             fraction: float = SESSION_MEMORY_FRACTION) -> bool:
    """Tier-2 trigger: True once usage passes 70% of the window."""
    if window_tokens < 1:
        raise ValueError("window_tokens must be >= 1")
    return context_tokens >= int(window_tokens * fraction)


def full_compact_trigger(context_tokens: int, window_tokens: int,
                         reserve: int = FULL_COMPACT_RESERVE) -> bool:
    """Tier-3 trigger: True at ``window - reserve`` tokens."""
    if window_tokens < 1:
        raise ValueError("window_tokens must be >= 1")
    return context_tokens >= window_tokens - reserve


def build_condense_prompt(messages: list[dict[str, Any]], summary: RollingSummary) -> str:
    """Tier-3 condensation prompt with the fixed 9-section contract."""
    transcript = []
    for message in messages[-40:]:
        role = message.get("role", "?") if isinstance(message, dict) else "?"
        content = message.get("content", "") if isinstance(message, dict) else ""
        text = content if isinstance(content, str) else str(content)
        transcript.append(f"[{role}] {text[:400]}")
    sections = "\n".join(f"{i + 1}. {name}" for i, name in enumerate(CONDENSE_SECTIONS))
    return (
        "Condense this coding-agent session into exactly these sections:\n"
        f"{sections}\n\nRolled-up memory so far:\n{summary.render()}\n\n"
        "Recent transcript:\n" + "\n".join(transcript)
    )


async def full_compact(
    messages: list[dict[str, Any]],
    summary: RollingSummary,
    read_file: Callable[[str], str],
    summarize: Callable[[str], Awaitable[str]],
    tail_limit: int = FULL_TAIL_FILES,
) -> tuple[str, TierReport]:
    """Tier 3: condense via injected summarizer, re-inject 5 freshest files.

    Returns ``(compacted_context, report)``. The caller replaces history
    with the result; raw tail files ground the model post-condensation.
    """
    prompt = build_condense_prompt(messages, summary)
    try:
        condensed = await summarize(prompt)
    except Exception as exc:
        logger.warning("condensation summarizer failed: %s", exc)
        return "", TierReport(tier="full", changed=False, detail=f"summarizer failed: {exc}")
    tail_blocks: list[str] = []
    for path in summary.touched_files[-tail_limit:]:
        try:
            tail_blocks.append(f"--- {path} ---\n{read_file(path)}")
        except Exception:
            logger.debug("tail re-inject skipped %s", path, exc_info=True)
    context = condensed + ("\n\n" + "\n".join(tail_blocks) if tail_blocks else "")
    return context, TierReport(tier="full", changed=True,
                               detail=f"9 sections + {len(tail_blocks)} tail file(s)")
