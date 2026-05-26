"""Autocomplete engine for Wisp — fill-in-the-middle completions via configured LLM.

BYOK-first: passes through to the user's configured provider (Ollama by default).
Accepts cursor position + surrounding context, builds a fill-in-the-middle prompt,
and returns only the completion text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from wisp.config import WispConfig
from wisp.providers import get_provider

logger = logging.getLogger(__name__)

# Default context window around cursor
DEFAULT_CONTEXT_TOKENS = 2048
MAX_COMPLETION_TOKENS = 256


@dataclass
class CompletionRequest:
    file_content: str
    cursor_line: int  # 0-based
    cursor_char: int   # 0-based
    path: str = ""
    language: str = ""


@dataclass
class CompletionResult:
    text: str
    finish_reason: str = "stop"


def _extract_context(file_content: str, cursor_line: int, cursor_char: int,
                     max_tokens: int = DEFAULT_CONTEXT_TOKENS) -> tuple[str, str, str]:
    """Split file content into prefix (before cursor) and suffix (after cursor).

    Returns (prefix, suffix, cursor_line_text_prefix).
    Approximate token limit via character count (4 chars ≈ 1 token).
    """
    max_chars = max_tokens * 4
    lines = file_content.split("\n")

    if cursor_line >= len(lines):
        cursor_line = len(lines) - 1
    if cursor_line < 0:
        cursor_line = 0

    line = lines[cursor_line] if cursor_line < len(lines) else ""
    if cursor_char > len(line):
        cursor_char = len(line)
    if cursor_char < 0:
        cursor_char = 0

    # Prefix: everything before cursor
    prefix_lines = lines[:cursor_line]
    prefix_text = "\n".join(prefix_lines)
    if prefix_lines:
        prefix_text += "\n"
    prefix_text += line[:cursor_char]

    # Suffix: everything after cursor
    suffix_text = line[cursor_char:]
    if cursor_line + 1 < len(lines):
        suffix_text += "\n" + "\n".join(lines[cursor_line + 1:])

    # Trim to context window, keeping content nearest cursor
    if len(prefix_text) > max_chars:
        prefix_text = prefix_text[-max_chars:]
    if len(suffix_text) > max_chars:
        suffix_text = suffix_text[:max_chars]

    return prefix_text, suffix_text, line[:cursor_char]


def build_completion_prompt(prefix: str, suffix: str, path: str = "",
                            language: str = "") -> str:
    """Build a fill-in-the-middle prompt for code completion."""
    lang_hint = f" in {language}" if language else ""
    file_hint = f"\nFile: {path}" if path else ""

    return f"""You are a code completion assistant. Continue the code at the <CURSOR> position.
Output ONLY the completion text — no explanation, no markdown fences.{file_hint}

```{language or 'code'}
{prefix}<CURSOR>{suffix}
```

Complete the code after <CURSOR>. The completion should be syntactically valid{lang_hint}.
Output only the code that replaces <CURSOR>. Do not repeat existing code."""


async def generate_completion(
    request: CompletionRequest,
    config: WispConfig,
    max_tokens: int = MAX_COMPLETION_TOKENS,
) -> CompletionResult:
    """Generate a code completion using the configured provider.

    Uses a non-streaming generate call with a low-temperature prompt
    optimized for fill-in-the-middle completions.
    """
    prefix, suffix, _ = _extract_context(
        request.file_content, request.cursor_line, request.cursor_char
    )

    if not prefix.strip() and not suffix.strip():
        return CompletionResult(text="", finish_reason="stop")

    prompt = build_completion_prompt(
        prefix, suffix, request.path, request.language
    )

    # Use lower temperature for precise completions
    completion_config = config.replace(temperature=0.1)
    try:
        provider = get_provider(completion_config)

        messages = [{"role": "user", "content": prompt}]
        response = provider.generate(system_prompt="", messages=messages)

        completion_text = ""
        if isinstance(response, dict):
            content = response.get("message", {}).get("content", "")
            if not content:
                content = response.get("response", "")
            completion_text = content.strip()

        # Strip markdown fences if present
        if completion_text.startswith("```"):
            lines = completion_text.split("\n")
            # Remove first line (```lang or ```)
            if len(lines) > 1:
                lines = lines[1:]
            # Remove last line if it's just ```
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            completion_text = "\n".join(lines).strip()

        # Remove trailing whitespace-only lines, keep structure
        while completion_text.endswith("\n\n"):
            completion_text = completion_text[:-1]

        return CompletionResult(text=completion_text, finish_reason="stop")

    except Exception as e:
        logger.warning("Completion generation failed: %s", e)
        return CompletionResult(text="", finish_reason="error")
