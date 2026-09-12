"""PHASE 13-H0 — control-loop, steering & capability-gating forensics.

AUDIT ONLY. No production code is modified by this file. Deterministic
tests pin the causal chain behind the live trace (read-only analysis →
skill__setup activation → steering echoes → fanout proposal → approval
wait → user denial → list_files fallback): which layer decided what.
"""
from __future__ import annotations

import pytest


def _call(name, args):
    return {"function": {"name": name, "arguments": args}}


# ── H0-1: disable-model-invocation opt-out is now honored (13-I0) ──

def test_h01_opt_out_flag_not_honored(tmp_path):
    """13-I0 INVERSION: a skill declaring boolean
    `disable-model-invocation: true` is discovered but NOT advertised."""
    from wisp.extensions.skills import SkillExtension
    d = tmp_path / ".agents" / "skills" / "quiet-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: quiet-skill\ndescription: Quiet helper.\n"
        "disable-model-invocation: true\n---\n\nBody.\n")
    ext = SkillExtension(workspace=str(tmp_path))
    ext._skills = []
    from wisp.skills import discover_skills
    found = [s for s in discover_skills(str(tmp_path))
             if s.name == "quiet-skill"]
    assert found, "skill must be discoverable for this test to mean anything"
    assert found[0].model_invocable is False
    ext._skills = found
    names = [t["function"]["name"] for t in ext.tools()]
    assert "skill__quiet-skill" not in names  # hidden despite discovery


# ── H0-2: skill invocation needs no approval, returns text, keeps ID ──

@pytest.mark.asyncio
async def test_h02_skill_call_read_like_no_prompt(tmp_path):
    """Invoking a skill prompts nothing, denies nothing, executes nothing
    but text delivery — with the originating ID preserved. A strict
    handler that fails on ANY prompt proves no prompt happens."""
    from wisp.config import WispConfig
    from wisp.tool_executor import ToolExecutor

    async def strict_no_prompt(name, args, reason):
        raise AssertionError(f"prompted for {name}")

    from types import SimpleNamespace
    ext = SimpleNamespace(call_tool=lambda n, a, ws: {
        "status": "ok", "tool": n, "data": "INSTRUCTIONS " * 10})
    ex = ToolExecutor(config=WispConfig(), hook_manager=None, mcp=None,
                      file_lock=None, lsp_manager=None,
                      subagent_orchestrator=None, extensions=ext)
    yielded = [e async for e in ex.execute(
        "skill__quiet-skill", {"prompt": "analyze"},
        str(tmp_path), tool_call_id="call_SK",
        approval_handler=strict_no_prompt)]
    flat = [e if isinstance(e, dict) else {"type": str(e.type), **dict(e.data)}
            for e in yielded]
    results = [d for d in flat if d.get("type") == "tool_result"]
    assert len(results) == 1
    assert results[0].get("tool_call_id") == "call_SK"
    assert "INSTRUCTIONS" in str(results[0].get("result", ""))


# ── H0-3: fanout args pass through unmodified (model decomposes) ──

@pytest.mark.asyncio
async def test_h03_fanout_tasks_are_model_proposed_verbatim(tmp_path):
    """The host performs no bullet→task decomposition: the executor
    receives exactly the model's fanout args (4 tasks in, 4 tasks seen)."""
    from wisp.config import WispConfig
    from wisp.tool_executor import ToolExecutor
    seen = []

    async def fake_dispatch(name, args, ws):
        seen.append((name, dict(args)))
        return '{"status":"ok","data":"done"}', 1.0

    ex = ToolExecutor(config=WispConfig(), hook_manager=None, mcp=None,
                      file_lock=None, lsp_manager=None,
                      subagent_orchestrator=None, extensions=None)
    ex._execute_tool = fake_dispatch

    async def approve(name, args, reason):
        return True, None

    tasks = [{"q": b} for b in ("Core Components", "Component Interactions",
                                "Deployment Architecture", "Runtime Behavior")]
    yielded = [e async for e in ex.execute(
        "fanout", {"tasks": tasks, "max_concurrent": 4}, str(tmp_path),
        tool_call_id="call_F", approval_handler=approve)]
    assert seen and seen[0][1]["tasks"] == tasks  # verbatim, host adds nothing
    assert any((e if isinstance(e, dict) else {"type": str(e.type)}).get("type")
               == "tool_result" for e in yielded)


