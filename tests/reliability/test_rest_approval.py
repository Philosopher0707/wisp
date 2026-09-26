"""REST approval through the WebSocket channel, pinned (ADR-0057).

The decision has four parts, and each has a property here:

* **the frame** is the vocabulary both shipped clients already read, so the client
  change is zero (a frame neither client reads is the defect ADR-0057 measured);
* **the round-trip** resolves by correlation key, and every failure path denies;
* **the trigger** is per-route and per-mode, and the flag OFF means today's code;
* **the four non-violations** — the agent's gate chain, the two decision models, the
  turn path's approval model, and the turn predicate / floor guard / precedence.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent


# ── the round-trip ──────────────────────────────────────────────────────────

class _StubChannel:
    """A connected client that answers (or does not). No real WebSocket needed."""

    def __init__(self, answer: bool | None = None) -> None:
        self.frames: list[dict] = []
        self._answer = answer
        self.bridge = None

    async def send_approval_frame(self, frame: dict) -> None:
        self.frames.append(frame)
        if self._answer is not None and self.bridge is not None:
            self.bridge.resolve(frame["call_id"], self._answer)


def _bridge(**kw):
    from wisp.server.approval_bridge import ApprovalBridge
    return ApprovalBridge(**kw)


def test_a_connected_client_can_approve():
    b = _bridge()
    ch = _StubChannel(answer=True)
    ch.bridge = b
    b.register(ch)

    approved = asyncio.run(b.request_approval(name="hooks.create",
                                             arguments={"name": "x"}))
    assert approved is True
    assert len(ch.frames) == 1, "exactly one frame per request (floor + cardinality)"
    assert b.pending == 0, "the pending entry is cleared on every path"


def test_a_connected_client_can_deny():
    b = _bridge()
    ch = _StubChannel(answer=False)
    ch.bridge = b
    b.register(ch)
    assert asyncio.run(b.request_approval(name="mcp.add_server")) is False


def test_no_client_denies_and_does_not_hang():
    """The brief's constraint: *"it must not hang, and it must not silently allow."*"""
    b = _bridge(timeout_s=5.0)
    assert b.has_client() is False
    loop = asyncio.new_event_loop()
    try:
        approved = loop.run_until_complete(
            asyncio.wait_for(b.request_approval(name="plugins.install"), timeout=1.0))
    finally:
        loop.close()
    assert approved is False, "no client must DENY, not allow and not hang"


def test_a_silent_client_times_out_to_deny():
    b = _bridge(timeout_s=0.01)
    ch = _StubChannel(answer=None)      # never answers
    ch.bridge = b
    b.register(ch)
    assert asyncio.run(b.request_approval(name="hooks.create")) is False
    assert b.pending == 0


def test_a_disconnected_client_stops_being_asked():
    b = _bridge()
    ch = _StubChannel(answer=True)
    ch.bridge = b
    b.register(ch)
    b.unregister(ch)
    assert b.has_client() is False
    assert asyncio.run(b.request_approval(name="hooks.create")) is False


def test_the_correlation_key_resolves_the_right_request():
    """Two concurrent requests; only the named one resolves."""
    b = _bridge()
    ch = _StubChannel(answer=None)
    ch.bridge = b
    b.register(ch)

    async def scenario():
        first = asyncio.ensure_future(b.request_approval(name="a"))
        second = asyncio.ensure_future(b.request_approval(name="b"))
        await asyncio.sleep(0)
        assert len(ch.frames) == 2
        # Resolve the SECOND frame's id; the first must stay pending.
        assert b.resolve(ch.frames[1]["call_id"], True) is True
        await asyncio.sleep(0)
        assert second.done()
        assert b.resolve(ch.frames[0]["call_id"], False) is True
        return await first, await second

    first, second = asyncio.new_event_loop().run_until_complete(scenario())
    assert (first, second) == (False, True), "the id must select the request"


def test_an_unknown_correlation_key_resolves_nothing():
    b = _bridge()
    assert b.resolve("rest:does-not-exist", True) is False


# ── the frame is the clients' vocabulary ────────────────────────────────────

