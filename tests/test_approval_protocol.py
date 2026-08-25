"""Approval-protocol integrity: denied/lost approvals must not desync the
tool-calling protocol or replay identical calls forever.

Live repro (pty, openrouter): tool_call → approve → result never rendered →
identical tool_call re-emitted → approval again → infinite loop that looks
like a hung spinner. Root causes pinned here:
  1. Denied calls were never registered as assistant tool_calls + tool role
     replies, so the provider saw its own function_call vanish and replayed.
  2. Engine gate AND ToolExecutor both asked the same handler (double prompt).
  3. An unanswered approval prompt gave zero feedback — indistinguishable
     from work in progress.
"""


import pytest

from tests.test_core_stateless import _MockProvider


def _make_core(provider, security):
    from wisp.core.engine import WispAgentCore
    from wisp.infra.extensions import ExtensionHost
    from wisp.tool_executor import ToolExecutor
    from wisp.config import WispConfig

    return WispAgentCore(
        provider=provider,
        security=security,
        extensions=ExtensionHost(),
        tool_executor=ToolExecutor(WispConfig().replace(workspace="/tmp")),
        config=WispConfig().replace(workspace="/tmp"),
    )


class TestDeniedCallProtocolIntegrity:
    @pytest.mark.asyncio
    async def test_denied_tool_gets_tool_reply_in_history(self):
        """A user-declined call must land in history as assistant.tool_calls
        plus a refusal role:'tool' message, so the model can change course."""
        calls = {"n": 0}

        class ReplayProvider(_MockProvider):
            def generate_stream_events(self, system_prompt, messages,
                                       tools=None, checkpoint_every=50):
                calls["n"] += 1
                if calls["n"] == 1:
                    yield {"type": "tool_call", "name": "run_bash",
                           "arguments": {"command": "echo hi"},
                           "id": "call_deny_1"}
                else:
                    # Second round-trip must SEE the refusal
                    self.second_messages = messages
                    yield {"type": "content", "text": "understood"}

        from wisp.infra.security import SecurityPolicy, PermissionMode

        provider = ReplayProvider()
        core = _make_core(provider, SecurityPolicy(
            permission_mode=PermissionMode.ASK_ALL))

        decisions = []

        async def handler(event):
            decisions.append(event.get("name"))
            return False  # decline

        session = {"id": "t", "messages": [], "model": "m",
                   "workspace": "/tmp"}
        texts = []
        async for ev in core.turn(session, "do it",
                                  approval_handler=handler):
            if ev.get("type") == "content":
                texts.append(ev.get("text") or "")

        assert "run_bash" in decisions
        # History must be protocol-consistent for the follow-up round-trip
        second = getattr(provider, "second_messages", None)
        assert second is not None, "provider was never consulted again"
        assistant_with_calls = [m for m in second
                                if m.get("role") == "assistant"
                                and m.get("tool_calls")]
        tool_replies = [m for m in second if m.get("role") == "tool"]
        assert assistant_with_calls, \
            "declined call vanished from history — provider will replay it"
        assert tool_replies, "no tool reply for the declined call"
        assert any("eclin" in str(m.get("content", "")) or
                   "lock" in str(m.get("content", "")).lower()
                   for m in tool_replies)


class TestSinglePromptPerTool:
    @pytest.mark.asyncio
    async def test_gated_write_prompts_once(self):
        """Engine gate + executor must not both ask: one prompt per call."""
        asks = []

        class OneCallProvider(_MockProvider):
            def generate_stream_events(self, system_prompt, messages,
                                       tools=None, checkpoint_every=50):
                if not any(m.get("role") == "tool" for m in messages):
                    yield {"type": "tool_call", "name": "edit_file_multi",
                           "arguments": {
                               "path": "/tmp/approval_once.txt",
                               "edits": [{"old_text": "a", "new_text": "b"}]},
                           "id": "call_once"}
                else:
                    yield {"type": "content", "text": "ok"}

        from wisp.infra.security import SecurityPolicy, PermissionMode

        async def counting_handler(event):
            asks.append(event.get("name"))
            return True

        provider = OneCallProvider()
        # AUTO_EDIT is the default REPL mode: bash is exactly the shape the
        # live pty repro double-prompted on.
        core = _make_core(provider, SecurityPolicy(
            permission_mode=PermissionMode.AUTO_EDIT))
        session = {"id": "t2", "messages": [], "model": "m",
                   "workspace": "/tmp"}
        open("/tmp/approval_once.txt", "w").write("a\n")
        async for _ in core.turn(session, "edit it",
                                 approval_handler=counting_handler):
            pass
        assert len(asks) <= 1, (
            f"tool prompted {len(asks)}× per call: {asks} — double-gate "
            f"fatigue trains users to mash y and miss real prompts")
