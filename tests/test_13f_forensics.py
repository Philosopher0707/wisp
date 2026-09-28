"""PHASE 13F — forensic/reproduction tests (AUDIT ONLY).

No production code is modified by this file. Each test pins OBSERVED
behavior of the approval/policy/denial boundary with deterministic
mocks so the audit report can cite PROVEN evidence. Working tree at
creation: HEAD a6fb8e0 + uncommitted G1C/D/E + tool-identity deltas
(recorded in the report; turn-level tests exercise that tree).
"""
from __future__ import annotations

import asyncio

import pytest

from wisp.config import WispConfig
from wisp.core.approval_gate import ApprovalGate
from wisp.infra.security import Action, Context, SecurityPolicy
from wisp.tool_executor import ToolExecutor


def _policy():
    return SecurityPolicy()


def _ctx(tmp_path=None):
    from pathlib import Path
    return Context(workspace=Path(str(tmp_path or ".")))


def _executor():
    return ToolExecutor(config=WispConfig(), hook_manager=None, mcp=None,
                        file_lock=None, lsp_manager=None,
                        subagent_orchestrator=None, extensions=None)


def _flat(ev):
    return ev if isinstance(ev, dict) else {"type": str(getattr(ev, "type", "")),
                                            **dict(getattr(ev, "data", {}))}


# ── F1: policy denies fanout in AUTO_EDIT ──

def test_f_policy_denies_fanout_auto_edit(tmp_path):
    """13F.1: fanout is REQUIRE_APPROVAL (not hard DENY); git_push is the
    hard-DENY case — it never prompts and no `y` can run it. (run_bash was,
    until d0d4bea moved it to REQUIRE_APPROVAL.)"""
    d = _policy().check(Action(name="fanout", args={}), _ctx(tmp_path))
    assert d.allowed is True and d.approval_required is True
    d2 = _policy().check(Action(name="git_push", args={}), _ctx(tmp_path))
    assert d2.allowed is False
    assert "AUTO_EDIT mode blocks git_push" in d2.reason


# ── F2: gate refusal shape — plain string, no structured semantics ──

@pytest.mark.asyncio
async def test_f_gate_refusal_is_structured_envelope(tmp_path):
    gate = ApprovalGate(_policy())

    async def decline(event):
        return False

    session = {"workspace": str(tmp_path)}
    allowed, reason = await gate.check(
        {"type": "tool_call", "name": "fanout", "arguments": {},
         "id": "call_F"}, session, approval_handler=decline)
    assert allowed is False and "requires approval" in (reason or "")
    # What the model actually receives (stateless._refusal_result_event):
    from wisp.core.stateless import WispAgentCore
    core = WispAgentCore(config=WispConfig())
    ev = core._refusal_result_event(
        {"name": "fanout", "id": "call_F", "_blocked": reason,
         "_denial": "USER_DENIED"})
    res = ev.get("result")
    assert isinstance(res, dict)  # 13F.1: structured denial envelope
    assert res.get("status") in ("POLICY_DENIED", "USER_DENIED",
                                 "APPROVAL_TIMEOUT", "CANCELLED")
    assert res.get("authorized") is False and res.get("executed") is False
    assert res.get("retryable") is False


# ── F3: user approval FLIPS a policy denial at the gate (override proof) ──

@pytest.mark.asyncio
async def test_f_approval_overrides_policy_denial(tmp_path):
    """13F.1 INVERSION (S3): a hard policy DENY can no longer be overridden
    by user approval — the handler is never even consulted."""
    gate = ApprovalGate(_policy())
    prompted = []

    async def approve(event):
        prompted.append(event)
        return True

    session = {"workspace": str(tmp_path)}
    allowed, reason = await gate.check(
        {"type": "tool_call", "name": "git_push",
         "arguments": {"remote": "o", "branch": "b"}},
        session, approval_handler=approve)
    assert allowed is False
    assert prompted == []


# ── F4: M2 authorize() disagrees — allows fanout with approval obligation ──

def test_f_m2_layer_disagreement_on_fanout(tmp_path):
    """13F.1: M2 now agrees with policy (both deny run_bash, both
    approval-gate fanout). Kept name for traceability; asserts agreement."""
    from wisp.auth.decision import authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import classify_workspace
    dec = authorize(local_principal(workspace=str(tmp_path), profile="default"),
                    "fanout", {}, classify_workspace(str(tmp_path)),
                    permission_mode="auto_edit", effective_policy=None)
    assert dec.allowed is True and dec.approval_required is True


# ── F5: policy coverage matrix (documents T9 conflation surface) ──

