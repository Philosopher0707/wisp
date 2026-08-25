"""TUI wiring: approval controller contract + local event routing."""

import asyncio

import pytest

from wisp.approval_state import ApprovalSessionState
from wisp.transport.tui import TUIApprovalController


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestApprovalController:
    def test_prompt_mode_asks_then_y_approves_once(self):
        c = TUIApprovalController(notify=lambda m: None)
        seen = []

        async def scenario():
            task = asyncio.ensure_future(c.approve({"name": "read_file", "arguments": {}}))
            await asyncio.sleep(0)
            seen.append(c.state.should_ask("read_file"))
            c.resolve("y")
            first = await task
            # y approves once — same tool asks again
            second_asked = c.state.should_ask("read_file")
            return first, second_asked

        first, still_asks = _run(scenario())
        assert first is True
        assert still_asks is True

    def test_shift_y_remembers_tool_for_session(self):
        c = TUIApprovalController(notify=lambda m: None)

        async def scenario():
            t = asyncio.ensure_future(c.approve({"name": "run_bash", "arguments": {}}))
            await asyncio.sleep(0)
            c.resolve("Y")
            await t
            return c.state.should_ask("run_bash")

        assert _run(scenario()) is False  # memory says no more asking

    def test_shift_n_denies_and_remembers(self):
        c = TUIApprovalController()

        async def scenario():
            t = asyncio.ensure_future(c.approve({"name": "rm", "arguments": {}}))
            await asyncio.sleep(0)
            c.resolve("N")
            return await t

        assert _run(scenario()) is False
        assert c.state.should_ask("rm") is False

    def test_a_auto_allows_everything(self):
        c = TUIApprovalController()

        async def scenario():
            t = asyncio.ensure_future(c.approve({"name": "x", "arguments": {}}))
            await asyncio.sleep(0)
            c.resolve("a")
            return await t, c.state.session_policy.name

        result, policy = _run(scenario())
        assert result is True and policy == "AUTO"

    def test_d_blocks_everything_without_asking(self):
        c = TUIApprovalController()

        async def scenario():
            t = asyncio.ensure_future(c.approve({"name": "x", "arguments": {}}))
            await asyncio.sleep(0)
            c.resolve("d")
            await t
            asked = []
            c._notify = lambda m: asked.append(m)
            return c.state.session_policy.name, await c.approve(
                {"name": "other", "arguments": {}}), asked

        policy, allowed, asked = _run(scenario())
        assert policy == "BLOCK" and allowed is False and asked == []

    def test_c_cancels_the_turn(self):
        c = TUIApprovalController()

        async def scenario():
            t = asyncio.ensure_future(c.approve({"name": "x", "arguments": {}}))
            await asyncio.sleep(0)
            c.resolve("c")
            with pytest.raises(asyncio.CancelledError):
                await t

        _run(scenario())

    def test_unknown_key_denies_once(self):
        c = TUIApprovalController()
        assert c.resolve("z") is False  # not delivered

    def test_preview_redacts_secrets(self):
        c = TUIApprovalController()
        _, args_text = c._preview({
            "name": "http_request",
            "arguments": {"url": "https://api.dev", "api_key": "sk-super-secret"},
        })
        assert "sk-super-secret" not in args_text


