#!/usr/bin/env python3
"""Example: Custom transport — build your own I/O layer.

This example shows how to create a custom transport that consumes
AgentEvent instances from WispAgentCore and handles them however you want.

Run:
    python examples/custom_transport.py

Use case: Discord bot, Slack integration, web dashboard, etc.
"""

import json
from wisp import WispAgentCore, WispConfig


class JSONTransport:
    """A custom transport that prints events as JSON lines (JSONL).

    This is useful for:
    - Logging to a file
    - Streaming to a web frontend
    - Building custom UIs
    """

    def __init__(self, core: WispAgentCore):
        self.core = core

    def run(self, prompt: str) -> None:
        """Run one prompt and print each event as JSON."""
        import asyncio

        async def _consume():
            async for event in self.core.run(prompt):
                # Serialize to JSON
                print(json.dumps(event.to_dict(), default=str))

        asyncio.run(_consume())


def main():
    # Configure the agent
    config = WispConfig()
    config.model = "llama3.2"
    config.workspace = "."
    config.auto_approve = True  # Auto-approve for demo

    # Create core + custom transport
    core = WispAgentCore(config=config)
    transport = JSONTransport(core)

    # Run a prompt
    print("🤖 Running agent with JSON transport...\n")
    transport.run("What files are in the current directory?")


if __name__ == "__main__":
    main()
