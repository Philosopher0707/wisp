"""Width-aware terminal rendering with ASCII fallback and accessible modes.

Production-grade terminal output requires:
  1. Correct display width for CJK, emoji, combining chars (wcwidth)
  2. Unicode / ASCII fallback for box drawing
  3. Accessible (screen-reader friendly) output mode
  4. High-contrast mode for colorblind users

Modes are detected from environment:
  WISP_OUTPUT_MODE=ascii     → ASCII-only output (no box drawing)
  WISP_OUTPUT_MODE=accessible  → Semantic markup, no unicode boxes
  WISP_OUTPUT_MODE=minimal     → Single-line, no formatting
  WISP_OUTPUT_MODE=unicode     → Full unicode + colors (default)

  WISP_HIGH_CONTRAST=1       → Colorblind-safe palette
  WISP_ACCESSIBLE=1          → Force accessible mode

All width calculations use display width, not Python len().
"""

from __future__ import annotations

import os
import re
from enum import StrEnum


class OutputMode(StrEnum):
    """Terminal output quality level."""
    UNICODE = "unicode"
    ASCII = "ascii"
    ACCESSIBLE = "accessible"
    MINIMAL = "minimal"


# ── Mode detection ───────────────────────────────────────────────────

def _detect_mode() -> OutputMode:
    """Detect output mode from environment."""
    if os.environ.get("WISP_ACCESSIBLE") or os.environ.get("ACCESSIBLE"):
        return OutputMode.ACCESSIBLE
    explicit = os.environ.get("WISP_OUTPUT_MODE", "").lower()
    if explicit in {"ascii", "a"}:
        return OutputMode.ASCII
    if explicit in {"accessible", "acc", "screen-reader", "a11y"}:
        return OutputMode.ACCESSIBLE
    if explicit in {"minimal", "min", "plain", "raw"}:
        return OutputMode.MINIMAL
    if explicit in {"unicode", "fancy", "full"}:
        return OutputMode.UNICODE
    # Auto-detect: if NO_COLOR or non-TTY
    if os.environ.get("NO_COLOR") or not __import__("sys").stdout.isatty():
        return OutputMode.ASCII
    # Check TERM for dumb terminals
    term = os.environ.get("TERM", "")
    if "dumb" in term or "vt100" in term.lower():
        return OutputMode.ASCII
    # Default: unicode
    return OutputMode.UNICODE


# Global mode — set at import time
OUTPUT_MODE = _detect_mode()


def get_output_mode() -> OutputMode:
    """Get the current output mode."""
    return OUTPUT_MODE


def set_output_mode(mode: str | OutputMode) -> None:
    """Override the output mode at runtime."""
    global OUTPUT_MODE
    if isinstance(mode, str):
        mode = mode.lower()
        OUTPUT_MODE = {
            "unicode": OutputMode.UNICODE, "fancy": OutputMode.UNICODE,
            "ascii": OutputMode.ASCII, "a": OutputMode.ASCII,
            "accessible": OutputMode.ACCESSIBLE, "acc": OutputMode.ACCESSIBLE,
            "a11y": OutputMode.ACCESSIBLE, "screen-reader": OutputMode.ACCESSIBLE,
            "minimal": OutputMode.MINIMAL, "min": OutputMode.MINIMAL,
            "plain": OutputMode.MINIMAL, "raw": OutputMode.MINIMAL,
        }.get(mode, OutputMode.UNICODE)
    else:
        OUTPUT_MODE = mode


def is_high_contrast() -> bool:
    """Check if high-contrast mode is requested."""
    return os.environ.get("WISP_HIGH_CONTRAST") is not None


def is_accessible() -> bool:
    """Check if accessible/screen-reader mode is active."""
    return OUTPUT_MODE == OutputMode.ACCESSIBLE or is_high_contrast()


# ── Display width (wcwidth) ──────────────────────────────────────────

def display_width(text: str) -> int:
    """Calculate the display width of text, accounting for wide characters.

    If wcwidth is installed, uses it for precise calculation.
    Otherwise falls back to a fast approximation.
    """
    if not text:
        return 0
    try:
        import wcwidth
        # Strip ANSI first, then calculate
        plain = _strip_ansi(text)
        total = 0
        for ch in plain:
            w = wcwidth.wcwidth(ch)
            if w is None:  # Control char / non-printable
                w = 0
            total += max(0, w)
        return total
    except ImportError:
        return _display_width_approx(text)


