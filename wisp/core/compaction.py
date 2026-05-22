"""Compactor — LLM-powered conversation summarization.

Replaces simple truncation with structured summarization that preserves
decisions, errors, and task state. Falls back to truncation if no
secondary model is available.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from wisp.infra.token_counter import TokenCounter

logger = logging.getLogger(__name__)

COMPACTION_SYSTEM_PROMPT = """You are a conversation compressor. Summarize the conversation below.
Preserve ALL of the following:
1. Key decisions made and their rationale
2. Files modified, created, or deleted (with paths)
3. Errors encountered and how they were resolved
4. Current task state and what remains to be done
5. Any important context the assistant will need to continue

Output ONLY the summary. No preamble, no "here is a summary", just the compressed content.
Be thorough but concise — capture everything needed to resume the conversation seamlessly."""


@dataclass
class CompactionResult:
    """Result of a compaction operation."""

    summary: str
    decisions_made: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    error_context: list[str] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    model_used: str = ""
    fallback_truncation: bool = False


@dataclass
class Compactor:
    """LLM-powered conversation compactor.

    Uses a secondary (typically smaller/faster) model to summarize
    conversation history. Falls back to simple truncation if:
    - No compaction_model is configured
    - The provider is unreachable
    - The summarization call fails
    """

    provider_factory: Callable[[str], Any]  # model_name -> Provider
    token_counter: TokenCounter = field(default_factory=TokenCounter)
    compaction_model: str = ""
    chars_per_token: int = 4

    async def compact(
        self,
        messages: list[dict],
        keep_recent: int = 6,
    ) -> CompactionResult:
        """Compact conversation history.

        Args:
            messages: Full message list to compact.
            keep_recent: Number of recent messages to preserve.

        Returns:
            CompactionResult with summary and metadata.
        """
        tokens_before = sum(
            self.token_counter.count(str(msg.get("content", "")))
            for msg in messages
        )

        if len(messages) <= keep_recent:
            return CompactionResult(
                summary="",
                tokens_before=tokens_before,
                tokens_after=tokens_before,
                fallback_truncation=True,
            )

        to_summarize = messages[:-keep_recent]
        kept = messages[-keep_recent:]

        # Try LLM summarization if model configured
        if self.compaction_model:
            try:
                result = await self._llm_summarize(to_summarize, kept)
                if result is not None:
                    return result
            except Exception:
                logger.warning("LLM compaction failed, falling back to truncation",
                               exc_info=True)

        # Fallback: simple truncation with basic summary
        return self._truncate_fallback(to_summarize, kept, tokens_before)

    async def _llm_summarize(
        self,
        to_summarize: list[dict],
        kept: list[dict],
    ) -> CompactionResult | None:
        """Attempt LLM-powered summarization."""
        conversation_text = self._format_messages(to_summarize)
        kept_text = self._format_messages(kept)

        user_prompt = f"""Recent context (WILL be preserved — do NOT repeat this):
{kept_text}

Messages to compress:
{conversation_text}"""

        provider = self.provider_factory(self.compaction_model)
        if provider is None:
            return None

        # Run sync provider in thread
        loop = asyncio.get_running_loop()
        start = time.time()

        summary_parts: list[str] = []

        def _run():
            try:
                for event in provider.generate_stream_events(
                    system_prompt=COMPACTION_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                    tools=None,
                ):
                    if event.get("type") == "content":
                        summary_parts.append(event.get("text", ""))
                    elif event.get("type") == "error":
                        raise RuntimeError(event.get("message", "Compaction failed"))
            except Exception as e:
                summary_parts.append(f"[ERROR: {e}]")

        await loop.run_in_executor(None, _run)

        summary = "".join(summary_parts).strip()
        if not summary or summary.startswith("[ERROR"):
            return None

        tokens_after = self.token_counter.count(summary)
        for msg in kept:
            tokens_after += self.token_counter.count(str(msg.get("content", "")))

        # Extract structured fields from summary
        decisions = self._extract_section(summary, "decisions")
        files = self._extract_section(summary, "files")
        errors = self._extract_section(summary, "errors")

        logger.info(
            "Compacted %d messages: %d → %d tokens (model=%s, %.1fs)",
            len(to_summarize),
            sum(self.token_counter.count(str(m.get("content", ""))) for m in to_summarize),
            tokens_after,
            self.compaction_model,
            time.time() - start,
        )

        return CompactionResult(
            summary=summary,
            decisions_made=decisions,
            files_touched=files,
            error_context=errors,
            tokens_before=sum(self.token_counter.count(str(m.get("content", ""))) for m in to_summarize + kept),
            tokens_after=tokens_after,
            model_used=self.compaction_model,
            fallback_truncation=False,
        )

    def _truncate_fallback(
        self,
        to_summarize: list[dict],
        kept: list[dict],
        tokens_before: int,
    ) -> CompactionResult:
        """Simple truncation fallback when LLM is unavailable."""
        truncated_messages = to_summarize[-20:] if len(to_summarize) > 20 else to_summarize
        excerpts: list[str] = []
        for msg in truncated_messages:
            content = str(msg.get("content", ""))
            role = msg.get("role", "unknown")
            if len(content) > 200:
                content = content[:200] + "..."
            if content.strip():
                excerpts.append(f"[{role}]: {content}")

        summary = f"[Compacted {len(to_summarize)} messages. Recent excerpts:\n" + "\n".join(excerpts) + "\n]"

        tokens_after = self.token_counter.count(summary)
        for msg in kept:
            tokens_after += self.token_counter.count(str(msg.get("content", "")))

        return CompactionResult(
            summary=summary,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            fallback_truncation=True,
        )

    def _format_messages(self, messages: list[dict]) -> str:
        """Format messages into a readable text block."""
        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", ""))
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    name = tc.get("function", {}).get("name", "unknown")
                    args = tc.get("function", {}).get("arguments", {})
                    lines.append(f"[{role} → tool_call: {name}({args})]")
            elif content.strip():
                lines.append(f"[{role}]: {content}")
        return "\n".join(lines)

    def _extract_section(self, text: str, label: str) -> list[str]:
        """Extract items from a labeled section in the summary."""
        import re
        pattern = rf'(?i){label}[:\s]*(.*?)(?=\n\n|\n[A-Z]|\Z)'
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            return []
        items = match.group(1).strip()
        if not items:
            return []
        return [
            item.strip().lstrip("-* ").strip()
            for item in items.split("\n")
            if item.strip()
        ]
