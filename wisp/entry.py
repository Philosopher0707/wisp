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
from pathlib import Path

from wisp.composition import CompositionRoot
from wisp.config import WispConfig
from wisp.transport.cli import CLITransport, _input_line, _install_signal_handler, _restore_signal_handler
from wisp.transport.tui import TUITransport
from wisp.transport.renderer import render_turn_stats, render_file_ticker
from wisp.colors import dim
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
    transport = CLITransport(root.runtime, config)
    transport.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        if prompt:
            # Single-shot mode: run one prompt on the persistent loop, then exit
            loop.run_until_complete(_run_single_prompt(transport, root, prompt, config, **kwargs))
        else:
            # REPL mode: reuse the same persistent loop for all turns
            _run_repl(transport, root, config, loop=loop, **kwargs)
    finally:
        try:
            loop.run_until_complete(asyncio.sleep(0))
        except Exception:
            pass
        loop.close()
        transport.stop()


def _term_width() -> int:
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


def _show_turn_stats(transport: CLITransport) -> None:
    """Render turn stats, file ticker, and separator after a turn."""
    import sys
    stats = transport._progress.on_done()
    width = _term_width()
    stats_line = render_turn_stats(stats, width)
    if stats_line:
        sys.stdout.write(stats_line + "\n")
    files = stats.get("files_changed", [])
    if files:
        ticker = render_file_ticker(files, width)
        if ticker:
            sys.stdout.write(ticker + "\n")
    sys.stdout.write("\n" + dim("─" * width) + "\n\n")
    sys.stdout.flush()


