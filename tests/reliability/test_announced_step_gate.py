"""A reply that announces its next step is not a final answer.

Observed live (SWE-bench pytest-dev__pytest-7236, OpenRouter stealth/space-bunny-alpha):
the model ended rounds with "Let me reproduce the issue first." or "Now let me see the
`PdbInvoke` hook body ... (lines 110-130):" and no tool call. With no code changed, no
completion gate fired, so the engine emitted `done` and the run reported ok=True with
the work never done.
"""

import pytest

from wisp.core.announced_step import announces_next_step


class TestDetector:
    @pytest.mark.parametrize("text", [
        "Let me reproduce the issue first.",
        "Bug reproduced. Now let me see the hook body (lines 110-130):",
        "I found the cause. I'll fix it in unittest.py next.",
        "Next, I'm going to run the test suite.",
        "Reading the file now:",
    ])
    def test_announcement_is_detected(self, text):
        assert announces_next_step(text)

    @pytest.mark.parametrize("text", [
        "",
        "Fixed: `Blueprint` now raises ValueError for an empty name. All tests pass.",
        "The change is complete. Let me know if you want a changelog entry.",
        "I could not reproduce the bug; details above.",
        "Let me summarize: the fix is in place and verified.",
    ])
    def test_final_answer_is_not_an_announcement(self, text):
        assert not announces_next_step(text)


class _ScriptedProvider:
    """One scripted reply per provider round."""

    def __init__(self, rounds: list[list[dict]]):
        self.rounds = list(rounds)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
        self.calls += 1
        yield from (self.rounds.pop(0) if self.rounds else [{"type": "done"}])


def _core(provider):
    from wisp.core.engine import WispAgentCore
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import PermissionMode, SecurityPolicy

    return WispAgentCore(provider=provider,
                         security=SecurityPolicy(permission_mode=PermissionMode.FULL),
                         extensions=ExtensionHost())


def _text(s):
    # The shape the OpenAI-compatible provider emits (wisp/providers/openai.py).
    return [{"type": "content", "text": s}, {"type": "done"}]


class TestEngine:
    @pytest.mark.asyncio
    async def test_announced_step_gets_another_round(self):
        provider = _ScriptedProvider([_text("Let me reproduce the issue first."),
                                      _text("Done: the fix is in place and verified.")])
        events = [e async for e in _core(provider).turn({"id": "s", "messages": [], "model": "m"}, "fix it")]
        assert provider.calls == 2
        assert events[-1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_final_answer_ends_the_turn_at_once(self):
        provider = _ScriptedProvider([_text("Done: the fix is in place and verified.")])
        events = [e async for e in _core(provider).turn({"id": "s", "messages": [], "model": "m"}, "fix it")]
        assert provider.calls == 1
        assert events[-1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_repeated_announcements_still_terminate(self):
        provider = _ScriptedProvider([_text("Let me look.")] * 10)
        events = [e async for e in _core(provider).turn({"id": "s", "messages": [], "model": "m"}, "fix it")]
        assert provider.calls <= 3
        assert events[-1]["type"] == "done"
