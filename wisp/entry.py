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

logger = logging.getLogger(__name__)


class ServerTransport:
    """Placeholder for server transport."""

    def __init__(self, runtime: Any):
        self.runtime = runtime

    async def run(self) -> None:
        from wisp.server import main as server_main
        server_main()


def run_mode(mode: str, prompt: str | None = None, **kwargs) -> None:
    """Run Wisp in the specified mode.

    Args:
        mode: "cli", "server", or "tui"
        prompt: Optional initial prompt for CLI mode
    """
    config = WispConfig()
    root = CompositionRoot(config)

    try:
        root.start()

        if mode == "cli":
            transport = CLITransport(root.runtime)
            # TODO: implement full CLI run with prompt
            logger.info(f"CLI mode with prompt: {prompt}")
        elif mode == "server":
            transport = ServerTransport(root.runtime)
            asyncio.run(transport.run())
        else:
            raise ValueError(f"Unknown mode: {mode}")

    finally:
        root.shutdown()
