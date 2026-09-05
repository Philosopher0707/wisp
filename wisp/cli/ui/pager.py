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


def _pager_verdict(key: str) -> str | None:
    """Pure key mapping for the pager: y=apply, n/q/escape=abort."""
    if key in ("y", "Y"):
        return "apply"
    if key in ("n", "N", "q", "Q", "escape"):
        return "abort"
    return None


def show_diff(files: list[tuple[str, str, str]]) -> str:
    """Open unified diffs fullscreen; returns 'apply' | 'abort'.

    Never opens a pager without a TTY or without Textual (returns 'abort').
    Caller must hold TerminalGuard (alt-screen ownership lives there).
    """
    import sys
    if not sys.stdout.isatty():
        return "abort"
    try:
        from textual.app import App, ComposeResult
        from textual.widgets import Footer, Header, RichLog
    except ImportError:
        return "abort"
    from wisp.ui.diff_viewer import generate_unified_diff

    verdict = {"value": "abort"}

    class DiffPager(App):
        def compose(self) -> ComposeResult:
            yield Header()
            yield RichLog(highlight=True, markup=False)
            yield Footer()

        def on_mount(self) -> None:
            log = self.query_one(RichLog)
            for path, old, new in files:
                log.write(f"--- {path}")
                log.write(generate_unified_diff(old, new, file_path=path))

        def on_key(self, event) -> None:
            v = _pager_verdict(event.key)
            if v is not None:
                verdict["value"] = v
                self.exit()

    DiffPager().run()
    return verdict["value"]
