"""Approval-handler dual-protocol contract pins.

Two handler signatures coexist (skill case file #8), each owned by a
different layer:

* ENGINE GATE (`wisp/core/approval_gate.py`): ``await handler(event) -> bool``
  — single tool_call-event dict argument.
* EXECUTOR LEGACY PATH (`wisp/tool_executor.py`): ``await
  handler(name, args, reason) -> (approved, modified)`` — three arguments,
  tuple return; ``modified`` args are applied before dispatch.

Adaptation lives in exactly two places and nowhere else:

* ``WispAgentCore._memoize_handler`` — dual-arity memo shared by gate +
  executor within one turn (collapses truthy ``(False, None)`` tuples,
  which are truthy and would otherwise flip denials into approvals).
* ``WispAgentCore._execute_tool`` — wraps a gate-style handler for the
  executor as ``(approved, None)``: the gate protocol has no modified-args
  channel, so arg rewrites do NOT survive this adaptation (pinned below).

Production transports implement the single-arg form; the gate is what
consults them. Direct ``ToolExecutor.execute`` callers must supply the
3-arg form (or None). These tests pin the OBSERVED contract so a future
"unification" cannot silently change approval semantics.
"""
from __future__ import annotations

import pytest

from wisp.config import WispConfig
from wisp.core.approval_gate import ApprovalGate
from wisp.infra.security import SecurityPolicy
from wisp.tool_executor import ToolExecutor


def _executor():
    return ToolExecutor(config=WispConfig(), hook_manager=None, mcp=None,
                        file_lock=None, lsp_manager=None,
                        subagent_orchestrator=None, extensions=None)


def _flat(ev):
    return ev if isinstance(ev, dict) else {"type": str(getattr(ev, "type", "")),
                                            **dict(getattr(ev, "data", {}))}


def _results(yielded):
    return [_flat(e) for e in yielded
            if _flat(e).get("type") == "tool_result"]


# NOTE: gate tests use ``fanout`` because it is REQUIRE_APPROVAL (not
# hard-DENY) under the default AUTO_EDIT policy — the gate actually
# consults the handler. Plain writes are policy-allowed in this mode and
# would never reach the handler (the executor prompts for those instead).


# ── Gate protocol: (event) -> bool ──

@pytest.mark.asyncio
async def test_gate_passes_event_dict_to_single_arg_handler(tmp_path):
    seen = []

    async def handler(event):
        seen.append(event)
        return True

    gate = ApprovalGate(SecurityPolicy())
    event = {"type": "tool_call", "name": "fanout",
             "arguments": {"tasks": ["a"]}, "id": "call_G"}
    allowed, _ = await gate.check(dict(event), {"workspace": str(tmp_path)},
                                  approval_handler=handler)
    assert allowed is True
    assert seen and seen[0].get("name") == "fanout"
    assert seen[0].get("arguments") == {"tasks": ["a"]}


@pytest.mark.asyncio
async def test_gate_denial_on_false(tmp_path):
    async def decline(event):
        return False

    gate = ApprovalGate(SecurityPolicy())
    allowed, reason = await gate.check(
        {"type": "tool_call", "name": "fanout",
         "arguments": {"tasks": ["a"]}},
        {"workspace": str(tmp_path)}, approval_handler=decline)
    assert allowed is False
    assert reason


# ── Executor protocol: (name, args, reason) -> (approved, modified) ──

@pytest.mark.asyncio
async def test_executor_passes_three_args_and_applies_modified(tmp_path):
    seen = {}

    async def handler(name, args, reason):
        seen["call"] = (name, dict(args), reason)
        return True, {"path": "rewritten.txt", "content": "via-modified"}

    yielded = [e async for e in _executor().execute(
        "write_file", {"path": "orig.txt", "content": "orig"},
        str(tmp_path), tool_call_id="call_M", approval_handler=handler)]

    name, args, reason = seen["call"]
    assert name == "write_file"
    assert args == {"path": "orig.txt", "content": "orig"}
    assert isinstance(reason, str) and reason
    assert (tmp_path / "rewritten.txt").read_text() == "via-modified"
    assert not (tmp_path / "orig.txt").exists()
    assert _results(yielded), "approval path must still yield a tool_result"


@pytest.mark.asyncio
async def test_executor_denial_yields_user_denied_without_executing(tmp_path):
    async def decline(name, args, reason):
        return False, None

    yielded = [e async for e in _executor().execute(
        "write_file", {"path": "nope.txt", "content": "x"},
        str(tmp_path), tool_call_id="call_D", approval_handler=decline)]
    assert not (tmp_path / "nope.txt").exists()
    assert any("USER_DENIED" in str(d.get("result", ""))
               for d in _results(yielded))


# ── Core adaptation: gate-style handler through _execute_tool ──

def _core(tmp_path):
    from wisp.core.engine import WispAgentCore
    from wisp.infra.extensions import ExtensionHost

    return WispAgentCore(
        provider=object(),
        security=SecurityPolicy(),
        extensions=ExtensionHost(),
        config=WispConfig().replace(workspace=str(tmp_path)),
        tool_executor=_executor(),
    )


@pytest.mark.asyncio
async def test_core_adapts_gate_style_handler_for_executor(tmp_path):
    """A single-arg ``(event) -> bool`` handler works through
    ``core._execute_tool``: approval honored, real write lands."""
    core = _core(tmp_path)

    async def approve(event):
        assert set(event) >= {"name", "arguments"}
        return True

    event = {"name": "write_file",
             "arguments": {"path": "core.txt", "content": "core-ok"},
             "id": "call_C"}
    yielded = [e async for e in core._execute_tool(
        dict(event), {"workspace": str(tmp_path)}, approval_handler=approve)]
    assert (tmp_path / "core.txt").read_text() == "core-ok"
    assert _results(yielded)


@pytest.mark.asyncio
async def test_core_adaptation_drops_modified_args_by_design(tmp_path):
    """The gate protocol has no modified-args channel: a gate-style
    handler returning ``(True, {...})`` approves (tuple is truthy) but
    the rewrite is NOT applied — original args execute. Pinned so any
    future unification makes this loss explicit, not silent."""

    async def approve_with_rewrite(event):
        return True, {"path": "ghost.txt", "content": "ghost"}

    core = _core(tmp_path)
    event = {"name": "write_file",
             "arguments": {"path": "real.txt", "content": "real"},
             "id": "call_R"}
    yielded = [e async for e in core._execute_tool(
        dict(event), {"workspace": str(tmp_path)},
        approval_handler=approve_with_rewrite)]
    assert (tmp_path / "real.txt").read_text() == "real"
    assert not (tmp_path / "ghost.txt").exists()
    assert _results(yielded)


@pytest.mark.asyncio
async def test_core_gate_style_denial_blocks_write(tmp_path):
    async def decline(event):
        return False

    core = _core(tmp_path)
    event = {"name": "write_file",
             "arguments": {"path": "blocked.txt", "content": "x"},
             "id": "call_B"}
    yielded = [e async for e in core._execute_tool(
        dict(event), {"workspace": str(tmp_path)},
        approval_handler=decline)]
    assert not (tmp_path / "blocked.txt").exists()
    assert any("USER_DENIED" in str(d.get("result", ""))
               for d in _results(yielded))
