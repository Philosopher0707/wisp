"""ApprovalGate — single module for tool-call approval gating.

Replaces 3 duplicated approval blocks in engine.py with one check() call.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from wisp.exceptions import ApprovalCancelled, ApprovalTimeout
from wisp.infra.security import Action, Context

if TYPE_CHECKING:
    from wisp.core.contracts import ApprovalDecision

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

        Control-plane contract (13F.1): ALLOW executes without prompting;
        REQUIRE_APPROVAL prompts (approval flips it to an allowance);
        DENY is hard — it never invokes the handler, so no `y` can
        override policy. Fail-closed throughout: handler absence,
        timeout, errors, and cancellation all become denials.
        """
        from wisp.core.contracts import ApprovalDecision, risk_for_tool
        from wisp.core.events import (
            DENIAL_APPROVAL_TIMEOUT,
            DENIAL_CANCELLED,
            DENIAL_POLICY_DENIED,
            DENIAL_USER_DENIED,
        )

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
                # Hard DENY: authoritative, no approval prompt (13F.1 R1).
                verdict = decision_to_approval_decision(decision, tool_name=tool_name)
                return ApprovalDecision(allowed=False, reason=verdict.reason,
                                        modified_args=verdict.modified_args,
                                        risk=verdict.risk,
                                        denial=DENIAL_POLICY_DENIED)
            if not decision.approval_required:
                return ApprovalDecision(allowed=True, risk=risk_for_tool(tool_name))
            # REQUIRE_APPROVAL: the only path that may consult a human.
            handler = approval_handler or self.handler
            if handler is None:
                return ApprovalDecision(
                    allowed=False,
                    reason=f"approval required for {tool_name or 'tool'} "
                           "but no approval handler is available",
                    risk=risk_for_tool(tool_name),
                    denial=DENIAL_POLICY_DENIED,
                )
            try:
                approved = await handler(event)
                if approved:
                    return ApprovalDecision(allowed=True, risk=risk_for_tool(tool_name))
            except (asyncio.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit):
                # Genuine external cancellation — propagate untouched.
                # (Cancellation-first: only these types carry it; the
                # handler must never synthesize them from input.)
                raise
            except ApprovalCancelled as cancelled:
                # User verdict, not an interruption: record it as an
                # explicit denial so history stays protocol-consistent
                # and the model sees an outcome instead of replaying.
                return ApprovalDecision(
                    allowed=False,
                    reason=f"cancelled by user at approval ({cancelled.tool_name or tool_name})",
                    risk=risk_for_tool(tool_name),
                    denial=DENIAL_CANCELLED,
                )
            except ApprovalTimeout as timed_out:
                # Approval lapsed without a verdict: fail closed with a
                # distinct reason so it never reads as an ordinary deny.
                return ApprovalDecision(
                    allowed=False,
                    reason=f"approval timed out for {timed_out.tool_name or tool_name}",
                    risk=risk_for_tool(tool_name),
                    denial=DENIAL_APPROVAL_TIMEOUT,
                )
            except Exception as e:
                logger.exception("Approval handler failed: %s", e)
            return ApprovalDecision(
                allowed=False,
                reason=f"not approved: {tool_name or 'tool'} requires approval",
                risk=risk_for_tool(tool_name),
                denial=DENIAL_USER_DENIED,
            )
        except (asyncio.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit):
            raise
        except ApprovalCancelled as cancelled:
            return ApprovalDecision(
                allowed=False,
                reason=f"cancelled by user at approval ({cancelled.tool_name or tool_name})",
                risk=risk_for_tool(tool_name),
                denial=DENIAL_CANCELLED,
            )
        except Exception as e:
            logger.exception("Security check failed — treating as deny: %s", e)
            return ApprovalDecision(allowed=False, reason=str(e),
                                    risk=risk_for_tool(tool_name),
                                    denial=DENIAL_POLICY_DENIED)

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