def test_the_frame_is_the_one_both_clients_read():
    """**The finding ADR-0057 rests on.** The server's own frame
    (`approval_request` / `{approval_id, tool_call}`) is read by no client, so
    adopting the clients' vocabulary is what makes the client change zero.

    A text check, not an AST one: the clients are TypeScript, so there is no Python
    tree to parse. It is deliberately narrow — the string AND the field the clients
    read — and it fails if either client stops reading them.
    """
    from wisp.server.approval_bridge import REST_APPROVAL_FRAME
    assert REST_APPROVAL_FRAME == "tool_approval_request"

    clients = {
        "wisp-desktop/src/renderer/hooks/useWebSocket.ts": "tool_approval_request",
        "wisp/tui/data/ws_client.py": "tool_approval_request",
    }
    assert clients, "floor"
    for rel, expected in clients.items():
        src = (REPO / rel).read_text(encoding="utf-8")
        assert expected in src, (
            f"{rel} no longer branches on {expected!r} — the bridge's frame is no "
            "longer the clients' vocabulary, so the round-trip needs a client change"
        )
    # And the field the response correlates on.
    desktop = (REPO / "wisp-desktop/src/renderer/components/ApprovalPrompt.tsx").read_text(
        encoding="utf-8")
    assert "tool_approval" in desktop and "approved" in desktop, (
        "the desktop client no longer sends a tool_approval frame — the response "
        "direction ADR-0057 relies on has changed"
    )


def test_the_timeout_is_bounded_and_named():
    from wisp.server.approval_bridge import REST_APPROVAL_TIMEOUT_S
    assert 0 < REST_APPROVAL_TIMEOUT_S <= 120, (
        "the REST approval timeout must be bounded — an unbounded wait holds an HTTP "
        "connection open forever"
    )


# ── the trigger: per route, per mode ────────────────────────────────────────

def test_the_trigger_is_the_three_executable_config_actions():
    from wisp.server.approval_bridge import (REST_APPROVAL_ACTIONS,
                                             REST_APPROVAL_MODES,
                                             action_requires_rest_approval)
    assert REST_APPROVAL_ACTIONS == {"hooks.create", "mcp.add_server",
                                     "plugins.install"}
    assert REST_APPROVAL_MODES == {"auto_edit", "ask_all"}

    for action in sorted(REST_APPROVAL_ACTIONS):
        assert action_requires_rest_approval(action, "auto_edit") is True
        assert action_requires_rest_approval(action, "ask_all") is True
        # `full` relaxes approval, and always did.
        assert action_requires_rest_approval(action, "full") is False
        # `read_only` denies outright and never reaches the bridge.
        assert action_requires_rest_approval(action, "read_only") is False
    # A file write is not an executable-config action.
    assert action_requires_rest_approval("write_file", "auto_edit") is False


def test_an_unknown_mode_normalises_like_security_policy():
    from wisp.server.approval_bridge import action_requires_rest_approval
    assert action_requires_rest_approval("hooks.create", "") is True
    assert action_requires_rest_approval("hooks.create", None) is True


def test_each_route_asks_after_the_policy_gate():
    """Policy first (so `read_only` never prompts), then the approval request.

    Parsed, not scanned: a string scan would read the comment that *describes* the
    order as readily as the order itself.
    """
    routes = {
        "wisp/server/routes/hooks.py": "hooks.create",
        "wisp/server/routes/mcp.py": "mcp.add_server",
        "wisp/server/routes/plugins.py": "plugins.install",
    }
    assert routes, "floor"
    for rel, action in routes.items():
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        calls: dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ("require_tool_allowed", "require_rest_approval"):
                    calls.setdefault(node.func.id, node.lineno)
        assert set(calls) == {"require_tool_allowed", "require_rest_approval"}, (
            f"{rel} no longer calls both gates: {sorted(calls)}"
        )
        assert calls["require_tool_allowed"] < calls["require_rest_approval"], (
            f"{rel} asks for approval before the policy gate — a read_only session "
            "would prompt instead of denying"
        )


def test_the_flag_off_preserves_todays_behaviour():
    """With `WISP_REST_APPROVAL` OFF, `require_rest_approval` returns immediately."""
    from wisp.config import WispConfig
    cfg = WispConfig()
    assert cfg.rest_approval is False, "the flag must default OFF"

    class _Root:
        config = cfg
        approval_bridge = None

    class _State:
        root = _Root()

    class _App:
        state = _State()

    class _Req:
        app = _App()

    from wisp.server.deps import require_rest_approval
    # No bridge, flag off -> returns without raising.
    asyncio.run(require_rest_approval(_Req(), "hooks.create", {"name": "x"}, "."))