# ── H0-4: default mode is AUTO_EDIT; no read-only inference exists ──

def test_h04_default_mode_auto_edit_no_intent_detection(tmp_path):
    """Fresh sessions inherit AUTO_EDIT; nothing in the turn path infers
    a read-only task mode from the prompt."""
    from wisp.config import WispConfig
    from wisp.infra.security import PermissionMode, SecurityPolicy
    assert str(WispConfig().permission_mode) == "auto_edit"
    assert SecurityPolicy().permission_mode == PermissionMode.AUTO_EDIT
    # fanout needs approval under the default before anything runs
    import asyncio
    from wisp.core.approval_gate import ApprovalGate

    async def _go():
        return await ApprovalGate(SecurityPolicy()).check(
            {"type": "tool_call", "name": "fanout", "arguments": {}},
            {"workspace": str(tmp_path)}, approval_handler=None)

    allowed, reason = asyncio.run(_go())
    assert allowed is False and reason is not None


# ── H0-5: denial → fallback costs exactly one wasted iteration ──

@pytest.mark.asyncio
async def test_h05_denial_recovery_cost_one_iteration(tmp_path):
    """Denied fanout then list_files: 2 generations, 1 wasted iteration,
    denial + fallback both correctly paired."""
    from wisp.config import WispConfig
    from wisp.core.engine import WispAgentCore
    from wisp.core.runtime import AgentRuntime
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import SecurityPolicy
    from wisp.infra.store import UnifiedStore
    from wisp.infra.telemetry import Telemetry
    from wisp.providers.mock import MockProvider
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    config = WispConfig().replace(workspace=str(ws), max_iterations=4)
    provider = MockProvider(
        responses=["", "", "overview"],
        tool_calls=[[ _call("fanout", {"tasks": ["a", "b", "c", "d"]}) ],
                    [ _call("list_files", {"path": "."}) ]],
    )

    async def decline(event, args=None, reason=None):
        return False

    def factory():
        from wisp.tool_executor import ToolExecutor
        te = ToolExecutor(config=config, hook_manager=None, mcp=None,
                          file_lock=None, lsp_manager=None,
                          subagent_orchestrator=None, extensions=None)

        async def fake_dispatch(name, args, ws_):
            if name == "fanout":
                return '{"status":"ok","data":"fanned"}', 1.0
            raise AssertionError("only fanout is stubbed here")

        # list_files runs for real (read-only); fanout is gate-denied first
        orig = te._execute_tool

        async def dispatch(name, args, ws_):
            if name == "fanout":
                return await fake_dispatch(name, args, ws_)
            return await orig(name, args, ws_)
        te._execute_tool = dispatch
        return WispAgentCore(config=config, provider=provider,
                             security=SecurityPolicy(), tool_executor=te)

    rt = AgentRuntime(store=UnifiedStore(tmp_path / "wisp.db"),
                      security=SecurityPolicy(), extensions=ExtensionHost(),
                      telemetry=Telemetry(), core_factory=factory,
                      config=config)
    session = await rt.get_or_create_session(
        "h05", model="mock-model", workspace=str(ws))
    events = [e async for e in rt.run_turn(
        session, prompt="overview", approval_handler=decline)]
    by_type = {}
    for e in events:
        by_type.setdefault(e.get("type"), []).append(e)
    assert by_type.get("done")
    results = {r.get("name"): r for r in by_type.get("tool_result", [])}
    assert set(results) == {"fanout", "list_files"}
    assert provider._index == 3  # 2 proposal rounds + final answer
