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

# ── Color mode detection ─────────────────────────────────────────

_HIGH_CONTRAST = os.environ.get("WISP_HIGH_CONTRAST") is not None


def _is_disabled() -> bool:
    """Determine if colors should be disabled."""
    return (
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
        if _is_disabled() or not text:
            return text
        return f"\033[{self.code}m{text}\033[0m"

    def raw(self, text: str) -> str:
        """Apply style even when colors are disabled (for testing)."""
        return f"\033[{self.code}m{text}\033[0m"


# ── Semantic palette ─────────────────────────────────────────────────

# High-contrast (colorblind-safe): blue/yellow instead of red/green
if _HIGH_CONTRAST:
    # Deuteranopia/protanopia-safe: avoid red/green confusion
    success = _Style("34;1")      # bright blue (was green)
    error = _Style("31;1")        # keep red but bold — paired with [FAIL] text
    warning = _Style("93")        # bright yellow
    info = _Style("96")           # bright cyan
    dim = _Style("90")            # bright black
    bold = _Style("1")            # bold
    accent = _Style("95")         # bright magenta
    muted = _Style("37")          # default foreground
    border = _Style("94")         # blue for borders
    highlight = _Style("97")    # bright white
else:
    success = _Style("32")       # green
    error = _Style("31")         # red
    warning = _Style("33")       # yellow
    info = _Style("36")          # cyan
    dim = _Style("90")           # bright black / gray
    bold = _Style("1")           # bold
    accent = _Style("35")        # magenta
    muted = _Style("37")         # default foreground
    border = _Style("90")        # same as dim, semantically for panel borders
    highlight = _Style("97")     # bright white


# ── Helpers ──────────────────────────────────────────────────────────

def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    import re
    return re.sub(r"\033\[[0-9;]*m", "", text)


def is_enabled() -> bool:
    """Return True if colors are currently enabled."""
    return not _is_disabled()


def is_high_contrast() -> bool:
    """Return True if high-contrast (colorblind-safe) mode is active."""
    return _HIGH_CONTRAST
