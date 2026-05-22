"""ApprovalGate — single module for tool-call approval gating.

Replaces 3 duplicated approval blocks in engine.py with one check() call.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Awaitable

from wisp.infra.security import Action, Context

logger = logging.getLogger(__name__)

ApprovalHandler = Callable[[dict], Awaitable[bool]]


class ApprovalGate:
    """Security gate for tool calls with optional interactive override."""

    def __init__(self, security: Any, approval_handler: ApprovalHandler | None = None):
        self.security = security
        self.handler = approval_handler

    async def check(
        self,
        event: dict,
        session: dict,
        *,
        approval_handler: ApprovalHandler | None = None,
    ) -> tuple[bool, str | None]:
        """Check if tool call is allowed.

        Returns (allowed, reason). reason is set when blocked.
        approval_handler overrides the instance-level handler for this call.
        """
        if self.security is None:
            return True, None

        action = Action(
            name=event.get("name", ""),
            args=event.get("arguments", {}),
        )
        context = Context(workspace=Path(session.get("workspace", ".")))

        try:
            decision = self.security.check(action, context)
            if not decision.allowed:
                handler = approval_handler or self.handler
                if handler is not None:
                    try:
                        approved = await handler(event)
                        if approved:
                            return True, None
                    except Exception as e:
                        logger.exception("Approval handler failed: %s", e)
                return False, decision.reason
        except Exception as e:
            logger.exception("Security check failed — treating as deny: %s", e)
            return False, str(e)

        return True, None
