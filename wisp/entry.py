"""New entry point using CompositionRoot.

Replaces: scattered instantiation in __main__.py.
Pattern:
  1. Load config once
  2. Create CompositionRoot
  3. Start all services
  4. Run the appropriate transport
  5. Shutdown all services
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore", category=SyntaxWarning)

import asyncio
import logging
import signal
from collections import deque
from pathlib import Path
from typing import Any

from wisp.composition import CompositionRoot
from wisp.config import WispConfig
from wisp.transport.cli import CLITransport
# Re-exported for backward compatibility (tests + external shims patch
# these entry-level names). Canonical homes: wisp.transport.cli / wisp.cli.repl.
from wisp.transport.cli import _input_line as _input_line  # noqa: F401
from wisp.transport.cli import _input_multiline as _input_multiline  # noqa: F401
from wisp.transport.cli import _restore_signal_handler as _restore_signal_handler  # noqa: F401
from wisp.transport.typeahead import TypeAheadBuffer as TypeAheadBuffer  # noqa: F401
from wisp.transport.renderer import render_turn_stats, render_file_ticker
from wisp.terminal_width import status_symbols
from wisp.colors import dim, error
import shutil

logger = logging.getLogger(__name__)


def _run_async(coro, loop: asyncio.AbstractEventLoop):
    """Run a coroutine on the given event loop.

    Assumes the loop is already running (single persistent REPL loop).
    """
    return loop.run_until_complete(coro)


def run_mode(mode: str, prompt: str | None = None, **kwargs) -> None:
    """Run Wisp in the specified mode.

    Args:
        mode: "cli", "server", "tui"
        prompt: Optional initial prompt for CLI mode
    """
    if mode == "server":
        # Server creates its own CompositionRoot in lifespan
        _run_server(**kwargs)
        return

    config = WispConfig()

    # Apply overrides from kwargs (model, workspace, etc.)
    model = kwargs.get("model")
    if model:
        config = config.replace(model=model)
    workspace = kwargs.get("workspace")
    if workspace:
        config = config.replace(workspace=workspace)
    if kwargs.get("show_thinking") is not None:
        config = config.replace(show_thinking=kwargs["show_thinking"])
    if kwargs.get("auto_approve") is not None:
        config = config.replace(auto_approve=kwargs["auto_approve"])

    root = CompositionRoot(config)

    try:
        root.start()

        if mode == "cli":
            _run_cli(root, prompt, **kwargs)
        elif mode == "tui":
            _run_tui(root)
        else:
            raise ValueError(f"Unknown mode: {mode}")

    finally:
        root.shutdown()


def _run_cli(root: CompositionRoot, prompt: str | None = None, **kwargs) -> None:
    """Run CLI mode.

    Uses a single persistent event loop regardless of mode (single-shot or REPL).
    This avoids cross-loop issues (RuntimeWarning, orphaned background tasks)
    caused by ``asyncio.run()`` creating a fresh loop each time.
    """

    config = root.config
    transport = CLITransport(
        root.runtime,
        config,
        background_agents=getattr(root, "background_agents", None),
    )
    transport.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    root.bind_loop(loop)
    try:
        if prompt:
            # Single-shot mode: run one prompt on the persistent loop, then exit
            loop.run_until_complete(_run_single_prompt(transport, root, prompt, config, **kwargs))
        else:
            # REPL mode: reuse the same persistent loop for all turns
            _run_repl(transport, root, config, loop=loop, **kwargs)
    finally:
        # Single-shot turns can spawn detached work exactly like REPL turns;
        # without this drain they die mid-flight at loop.close().
        assert loop is not None  # bound above: own persistent loop
        try:
            from wisp.async_utils import drain_pending_tasks
            loop.run_until_complete(drain_pending_tasks(loop, timeout=3.0))
        except Exception:
            pass
        loop.close()
        transport.stop()


def _term_width() -> int:
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


def _history_path() -> Path:
    """Backward-compat alias — canonical home is wisp.cli.repl.history_path."""
    from wisp.cli.repl import history_path

    return history_path()


def _load_command_history() -> bool:
    """Backward-compat alias — canonical home is wisp.cli.repl."""
    from wisp.cli.repl import load_command_history

    return load_command_history()


def _save_command_history() -> bool:
    """Backward-compat alias — canonical home is wisp.cli.repl."""
    from wisp.cli.repl import save_command_history

    return save_command_history()


def _show_turn_stats(transport: CLITransport, adapter: Any | None = None) -> None:
    """Render turn stats, file ticker, and separator after a turn."""
    import sys
    stats = transport._progress.on_done()
    # Context meter input: estimate from the live session against the
    # configured window. Missing either side simply hides the meter.
    try:
        if adapter is not None and getattr(adapter, "session", None):
            est = adapter._estimate_tokens(adapter.session.get("messages", []))
            limit = getattr(transport.config, "max_context_tokens", 0)
            if est and limit:
                stats["ctx_tokens"] = est
                stats["ctx_limit"] = limit
    except Exception:
        pass  # meter is optional chrome; never break the stats line
    width = _term_width()
    stats_line = render_turn_stats(stats, width)
    if stats_line:
        sys.stderr.write(stats_line + "\n")
    files = stats.get("files_changed", [])
    if files:
        ticker = render_file_ticker(files, width)
        if ticker:
            sys.stderr.write(ticker + "\n")
    sys.stderr.write("\n" + dim("─" * width) + "\n\n")
    sys.stderr.flush()


def make_repl_sigint_handler(transport, get_current_task, restore_default):
    """Build the REPL's SIGINT handler.

    - Turn running: cancel it (first press), then hand control back to the
      default handler so a second press hard-quits.
    - Idle at prompt: raise KeyboardInterrupt — a single press exits
      single-line mode and triggers multiline's documented
      "Ctrl+C clears input" contract. The previous cooperative handler set
      a flag that nothing consumed, so the first press only printed a
      message while the turn kept running.
    """
    import sys

    def _repl_sigint_handler(signum, frame):
        spinner = getattr(transport, "_spinner", None)
        if spinner is not None:
            try:
                spinner.stop()
            except Exception:
                pass
        task = get_current_task()
        if task is not None and not task.done():
            task.cancel()
            sym = status_symbols()
            sys.stdout.write(f"\n{sym['cancel']}  Interrupted — cancelling turn… (Ctrl+C again to force quit)\n")
            sys.stdout.flush()
            restore_default()
        else:
            raise KeyboardInterrupt

    return _repl_sigint_handler


def _run_repl(transport: CLITransport, root: CompositionRoot, config: WispConfig, loop: asyncio.AbstractEventLoop | None = None, **kwargs) -> None:
    """Synchronous REPL — 5-step composition-root lifecycle driver.

    Step 1 boot_env → Step 2 preflight → Step 3 assembly (CompositionRoot,
    built by run_mode) → Step 4 banner → Step 5 interactive loop, all
    executed through the injected ReplRunner (wisp.cli.repl). See
    _run_repl_legacy for the pre-refactor inline implementation, kept for
    one release as a behavioral reference.
    """
    import uuid

    session_id = kwargs.get("session_id") or str(uuid.uuid4())
    skill = kwargs.get("skill")

    own_loop = loop is None
    if own_loop:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        root.bind_loop(loop)

    assert loop is not None
    session = loop.run_until_complete(root.runtime.get_or_create_session(
        session_id=session_id,
        model=config.model,
        workspace=config.workspace,
    ))
    is_continuation = len(session.get("messages", [])) > 0

    from wisp.cli.dispatcher import Dispatcher
    from wisp.cli.repl import ReplRunner
    from wisp.commands import dispatch as _legacy_dispatch

    runner = ReplRunner(
        runtime=root.runtime,
        transport=transport,
        renderer=None,
        dispatcher=Dispatcher(legacy_dispatch=_legacy_dispatch),
        config=config,
        session=session,
        loop=loop,
        on_turn_stats=lambda sess: _show_turn_stats(transport, runner.adapter),
    )
    # Step 1: environment & config boot.
    workspace = runner.boot_env()
    # Step 2: async pre-flight inside the startup budget (non-fatal).
    loop.run_until_complete(runner.preflight(workspace or config.workspace))
    # Step 3: transport & runtime assembly — owned by CompositionRoot,
    # constructed by run_mode before dispatch. Nothing to build here.
    # Step 4: single-frame startup banner.
    runner.banner(is_continuation=is_continuation, skill=skill)
    # Step 5: hand off to the interactive loop (shutdown runs inside).
    try:
        runner.run()
    finally:
        # Loop teardown stays here: the loop is owned by this driver
        # (or shared with single-shot mode), not by the runner.
        _previous_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda *_a: None)
        try:
            loop.run_until_complete(_drain_pending_tasks(loop, timeout=3.0))
            if own_loop:
                loop.close()
        finally:
            signal.signal(signal.SIGINT, _previous_sigint)


def _run_repl_legacy(transport: CLITransport, root: CompositionRoot, config: WispConfig, loop: asyncio.AbstractEventLoop | None = None, **kwargs) -> None:
    """Pre-refactor inline REPL (behavioral reference; remove next release).

    Synchronous REPL — single persistent event loop for the session.

    Uses *loop* if provided (shared with single-shot mode), otherwise creates
    a new persistent loop. Background threads reference this loop.
    Ctrl+C during a turn cancels the turn's tasks but keeps the loop running.
    Ctrl+C at the prompt exits gracefully.
    """
    import sys
    import uuid

    from wisp.transport.typeahead import TypeAheadBuffer  # noqa: F401  (legacy path only)

    session_id = kwargs.get("session_id") or str(uuid.uuid4())

    own_loop = loop is None
    if own_loop:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        root.bind_loop(loop)

    # Create session (async, on the persistent loop)
    session = loop.run_until_complete(root.runtime.get_or_create_session(
        session_id=session_id,
        model=config.model,
        workspace=config.workspace,
    ))

    # Check if this is a continuation (session has messages)
    is_continuation = len(session.get("messages", [])) > 0
    skill = kwargs.get("skill")

    # Up-arrow recall of prompts from previous sessions
    _load_command_history()

    # Tab-completion for slash commands / providers / models (no-op off-tty).
    from wisp.repl.completion import install_readline_completion
    install_readline_completion()

    # ── Pre-flight verification (non-blocking, 100 ms budget) ────────
    # Isolated from main REPL loop: failures degrade to warning banner,
    # never abort startup. Stored for /doctor.
    preflight_report = None
    preflight_banner = ""
    try:
        from wisp.core.doctor import run_preflight_sync, format_banner

        preflight_report = run_preflight_sync(workspace=config.workspace, config=config, timeout_s=0.1)
        preflight_banner = format_banner(preflight_report)
        try:
            import wisp.core.doctor as _doctor_mod

            _doctor_mod._LAST_REPORT = preflight_report  # type: ignore[attr-defined]
        except Exception:
            pass
    except Exception as e:
        logger.debug("pre-flight failed: %s", e, exc_info=True)

    if is_continuation:
        transport.print_continuation_banner(sys.stdout, session, config.model)
    else:
        transport.print_banner(sys.stderr, session, config.model, skill=skill)

    # Pre-flight banner line (one-liner, color-coded)
    if preflight_banner:
        try:
            from wisp.colors import success as _success, warning as _warning

            if preflight_report is not None and getattr(preflight_report, "healthy", False):
                sys.stderr.write(f"  {_success(preflight_banner)}\n")
            else:
                sys.stderr.write(f"  {_warning(preflight_banner)}\n")
            sys.stderr.flush()
        except Exception:
            # Fallback plain
            try:
                sys.stderr.write(f"  {preflight_banner}\n")
                sys.stderr.flush()
            except Exception:
                pass

    # Fallback DB banner: TCC can force UnifiedStore to /tmp/wisp_fallback.db
    # without any error at CompositionRoot construction — surface it here so
    # the operator knows history is in tmp and should /workspace to a real dir.
    try:
        _store_path = str(getattr(getattr(root, "store", None), "db_path", "") or "")
        if "wisp_fallback.db" in _store_path:
            sys.stderr.write(
                dim(f"  Workspace blocked by TCC; using fallback DB {_store_path} — run /workspace ~/Documents/wisp\n")
            )
            sys.stderr.flush()
    except Exception:
        pass

    # Warn if no provider is configured — turns will produce no output
    provider_name = getattr(config, "provider", None)
    if not provider_name:
        sys.stdout.write(f"\n{status_symbols()['warn']}  No LLM provider configured. Set WISP_PROVIDER or add 'provider' to config.\n")
        sys.stdout.write("   Example: wisp repl -m llama3   or   export WISP_PROVIDER=ollama\n\n")
        sys.stdout.flush()

    # Create adapter once for the session — persists metrics across turns
    from wisp.transport.cli import AgentAdapter
    adapter = AgentAdapter(root.runtime, config, session, loop=loop)

    # Input mode: 'single' or 'multi'
    input_mode = "single"

    def _show_resume() -> None:
        """Print the resume command for this session."""
        sys.stdout.write(f"\n{status_symbols()['pause']}  Turn interrupted. Session saved.\n")
        sys.stdout.write(f"   Resume: wisp repl -S {_current_session_id()}\n\n")
        sys.stdout.flush()

    def _show_exit() -> None:
        """Print exit message with resume command."""
        sys.stdout.write(f"\n{status_symbols()['exit']}  Exiting. Session saved.\n")
        sys.stdout.write(f"   Resume: wisp repl -S {_current_session_id()}\n\n")
        sys.stdout.flush()

    def _current_session_id() -> str:
        """Live session id — /new swaps adapter.session mid-REPL."""
        sid = getattr(adapter.session, "get", lambda *_: None)("id")
        return str(sid) if sid else session_id

    def _force_save() -> bool:
        """Best-effort save of the live session; returns success."""
        try:
            store = getattr(root.runtime, "store", None)
            if store is not None and isinstance(adapter.session, dict):
                store.save_session(adapter.session)
            return True
        except Exception:
            logger.warning("Failed to save session on exit", exc_info=True)
            return False

    _current_turn_task: asyncio.Task | None = None

    def _cancel_tasks() -> None:
        """Cancel the current turn task, not background services."""
        if _current_turn_task is not None and not _current_turn_task.done():
            _current_turn_task.cancel()

    def _arm_repl_sigint() -> None:
        handler = make_repl_sigint_handler(
            transport, lambda: _current_turn_task,
            restore_default=lambda: signal.signal(signal.SIGINT, signal.default_int_handler),
        )
        signal.signal(signal.SIGINT, handler)

    def _stop_spinner(transport) -> None:
        """Kill an active spinner so its \\r-thread can't overwrite error output.

        The success/fail paths in CLITransport already stop it; exception paths
        here are the leak that garbled tracebacks.
        """
        spinner = getattr(transport, "_spinner", None)
        if spinner is not None:
            try:
                spinner.stop()
            except Exception:
                pass

    def _run_turn(prompt: str, typeahead: "TypeAheadBuffer | None" = None) -> list[str]:
        """Run one turn on the persistent loop; returns prompts typed ahead."""
        async def _turn():
            # Read the session through the adapter at call time: commands
            # like /new replace adapter.session mid-REPL.
            async for event in root.runtime.run_turn(adapter.session, prompt, approval_handler=getattr(transport, "approve", None)):
                transport._render_event(sys.stdout, event)

        transport._reset_buffers()
        transport.start_wait_clock(stdout=sys.stdout)
        try:
            nonlocal _current_turn_task
            coro = _turn()
            _current_turn_task = loop.create_task(coro)
            if typeahead is not None:
                typeahead.start()
            loop.run_until_complete(_current_turn_task)
            transport.stop_wait_clock(stdout=sys.stdout)
            transport._flush_thinking(sys.stdout)
            transport._flush_content(sys.stdout)
            # Turn stats + file ticker + separator
            _show_turn_stats(transport, adapter)
        except KeyboardInterrupt:
            transport.stop_wait_clock(stdout=sys.stdout)
            _cancel_tasks()
            _stop_spinner(transport)
            transport._flush_thinking(sys.stdout)
            transport._flush_content(sys.stdout)
            _show_resume()
        except asyncio.CancelledError:
            # Task was cancelled (likely Ctrl+C or approval [c]ancel)
            transport.stop_wait_clock(stdout=sys.stdout)
            _stop_spinner(transport)
            transport._flush_thinking(sys.stdout)
            transport._flush_content(sys.stdout)
            _show_resume()
        except Exception as exc:
            import traceback
            transport.stop_wait_clock(stdout=sys.stdout)
            _stop_spinner(transport)
            transport._flush_thinking(sys.stdout)
            transport._flush_content(sys.stdout)
            sys.stderr.write(f"Error during turn: {exc}\n")
            traceback.print_exc(file=sys.stderr)
            sys.stdout.write(f"Error: {exc}\n")
            sys.stdout.flush()
        finally:
            _current_turn_task = None
            # Idempotent backstop: no exit path may leave the ticker live.
            transport.stop_wait_clock(stdout=sys.stdout)
            # The handler de-arms itself on first press (second Ctrl+C
            # force-quits); re-arm it for the next turn.
            _arm_repl_sigint()

        if typeahead is None or not typeahead.enabled:
            return []
        lines, partial = typeahead.drain()
        if partial:
            try:
                import readline
                readline.insert_text(partial)
            except ImportError:
                pass
        return lines

    # Install REPL-owned SIGINT handler for the session (after the closure
    # above exists — the handler reads _current_turn_task live).
    _arm_repl_sigint()

    _VALID_MODES = ("single", "multi")

    def _handle_multiline_command(args: str) -> None:
        """Toggle/validate input mode without round-tripping dispatch."""
        nonlocal input_mode
        args = args.strip().lower()
        if args in _VALID_MODES:
            input_mode = args
        elif args:
            sys.stdout.write(f"{error(status_symbols()['fail'])} Unknown mode '{args}'. Use single or multi.\n")
            return
        else:
            input_mode = "multi" if input_mode == "single" else "single"
        from wisp.colors import success, dim
        sys.stdout.write(f"{success(status_symbols()['ok'])} Input mode: {input_mode}\n")
        if input_mode == "multi":
            sys.stdout.write(f"{dim('  Enter blank line twice to submit, Ctrl+C to clear input')}\n")
        sys.stdout.flush()

    pending: deque[str] = deque()
    while True:
        try:
            if pending:
                # Prompt typed while the previous turn ran — replay it.
                line = pending.popleft()
            elif input_mode == "multi":
                line = _input_multiline("➜ ", "... ")
            else:
                line = _input_line("➜ ")
        except KeyboardInterrupt:
            # Single-line mode: Ctrl+C at prompt exits gracefully
            _show_exit()
            break
        except EOFError:
            _show_exit()
            break
        except Exception:
            logger.exception("Input read failed")
            _show_exit()
            break
        if line is None:
            _show_exit()
            break

        # In multiline mode, empty input (just pressing Enter twice) continues
        # In single mode, empty lines are skipped
        if input_mode == "single":
            prompt = line.strip()
            if not prompt:
                continue
        else:
            prompt = line.strip()
            if not prompt:
                # In multiline mode, empty input after content means submit
                # but if there's no content, continue
                continue
        if prompt.lower() in ("exit", "quit"):
            break

        # ── Input-mode command (intercepted before dispatch so it never
        # hits the registry as an unknown command) ────────────────────
        if prompt == "/multiline" or prompt.startswith("/multiline "):
            parts = prompt.split(maxsplit=1)
            _handle_multiline_command(parts[1] if len(parts) > 1 else "")
            continue

        # ── Slash commands ──────────────────────────────────────
        if prompt.startswith("/"):
            from wisp.commands import dispatch
            from wisp.exceptions import ExitREPL
            try:
                result = dispatch(prompt, adapter)
                # Commands rebind adapter.config (frozen dataclass —
                # replace() returns a new object). Re-share it with the
                # transport so rendering flags like show_thinking stay
                # live for subsequent turns.
                transport.config = adapter.config
                if isinstance(result, str) and result:
                    # Command returned a prompt to run (e.g. /continue)
                    _run_turn(result)
                continue
            except ExitREPL:
                break
            except Exception as exc:
                import logging
                logging.getLogger(__name__).exception("Slash command failed")
                sys.stdout.write(f"Error: {exc}\n")
                sys.stdout.flush()
                continue

        # Run one turn. Lines typed while it runs are steered into the live
        # turn at tool boundaries; anything the engine never consumed (no
        # boundary occurred) replays as a normal prompt afterwards.
        sid = adapter.session.get("id", "")

        def _steer_inbox(text: str) -> None:
            try:
                # Direct append: the reader thread is the only producer,
                # list ops are atomic under the GIL, and the engine drains
                # on the loop thread — no cross-thread scheduling needed.
                root.runtime.inject_steering(sid, text)
            except Exception:
                pass  # steering is best-effort; never break capture

        typeahead = TypeAheadBuffer(on_line=_steer_inbox)
        _run_turn(prompt, typeahead=typeahead)
        try:
            queued = root.runtime.drain_steering(sid)
        except Exception:
            queued = []
        if queued:
            sys.stdout.write(
                dim(f"{status_symbols()['info']}  {len(queued)} prompt(s) typed ahead\n")
            )
            pending.extend(queued)

    # Restore original signal handler before cleanup
    _restore_signal_handler()

    # Teardown is uninterruptible: a Ctrl+C landing mid-cleanup used to kill
    # the save, leave asyncio tasks half-reaped, and spray "Task exception
    # was never retrieved" tracebacks after the goodbye message. The exit
    # path itself is bounded now (<3.5s), so briefly ignoring SIGINT trades
    # nothing — force-quit still works at any point before teardown starts.
    _previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda *_a: None)
    try:
        saved = False
        try:
            _cancel_tasks()
            assert loop is not None  # bound above: own loop or caller's
            loop.run_until_complete(_drain_pending_tasks(loop, timeout=3.0))
        finally:
            _save_command_history()
            saved = _force_save()
            if own_loop:
                loop.close()
    finally:
        signal.signal(signal.SIGINT, _previous_sigint)
    if not saved:
        sys.stdout.write(f"\n{status_symbols()['warn']}  Could not save session.\n")
        sys.stdout.flush()


async def _drain_pending_tasks(loop: asyncio.AbstractEventLoop, timeout: float = 3.0) -> None:
    """REPL teardown drain — canonical impl in async_utils (shared with server/single-shot)."""
    from wisp.async_utils import drain_pending_tasks
    await drain_pending_tasks(loop, timeout=timeout)


async def _run_single_prompt(transport: CLITransport, root: CompositionRoot, prompt: str, config: WispConfig, **kwargs) -> None:
    """Run a single prompt and print results."""
    import sys
    import uuid

    session_id = kwargs.get("session_id") or str(uuid.uuid4())
    session = await root.runtime.get_or_create_session(
        session_id=session_id,
        model=config.model,
        workspace=config.workspace,
    )

    # ── Slash commands in single-shot mode ──────────────────
    adapter = None  # bound for every path — stats rendering reads it below
    if prompt.startswith("/"):
        from wisp.commands import dispatch
        from wisp.transport.cli import AgentAdapter
        adapter = AgentAdapter(root.runtime, config, session)
        try:
            result = dispatch(prompt, adapter)
            # Keep the transport's rendering flags in sync with any config
            # change the command made (same seam as _run_repl).
            transport.config = adapter.config
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("Slash command failed")
            sys.stdout.write(f"Error: {exc}\n")
            sys.stdout.flush()
            return
        if isinstance(result, str) and result:
            # Command returned a follow-up prompt (e.g. /continue) — run it,
            # same as the REPL does, instead of silently dropping it.
            prompt = result
        else:
            return

    transport._reset_buffers()
    async for event in root.runtime.run_turn(session, prompt, approval_handler=getattr(transport, "approve", None)):
        transport._render_event(sys.stdout, event)

    transport._flush_thinking(sys.stdout)
    transport._flush_content(sys.stdout)
    _show_turn_stats(transport, adapter)
    sys.stdout.flush()


def _run_server(**kwargs) -> None:
    """Run server mode.

    Server creates its own CompositionRoot in lifespan —
    no need to create one here.
    """
    from wisp.server.main import main as server_main
    host = kwargs.get("host", "127.0.0.1")
    port = kwargs.get("port", 8000)
    no_auth = kwargs.get("no_auth", False)
    server_main(host=host, port=port, no_auth=no_auth)


def _run_tui(root: CompositionRoot) -> None:
    """Run TUI mode.

    Launches the Textual TUI app and wires it to TUITransport.
    """
    from wisp.tui.app import WispTUIApp

    # Deferred: textual costs ~29ms of import time the CLI never uses.
    from wisp.transport.tui import TUITransport

    transport = TUITransport()
    transport.start()

    try:
        app = WispTUIApp(config=root.config, transport=transport,
                         runtime=root.runtime)
        transport.set_app(app)
        app.run()
    finally:
        transport.stop()


# ── Headless mode ──────────────────────────────────────────────────

_headless_root: CompositionRoot | None = None
_headless_root_key: str | None = None


def _make_headless_key(config: WispConfig) -> str:
    """Cache key for headless root.

    Uses WispConfig.fingerprint() — the SAME fields the runtime core cache
    keys on (provider, model, api_base, temperature, …). The old hand-rolled
    model:workspace:permission_mode key missed provider/api_base switches,
    so a cached root could serve turns through a stale provider.
    """
    return config.fingerprint()


async def run_headless(prompt: str, model: str | None = None,
                       workspace: str | None = None,
                       session_id: str | None = None,
                       permission_mode: str = "full",
                       root: CompositionRoot | None = None) -> dict:
    """Run a prompt headlessly and return structured result.

    Uses CompositionRoot + HeadlessTransport for consistent
    event collection across CLI, server, and background modes.

    Args:
        prompt: The user prompt to execute.
        model: Optional model override.
        workspace: Optional workspace override.
        session_id: Optional session ID.
        permission_mode: Permission mode for tool execution.
        root: Optional existing CompositionRoot to reuse.
              If not provided, a cached headless root is used.
    """
    from wisp.transport.headless import HeadlessTransport

    global _headless_root, _headless_root_key

    config = WispConfig()
    if model:
        config = config.replace(model=model)
    if workspace:
        config = config.replace(workspace=workspace)
    config = config.replace(permission_mode=permission_mode, auto_approve=True, show_thinking=True)

    own_root = root is None
    if own_root:
        cache_key = _make_headless_key(config)
        # Invalidate cache if config file on disk changed since last use
        cfg_path = Path.home() / ".config" / "wisp" / "config.json"
        cfg_mtime = cfg_path.stat().st_mtime if cfg_path.exists() else 0
        cache_valid = (
            _headless_root is not None
            and _headless_root_key == cache_key
            and getattr(_headless_root, "_config_mtime", 0) == cfg_mtime
        )
        if not cache_valid:
            if _headless_root is not None:
                _headless_root.shutdown()
            _headless_root = CompositionRoot(config)
            _headless_root.start()
            _headless_root_key = cache_key
            _headless_root._config_mtime = cfg_mtime
        root = _headless_root

    try:
        transport = HeadlessTransport()
        transport.start()

        session = await root.runtime.get_or_create_session(
            session_id=session_id or "headless",
            model=config.model,
            workspace=config.workspace,
        )

        async for event in root.runtime.run_turn(session, prompt):
            await transport.send(event)

        result = transport.collect_result()
        result["session_id"] = session.get("id", session_id)
        result["prompt"] = prompt
        result["model"] = config.model
        return result

    finally:
        if own_root:
            # Don't shutdown cached root — reuse it
            pass