def _run_repl(transport: CLITransport, root: CompositionRoot, config: WispConfig, loop: asyncio.AbstractEventLoop | None = None, **kwargs) -> None:
    """Synchronous REPL — single persistent event loop for the session.

    Uses *loop* if provided (shared with single-shot mode), otherwise creates
    a new persistent loop. Background threads reference this loop.
    Ctrl+C during a turn cancels the turn's tasks but keeps the loop running.
    Ctrl+C at the prompt exits gracefully.
    """
    import sys
    import uuid

    session_id = kwargs.get("session_id") or str(uuid.uuid4())

    own_loop = loop is None
    if own_loop:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # Create session (async, on the persistent loop)
    session = loop.run_until_complete(root.runtime.get_or_create_session(
        session_id=session_id,
        model=config.model,
        workspace=config.workspace,
    ))

    # Check if this is a continuation (session has messages)
    is_continuation = len(session.get("messages", [])) > 0
    skill = kwargs.get("skill")
    
    # Install custom SIGINT handler for graceful Ctrl+C during turns
    _install_signal_handler()

    if is_continuation:
        transport.print_continuation_banner(sys.stdout, session, config.model)
    else:
        transport.print_banner(sys.stdout, session, config.model, skill=skill)

    # Warn if no provider is configured — turns will produce no output
    provider_name = getattr(config, "provider", None)
    if not provider_name:
        sys.stdout.write("\n⚠  No LLM provider configured. Set WISP_PROVIDER or add 'provider' to config.\n")
        sys.stdout.write("   Example: wisp repl -m llama3   or   export WISP_PROVIDER=ollama\n\n")
        sys.stdout.flush()

    # Create adapter once for the session — persists metrics across turns
    from wisp.transport.cli import AgentAdapter
    adapter = AgentAdapter(root.runtime, config, session, loop=loop)

    def _show_resume() -> None:
        """Print the resume command for this session."""
        sys.stdout.write("\n⏸  Turn interrupted. Session saved.\n")
        sys.stdout.write(f"   Resume: wisp repl -S {session_id}\n\n")
        sys.stdout.flush()

    def _show_exit() -> None:
        """Print exit message with resume command."""
        sys.stdout.write("\n👋  Exiting. Session saved.\n")
        sys.stdout.write(f"   Resume: wisp repl -S {session_id}\n\n")
        sys.stdout.flush()

    _current_turn_task: asyncio.Task | None = None

    def _cancel_tasks() -> None:
        """Cancel the current turn task, not background services."""
        if _current_turn_task is not None and not _current_turn_task.done():
            _current_turn_task.cancel()

    def _run_turn(prompt: str) -> None:
        """Run one turn on the persistent loop."""
        async def _turn():
            async for event in root.runtime.run_turn(session, prompt, approval_handler=getattr(transport, "approve", None)):
                transport._render_event(sys.stdout, event)

        transport._reset_buffers()
        try:
            nonlocal _current_turn_task
            coro = _turn()
            _current_turn_task = loop.create_task(coro)
            loop.run_until_complete(_current_turn_task)
            transport._flush_thinking(sys.stdout)
            transport._flush_content(sys.stdout)
            # Turn stats + file ticker + separator
            _show_turn_stats(transport)
        except KeyboardInterrupt:
            _cancel_tasks()
            transport._flush_thinking(sys.stdout)
            transport._flush_content(sys.stdout)
            _show_resume()
        except asyncio.CancelledError:
            # Task was cancelled (likely Ctrl+C)
            transport._flush_thinking(sys.stdout)
            transport._flush_content(sys.stdout)
            _show_resume()
        except Exception as exc:
            import traceback
            transport._flush_thinking(sys.stdout)
            transport._flush_content(sys.stdout)
            sys.stderr.write(f"Error during turn: {exc}\n")
            traceback.print_exc(file=sys.stderr)
            sys.stdout.write(f"Error: {exc}\n")
            sys.stdout.flush()
        finally:
            _current_turn_task = None

    while True:
        try:
            line = _input_line("➜ ")
        except KeyboardInterrupt:
            # Ctrl+C at prompt — exit gracefully
            _show_exit()
            break
        except Exception:
            break
        if line is None:
            break

        prompt = line.strip()
        if not prompt:
            continue
        if prompt.lower() in ("exit", "quit"):
            break

        # ── Slash commands ──────────────────────────────────────
        if prompt.startswith("/"):
            from wisp.commands import dispatch
            from wisp.exceptions import ExitREPL
            try:
                result = dispatch(prompt, adapter)
                if result is False:
                    pass  # Not a slash command — fall through to _run_turn
                elif isinstance(result, str):
                    # Command returned a prompt to run (e.g. /continue)
                    _run_turn(result)
                    continue
                else:
                    # True/None: command consumed the input — do NOT send the
                    # literal "/<cmd>" text to the model as a prompt (previously
                    # fell through to _run_turn(prompt), causing e.g. "/model 1"
                    # to both switch the model AND be echoed to the LLM).
                    continue
            except ExitREPL:
                break
            except Exception as exc:
                import logging
                logging.getLogger(__name__).exception("Slash command failed")
                sys.stdout.write(f"Error: {exc}\n")
                sys.stdout.flush()
                continue

        # Run one turn
        _run_turn(prompt)

    # Restore original signal handler before cleanup
    _restore_signal_handler()

    # Clean up: cancel any in-progress turn and shut down services
    try:
        _cancel_tasks()
        loop.run_until_complete(asyncio.sleep(0.05))  # Let cancellation settle
    finally:
        if own_loop:
            loop.close()


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
    if prompt.startswith("/"):
        from wisp.commands import dispatch
        from wisp.transport.cli import AgentAdapter
        adapter = AgentAdapter(root.runtime, config, session)
        try:
            if dispatch(prompt, adapter):
                return
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("Slash command failed")
            sys.stdout.write(f"Error: {exc}\n")
            sys.stdout.flush()
            return

    transport._reset_buffers()
    async for event in root.runtime.run_turn(session, prompt, approval_handler=getattr(transport, "approve", None)):
        transport._render_event(sys.stdout, event)

    transport._flush_thinking(sys.stdout)
    transport._flush_content(sys.stdout)
    _show_turn_stats(transport)
    sys.stdout.flush()


def _run_server(**kwargs) -> None:
    """Run server mode.

    Server creates its own CompositionRoot in lifespan —
    no need to create one here.
    """
    from wisp.server.main import main as server_main
    host = kwargs.get("host", "0.0.0.0")
    port = kwargs.get("port", 8000)
    no_auth = kwargs.get("no_auth", False)
    server_main(host=host, port=port, no_auth=no_auth)


def _run_tui(root: CompositionRoot) -> None:
    """Run TUI mode.

    Launches the Textual TUI app and wires it to TUITransport.
    """
    from wisp.tui.app import WispTUIApp

    transport = TUITransport()
    transport.start()

    try:
        app = WispTUIApp(config=root.config, transport=transport)
        transport.set_app(app)
        app.run()
    finally:
        transport.stop()


# ── Headless mode ──────────────────────────────────────────────────

_headless_root: CompositionRoot | None = None
_headless_root_key: str | None = None


def _make_headless_key(config: WispConfig) -> str:
    """Cache key for headless root based on config."""
    return f"{config.model}:{config.workspace}:{config.permission_mode}"


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
