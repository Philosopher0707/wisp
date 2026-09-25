"""REST-originated approvals, routed through the WebSocket channel (ADR-0057).

**Why this module exists.** ADR-0055 §7 named the gap: *"REST cannot ask a human, so a
REST caller gets the agent's no-approver behaviour rather than its approver behaviour."*
The WebSocket channel is the place to ask — but ADR-0055's brief called it *"already
capable"*, and driving it shows that is **half true**:

| direction | server emits / reads | both shipped clients emit / read | match |
|---|---|---|---|
| server → client | `approval_request` · `{approval_id, tool_call}` | `tool_approval_request` · `{call_id, name, arguments, reason}` | **no** |
| client → server | `tool_approval` · `{approved}` | `tool_approval` · `{id, approved, reason?}` | yes |

So the **response** direction already works and the **request** direction does not: no
client recognises the frame `WebSocketTransport.approve()` sends, which means the agent
path's WebSocket approval prompt has never rendered (recorded as a finding; reconciling
*that* is its own ADR, because it changes a live path's behaviour).

**What this module decides instead.** The REST round-trip speaks the vocabulary the
**shipped clients already read** — `tool_approval_request` with `call_id` — so it needs
**no client change**. It does not touch `WebSocketTransport`, its pending map, or the
agent path: the bridge owns its own correlation map, so the four non-violations
(ADR-0057 R10) hold by construction rather than by care.

**Fail-closed, never silent.** No connected client ⇒ deny. The brief forbids
"fall through with a warning" (*"it must not silently allow"*) and forbids hanging, so
the two rejected alternatives are stated in the ADR, not implemented here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: The frame both shipped clients already recognise.
#:
#: `wisp-desktop/src/renderer/hooks/useWebSocket.ts:103` and
#: `wisp/tui/data/ws_client.py:127` both branch on this string, and both read
#: `call_id`, `name`, `arguments` and `reason`. Adopting their vocabulary is what makes
#: the client change **zero**.
REST_APPROVAL_FRAME = "tool_approval_request"

#: The response frame, as both clients send it (`ApprovalPrompt.tsx:31`,
#: `useKeybindings.ts:117`, `ws_client.py:157`). The correlation key is `id`.
REST_APPROVAL_RESPONSE_FRAME = "tool_approval"

#: Bounded, named, defaulted. ADR-0036's bounded delay is the nearest precedent — the
#: delay is bounded and the fallback is *deny*, never "assume yes". Shorter than the
#: channel's own 60s because a REST request is holding an HTTP connection open.
REST_APPROVAL_TIMEOUT_S = 30.0

#: The executable-config actions. These persist something Wisp later *executes* — a hook
#: command, an MCP server, a plugin — which is why they are the ones that ask. Stated as
#: a set so the decision is readable, not inferred from a risk-table default.
REST_APPROVAL_ACTIONS: frozenset[str] = frozenset({
    "hooks.create",
    "mcp.add_server",
    "plugins.install",
})

#: The modes in which those actions ask.
#:
#: `full` does not ask — it relaxes approval, and it always did (`SecurityPolicy`'s own
#: `_check`/`policy_hard_deny` treat `full` as the permissive mode). `read_only` denies
#: outright and never reaches this module. `auto_edit` and `ask_all` ask.
REST_APPROVAL_MODES: frozenset[str] = frozenset({"auto_edit", "ask_all"})


def action_requires_rest_approval(action_name: str, mode: Any) -> bool:
    """True when `action_name` must be approved by a human over the WebSocket.

    Total: an unknown mode is treated as `auto_edit` (the configured default), matching
    `SecurityPolicy`'s own normalisation, and an unknown action never asks.
    """
    m = str(getattr(mode, "value", mode) or "auto_edit").lower()
    return action_name in REST_APPROVAL_ACTIONS and m in REST_APPROVAL_MODES


class ApprovalChannel(Protocol):
    """What the bridge needs from a connected client.

    Deliberately tiny: the bridge owns correlation and timeouts, so a channel is just
    "something that can deliver a frame". That is also what makes the round-trip
    testable without a real WebSocket.
    """

    async def send_approval_frame(self, frame: dict) -> None: ...


class ApprovalBridge:
    """Registry of connected approval channels, and the REST round-trip over them.

    One instance lives on the composition root (`root.approval_bridge`). The WebSocket
    route registers a channel on connect and unregisters on disconnect.

    **The bridge never waits unboundedly and never allows by default.** Every path that
    cannot obtain a decision returns `False`.
    """

    def __init__(self, *, timeout_s: float = REST_APPROVAL_TIMEOUT_S) -> None:
        self.timeout_s = float(timeout_s)
        self._channels: list[ApprovalChannel] = []
        self._pending: dict[str, asyncio.Future] = {}
        self._counter = 0

    # ── the registry ────────────────────────────────────────────────

    def register(self, channel: ApprovalChannel) -> None:
        if channel not in self._channels:
            self._channels.append(channel)

    def unregister(self, channel: ApprovalChannel) -> None:
        try:
            self._channels.remove(channel)
        except ValueError:
            pass

    def has_client(self) -> bool:
        """True when at least one client could answer an approval."""
        return bool(self._channels)

    @property
    def pending(self) -> int:
        """How many approvals are outstanding (diagnostics, and a test floor)."""
        return sum(1 for f in self._pending.values() if not f.done())

    # ── the round-trip ──────────────────────────────────────────────

    async def request_approval(self, *, name: str, arguments: dict | None = None,
                               reason: str = "",
                               timeout_s: float | None = None) -> bool:
        """Ask a connected client; return its decision.

        **Deny on every failure path** — no client, timeout, a dead channel, or an
        exception. The brief's constraint is explicit: the no-client case *"must not
        hang, and it must not silently allow."*
        """
        if not self._channels:
            logger.warning(
                "Denying %s over REST: no client connected to approve it", name)
            return False

        self._counter += 1
        call_id = f"rest:{self._counter}"
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[call_id] = future
        frame = {
            "type": REST_APPROVAL_FRAME,
            "call_id": call_id,
            "name": name,
            "arguments": dict(arguments or {}),
            "reason": reason,
        }
        budget = self.timeout_s if timeout_s is None else float(timeout_s)
        try:
            for channel in list(self._channels):
                try:
                    await channel.send_approval_frame(dict(frame))
                except Exception:
                    logger.debug("approval channel failed to deliver %s", call_id,
                                 exc_info=True)
            return await asyncio.wait_for(future, timeout=budget)
        except asyncio.TimeoutError:
            logger.warning("REST approval for %s timed out after %.1fs", name, budget)
            return False
        except Exception:
            logger.exception("REST approval request failed for %s", name)
            return False
        finally:
            self._pending.pop(call_id, None)

    def resolve(self, call_id: str, approved: bool) -> bool:
        """Resolve a pending REST approval. True when one was waiting.

        Called by the WebSocket route when a client sends `tool_approval`. The
        correlation key is the client's own `id` field, which is the `call_id` the
        bridge sent — so a response reaches the request it belongs to rather than
        whichever one happened to be first.
        """
        entry = self._pending.get(call_id)
        if entry is None or entry.done():
            return False
        entry.set_result(bool(approved))
        return True
