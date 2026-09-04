"""Disk-safe code mutation primitives."""

from __future__ import annotations

from wisp.core.mutator.search_replace import (
    MatchTier,
    SearchReplaceBlock,
    SearchReplaceError,
    apply_block,
    match_block,
    parse_block,
)

__all__ = [
    "MatchTier",
    "SearchReplaceBlock",
    "SearchReplaceError",
    "apply_block",
    "match_block",
    "parse_block",
]
