"""Phase 2.4 RED tests — risk table single source + ApprovalDecision seam.

Target: TOOL_RISK_TABLE explicitly covers every builtin tool (no silent
fail-closed drift), and ApprovalGate exposes check_decision() returning
the contracts ApprovalDecision while check() keeps its tuple shape.
"""

from __future__ import annotations


def test_risk_table_covers_all_builtin_tools():
    from wisp.core.contracts import TOOL_RISK_TABLE
    from wisp.tools.registry import TOOL_SCHEMAS

    names = {s.get("function", {}).get("name", "") for s in TOOL_SCHEMAS}
    names.discard("")
    missing = sorted(n for n in names if n not in TOOL_RISK_TABLE)
    assert not missing, f"risk table missing: {missing}"


def test_high_risk_tools_are_exec():
    from wisp.core.contracts import ToolRisk, risk_for_tool

    for name in ("run_bash", "spawn_background", "orchestrate_vote",
                 "subagent_wait", "git_push", "gh_pr_create"):
        assert risk_for_tool(name) is ToolRisk.EXEC, name


def test_read_tools_are_read():
    from wisp.core.contracts import ToolRisk, risk_for_tool

    for name in ("read_file", "list_files", "git_status", "recall"):
        assert risk_for_tool(name) is ToolRisk.READ, name


def test_approval_gate_check_decision_returns_decision():
    from wisp.core.approval_gate import ApprovalGate
    from wisp.core.contracts import ApprovalDecision

    assert hasattr(ApprovalGate, "check_decision")
    import inspect

    assert "ApprovalDecision" in inspect.getsource(ApprovalGate.check_decision)


def test_approval_gate_check_tuple_stays_compatible():
    import asyncio
    import inspect

    from wisp.core.approval_gate import ApprovalGate

    src = inspect.getsource(ApprovalGate.check)
    # check() must keep returning the (bool, reason|None) tuple.
    assert "tuple[bool" in src or "Tuple[bool" in src or "-> tuple" in src

    async def _go():
        gate = ApprovalGate(security=None)
        ok, reason = await gate.check({"name": "read_file", "arguments": {}}, {"workspace": "."})
        assert (ok, reason) == (True, None)

    asyncio.run(_go())


def test_security_decision_converts_to_approval_decision():
    from wisp.core.approval_gate import decision_to_approval_decision
    from wisp.core.contracts import ApprovalDecision
    from wisp.infra.security import Decision

    out = decision_to_approval_decision(Decision(allowed=False, reason="ask_all"), tool_name="run_bash")
    assert isinstance(out, ApprovalDecision)
    assert out.allowed is False and out.reason == "ask_all"
    assert out.risk.value == "exec"
