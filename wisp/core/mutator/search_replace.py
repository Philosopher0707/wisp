"""Search-and-replace block applicator with 3-tier fuzzy matching.

Block format (strict fences, exact interior labels)::

    <<<<<<< SEARCH
    [exact lines to replace]
    =======
    [replacement lines]
    >>>>>>> REPLACE

Matching tiers, first hit wins:
  1. ``EXACT`` — byte-identical contiguous lines.
  2. ``WHITESPACE`` — indentation/whitespace-insensitive per-line match.
  3. ``SIMILAR`` — mean ``difflib.SequenceMatcher`` line similarity > 90%.

Disk safety: matching is pure (content in, span out). :func:`apply_block`
returns the patched text without touching disk; callers that persist must
do so themselves. A failed match raises :class:`SearchReplaceError` with
the best-tier diagnostic — the file on disk is never partially written
because it is never opened here at all.
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

SEARCH_FENCE = "<<<<<<< SEARCH"
MIDDLE_FENCE = "======="
REPLACE_FENCE = ">>>>>>> REPLACE"
SIMILARITY_THRESHOLD = 0.90


class SearchReplaceError(ValueError):
    """Raised when a block is malformed or matches nothing."""


class MatchTier(str, Enum):
    """Which matcher tier located the block."""

    EXACT = "exact"
    WHITESPACE = "whitespace"
    SIMILAR = "similar"


@dataclass(frozen=True)
class SearchReplaceBlock:
    """Parsed block: lines to find and lines to put in their place."""

    search_lines: tuple[str, ...]
    replace_lines: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.search_lines:
            raise SearchReplaceError("SEARCH section must not be empty")


def parse_block(text: str) -> SearchReplaceBlock:
    """Parse one fenced block; raises SearchReplaceError when malformed."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == SEARCH_FENCE)
        middle = next(i for i, line in enumerate(lines) if line.strip() == MIDDLE_FENCE and i > start)
        end = next(i for i, line in enumerate(lines) if line.strip() == REPLACE_FENCE and i > middle)
    except StopIteration:
        raise SearchReplaceError("block must contain SEARCH, =======, and REPLACE fences in order")
    search = tuple(lines[start + 1:middle])
    replace = tuple(lines[middle + 1:end])
    return SearchReplaceBlock(search_lines=search, replace_lines=replace)


def _normalize(line: str) -> str:
    return " ".join(line.split())


def _exact_span(content_lines: list[str], search: tuple[str, ...]) -> int | None:
    width = len(search)
    if width == 0 or width > len(content_lines):
        return None
    for i in range(len(content_lines) - width + 1):
        if content_lines[i:i + width] == list(search):
            return i
    return None


def _whitespace_span(content_lines: list[str], search: tuple[str, ...]) -> int | None:
    width = len(search)
    if width == 0 or width > len(content_lines):
        return None
    wanted = [_normalize(line) for line in search]
    for i in range(len(content_lines) - width + 1):
        if [_normalize(line) for line in content_lines[i:i + width]] == wanted:
            return i
    return None


def _similar_span(content_lines: list[str], search: tuple[str, ...]) -> int | None:
    width = len(search)
    if width == 0 or width > len(content_lines):
        return None
    best: tuple[float, int] | None = None
    for i in range(len(content_lines) - width + 1):
        window = content_lines[i:i + width]
        scores = [difflib.SequenceMatcher(None, want, got).ratio()
                  for want, got in zip(search, window)]
        mean = sum(scores) / len(scores)
        if best is None or mean > best[0]:
            best = (mean, i)
    if best is not None and best[0] > SIMILARITY_THRESHOLD:
        logger.debug("fuzzy block match at line %d (similarity %.3f)", best[1] + 1, best[0])
        return best[1]
    return None


def match_block(content: str, block: SearchReplaceBlock) -> tuple[int, MatchTier]:
    """Locate ``block`` in ``content``; returns (start_line_index, tier).

    Raises SearchReplaceError (with best-tier diagnostic) when no tier hits.
    Line indices are 0-based into ``content.splitlines()``.
    """
    lines = content.splitlines()
    start = _exact_span(lines, block.search_lines)
    if start is not None:
        return start, MatchTier.EXACT
    start = _whitespace_span(lines, block.search_lines)
    if start is not None:
        return start, MatchTier.WHITESPACE
    start = _similar_span(lines, block.search_lines)
    if start is not None:
        return start, MatchTier.SIMILAR
    raise SearchReplaceError(
        f"no match for {len(block.search_lines)} SEARCH line(s) "
        f"(best similarity below {SIMILARITY_THRESHOLD:.0%})"
    )


def apply_block(content: str, block: SearchReplaceBlock) -> tuple[str, MatchTier]:
    """Return patched text + matched tier. Pure: disk never touched."""
    lines = content.splitlines()
    start, tier = match_block(content, block)
    patched = lines[:start] + list(block.replace_lines) + lines[start + len(block.search_lines):]
    trailing_newline = content.endswith("\n")
    text = "\n".join(patched)
    if trailing_newline:
        text += "\n"
    return text, tier