@pytest.mark.parametrize("tool,expected", [
    ("read_file", (True, False)),
    ("write_file", (True, False)),      # executor-prompted, policy allows
    ("run_bash", (True, True)),         # REQUIRE_APPROVAL (d0d4bea)
    ("git_push", (False, False)),       # hard DENY
    ("spawn", (True, True)),            # REQUIRE_APPROVAL (13F.1)
    ("fanout", (True, True)),           # REQUIRE_APPROVAL (13F.1)
    ("spawn_background", (True, False)),  # policy allows, executor prompts
    ("exec_sandbox", (True, False)),    # policy allows, executor prompts
    ("fs_mutate", (True, False)),       # policy allows, executor prompts
])
def test_f_auto_edit_policy_coverage(tmp_path, tool, expected):
    d = _policy().check(Action(name=tool, args={}), _ctx(tmp_path))
    assert (d.allowed, d.approval_required) == expected, (tool, d)


# ── F6: declined fanout executes nothing at the executor ──

@pytest.mark.asyncio
async def test_f_declined_fanout_executes_nothing(tmp_path):
    ex = _executor()
    ran = []

    async def fake_dispatch(name, args, ws):
        ran.append(name)
        return "SHOULD NOT RUN", 0.0
    ex._execute_tool = fake_dispatch

    async def decline(name, args, reason):
        return False, None

    yielded = [_flat(e) async for e in ex.execute(
        "fanout", {"tasks": []}, str(tmp_path),
        tool_call_id="call_F1", approval_handler=decline)]
    assert ran == []
    results = [d for d in yielded if d.get("type") == "tool_result"]
    assert len(results) == 1
    assert results[0].get("tool_call_id") == "call_F1"
    # 13F.1: structured USER_DENIED (was "[Blocked: user declined …]")
    res = results[0].get("result", {})
    assert isinstance(res, dict) and res.get("status") == "USER_DENIED"
    assert res.get("executed") is False


# ── F7: exact turn-level repro — denial, then read-only continuation ──

def _make_runtime(provider, tmp_path, handler=None):
    from wisp.core.engine import WispAgentCore
    from wisp.core.runtime import AgentRuntime
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.store import UnifiedStore
    from wisp.infra.telemetry import Telemetry
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "main.rs").write_text("fn main() {}\n")
    config = WispConfig().replace(workspace=str(ws))

    def factory():
        return WispAgentCore(
            config=config, provider=provider, security=_policy(),
            tool_executor=ToolExecutor(
                config=config, hook_manager=None, mcp=None, file_lock=None,
                lsp_manager=None, subagent_orchestrator=None, extensions=None),
        )

    rt = AgentRuntime(store=UnifiedStore(tmp_path / "wisp.db"),
                      security=_policy(), extensions=ExtensionHost(),
                      telemetry=Telemetry(), core_factory=factory,
                      config=config)
    return rt, str(ws)


def _call(name, args):
    return {"function": {"name": name, "arguments": args}}


@pytest.mark.asyncio
async def test_f_denial_then_read_continues(tmp_path):
    """The observed run: fanout blocked, model continues with reads.
    13-J1: the call is structurally VALID (proper task objects) so it
    reaches approval and is declined -> USER_DENIED; malformed calls
    now die earlier as SCHEMA_INVALID (covered by a4/J1 suites)."""
    from wisp.providers.mock import MockProvider
    provider = MockProvider(
        responses=["", "", "analysis complete"],
        tool_calls=[[ _call("fanout", {"tasks": [
            {"task": "survey CLI"}, {"task": "survey core"}]}) ],
                    [ _call("read_file", {"path": "main.rs"}) ]],
    )
    prompts = []

    async def declining(event, args=None, reason=None):
        prompts.append(event.get("name") if isinstance(event, dict)
                       else event)
        return False

    rt, ws = _make_runtime(provider, tmp_path)
    session = await rt.get_or_create_session(
        "f7", model="mock-model", workspace=ws)
    events = [e async for e in rt.run_turn(
        session, prompt="analyze codebase", approval_handler=declining)]
    by_type = {}
    for e in events:
        by_type.setdefault(e.get("type"), []).append(e)
    # fanout prompted once, then denied; read executed; turn completed
    assert prompts == ["fanout"]
    results = {r.get("name"): r for r in by_type.get("tool_result", [])}
    assert "fanout" in results and "read_file" in results
    import json as _json
    res = results["fanout"].get("result", "")
    parsed = _json.loads(res) if isinstance(res, str) else res
    assert parsed.get("status") == "USER_DENIED"  # REQUIRE + decline
    assert parsed.get("executed") is False
    assert by_type.get("done"), "turn must complete (denial is not terminal)"
    # model-visible history carries the structured denial
    tools = [m for m in session["messages"] if m.get("role") == "tool"]
    assert any("USER_DENIED" in str(t.get("content", "")) for t in tools)


