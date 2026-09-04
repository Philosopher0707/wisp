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


def decision_to_approval_decision(decision: Any, *, tool_name: str = "") -> "ApprovalDecision":
    """Convert an infra SecurityPolicy Decision to a contracts ApprovalDecision.

    Phase 2.4 seam (D6): attaches the canonical ToolRisk for the tool so
    callers get risk + verdict in one immutable value. Local import keeps
    this module's import edge to contracts lazy (contracts is stdlib-only,
    so the edge is cycle-safe either way).
    """
    from wisp.core.contracts import ApprovalDecision, risk_for_tool

    allowed = bool(getattr(decision, "allowed", False))
    reason = str(getattr(decision, "reason", "") or "")
    modified = getattr(decision, "modified_args", None)
    if allowed:
        reason = ""
    return ApprovalDecision(
        allowed=allowed,
        reason=reason,
        modified_args=dict(modified) if isinstance(modified, dict) else None,
        risk=risk_for_tool(tool_name),
    )


class ApprovalGate:
    """Security gate for tool calls with optional interactive override."""

    def __init__(self, security: Any, approval_handler: ApprovalHandler | None = None):
        self.security = security
        self.handler = approval_handler

    async def check_decision(
        self,
        event: dict,
        session: dict,
        *,
        approval_handler: ApprovalHandler | None = None,
    ) -> "ApprovalDecision":
        """Check if a tool call is allowed, returning an ApprovalDecision.

        Same policy as check(), but the verdict carries the canonical
        ToolRisk and any modified args. Interactive override via the
        approval handler flips a denial to an allowance (risk preserved).
        Fail-closed: security exceptions become denials.
        """
        from wisp.core.contracts import ApprovalDecision, risk_for_tool

        tool_name = str(event.get("name", ""))
        if self.security is None:
            return ApprovalDecision(allowed=True, risk=risk_for_tool(tool_name))

        action = Action(
            name=tool_name,
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
                            return ApprovalDecision(allowed=True, risk=risk_for_tool(tool_name))
                    except Exception as e:
                        logger.exception("Approval handler failed: %s", e)
                return decision_to_approval_decision(decision, tool_name=tool_name)
        except Exception as e:
            logger.exception("Security check failed — treating as deny: %s", e)
            return ApprovalDecision(allowed=False, reason=str(e), risk=risk_for_tool(tool_name))

        return ApprovalDecision(allowed=True, risk=risk_for_tool(tool_name))

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

        Back-compat facade over check_decision(): the tuple shape is
        frozen for existing callers.
        """
        decision = await self.check_decision(event, session, approval_handler=approval_handler)
        return decision.to_tuple()
