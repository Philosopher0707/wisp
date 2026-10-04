"""PHASE 13F.1-A — repeated policy-denial loop forensics (AUDIT ONLY).

No production code is modified by this file. Deterministic mocks prove
the control-loop mechanics behind the live run_bash ×6 POLICY_DENIED
sequence: who repeats, what terminates it, and what the model saw.
"""
from __future__ import annotations

import json

import pytest

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.core.runtime import AgentRuntime
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import SecurityPolicy
from wisp.infra.store import UnifiedStore
from wisp.infra.telemetry import Telemetry
from wisp.providers.mock import MockProvider
from wisp.tool_executor import ToolExecutor


def _call(name, args):
    return {"function": {"name": name, "arguments": args}}


def _runtime(provider, tmp_path, max_iterations=4):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    config = WispConfig().replace(workspace=str(ws),
                                  max_iterations=max_iterations)

    def factory():
        return WispAgentCore(
            config=config, provider=provider, security=SecurityPolicy(),
            tool_executor=ToolExecutor(
                config=config, hook_manager=None, mcp=None, file_lock=None,
                lsp_manager=None, subagent_orchestrator=None, extensions=None),
        )

    rt = AgentRuntime(store=UnifiedStore(tmp_path / "wisp.db"),
                      security=SecurityPolicy(), extensions=ExtensionHost(),
                      telemetry=Telemetry(), core_factory=factory,
                      config=config)
    return rt, str(ws)


def _tool_results(events):
    return [e for e in events if e.get("type") == "tool_result"]


def _parse_result(r):
    res = r.get("result", "")
    return json.loads(res) if isinstance(res, str) and res.strip().startswith("{") else res


# ── A1: the loop is model-driven generations, host never reissues ──

@pytest.mark.asyncio
async def test_a1_each_denial_follows_its_own_generation(tmp_path):
    """Provider proposes run_bash every round: denials == generations,
    each with a distinct ID; no host/provider reissue in between."""
    provider = MockProvider(
        responses=["", "", "", "", "giving up"],
        tool_calls=[[ _call("run_bash", {"command": "echo hi"}) ]] * 4,
    )
    rt, ws = _runtime(provider, tmp_path, max_iterations=4)
    session = await rt.get_or_create_session(
        "a1", model="mock-model", workspace=ws)
    events = [e async for e in rt.run_turn(session, prompt="go")]
    denied = [r for r in _tool_results(events)
              if isinstance(_parse_result(r), dict)
              and _parse_result(r).get("status") == "POLICY_DENIED"]
    assert len(denied) == 4  # one denial per generation, no more, no fewer
    ids = [r.get("tool_call_id") for r in denied]
    assert len(set(ids)) == 4 and all(ids)  # distinct, non-empty
    # provider consulted once per round + once for the wrap-up: no hidden
    # provider/transport retry inflates the count
    assert provider._index == 5


# ── A2: termination is the iteration budget, not a denial rule ──

@pytest.mark.asyncio
async def test_a2_loop_terminates_at_max_iterations(tmp_path):
    provider = MockProvider(
        responses=["", "", "", "done"],
        tool_calls=[[ _call("run_bash", {"command": "echo hi"}) ]] * 3,
    )
    rt, ws = _runtime(provider, tmp_path, max_iterations=3)
    session = await rt.get_or_create_session(
        "a2", model="mock-model", workspace=ws)
    events = [e async for e in rt.run_turn(session, prompt="go")]
    by_type = {}
    for e in events:
        by_type.setdefault(e.get("type"), []).append(e)
    assert len(_tool_results(events)) == 3
    assert by_type.get("done"), "turn must end via budget wrap-up, not hang"


# ── A3: schema-error → corrected retry → policy-denial sequence ──

@pytest.mark.asyncio
async def test_a3_policy_precedes_schema_validation(tmp_path):
    """Ordering proof, 13-J1 order: a structurally INVALID prohibited
    call yields SCHEMA_INVALID (validation runs before policy/approval),
    while a structurally VALID prohibited call still yields POLICY_DENIED.
    Renamed intent: schema precedes policy; policy layer intact and
    distinguishable. (Pre-J1 name asserted the old order.)"""
    provider = MockProvider(
        responses=["", "", "", "done"],
        tool_calls=[[ _call("run_bash", {}) ],
                    [ _call("write_file", {"path": "x"}) ],
                    [ _call("run_bash", {"command": "echo hi"}) ]],
    )
    rt, ws = _runtime(provider, tmp_path, max_iterations=4)
    session = await rt.get_or_create_session(
        "a3", model="mock-model", workspace=ws)
    events = [e async for e in rt.run_turn(session, prompt="go")]
    results = _tool_results(events)
    assert len(results) == 3
    first = _parse_result(results[0])
    assert isinstance(first, dict) and first.get("status") == "SCHEMA_INVALID"
    second = _parse_result(results[1])
    # 13-J1: structurally invalid write_file is refused pre-approval with
    # the machine-readable denial envelope (not the legacy "error" shape).
    assert isinstance(second, dict) and second.get("status") == "SCHEMA_INVALID"
    assert "Failed validating" in json.dumps(second) or "Schema" in json.dumps(second)
    third = _parse_result(results[2])
    assert third.get("status") == "POLICY_DENIED"
    ids = [r.get("tool_call_id") for r in results]
    assert len(set(ids)) == 3  # distinct generations, distinct IDs


