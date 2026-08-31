"""Tool output collapsing — preview badge, link, pager.

Replaces wisp/transport/cli.py:_preview_lines 3-line truncation with
configurable, interactively expandable blocks:

  collapsed: 3 preview lines + … +47 more [press 'e' / /expand to view • .agent/logs/last_command.log]
  expanded:  full output in pager / scroll viewport

Wired: agent/tools/runner.py writes raw -> disk sinks first, returns
truncated DisplayPayload to UI.  formatter never drops bytes.

Config: WISP_MAX_TOOL_DISPLAY_LINES (default 10) or --verbose-tools
"""

from __future__ import annotations

import os
import pydoc
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    _RICH = True
except Exception:
    Console = Panel = Text = None  # type: ignore
    _RICH = False

__all__ = [
    "DisplayPayload",
    "collapse",
    "render_collapsed",
    "render_expanded",
    "pager",
]

DEFAULT_MAX_LINES = int(os.getenv("WISP_MAX_TOOL_DISPLAY_LINES", "10"))
ARTIFACT_DIR = Path(".agent/logs")
LAST_PATH = ARTIFACT_DIR / "last_command.log"


@dataclass
class DisplayPayload:
    """What runner returns to UI — truncated view + durable artifact."""

    preview: str  # collapsed text shown inline
    full_path: Optional[Path] = None  # .agent/logs/run_…log  (None if small)
    last_path: Optional[Path] = None  # .agent/logs/last_command.log
    total_lines: int = 0
    total_chars: int = 0
    truncated: bool = False
    tool: str = "run_bash"
    # keep raw for pager without re-reading file (bounded)
    _full_text: Optional[str] = None

    @property
    def badge(self) -> str:
        if not self.truncated:
            return ""
        return f"… +{self.total_lines - DEFAULT_MAX_LINES} more [press 'e' or /expand — {self.full_path or self.last_path}]"

    def read_full(self, max_chars: int = 2_000_000) -> str:
        if self._full_text is not None:
            return self._full_text[:max_chars]
        p = self.full_path or self.last_path
        if p and p.exists():
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
                return txt[:max_chars]
            except Exception:
                pass
        return self.preview


def collapse(
    text: str,
    *,
    tool: str = "run_bash",
    max_lines: Optional[int] = None,
    full_text: Optional[str] = None,
    artifact_path: Optional[Path] = None,
) -> DisplayPayload:
    """Build collapsed payload from (already sunk) output.

    `text` is the full raw stdout/stderr (runner already wrote it to disk).
    We slice preview here; disk artifact stays whole.
    """
    if max_lines is None:
        max_lines = DEFAULT_MAX_LINES
        # honor --verbose-tools / config flag
        if os.getenv("WISP_VERBOSE_TOOLS", "").lower() in ("1", "true", "yes"):
            max_lines = 10_000

    lines = text.splitlines()
    total_lines = len(lines)
    total_chars = len(text)
    truncated = total_lines > max_lines

    if not truncated:
        preview = text
    else:
        head = lines[:max_lines]
        preview = "\n".join(head) + f"\n… +{total_lines - max_lines} more [press 'e' or /expand — {artifact_path or LAST_PATH}]"
        if artifact_path or LAST_PATH.exists():
            preview += f"\n[✓ Full output → {artifact_path or LAST_PATH}]"

    return DisplayPayload(
        preview=preview,
        full_path=artifact_path,
        last_path=LAST_PATH if (artifact_path or truncated) else None,
        total_lines=total_lines,
        total_chars=total_chars,
        truncated=truncated,
        tool=tool,
        _full_text=text if truncated else None,
    )


def render_collapsed(payload: DisplayPayload, console: Optional[Console] = None) -> str:  # type: ignore
    """Render collapsed view — badge + preview + link."""
    if not _RICH or console is None:
        return payload.preview
    # Rich panel with subdued truncated hint
    title = f"{payload.tool} · {payload.total_lines} lines"
    if payload.truncated:
        title += f" · {payload.total_lines - DEFAULT_MAX_LINES} hidden"
    panel = Panel(
        Text(payload.preview, style="dim"),
        title=title,
        border_style="dim",
        subtitle=f"[dim]press 'e' / /expand — {payload.full_path or payload.last_path}[/dim]" if payload.truncated else None,
        subtitle_align="right",
    )
    # Caller prints via console; we return text for non-rich consumers
    return payload.preview


def render_expanded(payload: DisplayPayload) -> str:
    """Return full text for pager/scroll container."""
    return payload.read_full()


def pager(text: str, *, use_rich: bool = True) -> None:
    """Show full output in system pager (less) or rich scroll.

    Prefers `pydoc.pager` which invokes $PAGER/less; falls back to rich
    console pager when not a tty.
    """
    try:
        # pydoc.pager handles less/more + Windows automatically
        pydoc.pager(text)
        return
    except Exception:
        pass
    if _RICH:
        try:
            c = Console()
            # Use rich's pager helper
            with c.pager(styles=True):
                c.print(text)
            return
        except Exception:
            pass
    # last resort
    print(text)