def test_the_flag_on_without_a_bridge_denies():
    """Flag ON and no bridge is a misconfiguration, not a silent allow."""
    from fastapi import HTTPException

    from wisp.config import WispConfig
    from wisp.server.deps import require_rest_approval

    cfg = WispConfig()
    object.__setattr__(cfg, "rest_approval", True)

    class _Root:
        config = cfg
        approval_bridge = None

    class _State:
        root = _Root()

    class _App:
        state = _State()

    class _Req:
        app = _App()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_rest_approval(_Req(), "hooks.create", {"name": "x"}, "."))
    assert exc.value.status_code == 403


# ── the four non-violations (ADR-0057 R10) ──────────────────────────────────

def _tool_executor_execute_ast() -> ast.AST:
    tree = ast.parse((REPO / "wisp/tool_executor.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ToolExecutor":
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and sub.name == "execute":
                    return sub
    raise AssertionError("ToolExecutor.execute not found — the guard is stale")


def test_the_agent_gate_chain_is_unchanged():
    """R10.1 — `policy_hard_deny` -> `authorize` -> the approval test, in order."""
    fn = _tool_executor_execute_ast()
    first: dict[str, int] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name in ("policy_hard_deny", "authorize", "_get_write_tools"):
            first.setdefault(name, node.lineno)
    assert set(first) == {"policy_hard_deny", "authorize", "_get_write_tools"}, (
        f"a gate left ToolExecutor.execute: {sorted(first)}"
    )
    assert first["policy_hard_deny"] < first["authorize"] < first["_get_write_tools"]


def test_the_two_decision_models_are_unchanged():
    """R10.2 — signatures and decision fields."""
    from wisp.auth.decision import AuthorizationDecision, authorize
    from wisp.infra.security import SecurityPolicy

    assert list(inspect.signature(authorize).parameters) == [
        "principal", "tool_name", "args", "workspace_trust",
        "permission_mode", "sensitivity", "effective_policy",
    ]
    assert {"allowed", "reason", "controlling_layer", "approval_required"} <= set(
        AuthorizationDecision.__dataclass_fields__)
    assert list(inspect.signature(SecurityPolicy.check).parameters) == [
        "self", "action", "context",
    ]


def test_the_turn_paths_approval_model_is_unchanged():
    """R10.3 — the third model: `_get_write_tools` + `_needs_forced_approval`.

    ADR-0055 §1.3 established that this is *not* `authorize().approval_required`;
    ADR-0057 must not quietly move it.
    """
    from wisp.tool_executor import _get_write_tools
    write_tools = _get_write_tools(None)
    assert write_tools, "floor"
    assert {"write_file", "edit_file", "run_bash"} <= write_tools
    # The three REST-only names are still absent — ADR-0055's premise.
    for action in ("hooks.create", "mcp.add_server", "plugins.install"):
        assert action not in write_tools


def test_the_turn_predicate_floor_guard_and_precedence_are_untouched():
    """R10.4 — the three things every mission in this chain must not move."""
    from wisp.core.goal import PRECEDENCE
    from wisp.core.verification import VerificationFloorGuard

    assert len(PRECEDENCE) == 8, "the canonical precedence table is eight rows 0-7"
    assert [row[0] for row in PRECEDENCE] == list(range(8)), (
        "the precedence row indices changed — ADR-0049 R1 makes 0-7 canonical"
    )
    assert [row[2] for row in PRECEDENCE] == [
        "frozen — never rewritten (ADR-0020)",
        "CANCELLED",
        "ESCALATED_TO_HUMAN",
        "GOAL_FAILED",
        "GOAL_FAILED",
        "GOAL_STAGNATED",
        "GOAL_MET",
        "GOAL_UNVERIFIED",
    ], "the precedence outcomes changed by content — that is a new ADR"
    for member in ("rejection", "resolved", "wrote_code"):
        assert hasattr(VerificationFloorGuard, member), (
            f"VerificationFloorGuard lost {member!r}"
        )
    # `turn_succeeded` is still a projection of the terminal authority.
    runtime_src = (REPO / "wisp/core/runtime.py").read_text(encoding="utf-8")
    assert "terminal_outcome_from_evidence" in runtime_src, (
        "turn_succeeded is no longer derived from the terminal authority"
    )
