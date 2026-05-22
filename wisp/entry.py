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

import asyncio
import logging
from pathlib import Path
from typing import Any

from wisp.composition import CompositionRoot
from wisp.config import WispConfig
from wisp.transport.cli_v2 import CLITransport
from wisp.transport.tui import TUITransport

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run a coroutine, handling nested event loops gracefully.

    Uses asyncio.run() when no loop is running, otherwise uses
    the existing loop's run_until_complete().
    """
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop running — safe to use asyncio.run
        return asyncio.run(coro)
    # Already in a loop — use run_until_complete
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
        config.model = model
    workspace = kwargs.get("workspace")
    if workspace:
        config.workspace = workspace

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
    """Run CLI mode."""
    import sys
    import uuid

    config = root.config
    transport = CLITransport(root.runtime, config)
    transport.start()
    try:
        if prompt:
            # Single-shot mode: run one prompt and exit
            asyncio.run(_run_single_prompt(transport, root, prompt, config, **kwargs))
        else:
            # REPL mode: synchronous loop, async per-turn
            _run_repl(transport, root, config, **kwargs)
    finally:
        transport.stop()


def _run_repl(transport: CLITransport, root: CompositionRoot, config: WispConfig, **kwargs) -> None:
    """Synchronous REPL — reads stdin in main thread, async per turn.

    Ctrl+C behavior:
      - During a turn: interrupts the turn, saves session, shows resume command
      - At prompt: exits REPL gracefully with resume command
    """
    import sys
    import uuid

    session_id = kwargs.get("session_id") or str(uuid.uuid4())

    # Create session (async)
    session = asyncio.run(root.runtime.get_or_create_session(
        session_id=session_id,
        model=config.model,
        workspace=config.workspace,
    ))

    # Check if this is a continuation (session has messages)
    is_continuation = len(session.get("messages", [])) > 0
    skill = kwargs.get("skill")
    
    if is_continuation:
        transport.print_continuation_banner(sys.stdout, session, config.model)
    else:
        transport.print_banner(sys.stdout, session, config.model, skill=skill)

    def _show_resume() -> None:
        """Print the resume command for this session."""
        sys.stdout.write(f"\n⏸  Turn interrupted. Session saved.\n")
        sys.stdout.write(f"   Resume: wisp repl -S {session_id}\n\n")
        sys.stdout.flush()

    def _show_exit() -> None:
        """Print exit message with resume command."""
        sys.stdout.write(f"\n👋  Exiting. Session saved.\n")
        sys.stdout.write(f"   Resume: wisp repl -S {session_id}\n\n")
        sys.stdout.flush()

    while True:
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            # Ctrl+C at prompt — exit gracefully
            _show_exit()
            break
        except Exception:
            break
        if not line:
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
            from wisp.transport.cli_v2 import AgentAdapter
            adapter = AgentAdapter(root.runtime, config, session)
            try:
                if dispatch(prompt, adapter):
                    continue
            except ExitREPL:
                break
            except Exception as exc:
                import logging
                logging.getLogger(__name__).exception("Slash command failed")
                sys.stdout.write(f"Error: {exc}\n")
                sys.stdout.flush()
                continue

        # Run one turn (async)
        async def _turn():
            async for event in root.runtime.run_turn(session, prompt):
                transport._render_event(sys.stdout, event)

        transport._reset_buffers()
        try:
            _run_async(_turn())
            transport._flush_thinking(sys.stdout)
            transport._flush_content(sys.stdout)
        except KeyboardInterrupt:
            # Ctrl+C during turn — show resume and continue
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
        from wisp.transport.cli_v2 import AgentAdapter
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

    async for event in root.runtime.run_turn(session, prompt):
        transport._render_event(sys.stdout, event)

    transport._flush_thinking(sys.stdout)
    transport._flush_content(sys.stdout)
    sys.stdout.write("\n")
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
        config.model = model
    if workspace:
        config.workspace = workspace
    config.permission_mode = permission_mode
    config.auto_approve = True
    config.show_thinking = True

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
