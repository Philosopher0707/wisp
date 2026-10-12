"""A subagent's request for a yes, relayed to the person who is running the parent process.

A child has no keyboard. Its authority used to be its contract's `auto_approve` and nothing else, so in `auto_edit` mode every `edit_file`, `write_file` and
`run_tests` answered `NO_APPROVER` and graph workers, `/spawn` and `/swarm` children could only read. The CLI now hands the orchestrator the transport's own
approver, and this module is the only thing between the two:

* **Provenance.** The request carries the child's name, so the person is not shown an anonymous tool call.
* **One prompt at a time.** Children run concurrently and the terminal has one prompt; requests are served in turn (one lock per event loop: the graph runs on
  its own loop, the REPL on another).
* **A bound.** A child that keeps asking is refused after `MAX_REQUESTS_PER_CHILD` requests without bothering the person again.
* **Fail closed.** A handler that raises is a no. The person's *verdicts* (`ApprovalCancelled`, `ApprovalTimeout`) and a genuine cancellation (Ctrl-C) are passed
  on untouched: the approval gate records the first two as distinct denials and must see the third.

The relay never approves anything by itself: with no host handler there is no relay and the child is refused, as before.

Two ways to name the host, and they do not mix:

* **The orchestrator's own** (`SubagentRunner.set_approval_handler`): one person, one terminal. The CLI.
* **The turn's** (`turn_approver`): the server and the TUI. One orchestrator serves every WebSocket connection, so a handler on the orchestrator would send one
  person's child prompt to whoever connected last. A turn knows who is running it; it publishes its approver in a context variable for the length of the turn, and a
  child's task (created while the turn runs) inherits it. Nothing outside the turn sees it, a background child never asks, and when both exist the orchestrator's wins.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import sys
import weakref
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import Any

from wisp.exceptions import ApprovalCancelled, ApprovalTimeout

logger = logging.getLogger(__name__)

MAX_REQUESTS_PER_CHILD = 25


class ApprovalBroker:
    """Holds the host's approver and serves the children of one orchestrator, one prompt at a time."""

    def __init__(self, host: Callable[[dict], Any], announce: bool = True) -> None:
        self._host = host
        #: Write "subagent 'x' is asking" to stderr before the host's prompt: the terminal's chrome. A host that shows whose request it is itself (the server's
        #: frame, the TUI's modal) turns it off, because stderr is its log or its screen.
        self.announce = announce
        self._locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakKeyDictionary()

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = self._locks[loop] = asyncio.Lock()
        return lock

    async def ask(self, event: dict) -> bool:
        async with self._lock():
            try:
                verdict = self._host(event)
                if inspect.isawaitable(verdict):
                    verdict = await verdict
            except (asyncio.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit, ApprovalCancelled, ApprovalTimeout):
                raise
            except Exception:
                logger.warning("approval handler failed for a subagent request; treating it as a no", exc_info=True)
                return False
        return verdict is True

    def for_child(self, name: str, limit: int = MAX_REQUESTS_PER_CHILD) -> ChildApproval:
        return ChildApproval(self, name, limit)


class ChildApproval:
    """The approval handler one child is given: `await handler(event) -> bool`."""

    def __init__(self, broker: ApprovalBroker, name: str, limit: int) -> None:
        self._broker = broker
        self._name = str(name)[:128]
        self._limit = limit
        self._asked = 0

    async def __call__(self, event: dict) -> bool:
        self._asked += 1
        if self._asked > self._limit:
            logger.warning("subagent %s asked for approval more than %d times; refusing without asking", self._name, self._limit)
            return False
        if self._broker.announce:
            # The host's prompt shows the tool call; this line says whose it is. stderr, like the prompt's own chrome.
            sys.stderr.write(f"\n  ↳ subagent '{self._name}' is asking\n")
            sys.stderr.flush()
        return await self._broker.ask({**event, "subagent": self._name})


_TURN_BROKER: ContextVar[ApprovalBroker | None] = ContextVar("wisp_turn_approval_broker", default=None)


@contextlib.contextmanager
def turn_approver(host: Callable[[dict], Any] | None) -> Iterator[None]:
    """For the length of one parent turn, the children it starts ask `host` (an `await host(event) -> bool`). `None` publishes nothing.

    Use it around the loop that drives the turn, in the task that drives it: `with turn_approver(h): async for ev in runtime.run_turn(...)`. One broker per turn, so a
    turn's children are asked one at a time and each child is bounded (`ApprovalBroker`).
    """
    token = _TURN_BROKER.set(ApprovalBroker(host, announce=False) if host is not None else None)
    try:
        yield
    finally:
        _TURN_BROKER.reset(token)


def current_turn_broker() -> ApprovalBroker | None:
    """The broker of the turn this task belongs to, or None outside any turn that published one."""
    return _TURN_BROKER.get()
