"""Live-model E2E: token streaming + subagent heartbeat (opt-in).

Runs ONLY when WISP_E2E_LIVE=1 and WISP_API_KEY are set — never in CI.

Two regression scenarios from real REPL use:

1. Token streaming: content used to be hoarded by the CLI buffer and
   painted as one block at the first boundary. The core must DELIVER
   multiple content deltas across wall-clock time (the transport paints
   each immediately); a single monolithic content event means something
   upstream coalesced the stream again.

2. Subagent heartbeat: a blocking spawn/fanout used to sit silent for
   the child's whole budget (~240s+), which users read as a hang. The
   executor must interleave '⏳ ... running… Ns' heartbeats while the
   child works, plus orchestrator lifecycle events.

Run:
    WISP_E2E_LIVE=1 WISP_PROVIDER=openai \
    WISP_API_BASE=https://openrouter.ai/api/v1 \
    WISP_API_KEY=sk-or-... WISP_MODEL=<id> \
    python -m pytest tests/e2e_live_streaming.py -v -x -s
"""

from __future__ import annotations

import asyncio
import os
import re
import time

import pytest

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("WISP_E2E_LIVE") != "1" or not os.environ.get("WISP_API_KEY"),
        reason="live E2E requires WISP_E2E_LIVE=1 + WISP_API_KEY",
    ),
]

# Real researcher children legitimately take minutes (240s budget + retry).
TURN_TIMEOUT = float(os.environ.get("WISP_E2E_TURN_TIMEOUT", "600"))


def _root(tmp_path):
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig

    cfg = WispConfig().replace(workspace=str(tmp_path), permission_mode="full")
    root = CompositionRoot(config=cfg)
    root.start()
    return root


async def _collect_timed(runtime, session, prompt):
    """Collect events with local arrival timestamps."""
    timed = []
    async for ev in runtime.run_turn(session, prompt):
        timed.append((time.monotonic(), ev))
    return timed


def _timeline(timed, limit=40):
    t0 = timed[0][0] if timed else 0.0
    lines = []
    for t, e in timed[:limit]:
        kind = e.get("type", "?")
        detail = (e.get("message") or e.get("text") or e.get("name")
                  or e.get("kind") or "")
        lines.append(f"{t - t0:7.2f}s  {kind:12} {str(detail)[:70]}")
    return "\n".join(lines)


class TestTokenStreaming:
    @pytest.mark.asyncio
    async def test_content_deltas_arrive_progressively(self, tmp_path):
        root = _root(tmp_path)
        session = await root.runtime.get_or_create_session(
            session_id=f"e2e-stream-{int(time.time())}",
            model=root.config.model,
            workspace=str(tmp_path),
        )
        prompt = (
            "No tools. Answer directly from your own knowledge in 4-5 "
            "sentences: what are the main T cell subsets and what does "
            "each one do?"
        )
        timed = await asyncio.wait_for(
            _collect_timed(root.runtime, session, prompt), TURN_TIMEOUT
        )
        print(f"\n=== event timeline ({len(timed)} events) ===")
        print(_timeline(timed))

        deltas = [(t, e) for t, e in timed if e.get("type") == "content"]
        text = "".join(e.get("text", "") for _, e in deltas)
        assert len(text) > 200, f"no real answer: {text!r}"

        # Streaming proof: several deltas spread over measurable time.
        # The old buffered path delivered ONE content event at done.
        assert len(deltas) >= 3, (
            f"content arrived as {len(deltas)} chunk(s) — stream coalesced"
        )
        span = deltas[-1][0] - deltas[0][0]
        assert span >= 0.5, (
            f"all {len(deltas)} deltas landed within {span:.2f}s — not streaming"
        )
        print(f"\nstreaming OK: {len(deltas)} deltas over {span:.1f}s, "
              f"{len(text)} chars")
        root.shutdown()


class TestSubagentHeartbeat:
    @pytest.mark.asyncio
    async def test_spawn_emits_heartbeat_while_child_works(self, tmp_path):
        root = _root(tmp_path)
        session = await root.runtime.get_or_create_session(
            session_id=f"e2e-heartbeat-{int(time.time())}",
            model=root.config.model,
            workspace=str(tmp_path),
        )
        prompt = (
            "Use subagents to research online the two-signal model of T "
            "cell activation (TCR signaling plus co-stimulation). Summarize "
            "the mechanism in one short paragraph."
        )
        t0 = time.monotonic()
        timed = await asyncio.wait_for(
            _collect_timed(root.runtime, session, prompt), TURN_TIMEOUT
        )
        elapsed = time.monotonic() - t0
        print(f"\n=== event timeline ({len(timed)} events, {elapsed:.1f}s) ===")
        print(_timeline(timed, limit=80))

        events = [e for _, e in timed]
        tool_names = [e.get("name", "") for e in events if e.get("type") == "tool_call"]
        delegation = {"spawn", "fanout", "spawn_background"}
        assert any(n in delegation for n in tool_names), tool_names

        sys_msgs = [str(e.get("message", "")) for e in events
                    if e.get("type") == "system"]
        heartbeats = [m for m in sys_msgs if m.startswith("⏳")]
        assert heartbeats, f"no heartbeat while child ran; sys={sys_msgs}"

        kinds = [str(e.get("kind", "")) for e in events if e.get("type") == "subagent"]
        assert any(k.startswith("task_") for k in kinds), kinds

        answer = "".join(e.get("text", "") for e in events if e.get("type") == "content")
        assert len(answer) > 100, f"no final answer; tail={events[-3:]}"

        # Heartbeats must be INTERLEAVED between child start and result,
        # not dumped after — check ordering by arrival index.
        idx_started = next(i for i, (_, e) in enumerate(timed)
                           if e.get("type") == "subagent")
        idx_result = next(i for i, (_, e) in enumerate(timed)
                          if e.get("type") == "tool_result"
                          and e.get("name") in delegation)
        idx_tick = next(i for i, (_, e) in enumerate(timed)
                        if e.get("type") == "system"
                        and str(e.get("message", "")).startswith("⏳"))
        assert idx_started < idx_tick < idx_result

        print(f"\nheartbeat OK: {len(heartbeats)} tick(s) interleaved; "
              f"turn {elapsed:.1f}s; tools={tool_names}")
        root.shutdown()
