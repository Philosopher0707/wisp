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

from typing import Iterator, Optional

from wisp.core.agent import WispAgentCore
from wisp.core.events import AgentEvent
from wisp.config import WispConfig
from wisp.async_utils import run_sync


class Wisp:
    """High-level synchronous wrapper around WispAgentCore.

    Usage:
        with Wisp(model="llama3.2", workspace=".") as agent:
            for event in agent.run("refactor auth.py"):
                print(event.text)

    Without context manager, call shutdown() when done to clean up MCP connections.
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

        self._core = WispAgentCore(config=config)
        self._skill_name = skill_name
        self._closed = False

        # Session handling
        if session_id:
            loaded = self._core._resolve_session(session_id)
            if loaded is not None:
                self._core.session = loaded
                self._core.messages = list(loaded.messages)
            else:
                raise ValueError(f"Session '{session_id}' not found.")
        elif auto_new_session:
            from wisp.session import Session
            self._core.session = Session.create(
                model=self._core.config.model,
                workspace=self._core.config.workspace or ".",
                first_prompt="SDK session",
            )

    def run(self, prompt: str) -> Iterator[AgentEvent]:
        """Run one prompt and yield all events synchronously.

        Blocks until the turn is complete. For async usage,
        use WispAgentCore directly.
        """
        if self._closed:
            raise RuntimeError("Wisp agent is closed. Create a new instance.")
        return self._run_impl(prompt)

    def _run_impl(self, prompt: str) -> Iterator[AgentEvent]:
        """Implementation of run() as a generator — validation done in run()."""
        system = self._core._build_system_prompt(self._skill_name)
        try:
            async_gen = self._core._arun(prompt, system=system)
            events = run_sync(async_gen)
            for event in events:
                yield event
        finally:
            self._core._save_session()

    def shutdown(self):
        """Shut down the agent and release resources (MCP connections, etc.)."""
        if self._closed:
            return
        self._closed = True
        try:
            self._core._save_session()
        except Exception:
            pass
        try:
            self._core.mcp.shutdown()
        except Exception:
            pass
        try:
            self._core.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()
        return False

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
