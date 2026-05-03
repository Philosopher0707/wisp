"""Markdown parser — extract structured data from LLM markdown responses.

Provides utilities for parsing code blocks, thinking sections, front matter,
lists, and tables from markdown text returned by the LLM.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CodeBlock:
    """A fenced code block extracted from markdown."""

    language: Optional[str]
    code: str
    start_line: int
    end_line: int


@dataclass
class ThinkingSection:
    """A thinking section extracted from markdown (between  thinking and /thinking)."""

    content: str
    start_line: int
    end_line: int


@dataclass
class MarkdownDocument:
    """Parsed markdown document with extracted structured elements."""

    raw: str
    thinking_sections: list[ThinkingSection] = field(default_factory=list)
    code_blocks: list[CodeBlock] = field(default_factory=list)
    front_matter: dict[str, str] = field(default_factory=dict)
    content_without_thinking: str = ""
    content_without_code: str = ""
    content_clean: str = ""


def parse_markdown(text: str) -> MarkdownDocument:
    """Parse a markdown string and extract structured elements.

    Extracts:
    - Thinking sections (between  thinking and /thinking)
    - Fenced code blocks (```lang ... ```)
    - YAML front matter (between --- delimiters)

    Returns a MarkdownDocument with all extracted elements.
    """
    doc = MarkdownDocument(raw=text)
    lines = text.splitlines()

    # Extract thinking sections
    doc.thinking_sections = _extract_thinking_sections(lines)
    doc.content_without_thinking = _remove_thinking_sections(text)

    # Extract code blocks
    doc.code_blocks = _extract_code_blocks(lines)
    doc.content_without_code = _remove_code_blocks(text)

    # Extract front matter
    doc.front_matter = _extract_front_matter(text)

    # Build clean content (no thinking, no code blocks)
    clean = doc.content_without_thinking
    clean = _remove_code_blocks(clean)
    clean = _clean_markdown(clean)
    doc.content_clean = clean.strip()

    return doc


def extract_code_blocks(text: str) -> list[CodeBlock]:
    """Extract all fenced code blocks from markdown text.

    Supports ```lang ... ``` fences with optional language tag.
    """
    return _extract_code_blocks(text.splitlines())


def extract_thinking(text: str) -> Optional[str]:
    """Extract the content of the first thinking section.

    Returns None if no thinking section is found.
    """
    sections = _extract_thinking_sections(text.splitlines())
    if sections:
        return sections[0].content
    return None


def extract_front_matter(text: str) -> dict[str, str]:
    """Extract YAML front matter from markdown text.

    Front matter must be between --- delimiters at the start of the text.
    Returns a dict of key-value pairs.
    """
    return _extract_front_matter(text)


def strip_markdown(text: str) -> str:
    """Strip markdown formatting, returning plain text.

    Removes:
    - Bold/italic markers (*, _)
    - Inline code (`)
    - Links [text](url) → text
    - Images ![alt](url) → alt
    - Headings (#)
    - Horizontal rules (---, ***)
    - Blockquotes (>)
    """
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # bold
    text = re.sub(r'\*(.+?)\*', r'\1', text)       # italic
    text = re.sub(r'__(.+?)__', r'\1', text)        # bold alt
    text = re.sub(r'_(.+?)_', r'\1', text)          # italic alt
    text = re.sub(r'`([^`]+)`', r'\1', text)        # inline code
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)  # images (before links!)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)   # links
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # headings
    text = re.sub(r'^[-*]{3,}\s*$', '', text, flags=re.MULTILINE)  # hr
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)  # blockquotes
    return text.strip()


def format_code_block(block: CodeBlock, show_line_numbers: bool = False) -> str:
    """Format a code block for display, optionally with line numbers."""
    lines = block.code.splitlines()
    if show_line_numbers:
        width = len(str(len(lines)))
        numbered = []
        for i, line in enumerate(lines, 1):
            numbered.append(f"{i:>{width}}  {line}")
        code = "\n".join(numbered)
    else:
        code = block.code

    if block.language:
        return f"```{block.language}\n{code}\n```"
    return f"```\n{code}\n```"


# ── Internal helpers ─────────────────────────────────────────────────


def _extract_thinking_sections(lines: list[str]) -> list[ThinkingSection]:
    """Extract  thinking ... /thinking sections."""
    sections: list[ThinkingSection] = []
    in_thinking = False
    start = 0
    content_lines: list[str] = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped == "thinking" and not in_thinking:
            in_thinking = True
            start = i
            content_lines = []
            continue

        if stripped == "/thinking" and in_thinking:
            sections.append(ThinkingSection(
                content="\n".join(content_lines),
                start_line=start,
                end_line=i,
            ))
            in_thinking = False
            content_lines = []
            continue

        if in_thinking:
            content_lines.append(line)

    return sections


def _remove_thinking_sections(text: str) -> str:
    """Remove  thinking ... /thinking sections from text."""
    return re.sub(
        r'^thinking\s*\n.*?^/thinking\s*$',
        '',
        text,
        flags=re.MULTILINE | re.DOTALL,
    )


def _extract_code_blocks(lines: list[str]) -> list[CodeBlock]:
    """Extract fenced code blocks (```...```)."""
    blocks: list[CodeBlock] = []
    in_block = False
    start = 0
    language: Optional[str] = None
    code_lines: list[str] = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Fence: ``` or ```lang
        m = re.match(r'^```(\w*)$', stripped)
        if m:
            if not in_block:
                in_block = True
                start = i
                language = m.group(1) or None
                code_lines = []
            else:
                blocks.append(CodeBlock(
                    language=language,
                    code="\n".join(code_lines),
                    start_line=start,
                    end_line=i,
                ))
                in_block = False
                language = None
                code_lines = []
            continue

        if in_block:
            code_lines.append(line)

    return blocks


def _remove_code_blocks(text: str) -> str:
    """Remove fenced code blocks from text."""
    return re.sub(
        r'^```\w*\s*\n.*?^```\s*$',
        '',
        text,
        flags=re.MULTILINE | re.DOTALL,
    )


def _extract_front_matter(text: str) -> dict[str, str]:
    """Extract YAML front matter (between --- delimiters at start)."""
    if not text.startswith("---"):
        return {}

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}

    front_matter: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            front_matter[key.strip()] = value.strip().strip("\"'")

    return front_matter


def _clean_markdown(text: str) -> str:
    """Light cleaning of markdown: normalize whitespace, remove HRs."""
    # Remove horizontal rules
    text = re.sub(r'^[-*]{3,}\s*$', '', text, flags=re.MULTILINE)
    # Collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
