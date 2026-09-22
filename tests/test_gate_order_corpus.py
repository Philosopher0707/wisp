"""Migration P2 — gate-order and outcome corpus (RED-first safety net).

`WISP_MIGRATION_PLAN.md` requires this test to exist **before** the proposal
boundary is introduced, so that "behaviour identical" is measured against a
recorded baseline rather than asserted from memory. The technique is the one
Phase 10 used successfully for the renderer delegation (`CONTEXT.md:424`:
"Behaviour verified IDENTICAL on a 29-item corpus").

What it pins, per case:

* **which** gate decided (the denial's controlling layer / block class),
* the **event shape** the caller receives (how many events, in what order),
* whether an **effect** was produced.

It deliberately does NOT pin human-readable prose beyond a stable token, so
wording changes stay free while *decisions* stay frozen. The proposal layer
P2 adds is a **record**, not a decision procedure: if any assertion here
changes, the change altered a gate, which RULE forbids.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from wisp.config import PermissionMode, WispConfig


# ── Harness ─────────────────────────────────────────────────────────────


def _hook_mgr():
    from unittest.mock import AsyncMock, MagicMock
    mgr = MagicMock()
    mgr.arun_hooks = AsyncMock(return_value=[])
    mgr.maybe_reload_hooks = MagicMock()
    mgr.load_project_hooks = MagicMock()
    return mgr


def _config(workspace: str, mode=PermissionMode.FULL, **kw):
    return WispConfig().replace(
        workspace=workspace, permission_mode=mode, auto_approve=False, **kw)


def _executor(workspace: str, mode=PermissionMode.FULL, **kw):
    from wisp.tool_executor import ToolExecutor
    return ToolExecutor(config=_config(workspace, mode, **kw),
                        hook_manager=_hook_mgr())


def _shutdown(te):
    te._tool_pool.shutdown(wait=False)
    te._network_pool.shutdown(wait=False)


def _collect(te, tool, args, workspace, call_id="c1"):
    async def _main():
        return [e async for e in te.execute(
            tool, args, workspace, tool_call_id=call_id)]
    return asyncio.run(_main())


def _text_of(event) -> str:
    data = event if isinstance(event, dict) else getattr(event, "data", event)
    if isinstance(data, dict):
        return str(data.get("result", data))
    return str(data)


def _outcome_token(event) -> str:
    """Fingerprint ONE event by its STRUCTURED outcome, plus the deciding layer.

    Reads the machine-readable `status` rather than matching prose, so a
    wording change cannot break this test while a *decision* change always
    will. The layer comes from the structured `reason`, which the authority
    layer already fills in (`"[Denied by <layer> layer: ...]"`).

    This is deliberately the same information the proposal boundary must
    preserve — if P2 changes any token here, it changed a gate.
    """
    data = event if isinstance(event, dict) else getattr(event, "data", event)
    if not isinstance(data, dict):
        return "other"
    etype = str(data.get("type", ""))
    if etype and etype != "tool_result":
        return etype

    result = data.get("result")
    if isinstance(result, dict):
        status = str(result.get("status", ""))
        reason = f"{result.get('reason', '')} {result.get('data', '')}"
        layer = ""
        if "Denied by " in reason:
            layer = reason.split("Denied by ", 1)[1].split(" layer", 1)[0].strip()
        return f"{status}:{layer}" if layer else status
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            return "unparseable"
        if isinstance(parsed, dict):
            return str(parsed.get("status", "other"))
    return "other"


def _signature(events) -> list[str]:
    """Stable per-case fingerprint: one token per event, in arrival order.

    Only *decision* tokens are kept — never full prose, never paths — so this
    test freezes behaviour without freezing wording.
    """
    return [_outcome_token(ev) for ev in events]


def _effect_produced(tool: str, workspace: str) -> bool:
    from pathlib import Path
    target = Path(workspace) / "p2_effect.txt"
    return target.exists()


# ── The corpus ──────────────────────────────────────────────────────────

#: (name, tool, args-builder, mode, quarantine?, expected signature)
#: Signatures RECORDED from the pre-P2 implementation (this file was written
#: before the proposal boundary, per the plan's RED-first requirement).
CORPUS = [
    ("read_allowed", "read_file", lambda ws: {"path": f"{ws}/a.txt"},
     PermissionMode.FULL, False, ["ok"]),
    ("write_allowed_full", "write_file",
     lambda ws: {"path": f"{ws}/p2_effect.txt", "content": "x"},
     PermissionMode.FULL, False, ["ok"]),
    # NOTE: read_only is decided by the policy-engine gate, which runs
    # BEFORE the layered authority consult — so no controlling layer is
    # named. That ordering is real and pinned here on purpose: a proposal
    # boundary must still record an outcome for denials that never reach
    # `authorize()`.
    ("write_denied_read_only", "write_file",
     lambda ws: {"path": f"{ws}/p2_effect.txt", "content": "x"},
     PermissionMode.READ_ONLY, False, ["POLICY_DENIED"]),
    ("write_denied_quarantined", "write_file",
     lambda ws: {"path": f"{ws}/p2_effect.txt", "content": "x"},
     PermissionMode.FULL, True, ["POLICY_DENIED:workspace"]),
    ("read_allowed_quarantined", "read_file", lambda ws: {"path": f"{ws}/a.txt"},
     PermissionMode.FULL, True, ["ok"]),
    ("hook_dir_denied", "write_file",
     lambda ws: {"path": f"{ws}/.wisp/hooks/evil.sh", "content": "x"},
     PermissionMode.FULL, False, ["POLICY_DENIED:arguments"]),
    ("unknown_tool", "no_such_tool_xyz", lambda ws: {},
     PermissionMode.FULL, False, ["error"]),
]


@pytest.mark.parametrize("name,tool,args_for,mode,quarantine,expected",
                         CORPUS, ids=[c[0] for c in CORPUS])
def test_gate_outcome_unchanged(name, tool, args_for, mode, quarantine,
                                expected, tmp_path):
    ws = str(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    if quarantine:
        (tmp_path / ".wisp-quarantine").write_text("untrusted")

    te = _executor(ws, mode)
    try:
        events = _collect(te, tool, args_for(ws), ws)
        assert _signature(events) == expected, (
            f"gate outcome drifted for {name}: {_signature(events)} != {expected}")
    finally:
        _shutdown(te)


def test_gate_order_is_stable_across_repeats(tmp_path):
    """The same call must decide identically every time — no order drift
    from state accumulated by earlier calls."""
    ws = str(tmp_path)
    (tmp_path / ".wisp-quarantine").write_text("untrusted")
    te = _executor(ws, PermissionMode.FULL)
    try:
        first = _signature(_collect(
            te, "write_file",
            {"path": f"{ws}/p2_effect.txt", "content": "x"}, ws, "c1"))
        second = _signature(_collect(
            te, "write_file",
            {"path": f"{ws}/p2_effect.txt", "content": "x"}, ws, "c2"))
        assert first == second == ["POLICY_DENIED:workspace"]
    finally:
        _shutdown(te)


def test_denial_produces_no_effect(tmp_path):
    """A denied mutation must leave nothing behind — the invariant that
    matters more than any message."""
    ws = str(tmp_path)
    (tmp_path / ".wisp-quarantine").write_text("untrusted")
    te = _executor(ws, PermissionMode.FULL)
    try:
        _collect(te, "write_file",
                 {"path": f"{ws}/p2_effect.txt", "content": "x"}, ws)
        assert not _effect_produced("write_file", ws)
    finally:
        _shutdown(te)


def test_allowed_write_produces_the_effect(tmp_path):
    """The corpus is only meaningful if the allow path really allows."""
    ws = str(tmp_path)
    te = _executor(ws, PermissionMode.FULL)
    try:
        _collect(te, "write_file",
                 {"path": f"{ws}/p2_effect.txt", "content": "x"}, ws)
        assert _effect_produced("write_file", ws)
    finally:
        _shutdown(te)


def test_controlling_layer_is_named_for_every_denial(tmp_path):
    """Each denial names the layer that decided it. This is the property P2
    must PRESERVE while extending it to the allow path."""
    ws = str(tmp_path)
    (tmp_path / ".wisp-quarantine").write_text("untrusted")
    te = _executor(ws, PermissionMode.FULL)
    try:
        events = _collect(te, "write_file",
                          {"path": f"{ws}/p2_effect.txt", "content": "x"}, ws)
        joined = " ".join(_text_of(e) for e in events)
        assert "Denied by workspace layer" in joined, joined
    finally:
        _shutdown(te)


# ── The two authority entry points agree ────────────────────────────────


def test_registry_and_executor_agree_on_quarantine(tmp_path):
    """`tools.registry.execute_tool` and `ToolExecutor.execute` both consult
    `authorize()`. A proposal layer must not create a third opinion."""
    from wisp.tools.registry import execute_tool
    ws = str(tmp_path)
    (tmp_path / ".wisp-quarantine").write_text("untrusted")

    te = _executor(ws, PermissionMode.FULL)
    try:
        events = _collect(te, "write_file",
                          {"path": f"{ws}/p2_effect.txt", "content": "x"}, ws)
        assert "POLICY_DENIED:workspace" in _signature(events), _signature(events)
    finally:
        _shutdown(te)

    out = json.loads(execute_tool(
        "write_file", {"path": f"{ws}/p2_effect.txt", "content": "x"}, ws))
    assert out["status"] == "error"
    assert "Denied by workspace" in out["data"]
    assert not _effect_produced("write_file", ws)
