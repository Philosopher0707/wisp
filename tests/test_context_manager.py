"""Context manager tests — live byte budgets without behavior drift."""

from __future__ import annotations

from wisp.core.context_manager import (
    ContextBudget,
    live_tool_bytes,
    prune_live_session,
)


def _session(n_tool: int = 10, payload: int = 20000) -> dict:
    messages: list[dict] = [{"role": "user", "content": "go"}]
    for i in range(n_tool):
        messages.append({"role": "assistant", "content": f"step {i}"})
        messages.append({"role": "tool", "name": "read_file",
                         "content": f"--- FILE: f{i}.py | LINES: 500 | SHOWING: 1-500 ---\n" + ("x" * payload)})
    return {"id": "s", "messages": messages}


def test_under_budget_no_mutation():
    session = _session(n_tool=2, payload=100)
    before = [dict(m) for m in session["messages"]]
    report = prune_live_session(session)
    assert report.pruned_results == 0 and report.at_budget
    assert session["messages"] == before


def test_historical_condensed_recent_kept():
    session = _session(n_tool=10, payload=20000)
    report = prune_live_session(session, ContextBudget())
    assert report.pruned_results == 7  # 10 - keep 3
    assert report.at_budget
    assert report.bytes_after < report.bytes_before
    # Most recent 3 tool payloads verbatim.
    tools = [m for m in session["messages"] if m.get("role") == "tool"]
    for message in tools[-3:]:
        assert len(message["content"]) > 15000
    # Older ones condensed to headers.
    for message in tools[:-3]:
        assert len(message["content"]) < 3000


def test_user_assistant_messages_never_touched():
    session = _session(n_tool=10, payload=20000)
    users = [m["content"] for m in session["messages"] if m.get("role") == "user"]
    assistants = [m["content"] for m in session["messages"] if m.get("role") == "assistant"]
    prune_live_session(session, ContextBudget())
    assert [m["content"] for m in session["messages"] if m.get("role") == "user"] == users
    assert [m["content"] for m in session["messages"] if m.get("role") == "assistant"] == assistants


def test_idempotent():
    session = _session(n_tool=10, payload=20000)
    budget = ContextBudget()
    first = prune_live_session(session, budget)
    snapshot = [m.get("content") for m in session["messages"]]
    second = prune_live_session(session, budget)
    assert second.pruned_results == 0
    assert [m.get("content") for m in session["messages"]] == snapshot
    assert first.bytes_after == second.bytes_before


def test_total_ceiling_sheds_oldest_first():
    session = _session(n_tool=10, payload=60000)
    budget = ContextBudget(max_total_bytes=30000, keep_last_n_full=1)
    report = prune_live_session(session, budget)
    assert report.bytes_after <= 30000 + 60000  # recent-1 full + ceiling
    assert live_tool_bytes(session["messages"]) == report.bytes_after


def test_handles_missing_or_malformed_messages():
    assert prune_live_session({}).at_budget
    assert prune_live_session({"messages": None}).at_budget  # type: ignore[dict-item]
    assert prune_live_session({"messages": [{"role": "tool"}]}).at_budget


def test_budget_validates():
    assert ContextBudget().validate() == []
    assert ContextBudget(keep_last_n_full=0).validate() != []


def test_runtime_turn_bounds_live_bytes():
    """Integration: 8 over-budget turns leave live tool bytes capped."""
    import asyncio
    import tempfile
    from pathlib import Path

    from wisp.config import WispConfig
    from wisp.core.context_manager import live_tool_bytes
    from wisp.core.runtime import AgentRuntime
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import SecurityPolicy
    from wisp.infra.store import UnifiedStore
    from wisp.infra.telemetry import Telemetry

    class _Core:
        async def turn(self, session, prompt, approval_handler=None, steering_drain=None):
            yield {"type": "content", "text": "ok"}
            yield {"type": "tool_call", "name": "read_file", "arguments": {"path": "a.py"}}
            yield {"type": "tool_result", "name": "read_file", "result": "z" * 50000}

    async def _go():
        with tempfile.TemporaryDirectory() as td:
            config = WispConfig().replace(workspace=td)
            runtime = AgentRuntime(
                store=UnifiedStore(Path(td) / "wisp.db"),
                security=SecurityPolicy(),
                extensions=ExtensionHost(),
                telemetry=Telemetry(),
                core_factory=_Core,
                config=config,
            )
            session = await runtime.get_or_create_session(
                session_id="s", model="mock", workspace=td)
            for i in range(8):
                async for _ in runtime.run_turn(session, f"q{i}"):
                    pass
            return live_tool_bytes(session.get("messages", []))

    total = asyncio.run(_go())
    # 8x50KB raw would be 400KB; budget caps live bytes near the ceiling.
    assert total < 400000
    assert total <= 200000 + 3 * 50000
