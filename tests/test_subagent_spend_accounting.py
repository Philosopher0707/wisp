"""What a subagent is charged must be what it spent, not the size of its final transcript.

`tokens_used` (the figure the orchestrator's global session ceiling counts) was the size of the final message list,
counted once. Every provider call re-sends the system prompt, the tool schemas and the history so far, so a coder
with 8 tool rounds recorded about 3.4k tokens while roughly 54k were sent: the 2,000,000-token ceiling was about 16x
weaker than its number. Providers report no usage in the stream, so this is an estimate, but of the right quantity:
per provider call, the fixed overhead plus the history up to that call as input, and the reply as output.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wisp.config import WispConfig
from wisp.multi_agent._runner import SubagentRunner
from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator
from wisp.multi_agent.task import SubagentContract

# 4 characters per token, every figure below a multiple of 4 so the expected values are exact.
OVERHEAD_CHARS = 12_000  # system prompt + tool schemas: 3,000 tokens per call


def _config() -> WispConfig:
    return WispConfig().replace(
        model="test-model", provider="ollama", workspace="/tmp", chars_per_token=4,
        max_context_tokens=128000, permission_mode="full", ollama_url="http://localhost:11434",
    )


def _conversation() -> list[dict]:
    return [
        {"role": "user", "content": "u" * 400},                                    # 100 tokens
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read_file", "arguments": "a" * 40}}]},
        {"role": "tool", "content": "t" * 800},                                    # 200 tokens
        {"role": "assistant", "content": "f" * 200},                               # the final report
    ]


class TestEstimateSpend:
    def _runner(self) -> SubagentRunner:
        return SubagentRunner(_config(), Path("/tmp"))

    def test_each_call_resends_the_overhead_and_the_history_so_far(self):
        in_tok, out_tok = self._runner()._estimate_spend(_conversation(), OVERHEAD_CHARS)
        # call 1: overhead + user                      = 12,400 chars -> 3,100 tokens in, 40 chars (10) out
        # call 2: overhead + user + call + tool result = 13,240 chars -> 3,310 tokens in, 200 chars (50) out
        assert in_tok == 3_100 + 3_310
        assert out_tok == 10 + 50

    def test_it_is_far_more_than_the_transcript_counted_once(self):
        r = self._runner()
        once = sum(r._estimate_tokens(_conversation())[:2])
        in_tok, out_tok = r._estimate_spend(_conversation(), OVERHEAD_CHARS)
        assert in_tok + out_tok > 15 * once

    def test_without_overhead_the_history_is_still_resent(self):
        in_tok, out_tok = self._runner()._estimate_spend(_conversation(), 0)
        assert in_tok == 100 + 310  # 400 chars, then 400 + 40 + 800 chars
        assert out_tok == 60

    def test_system_messages_count_as_input(self):
        msgs = [{"role": "system", "content": "s" * 400}] + _conversation()
        in_tok, _ = self._runner()._estimate_spend(msgs, 0)
        assert in_tok == (400 + 400) // 4 + (400 + 400 + 40 + 800) // 4

    def test_no_provider_call_means_no_spend(self):
        assert self._runner()._estimate_spend([{"role": "user", "content": "hi"}], OVERHEAD_CHARS) == (0, 0)
        assert self._runner()._estimate_spend([], OVERHEAD_CHARS) == (0, 0)


def _scripted_core(overhead):
    class Core:
        def __init__(self, **_):
            pass

        if overhead is not _MISSING:
            def prompt_overhead_chars(self, session):
                if isinstance(overhead, Exception):
                    raise overhead
                return overhead

        async def turn(self, session, task):
            session["messages"] = _conversation()
            yield {"type": "content", "text": "f" * 200}
            yield {"type": "done"}

    return Core


_MISSING = object()


def _run_core(overhead):
    runner = SubagentRunner(_config(), Path("/tmp"))
    contract = SubagentContract(name="t", task="do it", role="coder", tools=["read_file"], timeout_seconds=30,
                                max_iterations=10, auto_approve=True)
    with patch("wisp.providers.factory.ProviderFactory") as pf, patch("wisp.core.engine.WispAgentCore", _scripted_core(overhead)):
        pf.return_value.from_config.return_value = MagicMock()
        return asyncio.run(runner.run(contract, "/tmp", "prompt"))


def _run_runtime(overhead):
    class Runtime:
        async def get_or_create_session(self, sid, model, ws):
            return {"id": sid, "model": model, "workspace": ws, "messages": [], "compaction_history": [],
                    "created_at": "2024-01-01T00:00:00", "updated_at": "2024-01-01T00:00:00"}

        async def run_turn(self, session, prompt):
            session["messages"] = _conversation()
            yield {"type": "content", "text": "f" * 200}
            yield {"type": "done"}

    if overhead is not _MISSING:
        def prompt_overhead_chars(self, session):
            if isinstance(overhead, Exception):
                raise overhead
            return overhead
        Runtime.prompt_overhead_chars = prompt_overhead_chars

    runner = SubagentRunner(_config(), Path("/tmp"), agent_runtime=Runtime())
    contract = SubagentContract(name="t", task="do it", role="coder", tools=["read_file"], timeout_seconds=30,
                                max_iterations=10, auto_approve=True)
    return asyncio.run(runner.run(contract, "/tmp", "prompt"))


@pytest.mark.parametrize("run", [_run_core, _run_runtime], ids=["core", "runtime"])
class TestTheRunnerChargesTheSpend:
    def test_tokens_used_is_the_estimated_spend(self, run):
        r = run(OVERHEAD_CHARS)
        assert r.success
        assert r.input_tokens == 3_100 + 3_310
        assert r.output_tokens == 60
        assert r.tokens_used == r.input_tokens + r.output_tokens == 6_470

    def test_an_unavailable_overhead_degrades_to_the_history_model(self, run):
        r = run(_MISSING)
        assert r.success
        assert r.tokens_used == 100 + 310 + 60

    def test_an_overhead_that_raises_never_fails_the_run(self, run):
        r = run(RuntimeError("boom"))
        assert r.success
        assert r.tokens_used == 100 + 310 + 60


class TestTheGlobalCeilingIsReached:
    def test_a_second_child_is_refused_once_the_first_has_really_spent_the_budget(self, tmp_path):
        """With the transcript counted once, child 1 recorded ~360 tokens and a 20,000 ceiling never closed."""
        cfg = _config().replace(workspace=str(tmp_path))
        orch = SubagentOrchestrator(config=cfg, workspace=tmp_path)
        orch.set_global_token_budget(20_000)

        class Heavy:
            def __init__(self, **_):
                pass

            def prompt_overhead_chars(self, session):
                return 40_000  # 10,000 tokens per provider call

            async def turn(self, session, task):
                session["messages"] = _conversation()
                yield {"type": "content", "text": "f" * 200}
                yield {"type": "done"}

        async def go():
            with patch("wisp.core.engine.WispAgentCore", Heavy), \
                 patch("wisp.tools.context.get_turn_deadline", return_value=None), \
                 patch.object(orch, "_resolve_worktree", new=AsyncMock(return_value=None)), \
                 patch.object(orch, "_fire_subagent_hook", new=AsyncMock()):
                first = await orch.run(SubagentContract(name="first", task="a", worktree_isolated=False))
                second = await orch.run(SubagentContract(name="second", task="b", worktree_isolated=False))
            return first, second

        first, second = asyncio.run(go())
        assert first.success
        assert orch.get_tokens_consumed() > 20_000
        assert not second.success
        assert "TOKEN BUDGET EXCEEDED" in second.output
