"""Interactive toggle — `e` / `/expand` / `/logs` between collapsed ↔ expanded.

Wiring:
  * Textual/TUI apps: bind `e` via `App.BINDINGS` and call `ToggleController.toggle()`
  * Plain CLI (wisp repl): slash-commands `/expand`, `/logs`, `/verbose` hook into
    `wisp.repl.commands` dispatcher; also single-key `e` when stdin is a TTY
    via `termios`/`tty` raw reader (non-blocking).
  * Status flag lives in `ToggleController` + file `DisplayPayload` on
    `AgentRuntime` last tool result — no global.

Example (CLI):
    from agent.ui.interactive import ToggleController, install_cli_hooks
    toggle = ToggleController()
    install_cli_hooks(repl_dispatcher, toggle)  # adds /expand /logs
    # in event loop: if toggle.should_expand(payload): pager(payload.read_full())
"""

from __future__ import annotations

import asyncio
import sys
import termios
import tty
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    from agent.ui.formatter import DisplayPayload, pager
except Exception:
    from ui.formatter import DisplayPayload, pager  # type: ignore

__all__ = ["ToggleController", "install_cli_hooks", "KeyListener"]


@dataclass
class ToggleController:
    """Holds last payload + expanded flag. One per session/transport."""

    last_payload: Optional[DisplayPayload] = None
    expanded: bool = False
    verbose: bool = False

    def set_last(self, payload: DisplayPayload) -> None:
        self.last_payload = payload
        # auto-collapse new payloads unless verbose
        if not self.verbose:
            self.expanded = False

    def toggle(self) -> Optional[str]:
        """Flip collapsed ↔ expanded, return text to display or None."""
        if not self.last_payload:
            return None
        if not self.last_payload.truncated and not self.verbose:
            return self.last_payload.preview
        self.expanded = not self.expanded
        if self.expanded:
            return self.last_payload.read_full()
        return self.last_payload.preview

    def expand(self) -> Optional[str]:
        if not self.last_payload:
            return None
        self.expanded = True
        return self.last_payload.read_full()

    def collapse(self) -> Optional[str]:
        if not self.last_payload:
            return None
        self.expanded = False
        return self.last_payload.preview

    def should_expand(self, payload: DisplayPayload) -> bool:
        return bool(payload.truncated and (self.expanded or self.verbose))


class KeyListener:
    """Single-key `e` listener for plain CLI TTYs.

    Non-blocking: polls stdin fd with select; restores termios on exit.
    Use only when not inside textual/rich.live.

    Example:
        listener = KeyListener(toggle, on_expand=lambda txt: pager(txt))
        listener.start()  # background task
        ...
        await listener.stop()
    """

    def __init__(self, controller: ToggleController, on_expand: Callable[[str], None] | None = None):
        self.controller = controller
        self.on_expand = on_expand or (lambda txt: pager(txt))
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def _loop(self) -> None:
        if not sys.stdin.isatty():
            return
        import select

        fd = sys.stdin.fileno()
        old = None
        try:
            old = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        except Exception:
            old = None
        try:
            while self._running:
                r, _, _ = select.select([sys.stdin], [], [], 0.2)
                if r:
                    try:
                        ch = sys.stdin.read(1)
                    except Exception:
                        ch = ""
                    if ch.lower() == "e":
                        txt = self.controller.toggle()
                        if txt is not None:
                            # pager in thread to avoid blocking event loop
                            await asyncio.to_thread(self.on_expand, txt)
                    elif ch == "\x03":  # Ctrl-C
                        break
                await asyncio.sleep(0.05)
        finally:
            if old is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
                except Exception:
                    pass

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._loop())
        except RuntimeError:
            # no loop — degrade to sync thread
            import threading

            def _run() -> None:
                asyncio.run(self._loop())

            t = threading.Thread(target=_run, daemon=True)
            t.start()

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


def install_cli_hooks(dispatcher: Any, controller: ToggleController) -> None:
    """Register `/expand`, `/collapse`, `/logs`, `/verbose` slash commands.

    `dispatcher` is `wisp.repl.commands` `CommandRegistry` or any object with
    `.register(name, fn)` / `.add_command`.
    Falls back to dict assignment if neither exists.
    """
    import inspect

    def _reg(name: str, fn: Callable, help_text: str) -> None:
        try:
            if hasattr(dispatcher, "register"):
                # inspect signature to handle both (name, fn) and (fn) styles
                try:
                    dispatcher.register(name, fn)  # type: ignore
                    return
                except TypeError:
                    pass
            if hasattr(dispatcher, "add_command"):
                dispatcher.add_command(name, fn, help_text)  # type: ignore
                return
            if isinstance(dispatcher, dict):
                dispatcher[name] = fn
                return
            # last resort — setattr
            setattr(dispatcher, f"cmd_{name}", fn)
        except Exception:
            pass

    def cmd_expand(args: str = "", **_: Any) -> str:
        txt = controller.expand()
        if txt is None:
            return "No tool output to expand."
        pager(txt)
        return f"[expanded {len(txt.splitlines())} lines]"

    def cmd_collapse(args: str = "", **_: Any) -> str:
        txt = controller.collapse()
        if txt is None:
            return "No tool output to collapse."
        return txt[:4000]

    def cmd_logs(args: str = "", **_: Any) -> str:
        p = Path(".agent/logs/last_command.log")
        if p.exists():
            return f"Last log: {p} ({p.stat().st_size} bytes) — `less {p}` or press 'e'"
        return "No logs yet — run a tool first."

    def cmd_verbose(args: str = "", **_: Any) -> str:
        controller.verbose = not controller.verbose
        return f"Verbose tools: {'on' if controller.verbose else 'off'} (max lines { '∞' if controller.verbose else '10'})"

    _reg("expand", cmd_expand, "Expand last truncated tool output in pager")
    _reg("collapse", cmd_collapse, "Collapse last tool output to preview")
    _reg("logs", cmd_logs, "Show path to last raw log artifact")
    _reg("verbose", cmd_verbose, "Toggle verbose tool output (no truncation)")


# Textual binding snippet for copy-paste into App:
TEXTUAL_BINDING_SNIPPET = """
from textual.binding import Binding
class WispApp(App):
    BINDINGS = [Binding("e", "toggle_expand", "Expand/Collapse log"), Binding("v", "toggle_verbose", "Verbose")]
    def action_toggle_expand(self) -> None:
        txt = self.toggle_controller.toggle()
        if txt:
            self.notify(txt[:200])
            # or push_screen with scroll container
"""
