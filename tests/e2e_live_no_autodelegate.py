"""Live-model E2E: auto-delegation removal (opt-in).

Runs ONLY when WISP_E2E_LIVE=1 and WISP_API_KEY are set — never in CI.

Regression scenario from real use: a prompt that *says* "use subagents"
used to be intercepted by the runtime's delegation analyzer, which
force-launched a researcher with a 180s wall clock, burned ~4.5 minutes
on doomed retries, spammed duplicated timeout warnings, and only then
let the model answer. After removal, the prompt must reach the model
untouched and the MAIN AGENT decides — via explicit tool calls — how
(or whether) to delegate.

Asserts:
  - no interception banner ("Auto-delegating to subagents...")
  - no [DELEGATION FAILED] marker injected into the turn
  - the turn completes inside the normal turn budget (no forced stall)
  - the main agent drives tools itself (research or delegation tool)

Run:
    WISP_E2E_LIVE=1 WISP_PROVIDER=openai \
    WISP_API_BASE=https://openrouter.ai/api/v1 \
    WISP_API_KEY=sk-or-... WISP_MODEL=<id> \
    python -m pytest tests/e2e_live_no_autodelegate.py -v -x
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("WISP_E2E_LIVE") != "1" or not os.environ.get("WISP_API_KEY"),
        reason="live E2E requires WISP_E2E_LIVE=1 + WISP_API_KEY",
    ),
]

# Generous: a genuine multi-child fanout + web rate-limits legitimately
# takes minutes. The assertion guards against the old forced-stall
# failure mode (~270s wasted before failing), not against slow work.
TURN_TIMEOUT = float(os.environ.get("WISP_E2E_TURN_TIMEOUT", "480"))


def _root(tmp_path):
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig

    cfg = WispConfig().replace(workspace=str(tmp_path), permission_mode="full")
    root = CompositionRoot(config=cfg)
    root.start()
    return root


class TestNoAutoDelegation:
    @pytest.mark.asyncio
    async def test_prompt_reaches_model_untouched(self, tmp_path):
        # Guard: the interception machinery is really gone.
        from wisp.core.runtime import AgentRuntime
        from wisp.config import WispConfig

        assert not [a for a in dir(AgentRuntime) if "delegat" in a.lower()]
        assert not [f.name for f in WispConfig().__dataclass_fields__.values()
                    if "delegat" in f.name]

        root = _root(tmp_path)
        session = await root.runtime.get_or_create_session(
            session_id=f"e2e-nodeleg-{int(time.time())}",
            model=root.config.model,
            workspace=str(tmp_path),
        )

        # The exact prompt shape that used to trigger the interceptor.
        prompt = (
            "use subagents to research online for the JEPA algorithm: "
            "search the web for what JEPA is, who introduced it, and give "
            "a three-sentence summary with sources."
        )

        t0 = time.monotonic()
        events = await asyncio.wait_for(
            _collect(root.runtime, session, prompt), TURN_TIMEOUT
        )
        elapsed = time.monotonic() - t0

        sys_msgs = [e.get("message", "") for e in events if e.get("type") == "system"]
        assert not any("Auto-delegating" in m for m in sys_msgs), sys_msgs
        assert not any("delegation failed" in m.lower() for m in sys_msgs), sys_msgs

        contents = "\n".join(e.get("text", "") for e in events if e.get("type") == "content")
        assert len(contents) > 100, f"model produced no answer; events={_summary(events)}"

        # The MAIN agent must have driven at least one research/delegation
        # tool itself — interception would have hidden this decision.
        tool_names = [e.get("name", "") for e in events if e.get("type") == "tool_call"]
        research_tools = {"web_search", "web_fetch", "spawn", "fanout",
                          "spawn_background", "orchestrate_vote",
                          "orchestrate_map_reduce", "orchestrate_chain"}
        assert any(n in research_tools for n in tool_names), (
            f"model used no research/delegation tool: {tool_names}"
        )

        # No forced multi-minute stall: the whole turn must fit the budget
        # with room to spare (the old path alone ate ~270s).
        assert elapsed < TURN_TIMEOUT, elapsed
        print(f"\nturn completed in {elapsed:.1f}s; tools={tool_names}")
        print(f"answer excerpt: {contents[:200]!r}")
        root.shutdown()


async def _collect(runtime, session, prompt) -> list[dict]:
    events = []
    async for ev in runtime.run_turn(session, prompt):
        events.append(ev)
    return events


def _summary(events) -> str:
    return [(e.get("type"), str(e)[:60]) for e in events][:20]