def _display_width_approx(text: str) -> int:
    """Fast approximate display width without wcwidth dependency.

    Handles basic wide characters (CJK, emoji) without the full
    Unicode East Asian Width database.
    """
    plain = _strip_ansi(text)
    total = 0
    for ch in plain:
        cp = ord(ch)
        if cp == 0x00AD:  # soft hyphen — zero width
            continue
        if cp < 0x1100:
            total += 1
            continue
        # Wide blocks
        if (0x1100 <= cp <= 0x115F or  # Hangul Jamo
            0x2E80 <= cp <= 0x9FFF or  # CJK Unified Ideographs
            0xA960 <= cp <= 0xA97F or  # Hangul Jamo Extended-A
            0xAC00 <= cp <= 0xD7AF or  # Hangul Syllables
            0xD7B0 <= cp <= 0xD7FF or  # Hangul Jamo Extended-B
            0xF900 <= cp <= 0xFAFF or  # CJK Compatibility Ideographs
            0xFE10 <= cp <= 0xFE19 or  # Vertical Forms
            0xFE30 <= cp <= 0xFE6F or  # CJK Compatibility Forms
            0xFF00 <= cp <= 0xFF60 or  # Fullwidth Forms
            0xFFE0 <= cp <= 0xFFE6 or  # Fullwidth Symbols
            0x1F300 <= cp <= 0x1F64F or  # Emoticons / Misc Symbols
            0x1F900 <= cp <= 0x1F9FF or  # Supplemental Symbols
            0x20000 <= cp <= 0x2A6DF or  # CJK Extension B
            0x2A700 <= cp <= 0x2B73F or  # CJK Extension C/D
            0x2B740 <= cp <= 0x2B81F or  # CJK Extension E
            0x2B820 <= cp <= 0x2CEAF or  # CJK Extension F
            0x2CEB0 <= cp <= 0x2EBEF or  # CJK Extension G
            0x30000 <= cp <= 0x3134F     # CJK Extension H
        ):
            total += 2
        elif 0x0300 <= cp <= 0x036F:  # Combining diacriticals — zero width
            total += 0
        else:
            total += 1
    return total


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\033\[[0-9;]*m", "", text)


# ── Width-aware text wrapping ───────────────────────────────────────

def wrap_text_wide(text: str, width: int, indent: str = "") -> list[str]:
    """Wrap text to display width, accounting for wide characters.

    Args:
        text: The text to wrap
        width: Maximum display width per line
        indent: Indentation for continuation lines

    Returns:
        List of wrapped lines respecting display width
    """
    if not text:
        return [""]

    lines = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        if display_width(paragraph) <= width:
            lines.append(paragraph)
            continue

        # Manual wrap respecting display width
        words = paragraph.split(" ")
        current = ""
        current_width = 0
        first = True

        for word in words:
            w = display_width(word)
            if w > width:
                # Word too long: break it
                _break_long_word(lines, current, word, width, indent, is_first=first)
                current = ""
                current_width = 0
                first = False
                continue

            space = 0 if not current else 1
            new_width = current_width + space + w
            if new_width <= width:
                if current:
                    current += " " + word
                else:
                    current = word
                current_width = new_width
            else:
                # Flush current line
                if first:
                    lines.append(current)
                    first = False
                else:
                    lines.append(indent + current)
                current = word
                current_width = w

        if current:
            if first:
                lines.append(current)
            else:
                lines.append(indent + current)

    return lines


def _break_long_word(lines: list, prefix: str, word: str, width: int,
                     indent: str, is_first: bool) -> None:
    """Break a single long word across multiple lines."""
    # Try to break at grapheme boundaries if possible
    current = prefix
    current_width = display_width(prefix) if prefix else 0

    for ch in word:
        cw = display_width(ch)
        if current and current_width + cw > width:
            if is_first and not lines:
                lines.append(current)
                is_first = False
            else:
                lines.append(indent + current)
            current = ch
            current_width = cw
        else:
            current += ch
            current_width += cw

    if current:
        if is_first and not lines:
            lines.append(current)
        else:
            lines.append(indent + current)


# ── Width-aware padding ─────────────────────────────────────────────

def pad_right(text: str, target_width: int, fill: str = " ") -> str:
    """Right-pad text to target display width."""
    current = display_width(text)
    if current >= target_width:
        return text
    # If the pad char is wide, adjust count
    pad_width = display_width(fill)
    if pad_width == 0:
        return text
    count = (target_width - current) // pad_width
    remainder = (target_width - current) % pad_width
    return text + fill * count + (" " * remainder)


def pad_left(text: str, target_width: int, fill: str = " ") -> str:
    """Left-pad text to target display width."""
    current = display_width(text)
    if current >= target_width:
        return text
    pad_width = display_width(fill)
    if pad_width == 0:
        return text
    count = (target_width - current) // pad_width
    remainder = (target_width - current) % pad_width
    return fill * count + (" " * remainder) + text


def center(text: str, target_width: int, fill: str = " ") -> str:
    """Center text in target display width."""
    current = display_width(text)
    if current >= target_width:
        return text
    pad_width = display_width(fill)
    if pad_width == 0:
        return text
    left = (target_width - current) // 2
    right = target_width - current - left
    left_count = left // pad_width
    right_count = right // pad_width
    return fill * left_count + text + fill * right_count


# ── Box-drawing characters per mode ──────────────────────────────────

class BoxChars:
    """Box-drawing characters for each output mode."""

    def __init__(self, mode: OutputMode | None = None):
        self.mode = mode or OUTPUT_MODE
        self._chars = self._get_chars()

    def _get_chars(self) -> dict[str, str]:
        """Get box characters for current mode."""
        if self.mode == OutputMode.ASCII:
            return {
                "tl": "+", "tr": "+", "bl": "+", "br": "+",
                "hz": "-", "vt": "|",
                "rule_h": "-", "rule_v": "|",
            }
        elif self.mode == OutputMode.MINIMAL:
            return {
                "tl": "", "tr": "", "bl": "", "br": "",
                "hz": "", "vt": "",
                "rule_h": "=", "rule_v": "",
            }
        elif self.mode == OutputMode.ACCESSIBLE:
            return {
                "tl": "[", "tr": "]", "bl": "[", "br": "]",
                "hz": "-", "vt": "|",
                "rule_h": "-", "rule_v": "|",
            }
        else:  # UNICODE
            return {
                "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
                "hz": "─", "vt": "│",
                "rule_h": "─", "rule_v": "│",
            }

    @property
    def tl(self) -> str: return self._chars["tl"]
    @property
    def tr(self) -> str: return self._chars["tr"]
    @property
    def bl(self) -> str: return self._chars["bl"]
    @property
    def br(self) -> str: return self._chars["br"]
    @property
    def hz(self) -> str: return self._chars["hz"]
    @property
    def vt(self) -> str: return self._chars["vt"]
    @property
    def rule_h(self) -> str: return self._chars["rule_h"]
    @property
    def rule_v(self) -> str: return self._chars["rule_v"]

    def horizontal(self, width: int) -> str:
        """Horizontal line of given width."""
        if self.mode == OutputMode.MINIMAL:
            return ""
        return self.hz * width

    def top(self, width: int, title: str = "") -> str:
        """Top border with optional title."""
        if self.mode == OutputMode.MINIMAL:
            return f"[{title}]" if title else ""
        hz = self.hz
        available = width - 2
        if title:
            title_text = f" {title} "
            title_width = display_width(title_text)
            if title_width > available:
                title_text = title_text[:available]
                title_width = display_width(title_text)
            left = (available - title_width) // 2
            right = available - title_width - left
            return self.tl + hz * left + title_text + hz * right + self.tr
        return self.tl + hz * available + self.tr

    def bottom(self, width: int) -> str:
        """Bottom border."""
        if self.mode == OutputMode.MINIMAL:
            return ""
        return self.bl + self.hz * (width - 2) + self.br

    def line(self, width: int, content: str = "") -> str:
        """Content line with side borders."""
        if self.mode == OutputMode.MINIMAL:
            return content
        inner_width = width - 4  # 2 borders + 2 spaces padding
        content_width = display_width(content)
        if content_width > inner_width:
            # Truncate content
            plain = content
            truncated = ""
            current_w = 0
            for ch in plain:
                cw = display_width(ch)
                if current_w + cw > inner_width:
                    break
                truncated += ch
                current_w += cw
            content = truncated
        padding = " " * (inner_width - display_width(content))
        return self.vt + " " + content + padding + " " + self.vt


# ── Text helpers ─────────────────────────────────────────────────────

def truncate(text: str, max_width: int, suffix: str = "...") -> str:
    """Truncate text to max display width."""
    w = display_width(text)
    if w <= max_width:
        return text
    suffix_w = display_width(suffix)
    available = max_width - suffix_w
    result = ""
    current_w = 0
    for ch in text:
        cw = display_width(ch)
        if current_w + cw > available:
            return result + suffix
        result += ch
        current_w += cw
    return result


def truncate_no_ellipsis(text: str, max_width: int) -> str:
    """Truncate text to max display width, no suffix."""
    w = display_width(text)
    if w <= max_width:
        return text
    result = ""
    current_w = 0
    for ch in text:
        cw = display_width(ch)
        if current_w + cw > max_width:
            return result
        result += ch
        current_w += cw
    return result


def status_symbols() -> dict[str, str]:
    """Mode-aware status glyphs for user-facing markers.

    Single source of truth so prompts, banners, and event rendering agree
    per mode: accessible spells words out, ASCII stays printable, minimal
    drops decoration entirely, unicode gets the pretty glyphs.
    """
    if is_accessible():
        return {
            "ok": "[PASS]", "fail": "[FAIL]", "warn": "[WARN]",
            "info": "[NOTE]", "cancel": "[STOP]", "pause": "[PAUSED]",
            "thinking": "[THINKING]", "exit": "[EXIT]",
        }
    mode = OUTPUT_MODE
    if mode == OutputMode.ASCII:
        return {
            "ok": "[OK]", "fail": "[X]", "warn": "[!]",
            "info": "*", "cancel": "x", "pause": "||",
            "thinking": "...", "exit": "bye",
        }
    if mode == OutputMode.MINIMAL:
        # Status markers carry information, so they survive minimality;
        # purely decorative glyphs do not.
        return {
            "ok": "[OK]", "fail": "[X]", "warn": "[!]",
            "info": "", "cancel": "", "pause": "",
            "thinking": "", "exit": "",
        }
    return {
        "ok": "✓", "fail": "✗", "warn": "⚠",
        "info": "•", "cancel": "⏹", "pause": "⏸",
        "thinking": "🧠", "exit": "👋",
    }
