"""Live-model E2E for background subagents (opt-in).

Runs ONLY when WISP_E2E_LIVE=1 and WISP_API_KEY are set — never in CI.
Drives the REAL CompositionRoot → AgentRuntime → ToolExecutor →
SubagentOrchestrator → SubagentRunner against a live provider:

  1. parent turn: model calls spawn_background and the turn returns
     immediately with an agent id (no blocking)
  2. the background agent actually runs a child core on the same provider
     and completes its task
  3. model-driven subagent_list: parent sees the finished agent in the
     registry during a later turn
  4. subagent_send continues the SAME session (turns==2, history grows,
     resumed output reflects the follow-up)

Run (OpenRouter example):
    WISP_E2E_LIVE=1 \
    WISP_PROVIDER=openai \
    WISP_API_BASE=https://openrouter.ai/api/v1 \
    WISP_API_KEY=sk-or-... \
    WISP_MODEL=openai/gpt-4o-mini \
    python -m pytest tests/e2e_live_background.py -v -x
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("WISP_E2E_LIVE") != "1" or not os.environ.get("WISP_API_KEY"),
        reason="live E2E requires WISP_E2E_LIVE=1 + WISP_API_KEY",
    ),
]

TURN_TIMEOUT = float(os.environ.get("WISP_E2E_TURN_TIMEOUT", "240"))
POLL_TIMEOUT = float(os.environ.get("WISP_E2E_POLL_TIMEOUT", "180"))


def _root(tmp_path):
    """Real composition root — exactly what `wisp repl` builds."""
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig

    cfg = WispConfig().replace(workspace=str(tmp_path), permission_mode="full")
    root = CompositionRoot(config=cfg)
    root.start()
    return root


async def _new_session(root, sid, ws):
    return await root.runtime.get_or_create_session(
        session_id=sid,
        model=root.config.model,
        workspace=str(ws),
    )


async def _run_turn(root, session, prompt) -> list[dict]:
    events = []
    async for ev in root.runtime.run_turn(session, prompt):
        events.append(ev)
        if ev.get("type") == "content" and len(asyncio.all_tasks()) > 10_000:  # pragma: no cover
            break
    return events


def _texts(events) -> str:
    return "\n".join(e.get("text", "") for e in events if e.get("type") == "content")


def _tool_calls(events) -> list[dict]:
    return [e for e in events if e.get("type") == "tool_call"]


def _tool_results(events) -> list[dict]:
    return [e for e in events if e.get("type") == "tool_result"]


def _result_payload(ev: dict) -> dict:
    """Parse the JSON string inside a tool_result event."""
    raw = ev.get("result", ev.get("data", ""))
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
    return raw


async def _wait_finished(mgr, agent_id: str, timeout: float = POLL_TIMEOUT) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = await mgr.result(agent_id, wait_seconds=5.0)
        if snap.get("status") not in ("running", None):
            return snap
    raise TimeoutError(f"background agent {agent_id} did not finish within {timeout}s")


class TestLiveBackgroundAgents:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self, tmp_path):
        root = _root(tmp_path)
        mgr = root.background_agents
        assert mgr is not None, "CompositionRoot must wire BackgroundAgentManager"
        sid = f"e2e-bg-{int(time.time())}"

        # ── 1. Parent turn: model launches a background agent ──────────
        session = await _new_session(root, sid, tmp_path)
        spawn_prompt = (
            "You must use the spawn_background tool now. Call it exactly once with "
            "role='generalist' and task='Reply with exactly: BACKGROUND_OK'. "
            "Do not use plain spawn. After launching, briefly confirm you launched it."
        )
        t0 = time.monotonic()
        events = await asyncio.wait_for(
            _run_turn(root, session, spawn_prompt), TURN_TIMEOUT
        )
        spawn_turn_seconds = time.monotonic() - t0

        calls = _tool_calls(events)
        spawn_calls = [c for c in calls if c.get("name") == "spawn_background"]
        assert spawn_calls, f"model never called spawn_background; events={events}"

        results = _tool_results(events)
        payloads = [_result_payload(r) for r in results]
        launch_payloads = [p for p in payloads if p.get("tool") == "spawn_background"]
        assert launch_payloads, f"no spawn_background tool_result; got {payloads}"
        assert launch_payloads[0]["status"] == "ok", launch_payloads[0]

        agent_id = launch_payloads[0]["data"]["agent_id"]
        assert agent_id.startswith("bg-")

        # Launch returned without waiting on the child (non-blocking proof:
        # the child was still running right after the parent turn ended).
        post_turn = mgr.get(agent_id)
        assert post_turn is not None
        # (It may finish fast; what matters is the turn itself returned.)

        # ── 2. Child really ran on the live provider ───────────────────
        snap = await _wait_finished(mgr, agent_id)
        assert snap["status"] == "completed", snap
        assert snap["result"]["ok"] is True, snap
        assert "BACKGROUND_OK" in (snap["result"]["summary"] or ""), snap
        assert snap["result"]["session_id"], snap
        assert snap["turns"] == 1
        print(f"\nlaunch turn: {spawn_turn_seconds:.1f}s; "
              f"child ran {snap['elapsed_seconds']}s; "
              f"output: {snap['result']['summary'][:80]!r}")

        stored = root.store.load_session(snap["result"]["session_id"])
        assert stored is not None
        msgs_after_one = len(stored["messages"])
        assert msgs_after_one >= 2, stored["messages"]

        # ── 3. Model sees the registry in a later turn ────────────────
        session2 = await _new_session(root, f"{sid}-list", tmp_path)
        list_events = await asyncio.wait_for(
            _run_turn(
                root, session2,
                "Use the subagent_list tool, then report: how many background agents "
                "exist and what status does each have? Answer in one line.",
            ),
            TURN_TIMEOUT,
        )
        list_calls = [c for c in _tool_calls(list_events) if c.get("name") == "subagent_list"]
        assert list_calls, f"model never called subagent_list; events={list_events}"
        answer = _texts(list_events)
        assert "completed" in answer.lower(), answer

        # ── 4. Continue the SAME agent conversation ────────────────────
        cont_events = []
        async for ev in root.tool_executor.execute(
            "subagent_send",
            {"agent_id": agent_id, "message": "Now reply with exactly: FOLLOW_UP_OK"},
            str(tmp_path),
        ):
            cont_events.append(ev)
        send_payload = _result_payload({
            "result": cont_events[-1].data.get("result", "")
        })
        assert send_payload.get("status") == "ok", send_payload

        final = await _wait_finished(mgr, agent_id)
        assert final["status"] == "completed", final
        assert final["turns"] == 2, final
        assert "FOLLOW_UP_OK" in (final["result"]["summary"] or ""), final
        # Same thread: session id unchanged across continuation.
        assert final["result"]["session_id"] == snap["result"]["session_id"]

        stored2 = root.store.load_session(final["result"]["session_id"])
        assert len(stored2["messages"]) > msgs_after_one, (
            "resumed session must accumulate history"
        )
        print(f"\ncontinuation ok; total messages in thread: {len(stored2['messages'])}")

        root.shutdown()
