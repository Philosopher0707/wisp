"""Server-side approval contract: session memory lives in AgentRuntime."""

import asyncio

from wisp.approval_state import SessionPolicy
from wisp.core.runtime import AgentRuntime
from wisp.transport.websocket import WebSocketTransport


class _StubRuntime:
    """Minimal runtime exposing the real approval-memory API."""

    def __init__(self):
        self._approval_states = {}
        from types import MethodType
        self.approval_state = MethodType(AgentRuntime.approval_state, self)
        self.apply_approval_decision = MethodType(
            AgentRuntime.apply_approval_decision, self)


def _connected_transport():
    rt = _StubRuntime()
    t = WebSocketTransport(rt)

    class FakeWS:
        def __init__(self):
            self.sent = []
        async def send_json(self, payload):
            self.sent.append(payload)

    t._current_ws = FakeWS()
    t._session_id = "sess-1"
    return t


class TestRuntimeApprovalMemory:
    def test_y_answers_once_without_memory(self):
        rt = _StubRuntime()
        assert rt.apply_approval_decision("s", "run_bash", "y") is True
        state = rt.approval_state("s")
        assert "run_bash" not in state.allowed_tools
        assert state.session_policy is SessionPolicy.PROMPT

    def test_shift_y_remembers_and_next_call_short_circuits(self):
        rt = _StubRuntime()
        rt.apply_approval_decision("s", "read_file", "Y")
        # Memory path returns True even for a key that would deny.
        assert rt.apply_approval_decision("s", "read_file", "n") is True

    def test_a_then_d_last_wins(self):
        rt = _StubRuntime()
        assert rt.apply_approval_decision("s", "x", "a") is True
        assert rt.apply_approval_decision("s", "x", "d") is False
        # d also denies previously allowed tools via BLOCK policy.
        assert rt.apply_approval_decision("s", "x", "n") is False

    def test_sessions_are_isolated(self):
        rt = _StubRuntime()
        rt.apply_approval_decision("s1", "rm", "N")
        # s2 has no memory of s1's denial and 'y' approves once.
        assert rt.apply_approval_decision("s2", "rm", "y") is True


class TestWebSocketContract:
    def test_auto_policy_skips_the_wire_entirely(self):
        t = _connected_transport()
        t.runtime.apply_approval_decision("sess-1", "anything", "a")

        async def scenario():
            return await t.approve({"name": "anything", "arguments": {}})

        approved = asyncio.new_event_loop().run_until_complete(scenario())
        assert approved is True
        assert t._current_ws.sent == [], "AUTO must not prompt the client"

    def test_decision_key_folds_memory_and_resolves(self):
        t = _connected_transport()

        async def scenario():
            task = asyncio.ensure_future(
                t.approve({"name": "run_bash", "arguments": {}}))
            await asyncio.sleep(0)
            assert t.resolve_decision("Y") is True
            first = await task
            asked_again = len(t._current_ws.sent)
            second = await t.approve({"name": "run_bash", "arguments": {}})
            return first, asked_again, second

        first, prompts_after_memory, second = (
            asyncio.new_event_loop().run_until_complete(scenario()))
        assert first is True
        assert prompts_after_memory == 1, "Y memory must skip the prompt"
        assert second is True

    def test_cancel_key_denies_this_call(self):
        t = _connected_transport()

        async def scenario():
            task = asyncio.ensure_future(
                t.approve({"name": "x", "arguments": {}}))
            await asyncio.sleep(0)
            t.resolve_decision("c")
            return await task

        assert asyncio.new_event_loop().run_until_complete(scenario()) is False

    def test_resolve_decision_noop_when_nothing_pending(self):
        t = _connected_transport()
        assert t.resolve_decision("y") is False

    def test_bool_path_still_works_back_compat(self):
        t = _connected_transport()

        async def scenario():
            task = asyncio.ensure_future(
                t.approve({"name": "x", "arguments": {}}))
            await asyncio.sleep(0)
            t.resolve_approval(True)
            return await task

        assert asyncio.new_event_loop().run_until_complete(scenario()) is True