class TestLocalEventRouting:
    def _widgets(self):
        class Chat:
            def __init__(self):
                self.calls = []
            def append_content(self, t): self.calls.append(("content", t))
            def append_thinking(self, t): self.calls.append(("thinking", t))

        class Status:
            connection_state = ""
            is_streaming = False

        return Chat(), Status()

    def test_tokens_route_by_phase(self):
        from wisp.tui.screens.workspace import route_local_event
        chat, status = self._widgets()
        route_local_event({"type": "token", "phase": "thinking", "text": "hmm"}, chat, status)
        route_local_event({"type": "token", "phase": "content", "text": "Hi"}, chat, status)
        route_local_event({"type": "token", "phase": "content", "text": ""}, chat, status)
        assert chat.calls == [("thinking", "hmm"), ("content", "Hi")]

    def test_tool_lifecycle_marks_ok_and_error(self):
        from wisp.tui.screens.workspace import route_local_event
        chat, status = self._widgets()
        route_local_event({"type": "tool_call", "data": {"name": "read_file"}}, chat, status)
        route_local_event({"type": "tool_result",
                           "data": {"name": "read_file", "duration_ms": 12.4}}, chat, status)
        route_local_event({"type": "tool_result",
                           "data": {"name": "run_bash", "is_error": True}}, chat, status)
        marks = [c[1] for c in chat.calls if c[0] == "content"]
        assert any(m.startswith("✓ read_file · 12ms") for m in marks)
        assert any(m.startswith("✗ run_bash") for m in marks)

    def test_done_resets_streaming_error_sets_message(self):
        from wisp.tui.screens.workspace import route_local_event
        chat, status = self._widgets()
        status.is_streaming = True
        route_local_event({"type": "done"}, chat, status)
        assert status.is_streaming is False
        route_local_event({"type": "error", "message": "boom"}, chat, status)
        assert "boom" in status.connection_state and status.is_streaming is False

    def test_steering_surfaces_in_status(self):
        from wisp.tui.screens.workspace import route_local_event
        chat, status = self._widgets()
        route_local_event({"type": "steering_inject",
                           "data": {"text": "focus on auth"}}, chat, status)
        assert "focus on auth" in status.connection_state


class TestSharedApprovalMemory:
    def test_external_state_unifies_paths(self):
        from wisp.approval_state import SessionPolicy
        shared = ApprovalSessionState()
        local = TUIApprovalController(state=shared)

        async def scenario():
            t = asyncio.ensure_future(local.approve({"name": "run_bash", "arguments": {}}))
            await asyncio.sleep(0)
            local.resolve("a")
            await t
            # A second gate sharing the same state never asks again.
            calls = []
            other = TUIApprovalController(
                notify=lambda m: calls.append(m), state=shared)
            return await other.approve({"name": "anything", "arguments": {}}), calls

        allowed, asked = _run(scenario())
        assert allowed is True and asked == []
        assert shared.session_policy is SessionPolicy.AUTO


class TestWiredWidgets:
    def test_log_viewer_appends_and_caps(self):
        from wisp.tui.widgets.monitor.log_viewer import LogViewer
        lv = LogViewer()
        for i in range(60):
            lv.append(f"line-{i}")
        assert lv.entries[0] == "line-10" and len(lv.entries) == 50

    def test_token_gauge_set_usage(self):
        from wisp.tui.widgets.agents.token_gauge import TokenGauge
        g = TokenGauge()
        g.set_usage(1234, 8000)
        assert g.tokens_used == 1234 and g.token_budget == 8000
        g.set_usage(-5)
        assert g.tokens_used == 0

    def test_task_tree_upsert_and_running(self):
        from wisp.tui.widgets.agents.task_tree import TaskTree
        t = TaskTree()
        t.upsert_task("researcher", "[researcher] digging", False)
        t.upsert_task("coder", "[coder] editing", False)
        assert t.running_count == 2
        t.upsert_task("coder", "[coder] editing", True)
        assert t.running_count == 1
        assert len(t.tasks) == 2

    def test_estimate_session_tokens_mirrors_slash_command(self):
        from wisp.tui.screens.workspace import estimate_session_tokens
        session = {"messages": [{"content": "x" * 400}, {"content": None}]}
        assert estimate_session_tokens(session) == 100
        assert estimate_session_tokens(None) == 0


class TestApprovalModal:
    def test_modal_carries_prompt_context(self):
        from wisp.tui.screens.approval_modal import ApprovalModal
        m = ApprovalModal("run_bash", "command='rm -rf /'")
        assert m.tool_name == "run_bash" and "rm -rf" in m.args_text

    def test_modal_bindings_cover_full_contract(self):
        from wisp.tui.screens.approval_modal import ApprovalModal
        keys = {b[0] for b in ApprovalModal.BINDINGS}
        assert {"y", "Y", "n", "N", "a", "d", "c"} <= keys