# ── F8: regeneration is re-gated (no silent pass, no budget) ──

@pytest.mark.asyncio
async def test_f_regeneration_regated_across_turns(tmp_path):
    from wisp.core.stateless import WispAgentCore
    prompts = []

    async def handler(event, args=None, reason=None):
        prompts.append(1)
        return False

    gate = ApprovalGate(_policy())
    session = {"workspace": str(tmp_path)}
    call = {"type": "tool_call", "name": "fanout", "arguments": {},
            "id": "call_R"}
    m1 = WispAgentCore._memoize_handler(handler)
    assert (await gate.check(dict(call), session, approval_handler=m1))[0] is False
    assert (await gate.check(dict(call), session, approval_handler=m1))[0] is False
    assert len(prompts) == 1  # same-turn replay: no re-prompt
    m2 = WispAgentCore._memoize_handler(handler)  # next turn
    assert (await gate.check(dict(call), session, approval_handler=m2))[0] is False
    assert len(prompts) == 2  # re-prompted; denials are never auto-passed


# ── F9: concurrent approvals stay attached (no cross-wire) ──

@pytest.mark.asyncio
async def test_f_concurrent_approvals_isolated(tmp_path):
    ex = _executor()
    ran = []

    async def fake_dispatch(name, args, ws):
        ran.append((name, dict(args)))
        return '{"status":"ok","data":"wrote"}', 1.0
    ex._execute_tool = fake_dispatch

    async def branching(name, args, reason):
        return (args.get("path") == "a.txt"), None  # approve A, deny B

    r = await asyncio.gather(*[
        asyncio.ensure_future(_collect(ex.execute(
            "write_file", {"path": p, "content": "x"}, str(tmp_path),
            tool_call_id=cid, approval_handler=branching)))
        for p, cid in (("a.txt", "call_A"), ("b.txt", "call_B"))])
    assert ran == [("write_file", {"path": "a.txt", "content": "x"})]
    got = {}
    for evs in r:
        for d in evs:
            if d.get("type") == "tool_result":
                got[d.get("tool_call_id")] = str(d.get("result", ""))
    assert "USER_DENIED" not in got["call_A"] and "USER_DENIED" in got["call_B"]


async def _collect(agen):
    return [_flat(e) async for e in agen]


# ── F10: denied write does not open bash (substitution boundary) ──

@pytest.mark.asyncio
async def test_f_denied_write_then_bash_still_blocked(tmp_path):
    # Architecture note (proven by F3 vs this test): the GATE prompts only
    # on policy denial; policy-allowed writes prompt in the EXECUTOR.
    ex = _executor()
    ran = []

    async def fake_dispatch(name, args, ws):
        ran.append(name)
        return "SHOULD NOT RUN", 0.0
    ex._execute_tool = fake_dispatch

    async def decline(name, args, reason):
        return False, None

    yielded = [_flat(e) async for e in ex.execute(
        "write_file", {"path": "x", "content": "y"}, str(tmp_path),
        tool_call_id="call_W", approval_handler=decline)]
    assert ran == []  # user-denied write never executes
    assert any(d.get("type") == "tool_result" and
               d.get("tool_call_id") == "call_W" for d in yielded)
    # model pivots to bash without fresh user intent: still refused — bash
    # needs an operator's yes in AUTO_EDIT, and there is no one to give it
    gate = ApprovalGate(_policy())
    session = {"workspace": str(tmp_path)}
    allowed_b, reason_b = await gate.check(
        {"type": "tool_call", "name": "run_bash",
         "arguments": {"command": "echo hi"}, "id": "call_B"},
        session, approval_handler=None)
    assert allowed_b is False and "approval" in (reason_b or "")


# ── F11: resume after denial does not resurrect the denied call ──

@pytest.mark.asyncio
async def test_f_resume_denial_stays_denied(tmp_path):
    from wisp.providers.mock import MockProvider
    provider = MockProvider(
        responses=["post-resume answer"],
        tool_calls=[],
    )
    rt, ws = _make_runtime(provider, tmp_path)
    session = await rt.get_or_create_session(
        "f11", model="mock-model", workspace=ws)
    # Simulate a persisted prior turn: denied fanout + honest read exchange
    session["messages"].extend([
        {"role": "user", "content": "analyze codebase"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_OLD", "type": "function",
                         "function": {"name": "fanout", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_OLD",
         "content": "[Blocked: AUTO_EDIT mode blocks fanout]"},
    ])
    events = [e async for e in rt.run_turn(session, prompt="continue")]
    by_type = {}
    for e in events:
        by_type.setdefault(e.get("type"), []).append(e)
    assert by_type.get("done")
    assert "fanout" not in [r.get("name")
                            for r in by_type.get("tool_result", [])]
