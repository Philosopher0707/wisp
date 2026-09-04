"""Startup status card for `wisp repl` (rich, single rounded frame).

All data arrives via :class:`BannerData` — this module performs no network
or provider I/O. Git state is best-effort with a tight thread budget so a
cold or giant repo can never stall boot. Rendering is width-bounded and
ANSI-clean: tests assert every line fits the target width with zero escape
sequences in export mode, which is what keeps macOS Terminal, iTerm2,
Alacritty, and Kitty free of margin drift.
"""

from __future__ import annotations

import concurrent.futures
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

GIT_BUDGET_S = 0.4
MIN_WIDTH = 40


def _version() -> str:
    try:
        from wisp import __version__

        return str(__version__)
    except ImportError:
        return "0.0.0"


@dataclass(frozen=True)
class BannerData:
    """Everything the status card may display. Plain strings, no I/O."""

    model: str = ""
    provider: str = ""
    session_id: str = ""
    workspace: str = ""
    git_segment: str = "—"
    ctx_used: int = 0
    ctx_limit: int = 0
    preflight_line: str = ""
    preflight_ok: bool = True
    pool_line: str = "Pool: —"
    transport_line: str = "⚡ Connected"
    skill: str = ""


def _git_state_fast(workspace: str, timeout_s: float = GIT_BUDGET_S) -> Any:
    """Fetch git state off-thread inside a hard budget (never raises)."""
    from wisp.git_context import get_git_state

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(get_git_state, workspace)
        try:
            return future.result(timeout=timeout_s)
        except Exception:
            future.cancel()
            return None


def collect_git_segment(workspace: str, timeout_s: float = GIT_BUDGET_S) -> str:
    """Compact ``branch ✗N`` segment; ``—`` when unavailable. Never raises."""
    try:
        state = _git_state_fast(workspace, timeout_s)
    except Exception:
        logger.debug("git segment failed", exc_info=True)
        return "—"
    if state is None or not getattr(state, "is_git_repo", False):
        return "—"
    branch = getattr(state, "branch", "") or "—"
    dirty = (len(getattr(state, "staged_files", None) or [])
             + len(getattr(state, "modified_files", None) or [])
             + len(getattr(state, "untracked_files", None) or [])
             + len(getattr(state, "deleted_files", None) or []))
    if getattr(state, "merge_conflict_files", None):
        return f"{branch} !{len(state.merge_conflict_files)}"
    return f"{branch} ✗{dirty}" if dirty else branch


def _kilo(value: int) -> str:
    if value <= 0:
        return "0"
    if value < 1000:
        return str(value)
    return f"{value // 1000}k"


def format_tokens(used: int, limit: int) -> str:
    """``0 / 128k · 0%`` style ceiling readout."""
    if limit <= 0:
        return f"{_kilo(max(0, used))} / —"
    pct = max(0, min(100, used * 100 // limit))
    return f"{_kilo(max(0, used))} / {_kilo(limit)} · {pct}%"


def _join(parts: list[str], sep: str = " · ") -> str:
    """Join non-empty segments — no orphaned separators on missing data."""
    return sep.join(p for p in parts if p)


def build_status_card(data: BannerData, width: int = 100):
    """Construct the rounded Panel. Never raises (falls back to plain text)."""
    from rich import box
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    width = max(MIN_WIDTH, int(width or 100))
    short_id = (data.session_id or "")[:8]
    ws = data.workspace or "."
    # Narrow terminals: shorten deep paths so the git segment survives.
    half = max(10, width // 2)
    if len(ws) > half - 14:
        parts = ws.replace("~", "").strip("/").split("/")
        ws = "…/" + "/".join(p for p in parts[-2:] if p)
    git = data.git_segment if data.git_segment not in ("", "—") else ""
    # Git state rides with the session id (short, survives ellipsis);
    # the workspace path takes the truncation hit on narrow terminals.
    left_lines = [
        _join([data.model, data.provider]) or "model: ?",
        _join([short_id, f"[git:{git}]" if git else ""]),
        ws,
    ]
    if data.skill:
        left_lines.append(f"skill: {data.skill}")
    right_lines = [
        format_tokens(data.ctx_used, data.ctx_limit),
        data.preflight_line or "pre-flight: —",
        data.pool_line or "Pool: —",
    ]

    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="left", overflow="ellipsis", max_width=max(10, width // 2))
    grid.add_column(justify="right", overflow="ellipsis", max_width=max(10, width // 2))

    def _cell(text: str) -> Text:
        # no_wrap + ellipsis: narrow terminals truncate cleanly instead of
        # spilling ragged fragments onto the next line (margin discipline).
        return Text(text, overflow="ellipsis", no_wrap=True)

    for lline, rline in zip(left_lines, right_lines):
        grid.add_row(_cell(lline), _cell(rline))
    for extra in left_lines[len(right_lines):] + right_lines[len(left_lines):]:
        grid.add_row(_cell(extra))

    tray_cmds = Text(_join(["/help", "/doctor", "/provider", "/model", "/clear"], sep="  "),
                     overflow="ellipsis", style="bold dim")
    tray_hints = Text("↑/↓ history · Tab complete · Ctrl+C exit",
                      overflow="ellipsis", style="dim")
    body = Table.grid(padding=(0, 0))
    body.add_column(overflow="ellipsis", max_width=max(10, width - 4))
    body.add_row(grid)
    body.add_row(Text(""))
    body.add_row(tray_cmds)
    body.add_row(tray_hints)

    return Panel(
        body,
        box=box.ROUNDED,
        title=f"🔮 WISP AGENT v{_version()}",
        subtitle=data.transport_line,
        width=min(width, 100),
        expand=False,
    )


def render_card_text(data: BannerData, width: int = 100) -> str:
    """Render the card to plain text (no ANSI) for tests and pipes."""
    from rich.console import Console

    width = max(MIN_WIDTH, int(width or 100))
    console = Console(record=True, width=width, force_terminal=False, color_system=None)
    try:
        with console.capture() as capture:
            console.print(build_status_card(data, width))
        return capture.get()
    except Exception:
        logger.debug("card render failed; falling back", exc_info=True)
        lines = [
            f"WISP {data.model} {data.session_id[:8]}",
            f"{data.workspace} {data.git_segment}",
            f"{format_tokens(data.ctx_used, data.ctx_limit)} {data.preflight_line}",
            f"{data.pool_line} /help /doctor /provider /model /clear",
        ]
        return "\n".join(lines) + "\n"
