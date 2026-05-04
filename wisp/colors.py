"""Minimal ANSI color support for Wisp terminal output.

Zero dependencies. Respects the NO_COLOR environment variable.
https://no-color.org/

Usage:
    from wisp.colors import success, error, warning, info, dim, bold, accent

    print(success("✓ Done"))
    print(error("✗ Failed"))
    print(dim("  🛠  read_file(...)"))
"""

import os
import sys

# Disable colors if NO_COLOR is set or stdout is not a TTY
_DISABLED = (
    os.environ.get("NO_COLOR") is not None
    or os.environ.get("WISP_NO_COLOR") is not None
    or not sys.stdout.isatty()
)


class _Style:
    """Lazy ANSI style wrapper."""

    __slots__ = ("code",)

    def __init__(self, code: str):
        self.code = code

    def __call__(self, text: str) -> str:
        if _DISABLED or not text:
            return text
        return f"\033[{self.code}m{text}\033[0m"

    def raw(self, text: str) -> str:
        """Apply style even when colors are disabled (for testing)."""
        return f"\033[{self.code}m{text}\033[0m"


# ── Semantic palette ─────────────────────────────────────────────────

success = _Style("32")      # green
error = _Style("31")        # red
warning = _Style("33")      # yellow
info = _Style("36")         # cyan
dim = _Style("90")          # bright black / gray
bold = _Style("1")          # bold
accent = _Style("35")       # magenta

# ── Combinations ─────────────────────────────────────────────────────

success_bold = _Style("1;32")
error_bold = _Style("1;31")
warning_bold = _Style("1;33")
info_bold = _Style("1;36")
accent_bold = _Style("1;35")


# ── Helpers ──────────────────────────────────────────────────────────

def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    import re
    return re.sub(r"\033\[[0-9;]*m", "", text)


def is_enabled() -> bool:
    """Return True if colors are currently enabled."""
    return not _DISABLED
