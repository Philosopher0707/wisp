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


def _run_cli(root: CompositionRoot, prompt: str | None = None) -> None:
    """Run CLI mode."""
    import sys
    transport = CLITransport(root.runtime)
    transport.start()
    try:
        if prompt:
            logger.info("CLI mode with prompt: %s", prompt)
            # TODO: run single turn with prompt
        else:
            logger.info("CLI REPL mode")
            # TODO: run REPL loop
    finally:
        transport.stop()


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
