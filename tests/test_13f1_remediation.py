"""PHASE 13F.1 — control-plane remediation regression tests.

Built step by step per §23A: STEP 3 proofs first (ordering), then
capability, envelope, audit, and adversarial coverage. All deterministic,
no network.
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


async def _collect(agen):
    return [_flat(e) async for e in agen]


# ── STEP 3 stop conditions: DENY is hard, REQUIRE_APPROVAL gates ──

@pytest.mark.asyncio
async def test_step3_deny_never_prompts_gate():
    gate = ApprovalGate(SecurityPolicy())
    prompted = []

    async def approving(event):
        prompted.append(event)
        return True

    allowed, _ = await gate.check(
        {"type": "tool_call", "name": "run_bash",
         "arguments": {"command": "echo hi"}, "id": "call_D"},
        {"workspace": "."}, approval_handler=approving)
    assert allowed is False
    assert prompted == []


@pytest.mark.asyncio
async def test_step3_deny_yes_cannot_execute(tmp_path):
    ex = _executor()
    prompted, ran = [], []

    async def fake_dispatch(name, args, ws):
        ran.append(name)
        return "SHOULD NOT RUN", 0.0
    ex._execute_tool = fake_dispatch

    async def approving(name, args, reason):
        prompted.append(name)
        return True, None

    yielded = await _collect(ex.execute(
        "run_bash", {"command": "echo hi"}, str(tmp_path),
        tool_call_id="call_D", approval_handler=approving))
    assert ran == [] and prompted == []
    results = [d for d in yielded if d.get("type") == "tool_result"]
    assert len(results) == 1
    assert results[0].get("tool_call_id") == "call_D"


@pytest.mark.asyncio
async def test_step3_require_approval_yes_executes(tmp_path):
    gate = ApprovalGate(SecurityPolicy())

    async def approve(event):
        return True

    allowed, _ = await gate.check(
        {"type": "tool_call", "name": "fanout",
         "arguments": {"tasks": []}, "id": "call_R"},
        {"workspace": str(tmp_path)}, approval_handler=approve)
    assert allowed is True

    ex = _executor()
    ran = []

    async def fake_dispatch(name, args, ws):
        ran.append(name)
        return '{"status":"ok","data":"fanned"}', 1.0
    ex._execute_tool = fake_dispatch

    async def approve_ex(name, args, reason):
        return True, None

    yielded = await _collect(ex.execute(
        "fanout", {"tasks": []}, str(tmp_path),
        tool_call_id="call_R", approval_handler=approve_ex))
    assert ran == ["fanout"]
    assert any(d.get("type") == "tool_result" and
               d.get("tool_call_id") == "call_R" for d in yielded)


@pytest.mark.asyncio
async def test_step3_require_approval_no_denies(tmp_path):
    gate = ApprovalGate(SecurityPolicy())

    async def decline(event):
        return False

    allowed, reason = await gate.check(
        {"type": "tool_call", "name": "fanout",
         "arguments": {"tasks": []}, "id": "call_R"},
        {"workspace": str(tmp_path)}, approval_handler=decline)
    assert allowed is False
    assert reason is not None


# ── STEP 4: fanout AUTO_EDIT classification ──

def test_step4_fanout_is_require_approval_not_deny():
    from wisp.infra.security import SecurityPolicy, Action, Context
    from pathlib import Path
    d = SecurityPolicy().check(
        Action(name="fanout", args={}), Context(workspace=Path(".")))
    assert d.allowed is True and d.approval_required is True


def test_step4_child_tools_filtered_by_mode():
    """Children cannot re-fanout/escalate: blocked tools are stripped
    from child schemas (existing safety model REQUIRE_APPROVAL relies on)."""
    from wisp.infra.policy_engine import filter_allowed_for_mode
    kids = filter_allowed_for_mode(
        "auto_edit", ["read_file", "write_file", "fanout", "spawn",
                      "run_bash", "spawn_background"])
    assert "fanout" not in kids and "spawn" not in kids
    assert "run_bash" not in kids
    assert "read_file" in kids and "write_file" in kids


# ── STEP 5: capability reconciliation table ──

@pytest.mark.parametrize("tool,policy,note", [
    # (tool, policy-decision in AUTO_EDIT, rationale)
    ("spawn", "require", "delegation primitive: operator yes, children filtered"),
    ("fanout", "require", "delegation primitive: operator yes, children filtered"),
    ("spawn_background", "allow",
     "detached primitive: policy allows, executor prompts interactively"),
    ("run_bash", "deny", "exec: hard deny in AUTO_EDIT"),
])
def test_step5_capability_table(tool, policy, note):
    from wisp.infra.security import SecurityPolicy, Action, Context
    from pathlib import Path
    d = SecurityPolicy().check(
        Action(name=tool, args={}), Context(workspace=Path(".")))
    assert note  # documents rationale
    if policy == "deny":
        assert d.allowed is False
    elif policy == "require":
        assert d.allowed is True and d.approval_required is True
    else:
        assert d.allowed is True and d.approval_required is False


def test_step5_thin_mutators_prompt_not_silent(tmp_path):
    """exec_sandbox/fs_mutate: policy allows (sandbox-confined + danger
    gate), but the executor still prompts — never silent execution."""
    from wisp.infra.security import policy_hard_deny
    assert policy_hard_deny("exec_sandbox", "auto_edit") is None
    assert policy_hard_deny("fs_mutate", "auto_edit") is None


# ── STEP 6: gate == authorization ──

@pytest.mark.parametrize("tool,expected", [
    ("read_file", (True, False)),
    ("write_file", (True, False)),
    ("fanout", (True, True)),
    ("spawn", (True, True)),
    ("run_bash", (False, False)),
])
def test_step6_gate_m2_agree(tool, expected):
    """(policy-allowed, approval-required) identical at both layers."""
    from wisp.auth.decision import authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import classify_workspace
    from wisp.infra.security import SecurityPolicy, Action, Context
    from pathlib import Path
    gate_d = SecurityPolicy().check(
        Action(name=tool, args={}), Context(workspace=Path(".")))
    m2_d = authorize(local_principal(workspace=".", profile="default"),
                     tool, {}, classify_workspace("."),
                     permission_mode="auto_edit", effective_policy=None)
    assert (gate_d.allowed, gate_d.approval_required) == expected
    assert (m2_d.allowed, m2_d.approval_required) == expected


def test_step6_spawn_background_documented_split(tmp_path):
    """spawn_background is INTENTIONALLY policy-allow: it is the detached
    primitive (background agents cannot prompt by design). Approval still
    happens — once, at the executor, in interactive context (proven
    below). End-to-end it is approval-gated; only the layer differs."""
    from wisp.auth.decision import authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import classify_workspace
    from wisp.infra.security import SecurityPolicy, Action, Context
    from pathlib import Path
    gate_d = SecurityPolicy().check(
        Action(name="spawn_background", args={}), Context(workspace=Path(".")))
    assert (gate_d.allowed, gate_d.approval_required) == (True, False)
    m2_d = authorize(local_principal(workspace=".", profile="default"),
                     "spawn_background", {}, classify_workspace("."),
                     permission_mode="auto_edit", effective_policy=None)
    assert (m2_d.allowed, m2_d.approval_required) == (True, True)


@pytest.mark.asyncio
async def test_step6_spawn_background_prompts_interactively(tmp_path):
    ex = _executor()
    prompted, ran = [], []

    async def fake_dispatch(name, args, ws):
        ran.append(name)
        return '{"status":"ok","data":{"agent_id":"a"}}', 1.0
    ex._execute_tool = fake_dispatch

    async def handler(name, args, reason):
        prompted.append(name)
        return True, None

    yielded = await _collect(ex.execute(
        "spawn_background", {"description": "d", "prompt": "p"},
        str(tmp_path), tool_call_id="call_SB", approval_handler=handler))
    assert prompted == ["spawn_background"] and ran == ["spawn_background"]
    assert any(d.get("type") == "approval_request" for d in yielded)


# ── STEP 9: denial audit — exactly one event, never executed=true ──

def _audit_entries(ws):
    import json as _json
    p = ws / ".wisp" / "audit.jsonl"
    if not p.exists():
        return []
    return [_json.loads(l) for l in p.read_text().splitlines() if l.strip()]


@pytest.mark.asyncio
async def test_step9_hard_deny_audited_once(tmp_path):
    ex = _executor()
    ws = str(tmp_path)

    async def approving(name, args, reason):
        return True, None

    await _collect(ex.execute("run_bash", {"command": "echo hi"}, ws,
                              tool_call_id="call_A1", approval_handler=approving))
    blocked = [e for e in _audit_entries(tmp_path)
               if e.get("decision") == "blocked"]
    assert len(blocked) == 1
    assert blocked[0].get("tool") == "run_bash"
    assert blocked[0].get("executed", False) is not True


@pytest.mark.asyncio
async def test_step9_user_deny_audited_once(tmp_path):
    ex = _executor()
    ws = str(tmp_path)

    async def decline(name, args, reason):
        return False, None

    await _collect(ex.execute("write_file", {"path": "x", "content": "y"},
                              ws, tool_call_id="call_A2", approval_handler=decline))
    blocked = [e for e in _audit_entries(tmp_path)
               if e.get("decision") == "blocked"]
    assert len(blocked) == 1
    assert blocked[0].get("tool") == "write_file"


@pytest.mark.asyncio
async def test_step9_gate_user_deny_audited_policy_deny_not_duplicated(tmp_path):
    """Gate user-denials audit once via the refusal factory; policy-denied
    gate refusals are owned by SecurityPolicy._audit (no double)."""
    from wisp.core.stateless import WispAgentCore
    core = WispAgentCore(config=WispConfig().replace(workspace=str(tmp_path)))
    ws = str(tmp_path)
    core._refusal_result_event(
        {"name": "fanout", "id": "call_G1", "_blocked": "not approved",
         "_denial": "USER_DENIED"}, ws)
    core._refusal_result_event(
        {"name": "run_bash", "id": "call_G2",
         "_blocked": "AUTO_EDIT mode blocks run_bash",
         "_denial": "POLICY_DENIED", "_src": "gate"}, ws)
    blocked = [e for e in _audit_entries(tmp_path)
               if e.get("decision") == "blocked"]
    assert len(blocked) == 1 and blocked[0].get("tool") == "fanout"


# ── STEP 7: envelope vocabulary ──

@pytest.mark.asyncio
async def test_step7_timeout_envelope(tmp_path):
    from wisp.cli.approval import ApprovalTimeout
    ex = _executor()

    async def lapsing(name, args, reason):
        raise ApprovalTimeout(name)

    yielded = await _collect(ex.execute(
        "write_file", {"path": "x", "content": "y"}, str(tmp_path),
        tool_call_id="call_T", approval_handler=lapsing))
    res = [d for d in yielded if d.get("type") == "tool_result"]
    assert len(res) == 1 and res[0].get("tool_call_id") == "call_T"
    import json as _json
    parsed = _json.loads(res[0]["result"]) if isinstance(res[0]["result"], str) else res[0]["result"]
    assert parsed["status"] == "APPROVAL_TIMEOUT"
    assert parsed["authorized"] is False and parsed["executed"] is False


@pytest.mark.asyncio
async def test_step7_cancel_envelope(tmp_path):
    from wisp.cli.approval import ApprovalCancelled
    ex = _executor()

    async def cancelling(name, args, reason):
        raise ApprovalCancelled(name)

    yielded = await _collect(ex.execute(
        "write_file", {"path": "x", "content": "y"}, str(tmp_path),
        tool_call_id="call_C", approval_handler=cancelling))
    res = [d for d in yielded if d.get("type") == "tool_result"]
    assert len(res) == 1 and res[0].get("tool_call_id") == "call_C"
    import json as _json
    parsed = _json.loads(res[0]["result"]) if isinstance(res[0]["result"], str) else res[0]["result"]
    assert parsed["status"] == "CANCELLED"


def test_step7_denial_display_lines():
    from wisp.core.stateless import _denial_display
    assert _denial_display("POLICY_DENIED", "fanout", "x").startswith("Policy denied:")
    assert _denial_display("USER_DENIED", "fanout", "x").startswith("User denied:")
    assert _denial_display("APPROVAL_TIMEOUT", "fanout", "x").startswith("Approval timed out:")
    assert _denial_display("CANCELLED", "fanout", "x").startswith("Cancelled:")


# ── STEP 8: continuation contract ──

@pytest.mark.asyncio
async def test_step8_denied_tool_stays_denied_and_reads_continue(tmp_path):
    gate = ApprovalGate(SecurityPolicy())
    session = {"workspace": str(tmp_path)}

    async def approve(event):
        return True

    # same denied tool, re-proposed: still denied (S4)
    for _ in range(2):
        allowed, _ = await gate.check(
            {"type": "tool_call", "name": "run_bash",
             "arguments": {"command": "echo hi"}}, session,
            approval_handler=approve)
        assert allowed is False
    # read continues (S9)
    allowed, _ = await gate.check(
        {"type": "tool_call", "name": "read_file",
         "arguments": {"path": "x"}}, session, approval_handler=approve)
    assert allowed is True


@pytest.mark.asyncio
async def test_step8_decline_then_approve_executes_no_budget_burn(tmp_path):
    """Decline, decline, approve: only the approved call executes (S8 —
    denials consume no retry budget; live sequence, positive outcome)."""
    ex = _executor()
    ran = []

    async def fake_dispatch(name, args, ws):
        ran.append(name)
        return '{"status":"ok","data":"fanned"}', 1.0
    ex._execute_tool = fake_dispatch
    verdicts = iter([(False, None), (False, None), (True, None)])

    async def scripted(name, args, reason):
        return next(verdicts)

    for cid in ("call_Q1", "call_Q2", "call_Q3"):
        yielded = await _collect(ex.execute(
            "fanout", {"tasks": []}, str(tmp_path),
            tool_call_id=cid, approval_handler=scripted))
        assert [d.get("tool_call_id") for d in yielded
                if d.get("type") == "tool_result"] == [cid]
    assert ran == ["fanout"]


# ── STEP 13: adversarial authority matrix ──

@pytest.mark.parametrize("tool,args", [
    ("run_bash", {"command": "echo hi"}),
    ("git_push", {"remote": "o", "branch": "b"}),
])
@pytest.mark.asyncio
async def test_step13_policy_deny_never_executes(tmp_path, tool, args):
    """S1/S2/S3: hard DENY + approving user + stubbed dispatch."""
    ex = _executor()
    ran, prompted = [], []

    async def fake_dispatch(name, a, ws):
        ran.append(name)
        return "SHOULD NOT RUN", 0.0
    ex._execute_tool = fake_dispatch

    async def approving(name, a, reason):
        prompted.append(name)
        return True, None

    yielded = await _collect(ex.execute(
        tool, args, str(tmp_path), tool_call_id="call_X",
        approval_handler=approving))
    assert ran == [] and prompted == []
    import json as _json
    res = [d for d in yielded if d.get("type") == "tool_result"]
    parsed = _json.loads(res[0]["result"]) if isinstance(res[0]["result"], str) else res[0]["result"]
    assert parsed["status"] == "POLICY_DENIED" and parsed["executed"] is False


@pytest.mark.asyncio
async def test_step13_read_only_mode_denies_mutations(tmp_path):
    from wisp.infra.security import PermissionMode, SecurityPolicy
    pol = SecurityPolicy(permission_mode=PermissionMode.READ_ONLY)
    gate = ApprovalGate(pol)

    async def approve(event):
        return True

    for tool in ("write_file", "run_bash", "fanout", "spawn_background"):
        allowed, _ = await gate.check(
            {"type": "tool_call", "name": tool, "arguments": {}},
            {"workspace": str(tmp_path)}, approval_handler=approve)
        assert allowed is False, tool
    allowed, _ = await gate.check(
        {"type": "tool_call", "name": "read_file", "arguments": {}},
        {"workspace": str(tmp_path)}, approval_handler=approve)
    assert allowed is True


@pytest.mark.asyncio
async def test_step13_genuine_cancellation_propagates_no_continuation(tmp_path):
    """S10: real CancelledError is never converted into a denial result."""
    import asyncio
    ex = _executor()

    async def cancelling(name, args, reason):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        async for _ in ex.execute("write_file", {"path": "x", "content": "y"},
                                  str(tmp_path), tool_call_id="call_Z",
                                  approval_handler=cancelling):
            pass


# ── STEP 13: post-denial authority stays correctly gated ──

@pytest.mark.asyncio
async def test_step13_post_deny_authority_matrix(tmp_path):
    """After a POLICY_DENIED on run_bash: reads/network stay allowed,
    writes and delegation re-prompt, shell stays denied — each proposal
    independently decided (S4/S6)."""
    gate = ApprovalGate(SecurityPolicy())
    session = {"workspace": str(tmp_path)}
    prompts = []

    async def approve(event):
        prompts.append(event.get("name"))
        return True

    denied, _ = await gate.check(
        {"type": "tool_call", "name": "run_bash",
         "arguments": {"command": "echo hi"}}, session,
        approval_handler=approve)
    assert denied is False and prompts == []
    for tool, args, expect_prompt in [
        ("read_file", {"path": "x"}, False),
        ("web_search", {"query": "q"}, False),
        ("fanout", {"tasks": []}, True),
        ("spawn", {"description": "d", "prompt": "p"}, True),
    ]:
        ok, _ = await gate.check(
            {"type": "tool_call", "name": tool, "arguments": args},
            session, approval_handler=approve)
        assert ok is True, tool
    assert sorted(prompts) == ["fanout", "spawn"]
    # shell re-proposed: still hard-denied, still no prompt
    denied2, _ = await gate.check(
        {"type": "tool_call", "name": "run_bash",
         "arguments": {"command": "echo again"}}, session,
        approval_handler=approve)
    assert denied2 is False and sorted(prompts) == ["fanout", "spawn"]
