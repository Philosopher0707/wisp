"""Clean terminal rendering — badges, ephemeral Live, high-signal panels.

Replaces raw JSON dumps and interleaved log spew with:
  * Ephemeral status via rich.live.Live / rich.status (clears on completion)
  * Single-line badges: ✓ read_file · ⚠ retry · ✗ error
  * Final synthesis: Panel + Table with P0/P1/P2 and file anchors

Used by wisp/transport/cli.py and wisp/transport/renderer.py via
`install_cli_renderer()` hook. Falls back to ANSI dim/bold when rich is absent.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from rich.console import Console
    from rich.live import Live
    from rich.status import Status
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    _RICH = True
except Exception:
    Console = Live = Status = Table = Panel = Text = None  # type: ignore
    _RICH = False

try:
    from agent.logger import truncate_payload
except Exception:
    def truncate_payload(p: Any, max_chars: int = 4000) -> Any:  # type: ignore
        return p

__all__ = ["CliRenderer", "badge_for_tool", "badge_for_retry", "badge_for_error", "install_cli_renderer"]


# ── Badge helpers — single-line, token-cheap ─────────────────────

def badge_for_tool(name: str, files: int = 0, kb: int = 0, ms: Optional[int] = None, ok: bool = True) -> str:
    """✓ read_file (4 files · 12 KB · 45ms)"""
    icon = "✓" if ok else "✗"
    parts = [f"{name}"]
    meta: List[str] = []
    if files:
        meta.append(f"{files} files")
    if kb:
        meta.append(f"{kb} KB")
    if ms is not None:
        meta.append(f"{ms}ms")
    if meta:
        parts.append("({})".format(" · ".join(meta)))
    return f"{icon} {' '.join(parts)}"


def badge_for_retry(reason: str, attempt: int, max_attempts: int = 3) -> str:
    """⚠ retry (provider stream timeout · attempt 1/3)"""
    return f"⚠ retry ({reason} · attempt {attempt}/{max_attempts})"


def badge_for_error(msg: str) -> str:
    """✗ error (file not found: path/to/file) — truncated to 80 cols"""
    m = msg.strip().splitlines()[0][:80]
    return f"✗ error ({m})"


# ── Ephemeral live renderer ──────────────────────────────────────

class CliRenderer:
    """Owns ephemeral Live region + badge emission.

    Usage:
        r = CliRenderer()
        r.install()  # optional — hooks logging sink
        with r.status("Thinking…"):
            ...
        r.tool_ok("read_file", files=4, kb=18)
        # fanout uses agent.telemetry.TelemetryTracker.live() separately
    """

    def __init__(self, console: Optional[Any] = None):
        self.console = console or (Console() if _RICH else None)  # type: ignore
        self._live: Optional[Any] = None
        self._status: Optional[Any] = None

    def install(self) -> None:
        """Wire logger sink so provider warnings never hit stdout."""
        try:
            from agent.logger import install as _install
            _install()
        except Exception:
            pass

    @contextmanager
    def status(self, msg: str):
        """Ephemeral spinner — clears on exit (no vertical spam)."""
        if not _RICH or self.console is None:
            start = time.monotonic()
            try:
                yield
            finally:
                elapsed = time.monotonic() - start
                print(f"· {msg} — {elapsed:.1f}s")
            return
        with Status(msg, console=self.console, spinner="dots") as st:  # type: ignore
            self._status = st
            try:
                yield st
            finally:
                self._status = None

    @contextmanager
    def live_table(self, title: str = "Workers"):
        """Ephemeral Live table for fanout — caller updates via `update(table)`."""
        if not _RICH or self.console is None:
            yield None
            return
        table = Table(title=title, expand=False)
        with Live(table, console=self.console, refresh_per_second=10, transient=True) as live:  # type: ignore
            self._live = live
            try:
                yield live
            finally:
                self._live = None

    # ── Badge emitters (stdout = badges only) ────────────────────

    def tool_ok(self, name: str, *, files: int = 0, kb: int = 0, ms: Optional[int] = None) -> None:
        msg = badge_for_tool(name, files, kb, ms, ok=True)
        self._emit(msg, style="green")

    def tool_retry(self, reason: str, attempt: int, max_attempts: int = 3) -> None:
        msg = badge_for_retry(reason, attempt, max_attempts)
        self._emit(msg, style="yellow")

    def tool_error(self, msg: str) -> None:
        m = badge_for_error(msg)
        self._emit(m, style="red")

    def _emit(self, msg: str, style: str = "white") -> None:
        if _RICH and self.console is not None:
            try:
                self.console.print(f"[{style}]{msg}[/{style}]")
                return
            except Exception:
                pass
        print(msg)

    # ── Final synthesis — high-signal panel ──────────────────────

    def final_panel(self, title: str, issue_count: int, file_count: int, p0: int = 0) -> None:
        if not _RICH or self.console is None:
            print(f"\n=== {title} ===")
            print(f"[✓ Audit {issue_count} findings ({p0} P0) · {file_count} files]")
            return
        panel = Panel(
            f"[bold green][✓ Audit {issue_count} findings ({p0} P0) · {file_count} files][/bold green]",
            title=title, border_style="green", expand=False
        )
        self.console.print(panel)

    def issue_table(self, rows: List[Dict[str, str]]) -> None:
        """rows: [{severity, file_anchor, summary, remediation}]"""
        if not _RICH or self.console is None:
            for r in rows:
                print(f"{r['severity']} {r['file_anchor']} {r['summary']}")
            return
        t = Table(title="Issue Matrix — Prioritized", expand=False)
        t.add_column("Sev", style="bold", no_wrap=True)
        t.add_column("Anchor", style="cyan")
        t.add_column("Summary", style="white")
        t.add_column("Remediation", style="green")
        sev_style = {"P0": "red", "P1": "yellow", "P2": "dim"}
        for r in sorted(rows, key=lambda x: {"P0": 0, "P1": 1, "P2": 2}.get(x.get("severity", ""), 9)):
            sev = r.get("severity", "")
            t.add_row(f"[{sev_style.get(sev,'white')}]{sev}[/]", r.get("file_anchor",""), r.get("summary", r.get("issue_summary",""))[:80], r.get("remediation","")[:80])
        self.console.print(t)

    def clean_payload(self, payload: Any) -> Any:
        """Apply badge truncation before render — suppress … +35 more."""
        return truncate_payload(payload)


def install_cli_renderer(console: Optional[Any] = None) -> CliRenderer:
    r = CliRenderer(console=console)
    r.install()
    return r
