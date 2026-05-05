"""High-level synchronous SDK for Wisp.

Provides a simple, blocking API for developers who don't need async control.

Example:
    from wisp import Wisp

    agent = Wisp(model="llama3.2", workspace=".")
    for event in agent.run("refactor auth.py"):
        print(f"[{event.type}] {event.text}")
"""

from __future__ import annotations

import asyncio
from typing import Iterator, Optional

from wisp.core.agent import WispAgentCore
from wisp.core.events import AgentEvent
from wisp.config import WispConfig


class Wisp:
    """High-level synchronous wrapper around WispAgentCore.

    Usage:
        agent = Wisp(model="llama3.2", workspace=".")
        for event in agent.run("refactor auth.py"):
            print(event.text)
    """

    def __init__(
        self,
        model: Optional[str] = None,
        workspace: Optional[str] = None,
        auto_approve: bool = False,
        show_thinking: bool = False,
        max_iterations: int = 10,
    ):
        config = WispConfig()
        if model:
            config.model = model
        if workspace:
            config.workspace = workspace
        config.auto_approve = auto_approve
        config.show_thinking = show_thinking
        config.max_iterations = max_iterations

        self._core = WispAgentCore(config=config)

    def run(self, prompt: str) -> Iterator[AgentEvent]:
        """Run one prompt and yield all events synchronously.

        This blocks until the turn is complete. For async usage,
        use WispAgentCore directly.
        """
        loop = asyncio.new_event_loop()
        try:
            async_gen = self._core.run(prompt)
            while True:
                try:
                    event = loop.run_until_complete(async_gen.__anext__())
                    yield event
                except StopAsyncIteration:
                    break
        finally:
            loop.close()

    @property
    def session_id(self) -> str:
        """Return the current session ID, or empty string if no session."""
        if self._core.session:
            return self._core.session.id
        return ""

    @property
    def messages(self) -> list[dict]:
        """Return the current conversation messages."""
        return list(self._core.messages)
