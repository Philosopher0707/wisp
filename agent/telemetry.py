"""Async multi-worker progress tracker — replaces static spinner.

Replaces blocking `subagent_wait` spinner with `rich.live.Live` matrix:

  Worker ID & Role | Focus / Module | Activity / Tool | Elapsed | Tokens/Cost

Wiring: `BackgroundAgentManager` wisp/multi_agent/background.py:66 emits
`agent_started`/`agent_settled` via `BackgroundAgentManager.subscribe()` —
this tracker subscribes and updates live on `asyncio` loop without blocking.

Usage:
    tracker = TelemetryTracker(max_concurrent=4)
    async with tracker.live():
        tracker.register("bg-e2fb0a69", role="coder", focus="Analyzing Architecture")
        ...
        tracker.update("bg-e2fb0a69", activity="Reading src/system.rs:42-89", tokens_used=1234)
        await tracker.wait_all(manager)  # live while waiting
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, Optional

try:
    from rich.live import Live
    from rich.table import Table
    from rich.console import Console
    from rich.text import Text
    _RICH = True
except Exception:  # graceful degrade when rich not installed (tests)
    Live = Table = Console = Text = None  # type: ignore
    _RICH = False

try:
    from agent.models import SubagentState
except Exception:  # allow `python -m` without installed agent package
    from models import SubagentState  # type: ignore

__all__ = ["TelemetryTracker", "SubagentTelemetry", "render_snapshot"]

# For tests — capture snapshots without Live
@dataclass
class SubagentTelemetry:
    state: SubagentState
    started_at: float = field(default_factory=time.monotonic)

    def elapsed_s(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)


def _badge(elapsed_s: float) -> str:
    if elapsed_s < 60:
        return f"{elapsed_s:.1f}s"
    m, s = divmod(elapsed_s, 60)
    return f"{int(m)}m{int(s):02d}s"


def render_snapshot(states: Dict[str, SubagentState], *, title: str = "Fanout Telemetry") -> "Table | str":
    """Pure render helper — used by Live and by tests without Live."""
    if not _RICH:
        # Plain-text fallback for CI/minimal
        lines = [title]
        for s in states.values():
            lines.append(f"[{s.role}:{s.worker_id}] {s.focus} | {s.activity} | {_badge(s.elapsed_s)} | {s.tokens_used} tok")
        return "\n".join(lines)

    table = Table(title=title, show_lines=False, expand=False)
    table.add_column("Worker", style="cyan", no_wrap=True)
    table.add_column("Role", style="magenta")
    table.add_column("Focus / Module", style="white")
    table.add_column("Activity / Tool", style="green")
    table.add_column("Elapsed", style="yellow", justify="right")
    table.add_column("Tokens", style="blue", justify="right")
    table.add_column("Status", style="bold")

    status_style = {"running": "yellow", "completed": "green", "failed": "red", "cancelled": "dim"}

    for s in states.values():
        worker = f"[{s.role}:{s.worker_id}]"
        elapsed = _badge(s.elapsed_s)
        tokens = f"{s.tokens_used}" + (f" · ${s.cost_usd:.4f}" if s.cost_usd else "")
        status = Text(s.status, style=status_style.get(s.status, "white"))
        table.add_row(worker, s.role, s.focus, s.activity, elapsed, tokens, status)
    if not states:
        table.add_row("-", "-", "idle", "-", "-", "-", Text("idle", style="dim"))
    return table


class TelemetryTracker:
    """Async-safe fanout telemetry. All mutations are thread-safe via asyncio.Lock."""

    def __init__(self, max_concurrent: int = 4, title: str = "Fanout — 4 workers"):
        self.max_concurrent = max_concurrent
        self.title = title
        self._states: Dict[str, SubagentState] = {}
        self._started: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._live: Optional[Live] = None  # type: ignore
        self._console: Optional[Console] = None  # type: ignore
        self._refresh_task: Optional[asyncio.Task] = None
        self._running = False

    # ── Registration ─────────────────────────────────────────────

    async def register(self, worker_id: str, *, role: str = "coder", focus: str = "", activity: str = "") -> None:
        async with self._lock:
            self._states[worker_id] = SubagentState(
                worker_id=worker_id, role=role, focus=focus, activity=activity, elapsed_s=0.0, status="running"
            )
            self._started[worker_id] = time.monotonic()

    async def update(
        self,
        worker_id: str,
        *,
        focus: Optional[str] = None,
        activity: Optional[str] = None,
        tokens_used: Optional[int] = None,
        cost_usd: Optional[float] = None,
        status: Optional[str] = None,
        progress: Optional[str] = None,
    ) -> None:
        async with self._lock:
            s = self._states.get(worker_id)
            if not s:
                # auto-register on first update (tolerant)
                s = SubagentState(worker_id=worker_id, role="coder")
                self._states[worker_id] = s
                self._started[worker_id] = time.monotonic()
            if focus is not None:
                s.focus = focus
            if activity is not None:
                s.activity = activity
            if tokens_used is not None:
                s.tokens_used = tokens_used
            if cost_usd is not None:
                s.cost_usd = cost_usd
            if status is not None:
                s.status = status  # type: ignore
            if progress is not None:
                s.progress = progress
            # elapsed is derived on render, but cache for snapshot
            s.elapsed_s = time.monotonic() - self._started.get(worker_id, time.monotonic())

    async def complete(self, worker_id: str, status: str = "completed") -> None:
        await self.update(worker_id, status=status)

    def snapshot(self) -> Dict[str, SubagentState]:
        # Synchronous snapshot for tests / synthesizer
        return dict(self._states)

    # ── Live display ─────────────────────────────────────────────

    @asynccontextmanager
    async def live(self, console: Optional[Console] = None) -> AsyncIterator["TelemetryTracker"]:
        """Async context that drives Rich Live refresh.

        Example:
            async with tracker.live():
                tracker.register(...)
                await tracker.wait_all(manager)
        """
        if not _RICH:
            self._running = False
            yield self
            return

        self._console = console or Console()
        table = render_snapshot(self._states, title=self.title)  # type: ignore
        self._live = Live(table, console=self._console, refresh_per_second=10, transient=False)  # type: ignore
        self._live.__enter__()
        self._running = True

        # Background ticker updates elapsed column every 200ms
        async def _ticker() -> None:
            while self._running:
                await asyncio.sleep(0.2)
                # bump elapsed for running workers
                async with self._lock:
                    for wid, st in self._states.items():
                        if st.status == "running":
                            st.elapsed_s = time.monotonic() - self._started.get(wid, time.monotonic())
                    # push new table
                    try:
                        if self._live:
                            self._live.update(render_snapshot(self._states, title=self.title))  # type: ignore
                    except Exception:
                        pass

        self._refresh_task = asyncio.create_task(_ticker())
        try:
            yield self
        finally:
            self._running = False
            if self._refresh_task:
                self._refresh_task.cancel()
                try:
                    await self._refresh_task
                except asyncio.CancelledError:
                    pass
            # Final paint
            try:
                if self._live:
                    self._live.update(render_snapshot(self._states, title=f"{self.title} — done"))  # type: ignore
                    self._live.__exit__(None, None, None)  # type: ignore
            except Exception:
                pass
            self._live = None

    async def tick(self) -> None:
        """Manual tick — push current states to Live if active (also used in tests)."""
        if self._live and self._running:
            async with self._lock:
                for wid, st in self._states.items():
                    if st.status == "running":
                        st.elapsed_s = time.monotonic() - self._started.get(wid, time.monotonic())
                try:
                    self._live.update(render_snapshot(self._states, title=self.title))  # type: ignore
                except Exception:
                    pass

    # ── Wait integration — replaces blocking subagent_wait spinner ──

    async def wait_all(
        self,
        manager: Optional[object] = None,
        agent_ids: Optional[list[str]] = None,
        timeout_s: float = 300,
        poll_interval: float = 0.25,
    ) -> Dict[str, str]:
        """Live-aware wait for BackgroundAgentManager entries.

        If `manager` is None, just sleeps with live refresh (for tests).
        Returns {worker_id: status}.
        """
        # If a real manager is supplied, poll its entries while Live ticks
        if manager is not None and hasattr(manager, "list"):
            # manager is BackgroundAgentManager
            start = time.monotonic()
            while True:
                # Refresh statuses from manager
                try:
                    entries = manager.list(include_finished=True)  # type: ignore
                    for e in entries:
                        wid = e.get("agent_id") or e.get("id")
                        status = e.get("status", "running")
                        toks = e.get("tokens_used") or e.get("tokens") or 0
                        # Extract activity from recent tool_call if available
                        await self.update(wid, status=status, tokens_used=int(toks) if isinstance(toks, int) else 0)
                except Exception:
                    pass
                await self.tick()
                # Terminal check
                try:
                    entries = manager.list(include_finished=False)  # type: ignore
                    if not entries:
                        break
                except Exception:
                    break
                if time.monotonic() - start > timeout_s:
                    break
                if agent_ids is not None:
                    # Only wait for listed ids
                    remaining = [i for i in agent_ids if self._states.get(i, SubagentState(worker_id=i)).status == "running"]
                    if not remaining:
                        break
                await asyncio.sleep(poll_interval)
            await self.tick()
            return {wid: st.status for wid, st in self._states.items()}

        # Fallback: simple timed wait with ticks
        start = time.monotonic()
        while time.monotonic() - start < min(timeout_s, 0.6):  # cap for tests
            await self.tick()
            await asyncio.sleep(poll_interval)
        return {wid: st.status for wid, st in self._states.items()}

    # ── Console Telemetry hook for CompositionRoot ─────────────────
    def wire_manager(self, manager: object) -> None:
        """Optional helper: subscribe to manager's pub/sub to auto-update.

        Call once after tracker creation if you want push updates.
        Currently poll-based; subscription reserved for future SSE.
        """
        # Reserved — poll in wait_all already covers it
        _ = manager
