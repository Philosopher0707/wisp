"""TDD for the verification loop (test-driven self-correction).

A turn that mutates code (write_file/edit_file) must not complete until a
run_bash verification command has exited 0 *after* the last edit. The
engine nudges the model (bounded) instead of emitting done.
"""

import pytest

from tests.test_core_stateless import _MockProvider


# ── Helpers ────────────────────────────────────────────────────────

def _tool_call(name: str, args: dict | None = None, idx: int = 0) -> dict:
    return {
        "type": "tool_call",
        "name": name,
        "arguments": args or {},
        "id": f"call_{idx}",
    }


def _mock_execute(core, results: dict[str, str]) -> None:
    """Patch core._execute_tool to return canned results per tool name."""

    async def _fake(tc, session, approval_handler=None):
        name = str(tc.get("name", ""))
        yield {
            "type": "tool_result",
            "name": name,
            "tool_call_id": tc.get("id", "call_0"),
            "result": results.get(name, "(no output)"),
        }

    core._execute_tool = _fake


BASH_OK = "all tests passed\n"
BASH_FAIL = "[exit code: 1]\n3 failed, 2 passed\n"


def _scripted_core(core, script: list[list[dict]]) -> None:
    """Provider that plays a scripted list of responses, one per call."""

    class ScriptedProvider:
        def __init__(self):
            self.call_count = 0

        def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
            i = self.call_count
            self.call_count += 1
            for ev in script[min(i, len(script) - 1)]:
                yield ev

    core.provider = ScriptedProvider()


async def _collect(core, session):
    events = []
    async for event in core.turn(session, "fix the bug"):
        events.append(event)
    return events


def _nudges(events):
    # `system` events carry their text under "message" (codebase convention —
    # see CLITransport._render_event reading ev.data.get("message")).
    return [
        e for e in events
        if e.get("type") == "system" and "Verification loop" in str(e.get("message", ""))
    ]


@pytest.fixture
def core():
    from wisp.core.engine import WispAgentCore
    from wisp.infra.security import SecurityPolicy, PermissionMode
    from wisp.infra.extensions import ExtensionHost

    return WispAgentCore(
        provider=_MockProvider(),
        security=SecurityPolicy(permission_mode=PermissionMode.FULL),
        extensions=ExtensionHost(),
    )


@pytest.fixture
def session():
    return {"id": "s1", "messages": [], "model": "qwen", "workspace": "/tmp"}


# ═══════════════════════════════════════════════════════════════════
# Done-path guard
# ═══════════════════════════════════════════════════════════════════

class TestVerificationLoop:
    @pytest.mark.asyncio
    async def test_no_edits_finishes_without_nudge(self, core, session):
        _scripted_core(core, [[
            {"type": "token", "text": "just an answer", "phase": "content"},
            {"type": "done"},
        ]])
        events = await _collect(core, session)

        assert any(e.get("type") == "done" for e in events)
        assert not _nudges(events)

    @pytest.mark.asyncio
    async def test_edit_then_finish_is_nudged_not_done(self, core, session):
        # edit → try to finish → nudged → try again → nudged (bounded 2) → done
        _scripted_core(core, [
            [_tool_call("edit_file", {"path": "a.py"}, 0), {"type": "done"}],
            [{"type": "token", "text": "done!", "phase": "content"}, {"type": "done"}],
        ])
        _mock_execute(core, {"edit_file": "wrote a.py"})

        events = await _collect(core, session)
        nudges = _nudges(events)
        assert len(nudges) == 2  # bounded self-correction, then it must finish
        assert "no verification command" in nudges[0].get("message", "")
        assert any(e.get("type") == "done" for e in events)
        assert core.provider.call_count == 4  # edit + 2 finish attempts + final

    @pytest.mark.asyncio
    async def test_edit_then_bash_exit0_finishes_clean(self, core, session):
        _scripted_core(core, [
            [_tool_call("edit_file", {"path": "a.py"}, 0), {"type": "done"}],
            [_tool_call("run_bash", {"command": "pytest"}, 1), {"type": "done"}],
            [{"type": "token", "text": "verified!", "phase": "content"}, {"type": "done"}],
        ])
        _mock_execute(core, {"edit_file": "wrote a.py", "run_bash": BASH_OK})

        events = await _collect(core, session)
        assert not _nudges(events)
        assert any(e.get("type") == "done" for e in events)

    @pytest.mark.asyncio
    async def test_bash_before_edit_is_stale(self, core, session):
        # Verification ran BEFORE the code change — it must not count.
        _scripted_core(core, [
            [_tool_call("run_bash", {"command": "pytest"}, 0), {"type": "done"}],
            [_tool_call("edit_file", {"path": "a.py"}, 1), {"type": "done"}],
            [{"type": "token", "text": "done!", "phase": "content"}, {"type": "done"}],
        ])
        _mock_execute(core, {"run_bash": BASH_OK, "edit_file": "wrote a.py"})

        events = await _collect(core, session)
        assert _nudges(events), "pre-edit verification must not count"

    @pytest.mark.asyncio
    async def test_edit_after_verification_is_stale(self, core, session):
        # edit → verify ok → edit AGAIN → try to finish: the second edit
        # invalidated the passing run, so a nudge is required.
        _scripted_core(core, [
            [_tool_call("edit_file", {"path": "a.py"}, 0), {"type": "done"}],
            [_tool_call("run_bash", {"command": "pytest"}, 1), {"type": "done"}],
            [_tool_call("edit_file", {"path": "b.py"}, 2), {"type": "done"}],
            [{"type": "token", "text": "done!", "phase": "content"}, {"type": "done"}],
        ])
        _mock_execute(core, {"edit_file": "wrote", "run_bash": BASH_OK})

        events = await _collect(core, session)
        assert _nudges(events)

    @pytest.mark.asyncio
    async def test_failing_bash_nudge_names_the_failure(self, core, session):
        _scripted_core(core, [
            [_tool_call("edit_file", {"path": "a.py"}, 0), {"type": "done"}],
            [_tool_call("run_bash", {"command": "pytest"}, 1), {"type": "done"}],
            [{"type": "token", "text": "should be fine", "phase": "content"}, {"type": "done"}],
        ])
        _mock_execute(core, {"edit_file": "wrote a.py", "run_bash": BASH_FAIL})

        events = await _collect(core, session)
        nudges = _nudges(events)
        assert nudges, "failing verification must trigger a nudge"
        assert "FAILED" in nudges[0].get("message", "")

    @pytest.mark.asyncio
    async def test_disabled_via_config_finishes_immediately(self, session):
        from wisp.core.engine import WispAgentCore
        from wisp.infra.security import SecurityPolicy, PermissionMode
        from wisp.infra.extensions import ExtensionHost

        config = type("Cfg", (), {"verification_loop": False})()
        core = WispAgentCore(
            provider=_MockProvider(),
            security=SecurityPolicy(permission_mode=PermissionMode.FULL),
            extensions=ExtensionHost(),
            config=config,
        )
        _scripted_core(core, [
            [_tool_call("edit_file", {"path": "a.py"}, 0), {"type": "done"}],
            [{"type": "token", "text": "done!", "phase": "content"}, {"type": "done"}],
        ])
        _mock_execute(core, {"edit_file": "wrote a.py"})

        events = await _collect(core, session)
        assert not _nudges(events)
        assert any(e.get("type") == "done" for e in events)
        assert core.provider.call_count == 2  # no extra round-trips
