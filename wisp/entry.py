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
from typing import Any

from wisp.composition import CompositionRoot
from wisp.config import WispConfig
from wisp.transport.cli_v2 import CLITransport
from wisp.transport.tui import TUITransport

logger = logging.getLogger(__name__)


def run_mode(mode: str, prompt: str | None = None, **kwargs) -> None:
    """Run Wisp in the specified mode.

    Args:
        mode: "cli", "server", "tui"
        prompt: Optional initial prompt for CLI mode
    """
    config = WispConfig()
    root = CompositionRoot(config)

    try:
        root.start()

        if mode == "cli":
            _run_cli(root, prompt)
        elif mode == "server":
            _run_server(root, **kwargs)
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
    transport = CLITransport(root.runtime)
    transport.start()
    try:
        if prompt:
            # Single-shot mode: run one prompt and exit
            asyncio.run(_run_single_prompt(transport, root, prompt, config, **kwargs))
        else:
            # REPL mode: interactive loop
            session_id = kwargs.get("session_id") or str(uuid.uuid4())
            asyncio.run(transport.run(
                stdin=sys.stdin,
                stdout=sys.stdout,
                session_id=session_id,
                model=config.model,
                workspace=config.workspace,
            ))
    finally:
        transport.stop()


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

    async for event in root.runtime.run_turn(session, prompt):
        transport._render_event(sys.stdout, event)

    sys.stdout.write("\n")
    sys.stdout.flush()


def _run_server(root: CompositionRoot, **kwargs) -> None:
    """Run server mode."""
    from wisp.server.main import main as server_main
    host = kwargs.get("host", "0.0.0.0")
    port = kwargs.get("port", 8000)
    no_auth = kwargs.get("no_auth", False)
    server_main(host=host, port=port, no_auth=no_auth)


def _run_tui(root: CompositionRoot) -> None:
    """Run TUI mode."""
    transport = TUITransport()
    transport.start()
    try:
        logger.info("TUI mode")
        # TODO: launch Textual app and wire to transport
    finally:
        transport.stop()


# ── Headless mode ──────────────────────────────────────────────────

async def run_headless(prompt: str, model: str | None = None,
                       workspace: str | None = None,
                       session_id: str | None = None,
                       permission_mode: str = "full") -> dict:
    """Run a prompt headlessly and return structured result.

    Uses CompositionRoot + HeadlessTransport for consistent
    event collection across CLI, server, and background modes.
    """
    from wisp.transport.headless import HeadlessTransport

    config = WispConfig()
    if model:
        config.model = model
    if workspace:
        config.workspace = workspace
    config.permission_mode = permission_mode
    config.auto_approve = True
    config.show_thinking = True

    root = CompositionRoot(config)
    root.start()

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
        root.shutdown()
