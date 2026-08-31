"""Autonomous mode — fully autonomous (Cursor-like) but safe.

Verifies `WISP_AUTONOMOUS=1` / `config.autonomous=True` makes
`AgentRuntime.run_turn` auto-approve safe tools without a handler,
while hard-blocking dangerous commands.
"""

import pytest

from wisp.config import WispConfig
from wisp.core.graph_state import GraphStatus


def _mock_provider(responses):
    class P:
        def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
            for r in responses:
                yield r
    return P()


@pytest.mark.asyncio
async def test_autonomous_auto_approves_safe_bash(tmp_path):
    from wisp.composition import CompositionRoot

    cfg = WispConfig().replace(workspace=str(tmp_path), autonomous=True, permission_mode="auto_edit")
    root = CompositionRoot(config=cfg)
    root.start()
    try:
        sess = await root.runtime.get_or_create_session(f"auto-safe-{tmp_path.name}", cfg.model, str(tmp_path))
        (tmp_path / "hello.txt").write_text("hi")
        # No approval_handler — autonomous should auto-approve run_bash "cat hello.txt"
        responses = [
            {"type": "tool_call", "name": "run_bash", "arguments": {"command": "cat hello.txt"}},
            {"type": "done"},
        ]
        # Second turn: content to complete (verification loop requires exit-0 then content)
        # Simulate by patching provider to yield tool_call then content
        call_n = {"c": 0}
        orig = root.runtime._get_core(sess["id"]).provider
        class FlippingProvider:
            def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
                call_n["c"] += 1
                if call_n["c"] == 1:
                    yield {"type": "tool_call", "name": "run_bash", "arguments": {"command": "cat hello.txt"}}
                    yield {"type": "done"}
                else:
                    yield {"type": "token", "text": "done", "phase": "content"}
                    yield {"type": "done"}
        root.runtime._get_core(sess["id"]).provider = FlippingProvider()
        events = [e async for e in root.runtime.run_turn(sess, "cat hello.txt")]
        # Should have tool_result, not blocked
        blocked = [e for e in events if e.get("type") == "tool_result" and "Blocked" in str(e.get("result", ""))]
        assert not blocked, f"autonomous wrongly blocked safe bash: {blocked}"
        assert any(e.get("type") == "tool_call" for e in events)
    finally:
        root.shutdown()


@pytest.mark.asyncio
async def test_autonomous_still_blocks_dangerous(tmp_path):
    from wisp.composition import CompositionRoot

    cfg = WispConfig().replace(workspace=str(tmp_path), autonomous=True)
    root = CompositionRoot(config=cfg)
    root.start()
    try:
        sess = await root.runtime.get_or_create_session(f"auto-danger-{tmp_path.name}", cfg.model, str(tmp_path))
        class DangerProvider:
            def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
                yield {"type": "tool_call", "name": "run_bash", "arguments": {"command": "sudo rm -rf /"}}
                yield {"type": "done"}
        root.runtime._get_core(sess["id"]).provider = DangerProvider()
        events = [e async for e in root.runtime.run_turn(sess, "do dangerous")]
        # Should be blocked via autonomous gate (check_dangerous_command)
        tool_results = [e for e in events if e.get("type") == "tool_result"]
        assert any("Blocked" in str(e.get("result", "")) or "dangerous" in str(e.get("result", "")).lower() for e in tool_results) or any(e.get("type") == "error" for e in events)
    finally:
        root.shutdown()


def test_autonomous_config_env(monkeypatch):
    monkeypatch.setenv("WISP_AUTONOMOUS", "true")
    cfg = WispConfig()
    assert cfg.autonomous is True
    monkeypatch.setenv("WISP_AUTONOMOUS", "false")
    cfg2 = WispConfig()
    assert cfg2.autonomous is False


@pytest.mark.asyncio
async def test_graph_state_from_session(tmp_path):
    from wisp.core.graph_state import GraphState
    sess = {"id": "s1", "workspace": str(tmp_path), "messages": [{"role": "user", "content": "hi"}]}
    cfg = WispConfig().replace(graph_max_iterations=7)
    gs = GraphState.from_session(sess, cfg)
    assert gs.session_id == "s1"
    assert gs.workspace == str(tmp_path)
    assert gs.max_iterations == 7
    # Malformed session fallback
    gs2 = GraphState.from_session(None, None)  # type: ignore[arg-type]
    assert gs2.status == GraphStatus.IN_PROGRESS
