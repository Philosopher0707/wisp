"""Live-model E2E for orchestration + skill-capture tools (opt-in).

Runs ONLY when WISP_E2E_LIVE=1 and WISP_API_KEY are set — never in CI.
Companion to e2e_live_background.py: verifies the surfaces added after it
against a REAL model, because fakes agree with fakes:

  1. orchestrate_dag — the model invents a valid dependency graph; the
     scheduler runs levels in order and upstream outputs flow downstream
  2. capture_skill — a demonstrated multi-tool workflow is captured from
     the recorder's repetition detector into a discoverable SKILL.md
  3. settlement notification — the parent learns a background agent
     finished WITHOUT calling any tool (operating-context drain)

Run (OpenRouter example):
    WISP_E2E_LIVE=1 \
    WISP_PROVIDER=openai \
    WISP_API_BASE=https://openrouter.ai/api/v1 \
    WISP_API_KEY=sk-or-... \
    WISP_MODEL=openai/gpt-4o-mini \
    python -m pytest tests/e2e_live_tools.py -v -x
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


class TestLiveDag:
    @pytest.mark.asyncio
    async def test_model_drives_dag(self, tmp_path):
        """Model invents a dependency graph; scheduler respects the levels."""
        root = _root(tmp_path)
        sid = f"e2e-dag-{int(time.time())}"
        session = await _new_session(root, sid, tmp_path)

        prompt = (
            "You must use the orchestrate_dag tool exactly once to plan a mini "
            "project with THREE nodes:\n"
            "1. node name='design': task='Reply with exactly DESIGN_DONE'\n"
            "2. node name='build': task='Reply with exactly BUILD_DONE', "
            "depends_on=['design']\n"
            "3. node name='test': task='Reply with exactly TEST_DONE', "
            "depends_on=['build']\n"
            "Each node's role must be 'generalist'. After it finishes, report "
            "the execution order you observed."
        )
        events = await asyncio.wait_for(
            _run_turn(root, session, prompt), TURN_TIMEOUT * 2
        )

        dag_calls = [c for c in _tool_calls(events) if c.get("name") == "orchestrate_dag"]
        assert dag_calls, f"model never called orchestrate_dag; calls={[c.get('name') for c in _tool_calls(events)]}"

        payloads = [_result_payload(r) for r in _tool_results(events)]
        dag_payloads = [p for p in payloads if p.get("tool") == "orchestrate_dag"]
        assert dag_payloads, f"no orchestrate_dag tool_result; got {payloads}"
        data = dag_payloads[0]["data"]
        assert dag_payloads[0]["status"] == "ok", dag_payloads[0]
        assert data["ok"] is True, data
        assert data["error"] is None, data

        # Scheduler ran design before build before test.
        levels = data["level_order"]
        flat = [n for level in levels for n in level]
        assert set(flat) == {"design", "build", "test"}, data
        assert flat.index("design") < flat.index("build") < flat.index("test"), data

        # Every node really succeeded on the live provider.
        summary = data["summary"]
        assert summary.count("[ok]") == 3, summary
        print(f"\ndag levels: {levels}; elapsed {data['elapsed_seconds']}s")
        print(f"summary:\n{summary}")
        root.shutdown()


class TestLiveSkillCapture:
    @pytest.mark.asyncio
    async def test_workflow_captured_from_recorder(self, tmp_path):
        """A demonstrated workflow lands in .agents/skills/ as SKILL.md."""
        root = _root(tmp_path)
        ws = str(tmp_path)
        sid = f"e2e-skill-{int(time.time())}"
        session = await _new_session(root, sid, ws)

        prompt = (
            "Do this EXACT sequence of four tool calls, one at a time:\n"
            "1. run_bash with command='echo step-one'\n"
            "2. list_files with path='.'\n"
            "3. run_bash with command='echo step-one' again\n"
            "4. list_files with path='.' again\n"
            "Then call the capture_skill tool with name='echo-inspect' and "
            "description='Echo then inspect files'. Do NOT pass explicit steps "
            "to capture_skill — let it capture what you just did."
        )
        events = await asyncio.wait_for(_run_turn(root, session, prompt), TURN_TIMEOUT * 2)

        cap_calls = [c for c in _tool_calls(events) if c.get("name") == "capture_skill"]
        assert cap_calls, f"model never called capture_skill; calls={[c.get('name') for c in _tool_calls(events)]}"
        payloads = [_result_payload(r) for r in _tool_results(events)]
        cap_payloads = [p for p in payloads if p.get("tool") == "capture_skill"]
        assert cap_payloads, f"no capture_skill result; got {payloads}"
        data = cap_payloads[0]["data"]
        assert cap_payloads[0]["status"] == "ok", cap_payloads[0]
        assert data["ok"] is True, data
        assert data["merged"] is False, data

        skill_file = (
            __import__("pathlib").Path(ws) / ".agents" / "skills"
            / "echo-inspect" / "SKILL.md"
        )
        assert skill_file.exists(), data
        body = skill_file.read_text(encoding="utf-8")
        assert "wisp_captures: 1" in body
        # Captured from the recorder, not hallucinated: the actual tools used.
        assert "run_bash" in body, body
        assert "list_files" in body, body

        # It must be a first-class citizen of skill discovery.
        from wisp.skills import discover_skills
        skills = discover_skills(ws)
        names = [s.name for s in skills]
        assert "echo-inspect" in names, names
        print(f"\nskill written:\n{body[:400]}")
        root.shutdown()


class TestLiveSettlementNotification:
    @pytest.mark.asyncio
    async def test_parent_notified_without_polling(self, tmp_path):
        """Next turn learns about settled work via the context drain —
        the model must NOT be allowed to call subagent_list here."""
        root = _root(tmp_path)
        mgr = root.background_agents
        assert mgr is not None
        sid = f"e2e-notify-{int(time.time())}"

        session = await _new_session(root, sid, tmp_path)
        spawn_prompt = (
            "Use spawn_background exactly once with role='generalist' and "
            "task='Reply with exactly: NOTIFY_MARKER_42'. Confirm briefly."
        )
        events = await asyncio.wait_for(_run_turn(root, session, spawn_prompt), TURN_TIMEOUT)
        payloads = [_result_payload(r) for r in _tool_results(events)]
        launch = next(p for p in payloads if p.get("tool") == "spawn_background")
        assert launch["status"] == "ok", launch
        agent_id = launch["data"]["agent_id"]

        snap = await _wait_finished(mgr, agent_id)
        assert snap["status"] == "completed", snap
        assert "NOTIFY_MARKER_42" in (snap["result"]["summary"] or ""), snap

        # Fresh turn, tool use forbidden: only the drained notification can
        # tell the model what happened.
        session2 = await _new_session(root, f"{sid}-q", tmp_path)
        ask_events = await asyncio.wait_for(
            _run_turn(
                root, session2,
                "Do NOT call any tools. From your operating context alone: "
                "did any background agent finish recently? If yes, quote its "
                "exact result marker. Answer in one line.",
            ),
            TURN_TIMEOUT,
        )
        tool_names = [c.get("name") for c in _tool_calls(ask_events)]
        assert not tool_names, f"model cheated with tools: {tool_names}"
        answer = _texts(ask_events)
        assert "NOTIFY_MARKER_42" in answer, answer
        print(f"\nmodel answered from notification alone: {answer.strip()[:120]}")
        root.shutdown()
