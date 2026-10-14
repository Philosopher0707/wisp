"""Subagent monitor screen — live worker roster + transcript (GH#10).

Promoted from the audit prototype. Fed by ``SubagentTelemetryBuffer``
rings: attach replays recent history, cursor poll tails live events, all
without pausing the worker. ``c`` cancels the focused worker via the
background manager (B5 without killing the parent turn).

Key discipline (verified empirically): Textual's Screen consumes Tab for
focus navigation even with zero focusable widgets, so worker cycling is
``]`` / ``[`` — never Tab.
"""

from __future__ import annotations

import logging
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, RichLog

logger = logging.getLogger(__name__)


class SubagentMonitorScreen(Screen):
    """Roster table (left) + live transcript pane (right)."""

    BINDINGS = [
        Binding("]", "next_worker", "Next worker"),
        Binding("[", "prev_worker", "Previous worker"),
        Binding("c", "cancel_worker", "Cancel worker"),
        Binding("q", "close_monitor", "Back"),
        Binding("ctrl+o", "close_monitor", "Back"),
        Binding("escape", "close_monitor", "Back"),
    ]

    def __init__(
        self,
        telemetry: Any,
        manager: Any | None = None,
        standalone: bool = False,
    ) -> None:
        super().__init__()
        self._telemetry = telemetry
        self._manager = manager
        self._standalone = standalone
        self._order: list[str] = []
        self._sel = 0
        self._cursor = -1  # attach cursor into the selected transcript

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            yield DataTable(id="roster")
            yield RichLog(id="detail", highlight=True, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#roster", DataTable)
        # Display-only panes: keep focus off so ] / [ / c reach the
        # screen-level bindings instead of widget handlers.
        table.can_focus = False
        self.query_one("#detail", RichLog).can_focus = False
        table.add_column("worker", width=12)
        table.add_column("role", width=10)
        table.add_column("status", width=12)
        table.add_column("tools", width=6)
        table.add_column("elapsed", width=8)
        self.set_interval(0.4, self.refresh_view)
        self.refresh_view()

    def refresh_view(self) -> None:
        """Rebuild roster; tail the selected transcript from the cursor."""
        try:
            order = self._telemetry.agents()
        except Exception:
            logger.debug("Monitor refresh: agents() failed", exc_info=True)
            return
        if not order:
            return
        self._order = order
        self._sel %= len(self._order)
        try:
            table = self.query_one("#roster", DataTable)
            table.clear()
            for wid in self._order:
                snap = self._telemetry.snapshot(wid)
                if snap is None:
                    continue
                elapsed = snap.elapsed if hasattr(snap, "elapsed") else 0.0
                table.add_row(wid, str(getattr(snap, "role", "")),
                              str(getattr(snap, "status", "")),
                              str(getattr(snap, "tool_calls", 0)), f"{elapsed:.0f}s")
            wid = self._order[self._sel]
            fresh = self._telemetry.poll(wid, self._cursor)
            if fresh:
                detail = self.query_one("#detail", RichLog)
                for ev in fresh:
                    detail.write(f"{ev.kind:>11} {ev.text}")
                    self._cursor = ev.seq
        except Exception:
            logger.debug("Monitor refresh failed", exc_info=True)

    def _select(self, index: int) -> None:
        if not self._order:
            return
        self._sel = index % len(self._order)
        self._cursor = -1  # re-attach: replay the newly selected worker
        try:
            self.query_one("#detail", RichLog).clear()
        except Exception:
            pass
        self.refresh_view()

    def action_next_worker(self) -> None:
        """Cycle to the next worker."""
        self._select(self._sel + 1)

    def action_prev_worker(self) -> None:
        """Cycle to the previous worker."""
        self._select(self._sel - 1)

    def action_cancel_worker(self) -> None:
        """Cancel the focused worker; the parent turn is untouched."""
        if not self._order or self._manager is None:
            return
        wid = self._order[self._sel]
        try:
            result = self._manager.cancel(wid)
            note = result.get("status", result) if isinstance(result, dict) else result
        except Exception as exc:
            note = f"error: {exc}"
        try:
            self.query_one("#detail", RichLog).write(f"cancel requested for {wid}: {note}")
        except Exception:
            pass

    def action_close_monitor(self) -> None:
        """Pop the screen (embedded) or exit the app (standalone bridge)."""
        if self._standalone:
            self.app.exit()
        else:
            self.app.pop_screen()


class SubagentMonitorApp(App):
    """Standalone host for the REPL bridge: owns the alternate screen.

    The stdio REPL cannot host a Screen (no App running there), so the
    bridge runs this App synchronously: Textual takes the alternate
    buffer on mount and restores cooked state + scrollback on exit.
    """

    def __init__(self, telemetry: Any, manager: Any | None = None) -> None:
        super().__init__()
        self._telemetry = telemetry
        self._manager = manager

    def on_mount(self) -> None:
        self.push_screen(SubagentMonitorScreen(
            self._telemetry, manager=self._manager, standalone=True))
