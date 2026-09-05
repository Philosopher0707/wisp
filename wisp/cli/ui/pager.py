"""Diff threshold + lazy Textual alt-screen pager."""
from __future__ import annotations

DIFF_PAGE_THRESHOLD = 60  # added+removed lines, per spec section 2 (aesthetics cap-50 unification is a follow-up)


def summarize_diffs(pairs: list[tuple[str, str, str]]) -> list[tuple[str, int, int]]:
    """(path, old_text, new_text) -> (path, added, removed) via diff_viewer (single source of truth)."""
    from wisp.ui.diff_viewer import compute_diff_stats
    out: list[tuple[str, int, int]] = []
    for path, old, new in pairs:
        added, removed, _ = compute_diff_stats(old, new)
        out.append((path, added, removed))
    return out


def should_page_diff(pairs: list[tuple[str, str, str]]) -> bool:
    """True when total added+removed lines exceed the threshold.

    Single-entry convention: text-pairs in everywhere, never counts-in.
    """
    return sum(a + r for _, a, r in summarize_diffs(pairs)) > DIFF_PAGE_THRESHOLD
