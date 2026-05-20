"""High-level synchronous SDK for Wisp.

Provides a simple, blocking API for developers who don't need async control.

Example:
    from wisp import Wisp

    with Wisp(model="llama3.2", workspace=".") as agent:
        for event in agent.run("refactor auth.py"):
            print(f"[{event.type}] {event.text}")

For conversation across multiple prompts, pass auto_new_session=False:

    with Wisp(model="llama3.2", workspace=".", auto_new_session=False) as agent:
        agent.run("read the code in src/")
        agent.run("now refactor it")
"""

from __future__ import annotations

import asyncio
from typing import Iterator, Optional

from wisp.core.events import AgentEvent
from wisp.config import WispConfig
from wisp.transport.headless import HeadlessTransport
from wisp.composition import CompositionRoot


def _run_async(coro):
    """Run a coroutine, handling nested event loops gracefully."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return loop.run_until_complete(coro)


class Wisp:
    """High-level synchronous wrapper using CompositionRoot + HeadlessTransport.

    Usage:
        with Wisp(model="llama3.2", workspace=".") as agent:
            for event in agent.run("refactor auth.py"):
                print(event.text)

    Without context manager, call shutdown() when done to clean up.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        workspace: Optional[str] = None,
        skill_name: Optional[str] = None,
        session_id: Optional[str] = None,
        auto_approve: bool = False,
        show_thinking: bool = False,
        max_iterations: int = 10,
        temperature: Optional[float] = None,
        auto_new_session: bool = True,
    ):
        config = WispConfig()
        if model:
            config.model = model
        if workspace:
            config.workspace = workspace
        if temperature is not None:
            config.temperature = temperature
        config.auto_approve = auto_approve
        config.show_thinking = show_thinking
        config.max_iterations = max_iterations

        self._config = config
        self._skill_name = skill_name
        self._closed = False
        self._root: Optional[CompositionRoot] = None
        self._session_id = session_id
        self._auto_new_session = auto_new_session

    def run(self, prompt: str) -> Iterator[AgentEvent]:
        """Run one prompt and yield all events synchronously.

        Blocks until the turn is complete.
        """
        if self._closed:
            raise RuntimeError("Wisp agent is closed. Create a new instance.")

        # Create root on first run
        if self._root is None:
            self._root = CompositionRoot(self._config)
            self._root.start()

        transport = HeadlessTransport()
        transport.start()

        async def _run():
            session = await self._root.runtime.get_or_create_session(
                session_id=self._session_id or "sdk",
                model=self._config.model,
                workspace=self._config.workspace,
            )

            async for event in self._root.runtime.run_turn(session, prompt):
                await transport.send(event)

        _run_async(_run())

        # Yield collected events as AgentEvent objects
        for event_dict in transport.events:
            yield AgentEvent(
                type=event_dict.get("type", "unknown"),
                data={k: v for k, v in event_dict.items() if k not in ("type", "timestamp")},
            )

    def shutdown(self):
        """Shut down the agent and release resources."""
        if self._closed:
            return
        self._closed = True
        if self._root is not None:
            self._root.shutdown()
            self._root = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()
        return False

    @property
    def session_id(self) -> str:
        """Return the current session ID, or empty string if no session."""
        return self._session_id or ""

    @property
    def messages(self) -> list[dict]:
        """Return the current conversation messages."""
        if self._root is None:
            return []
        # Load from store via runtime
        session = self._root.store.load_session(self._session_id or "sdk")
        if session is not None:
            return list(session.get("messages", []))
        return []
