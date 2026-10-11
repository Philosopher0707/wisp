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
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
import weakref
from collections.abc import Callable
from typing import Any

from wisp.exceptions import ApprovalCancelled, ApprovalTimeout

logger = logging.getLogger(__name__)

MAX_REQUESTS_PER_CHILD = 25


class ApprovalBroker:
    """Holds the host's approver and serves the children of one orchestrator, one prompt at a time."""

    def __init__(self, host: Callable[[dict], Any]) -> None:
        self._host = host
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
        # The host's prompt shows the tool call; this line says whose it is. stderr, like the prompt's own chrome.
        sys.stderr.write(f"\n  ↳ subagent '{self._name}' is asking\n")
        sys.stderr.flush()
        return await self._broker.ask({**event, "subagent": self._name})