# ── A4: repetition is generic, not run_bash-specific ──

@pytest.mark.asyncio
async def test_a4_repetition_generic_across_denied_tools(tmp_path, auto_edit_hard_deny_witness):
    provider = MockProvider(
        responses=["", "", "", "done"],
        tool_calls=[[ _call("git_push", {"remote": "o", "branch": "b"}) ],
                    [ _call("fanout", {"tasks": []}) ],
                    [ _call("run_bash", {"command": "echo hi"}) ]],
    )

    async def decline(event, args=None, reason=None):
        return False

    rt, ws = _runtime(provider, tmp_path, max_iterations=4)
    session = await rt.get_or_create_session(
        "a4", model="mock-model", workspace=ws)
    events = [e async for e in rt.run_turn(
        session, prompt="go", approval_handler=decline)]
    got = {}
    for r in _tool_results(events):
        parsed = _parse_result(r)
        got[r.get("name")] = parsed.get("status") if isinstance(parsed, dict) else parsed
    assert got.get("git_push") == "POLICY_DENIED"
    # run_bash is REQUIRE_APPROVAL in AUTO_EDIT (d0d4bea): the user declined it.
    assert got.get("run_bash") == "USER_DENIED"
    # fanout with empty tasks is structurally invalid (minItems 1, 13-J1)
    # so it is refused pre-approval as SCHEMA_INVALID, not USER_DENIED.
    assert got.get("fanout") == "SCHEMA_INVALID"


# ── A5: run_bash stays advertised in AUTO_EDIT (visibility proof) ──

def test_a5_denied_tool_remains_in_model_schemas(tmp_path):
    config = WispConfig().replace(workspace=str(tmp_path))
    core = WispAgentCore(config=config, provider=MockProvider(),
                         security=SecurityPolicy(), tool_executor=None)
    names = {t.get("function", {}).get("name") for t in core._get_tool_schemas()}
    assert "run_bash" in names  # no mode filtering at advertisement
    assert "fanout" in names and "git_push" in names


# ── A6: history carries structured denial per generation ──

@pytest.mark.asyncio
async def test_a6_history_distinguishes_each_denial(tmp_path):
    provider = MockProvider(
        responses=["", "", "done"],
        tool_calls=[[ _call("run_bash", {"command": "a"}) ],
                    [ _call("run_bash", {"command": "b"}) ]],
    )
    rt, ws = _runtime(provider, tmp_path, max_iterations=3)
    session = await rt.get_or_create_session(
        "a6", model="mock-model", workspace=ws)
    _ = [e async for e in rt.run_turn(session, prompt="go")]
    tools = [m for m in session["messages"] if m.get("role") == "tool"]
    assert len(tools) == 2
    for t in tools:
        parsed = json.loads(t["content"])
        assert parsed["status"] == "POLICY_DENIED"
        assert parsed["authorized"] is False and parsed["executed"] is False
        assert parsed["retryable"] is False
    assistants = [m for m in session["messages"] if m.get("tool_calls")]
    known = {b["id"] for a in assistants for b in a["tool_calls"]}
    assert {t["tool_call_id"] for t in tools} <= known  # every denial paired


# ── A7: denied-then-alternative still works (the loop is not mandatory) ──

@pytest.mark.asyncio
async def test_a7_denied_bash_then_read_executes(tmp_path):
    (tmp_path / "ws").mkdir(exist_ok=True)
    (tmp_path / "ws" / "f.txt").write_text("hi\n")
    provider = MockProvider(
        responses=["", "", "done"],
        tool_calls=[[ _call("run_bash", {"command": "echo hi"}) ],
                    [ _call("read_file", {"path": "f.txt"}) ]],
    )
    rt, ws = _runtime(provider, tmp_path, max_iterations=3)
    session = await rt.get_or_create_session(
        "a7", model="mock-model", workspace=ws)
    events = [e async for e in rt.run_turn(session, prompt="go")]
    names = [r.get("name") for r in _tool_results(events)]
    assert names == ["run_bash", "read_file"]
    assert "hi" in str(_tool_results(events)[1].get("result", ""))
