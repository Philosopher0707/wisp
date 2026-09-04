"""Refactored REPL interactive runner — injected dependencies only.

Lifecycle (driven by ``wisp.entry`` in this exact order):
  Step 1  boot_env()      — safe cwd, history load, file-logger sink note.
  Step 2  preflight()     — DoctorRunner.check_all() async, <100ms budget.
  Step 3  (assembly)      — CompositionRoot builds runtime/transport/registry
                            (owned by entry, injected here via constructor).
  Step 4  banner()        — single-frame startup banner (git, provider,
                            pre-flight state).
  Step 5  run()           — interactive loop until EXIT/EOF.

Decoupling rules:
  - The runner talks to the transport through :class:`EventRenderer`
    (render/reset/flush/wait-clock), never transport privates directly.
  - Slash commands go through :class:`Dispatcher` + :class:`ReplContext`;
    unmigrated names fall back to the legacy registry with the real
    transport-layer adapter (strangler-fig), never a thin shim.
  - History helpers live here (canonical home); ``wisp.entry`` re-exports
    them for backward compatibility.
  - Signal policy: SIGINT cancels the live turn (first press), restores the
    default disposition so a second press force-quits; SIGWINCH refreshes
    the cached terminal width (prompt_toolkit redraws natively).

Strictly typed, no placeholders.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from wisp.cli.dispatcher import CommandResult, Dispatcher, ReplContext

logger = logging.getLogger(__name__)

PREFLIGHT_BUDGET_S = 0.1
HISTORY_LENGTH = 5000


# ── Command history (canonical home; entry.py re-exports) ─────────────


def history_path() -> Path:
    custom = os.environ.get("WISP_HISTORY_FILE")
    return Path(custom).expanduser() if custom else Path.home() / ".wisp" / "history"


def load_command_history() -> bool:
    """Load prior prompts into readline so up-arrow recalls them."""
    try:
        import readline
    except ImportError:
        return False
    path = history_path()
    try:
        if path.exists():
            readline.read_history_file(str(path))
        readline.set_history_length(HISTORY_LENGTH)
        return True
    except Exception:
        logger.debug("Could not load command history from %s", path, exc_info=True)
        return False


def save_command_history() -> bool:
    try:
        import readline
    except ImportError:
        return False
    path = history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        readline.set_history_length(HISTORY_LENGTH)
        readline.write_history_file(str(path))
        return True
    except Exception:
        logger.debug("Could not save command history to %s", path, exc_info=True)
        return False


class EventRenderer(Protocol):
    """Minimal rendering surface the runner needs from a transport."""

    def render_event(self, out: Any, event: dict) -> None: ...
    def reset(self) -> None: ...
    def flush(self, out: Any) -> None: ...
    def wait_start(self, out: Any) -> None: ...
    def wait_stop(self, out: Any) -> None: ...


class CLIEventRenderer:
    """Adapter exposing CLITransport through the EventRenderer protocol."""

    def __init__(self, transport: Any) -> None:
        self._transport = transport

    def render_event(self, out: Any, event: dict) -> None:
        self._transport._render_event(out, event)

    def reset(self) -> None:
        self._transport._reset_buffers()

    def flush(self, out: Any) -> None:
        self._transport._flush_thinking(out)
        self._transport._flush_content(out)

    def wait_start(self, out: Any) -> None:
        self._transport.start_wait_clock(stdout=out)

    def wait_stop(self, out: Any) -> None:
        self._transport.stop_wait_clock(stdout=out)


@dataclass
class DoctorSummary:
    """Outcome of Step 2 — safe to render even when the check degraded."""

    healthy: bool = True
    banner: str = ""
    elapsed_s: float = 0.0


class DoctorRunner:
    """Runs the 5-contract pre-flight check inside an async budget.

    Wraps ``wisp.core.doctor.run_preflight`` (never raises; failures are
    CheckResults). The whole step is bounded by ``budget_s`` so REPL
    startup can never block on diagnostics.
    """

    def __init__(self, budget_s: float = PREFLIGHT_BUDGET_S) -> None:
        self.budget_s = budget_s
        self.report: Any = None

    async def check_all(self, workspace: str, config: Any) -> DoctorSummary:
        started = time.monotonic()
        try:
            from wisp.core.doctor import format_banner
        except ImportError as exc:
            return DoctorSummary(healthy=False, banner=f"doctor unavailable: {exc}",
                                 elapsed_s=time.monotonic() - started)
        try:
            from wisp.core.doctor import run_preflight_sync

            report = await asyncio.wait_for(
                asyncio.to_thread(
                    run_preflight_sync, workspace, config, self.budget_s
                ),
                timeout=self.budget_s + 0.5,
            )
        except Exception as exc:  # timeout or thread failure → degraded, never fatal
            logger.debug("pre-flight failed: %s", exc, exc_info=True)
            return DoctorSummary(healthy=False, banner="pre-flight unavailable",
                                 elapsed_s=time.monotonic() - started)
        self.report = report
        try:
            banner = format_banner(report)
        except Exception:
            banner = ""
        try:
            import wisp.core.doctor as _doctor_mod

            _doctor_mod._LAST_REPORT = report  # type: ignore[attr-defined]
        except Exception:
            pass
        return DoctorSummary(healthy=bool(getattr(report, "healthy", False)),
                             banner=banner, elapsed_s=time.monotonic() - started)


@dataclass
class ReplLifecycle:
    """Recorded step order — asserted by tests, informative at runtime."""

    steps: list[str] = field(default_factory=list)

    def mark(self, step: str) -> None:
        self.steps.append(step)


InputFn = Callable[[str], "str | None"]


PROMPT_TEXT = "wisp ❯ "
PROMPT_CONTINUATION = "... "


def make_input_fn(history_file: Path | None = None) -> InputFn:
    """Single-line prompt: prompt_toolkit when available+tty, else readline.

    The prompt_toolkit session binds FileHistory (persisted across launches),
    history auto-suggestions, and a styled ``wisp ❯ `` prompt. Multiline mode
    keeps the transport-layer ``_input_multiline`` reader (blank-line
    submit), which already handles readline and pipes. Window resizes
    (SIGWINCH) are handled natively by prompt_toolkit; the runner additionally
    refreshes its cached width for the banner and stats lines.
    """
    try:
        use_ptk = bool(sys.stdin.isatty())
    except Exception:
        use_ptk = False
    if use_ptk:
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
            from prompt_toolkit.history import FileHistory

            hist = str(history_file) if history_file is not None else str(history_path())
            try:
                session: Any = PromptSession(
                    history=FileHistory(hist),
                    auto_suggest=AutoSuggestFromHistory(),
                )
            except Exception:
                # Mocked/odd stdio (tests), missing terminfo, etc.
                logger.debug("prompt_toolkit session failed; using readline", exc_info=True)
                return _readline_input

            try:
                from prompt_toolkit.formatted_text import HTML

                _styled: Any = HTML("<ansigreen>wisp</ansigreen> ❯ ")
            except ImportError:
                _styled = PROMPT_TEXT

            def _ptk(prompt: str) -> str | None:
                try:
                    return session.prompt(_styled)
                except KeyboardInterrupt:
                    raise
                except EOFError:
                    return None
                except Exception:
                    logger.debug("prompt_toolkit read failed; falling back", exc_info=True)
                    return _readline_input(prompt)

            return _ptk
        except ImportError:
            pass
    return _readline_input


def _compat_entry_attr(name: str) -> Any | None:
    """Resolve a back-compat seam from the entry namespace if present.

    The pre-refactor loop read input/typeahead helpers as ``wisp.entry``
    module globals, and the suite patches those names. The canonical homes
    are transport-layer modules; this lookup preserves the historical
    patch points without reintroducing an import cycle (attribute read at
    call time, never imported).
    """
    entry = sys.modules.get("wisp.entry")
    if entry is not None and hasattr(entry, name):
        return getattr(entry, name)
    return None


def _readline_input(prompt: str) -> str | None:
    fn = _compat_entry_attr("_input_line")
    if fn is None:
        from wisp.transport.cli import _input_line as fn
    return fn(prompt)


def _readline_multiline(prompt: str = "➜ ", continuation: str = "... ") -> str | None:
    fn = _compat_entry_attr("_input_multiline")
    if fn is None:
        from wisp.transport.cli import _input_multiline as fn
    return fn(prompt, continuation)


def _typeahead_factory() -> Any:
    factory = _compat_entry_attr("TypeAheadBuffer")
    if factory is None:
        from wisp.transport.typeahead import TypeAheadBuffer as factory
    return factory


class ReplRunner:
    """Owns the interactive loop. All collaborators are injected."""

    _VALID_MODES = ("single", "multi")

    def __init__(
        self,
        *,
        runtime: Any,
        transport: Any,
        renderer: Any | None,
        dispatcher: Dispatcher | None,
        config: Any,
        session: dict,
        loop: asyncio.AbstractEventLoop,
        out: Any | None = None,
        err: Any | None = None,
        input_fn: InputFn | None = None,
        multiline_input_fn: InputFn | None = None,
        lifecycle: ReplLifecycle | None = None,
        on_turn_stats: Callable[[Any], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.transport = transport
        self.renderer = renderer or CLIEventRenderer(transport)
        if dispatcher is None:
            from wisp.commands import dispatch as _legacy_dispatch

            dispatcher = Dispatcher(legacy_dispatch=_legacy_dispatch)
        self.dispatcher = dispatcher
        self.config = config
        self.session = session
        self.loop = loop
        # Streams resolve lazily per access: binding sys.stdout/stderr as
        # defaults would freeze whatever buffer is installed at import or
        # construction time (e.g. pytest's collection capture, closed by
        # the time the loop runs). The old inline code read sys.stdout
        # fresh at every write; properties below preserve that.
        self._out = out
        self._err = err
        self.input_fn = input_fn or make_input_fn()
        self.multiline_input_fn = multiline_input_fn or _readline_multiline
        self.lifecycle = lifecycle or ReplLifecycle()
        self.on_turn_stats = on_turn_stats
        self.doctor = DoctorSummary()
        self.input_mode = "single"
        self._adapter: Any = None
        self._turn_task: asyncio.Task | None = None
        self._prev_sigint: Any = None
        self._prev_sigwinch: Any = None
        self._term_width = 80
        try:
            import shutil

            self._term_width = shutil.get_terminal_size().columns
        except Exception:
            pass

    @property
    def out(self) -> Any:
        return self._out if self._out is not None else sys.stdout

    @out.setter
    def out(self, value: Any) -> None:
        self._out = value

    @property
    def err(self) -> Any:
        return self._err if self._err is not None else sys.stderr

    @err.setter
    def err(self, value: Any) -> None:
        self._err = value

    # ── Adapter (legacy-compat bridge, owned here) ────────────────────

    @property
    def adapter(self) -> Any:
        """Transport-layer adapter for unmigrated slash commands."""
        if self._adapter is None:
            from wisp.transport.cli import AgentAdapter

            self._adapter = AgentAdapter(self.runtime, self.config, self.session, loop=self.loop)
        return self._adapter

    def _sync_from_adapter(self) -> None:
        """Re-share adapter state commands may have rebound (frozen replace())."""
        try:
            if self._adapter is not None:
                if isinstance(getattr(self._adapter, "session", None), dict):
                    self.session = self._adapter.session
                if getattr(self._adapter, "config", None) is not None:
                    self.config = self._adapter.config
                    self.transport.config = self._adapter.config
        except Exception:
            pass

    # ── Steps 1–2: environment + pre-flight ───────────────────────────

    def boot_env(self) -> str:
        """Step 1: safe cwd + history. Returns the resolved workspace."""
        self.lifecycle.mark("boot_env")
        try:
            from wisp.config import safe_getcwd

            workspace = safe_getcwd()
        except ImportError:
            workspace = os.getcwd()
        load_command_history()
        return workspace

    async def preflight(self, workspace: str) -> DoctorSummary:
        """Step 2: async health check inside the startup budget."""
        self.lifecycle.mark("preflight")
        runner = DoctorRunner()
        self.doctor = await runner.check_all(workspace, self.config)
        return self.doctor

    # ── Step 4: banner ────────────────────────────────────────────────

    def _banner_data(self, skill: str | None = None) -> Any:
        """Assemble BannerData from live runtime state (no network I/O)."""
        from wisp.ui.banner import BannerData, collect_git_segment

        workspace = str(getattr(self.config, "workspace", ".") or ".")
        messages = self.session.get("messages", []) if isinstance(self.session, dict) else []
        ctx_used = sum(len(str(m.get("content", ""))) for m in messages
                       if isinstance(m, dict)) // 4 if messages else 0
        ctx_limit = int(getattr(self.config, "max_context_tokens", 0) or 0)
        pool_size = getattr(getattr(self.runtime, "orchestrator", None), "_pool_size", None)
        try:
            pool_n = int(pool_size) if pool_size else 4
        except (TypeError, ValueError):
            pool_n = 4
        provider = getattr(self.config, "provider", "") or ""
        connected = bool(self.doctor.healthy and provider)
        return BannerData(
            model=str(getattr(self.config, "model", "") or ""),
            provider=str(provider),
            session_id=str(self.session.get("id", "") if isinstance(self.session, dict) else ""),
            workspace=workspace,
            git_segment=collect_git_segment(workspace),
            ctx_used=ctx_used,
            ctx_limit=ctx_limit,
            preflight_line=self.doctor.banner or "pre-flight: —",
            preflight_ok=self.doctor.healthy,
            pool_line=f"Pool: {pool_n} idle",
            transport_line="⚡ Connected" if connected else "⚠ Degraded",
            skill=skill or "",
        )

    def banner(self, *, is_continuation: bool, skill: str | None = None) -> None:
        """Step 4: single-frame startup banner.

        Fresh boot renders the unified rich status card (one Panel, one
        write — no duplicated diagnostic banners on stdout). Continuations
        keep the compact legacy banner. Any failure falls back to the
        legacy renderer so boot can never break on UI code.
        """
        self.lifecycle.mark("banner")
        if not is_continuation:
            try:
                from rich.console import Console

                from wisp.ui.banner import build_status_card

                console = Console(file=self.err, force_terminal=False,
                                  width=self._term_width)
                console.print(build_status_card(self._banner_data(skill),
                                                width=self._term_width))
                return
            except Exception:
                logger.debug("status card failed; legacy fallback", exc_info=True)
        try:
            if is_continuation:
                self.transport.print_continuation_banner(self.out, self.session, self.config.model)
            else:
                self.transport.print_banner(self.err, self.session, self.config.model, skill=skill)
        except Exception:
            logger.debug("banner render failed", exc_info=True)
        if self.doctor.banner:
            try:
                from wisp.colors import success as _ok, warning as _warn

                color = _ok if self.doctor.healthy else _warn
                self.err.write(f"  {color(self.doctor.banner)}\n")
                self.err.flush()
            except Exception:
                pass
        try:
            store_path = str(getattr(getattr(self.runtime, "store", None), "db_path", "") or "")
            if "wisp_fallback.db" in store_path:
                from wisp.colors import dim

                self.err.write(
                    dim(f"  Workspace blocked by TCC; using fallback DB {store_path}"
                        " — run /workspace ~/Documents/wisp\n")
                )
                self.err.flush()
        except Exception:
            pass
        if not getattr(self.config, "provider", None):
            from wisp.terminal_width import status_symbols

            self.out.write(
                f"\n{status_symbols()['warn']}  No LLM provider configured."
                " Set WISP_PROVIDER or add 'provider' to config.\n"
                "   Example: wisp repl -m llama3   or   export WISP_PROVIDER=ollama\n\n"
            )
            self.out.flush()

    # ── Signals ───────────────────────────────────────────────────────

    def _arm_signals(self) -> None:
        self._prev_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._on_sigint)
        try:
            self._prev_sigwinch = signal.getsignal(signal.SIGWINCH)
            signal.signal(signal.SIGWINCH, self._on_sigwinch)
        except (AttributeError, ValueError, OSError):
            self._prev_sigwinch = None  # non-POSIX or non-main thread

    def _restore_signals(self) -> None:
        try:
            if self._prev_sigint is not None:
                signal.signal(signal.SIGINT, self._prev_sigint)
        except (ValueError, OSError):
            pass
        try:
            if self._prev_sigwinch is not None:
                signal.signal(signal.SIGWINCH, self._prev_sigwinch)
        except (ValueError, OSError):
            pass

    def _on_sigint(self, signum: int, frame: Any) -> None:
        try:
            spinner = getattr(self.transport, "_spinner", None)
            if spinner is not None:
                spinner.stop()
        except Exception:
            pass
        task = self._turn_task
        if task is not None and not task.done():
            task.cancel()
            self.out.write("\nInterrupted — cancelling turn… (Ctrl+C again to force quit)\n")
            self.out.flush()
            signal.signal(signal.SIGINT, signal.default_int_handler)
        else:
            raise KeyboardInterrupt

    def _on_sigwinch(self, signum: int, frame: Any) -> None:
        try:
            import shutil

            self._term_width = shutil.get_terminal_size().columns
        except Exception:
            pass

    # ── Turns ─────────────────────────────────────────────────────────

    def _ctx(self) -> ReplContext:
        return ReplContext(runtime=self.runtime, transport=self.transport,
                           session=self.session, config=self.config,
                           out=[], adapter=self._adapter)

    def _flush_ctx(self, ctx: ReplContext) -> None:
        for line in ctx.out:
            self.out.write(line + "\n")
        if ctx.out:
            self.out.flush()

    def run_turn(self, prompt: str, typeahead: Any | None = None) -> list[str]:
        """Run one model turn with error boundaries. Returns replay lines."""
        approve = getattr(self.transport, "approve", None)

        async def _turn() -> None:
            async for event in self.runtime.run_turn(self.session, prompt, approval_handler=approve):
                self.renderer.render_event(self.out, event)

        self.renderer.reset()
        self.renderer.wait_start(self.out)
        try:
            self._turn_task = self.loop.create_task(_turn())
            if typeahead is not None:
                try:
                    typeahead.start()
                except Exception:
                    pass
            self.loop.run_until_complete(self._turn_task)
            self.renderer.wait_stop(self.out)
            self.renderer.flush(self.out)
            if self.on_turn_stats is not None:
                self.on_turn_stats(self.session)
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.renderer.wait_stop(self.out)
            self._stop_spinner()
            self.renderer.flush(self.out)
            self._show_resume()
        except Exception as exc:
            self.renderer.wait_stop(self.out)
            self._stop_spinner()
            self.renderer.flush(self.out)
            self.err.write(f"Error during turn: {exc}\n")
            self.err.flush()
            # Mirror to stdout like the legacy loop did: transcript
            # consumers (and tests) watch stdout for the terse line.
            self.out.write(f"Error: {exc}\n")
            self.out.flush()
        finally:
            self._turn_task = None
            self.renderer.wait_stop(self.out)
            self._arm_signals()
        if typeahead is None or not getattr(typeahead, "enabled", False):
            return []
        try:
            lines, partial = typeahead.drain()
        except Exception:
            return []
        if partial:
            try:
                import readline

                readline.insert_text(partial)
            except ImportError:
                pass
        return lines

    def _stop_spinner(self) -> None:
        spinner = getattr(self.transport, "_spinner", None)
        if spinner is not None:
            try:
                spinner.stop()
            except Exception:
                pass

    def _show_resume(self) -> None:
        from wisp.terminal_width import status_symbols

        sid = str(self.session.get("id", ""))
        self.out.write(f"\n{status_symbols()['pause']}  Turn interrupted. Session saved.\n")
        self.out.write(f"   Resume: wisp repl -S {sid}\n\n")
        self.out.flush()

    def _show_exit(self) -> None:
        from wisp.terminal_width import status_symbols

        sid = str(self.session.get("id", ""))
        self.out.write(f"\n{status_symbols()['exit']}  Exiting. Session saved.\n")
        self.out.write(f"   Resume: wisp repl -S {sid}\n\n")
        self.out.flush()

    # ── Step 5: loop ──────────────────────────────────────────────────

    def _handle_multiline_command(self, args: str) -> None:
        from wisp.colors import dim, error, success
        from wisp.terminal_width import status_symbols

        args = args.strip().lower()
        if args in self._VALID_MODES:
            self.input_mode = args
        elif args:
            self.out.write(f"{error(status_symbols()['fail'])} Unknown mode '{args}'. Use single or multi.\n")
            return
        else:
            self.input_mode = "multi" if self.input_mode == "single" else "single"
        self.out.write(f"{success(status_symbols()['ok'])} Input mode: {self.input_mode}\n")
        if self.input_mode == "multi":
            self.out.write(f"{dim('  Enter blank line twice to submit, Ctrl+C to clear input')}\n")
        self.out.flush()

    def run(self) -> str:
        """Step 5: read → dispatch → turn until EXIT/EOF. Returns 'exit'."""
        self.lifecycle.mark("loop")
        self._arm_signals()
        try:
            from wisp.terminal_width import status_symbols

            info_sym = status_symbols()["info"]
        except Exception:
            info_sym = "i"
        try:
            from wisp.colors import dim as _dim
        except ImportError:
            _dim = lambda s: s  # noqa: E731
        pending: deque[str] = deque()
        try:
            while True:
                try:
                    if pending:
                        line = pending.popleft()
                    elif self.input_mode == "multi":
                        line = self.multiline_input_fn(PROMPT_TEXT, PROMPT_CONTINUATION)
                    else:
                        line = self.input_fn(PROMPT_TEXT)
                except KeyboardInterrupt:
                    self._show_exit()
                    break
                except EOFError:
                    self._show_exit()
                    break
                except Exception:
                    logger.exception("Input read failed")
                    self._show_exit()
                    break
                if line is None:
                    self._show_exit()
                    break
                prompt = line.strip()
                if not prompt:
                    continue
                if prompt.lower() in ("exit", "quit"):
                    break
                if prompt == "/multiline" or prompt.startswith("/multiline "):
                    parts = prompt.split(maxsplit=1)
                    self._handle_multiline_command(parts[1] if len(parts) > 1 else "")
                    continue
                if prompt.startswith("/"):
                    # Ensure legacy handlers see a live adapter.
                    _ = self.adapter
                    ctx = self._ctx()
                    try:
                        outcome = self.dispatcher.dispatch(ctx, prompt)
                    except (KeyboardInterrupt, SystemExit):
                        break
                    self._flush_ctx(ctx)
                    self._sync_from_adapter()
                    try:
                        if ctx.config is not self.config:
                            self.config = ctx.config
                            self.transport.config = ctx.config
                    except Exception:
                        pass
                    if outcome is CommandResult.EXIT:
                        break
                    if outcome is CommandResult.FOLLOWUP and ctx.followup:
                        self.run_turn(ctx.followup)
                    continue
                # Model turn with typeahead steering.
                sid = str(self.session.get("id", ""))

                def _steer_inbox(text: str, _sid: str = sid) -> None:
                    try:
                        self.runtime.inject_steering(_sid, text)
                    except Exception:
                        pass  # steering is best-effort; never break capture

                try:
                    typeahead = _typeahead_factory()(on_line=_steer_inbox)
                except ImportError:
                    typeahead = None
                replay = self.run_turn(prompt, typeahead=typeahead)
                try:
                    queued = self.runtime.drain_steering(sid)
                except Exception:
                    queued = []
                if queued:
                    self.out.write(_dim(f"{info_sym}  {len(queued)} prompt(s) typed ahead\n"))
                    pending.extend(queued)
                pending.extend(replay)
        finally:
            self.shutdown()
        return "exit"

    # ── Shutdown ──────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Persist history + session and restore signals (loop owned by caller)."""
        self.lifecycle.mark("shutdown")
        save_command_history()
        try:
            store = getattr(self.runtime, "store", None)
            if store is not None and isinstance(self.session, dict):
                store.save_session(self.session)
        except Exception:
            logger.warning("Failed to save session on exit", exc_info=True)
        self._restore_signals()
