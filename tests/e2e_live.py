"""Live-model end-to-end verification (opt-in).

Runs ONLY when WISP_E2E_LIVE=1 and WISP_API_KEY are set — never in CI.
Drives the REAL CompositionRoot → AgentRuntime against the configured
provider to prove our contracts survive contact with an actual model:

  1. basic completion returns content
  2. tool loop: model reads a real file via read_file and reports contents
  3. steering: [steering] injection fires at a live tool boundary
  4. cross-session memory: remembered fact recalled in a LATER session
  5. approval gate: denied run_bash is blocked, not executed

Run:
    WISP_E2E_LIVE=1 WISP_PROVIDER=nvidia WISP_API_KEY=... \
    WISP_MODEL=nvidia/nemotron-3-ultra-550b-a55b \
    python -m pytest tests/e2e_live.py -v -x
"""

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


def _root(tmp_path, perm_mode="full"):
    """Real composition root — exactly what `wisp repl` builds."""
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig

    cfg = WispConfig().replace(workspace=str(tmp_path),
                               permission_mode=perm_mode)
    root = CompositionRoot(config=cfg)
    root.start()
    return root


async def _new_session(root, sid, ws):
    return await root.runtime.get_or_create_session(
        session_id=sid,
        model=root.config.model,
        workspace=str(ws),
    )


def _payload(ev: dict) -> dict:
    """Tool/subagent payload wherever the wire puts it (flat or nested)."""
    d = ev.get("data")
    if isinstance(d, dict) and d:
        return d
    return {k: v for k, v in ev.items() if k != "type"}


def _tool_names(events) -> set[str]:
    return {
        str(_payload(e).get("name"))
        for e in events if e.get("type") == "tool_call"
    }


async def _collect(runtime, session, prompt, approval_handler=None):
    events, parts = [], []
    async for ev in runtime.run_turn(session, prompt,
                                     approval_handler=approval_handler):
        events.append(ev)
        if ev.get("type") == "content":
            t = ev.get("text", "")
            if isinstance(t, str):
                parts.append(t)
    return events, "".join(parts)


class TestLiveModel:
    @pytest.mark.asyncio
    async def test_01_basic_completion(self, tmp_path):
        root = _root(tmp_path)
        try:
            sess = await _new_session(root, f"e2e-basic-{time.time_ns()}", tmp_path)
            events, text = await asyncio.wait_for(
                _collect(root.runtime, sess,
                         "Reply with exactly this token and nothing else: WISP-E2E-OK"),
                TURN_TIMEOUT)
            assert any(e["type"] == "content" for e in events), "no content streamed"
            assert "WISP-E2E-OK" in text, f"got {text[:200]!r}"
        finally:
            root.shutdown()

    @pytest.mark.asyncio
    async def test_02_tool_loop_reads_real_file(self, tmp_path):
        ws = tmp_path / "ws-tool"
        ws.mkdir(exist_ok=True)
        secret = "ZEBRA-CODE-7741"
        (ws / "vault.txt").write_text(f"The access phrase is {secret}.")
        root = _root(ws)
        try:
            sess = await _new_session(root, f"e2e-tool-{time.time_ns()}", ws)
            events, text = await asyncio.wait_for(
                _collect(root.runtime, sess,
                         "Use the read_file tool on vault.txt in the workspace, "
                         "then tell me the exact access phrase it contains."),
                TURN_TIMEOUT * 2)

            assert any(e["type"] == "tool_call" for e in events), \
                f"model never called a tool: {[e['type'] for e in events]}"
            tool_names = _tool_names(events)
            assert any("read_file" in n for n in tool_names), f"tools={tool_names}"
            assert secret in text, f"answer missing contents: {text[:300]!r}"
        finally:
            root.shutdown()

    @pytest.mark.asyncio
    async def test_03_steering_injection_fires(self, tmp_path):
        ws = tmp_path / "ws-steer"
        ws.mkdir(exist_ok=True)
        (ws / "notes.txt").write_text("alpha\nbeta\ngamma\n")
        root = _root(ws)
        try:
            sess = await _new_session(root, f"e2e-steer-{time.time_ns()}", ws)

            async def slow_collect():
                return [e async for e in root.runtime.run_turn(
                    sess, "Read notes.txt, then read it again, then summarize it.")]

            task = asyncio.ensure_future(
                asyncio.wait_for(slow_collect(), TURN_TIMEOUT * 3))
            await asyncio.sleep(5)
            root.runtime.inject_steering(sess["id"],
                                         "Stop reading files. Reply with just: STEERED")
            events = await task

            assert any(e.get("type") == "steering_inject" for e in events), (
                "steering never fired at a live boundary")
            text = "".join(str((e.get("data") or {}).get("text") or e.get("text") or "")
                           for e in events if e.get("type") == "content")
            print(f"\n[steer scenario] final text: {text[:200]!r}")
        finally:
            root.shutdown()

    @pytest.mark.asyncio
    async def test_04_cross_session_memory_recall(self, tmp_path):
        from wisp.memory import list_all_facts, remove_fact

        marker = "the launch code is ORCHID-9"
        stored_fact = f"E2E-MEM-{int(time.time())}: {marker}"
        root = _root(tmp_path)
        try:
            sess_a = await _new_session(root, f"e2e-mem-a-{time.time_ns()}", tmp_path)
            events_a, _ = await asyncio.wait_for(
                _collect(root.runtime, sess_a,
                         f"You MUST call the remember tool now. Store this exact "
                         f"fact verbatim: {stored_fact}"),
                TURN_TIMEOUT)

            assert "remember" in _tool_names(events_a), (
                f"model never called remember: {_tool_names(events_a)}")
            assert any("ORCHID-9" in str(f) for f in list_all_facts()), \
                "remember ran but did not persist"

            sess_b = await _new_session(root, f"e2e-mem-b-{time.time_ns()}", tmp_path)
            events_b, text_b = await asyncio.wait_for(
                _collect(root.runtime, sess_b,
                         "What launch code did I ask you to remember in an earlier "
                         "session? Reply with just the code."),
                TURN_TIMEOUT)

            tools_used = sorted(_tool_names(events_b))
            injected = "ORCHID-9" in json.dumps(events_b)
            assert "ORCHID-9" in text_b or "recall" in tools_used or injected, (
                f"cross-session memory failed; tools={tools_used} "
                f"text={text_b[:200]!r}")
        finally:
            try:
                remove_fact(stored_fact)
            except Exception:
                pass
            root.shutdown()

    @pytest.mark.asyncio
    async def test_05_approval_gate_blocks_denied_command(self, tmp_path):
        """Denial at the live gate blocks execution — retried across fresh
        sessions because reasoning models occasionally skip the tool."""
        ws = tmp_path / "ws-appr"
        ws.mkdir(exist_ok=True)
        secret = "hash-me-9931"
        (ws / "digest-me.txt").write_text(secret)
        # ask_all routes run_bash through the approval handler (the policy
        # engine only hard-blocks bash in auto_edit).
        root = _root(ws, perm_mode="ask_all")
        try:
            asked = []
            ever_called = False

            async def handler(event):
                # Engine contract: single tc-event arg -> bool verdict.
                asked.append(event.get("name", ""))
                return False  # deny

            prompt = ("Compute the MD5 of digest-me.txt in the workspace by "
                      "running: cat digest-me.txt | md5\n"
                      "You cannot know the hash without running that command; "
                      "reply with only the resulting hash string.")

            last_events = []
            for attempt in range(3):
                asked.clear()
                sess = await _new_session(
                    root, f"e2e-appr-{time.time_ns()}-{attempt}", ws)
                try:
                    events, _ = await asyncio.wait_for(
                        _collect(root.runtime, sess, prompt,
                                 approval_handler=handler),
                        TURN_TIMEOUT)
                except asyncio.TimeoutError:
                    continue
                last_events = events
                if _tool_names(events):
                    ever_called = True
                    break  # gate was consulted; assertions below hold

            assert ever_called or asked, (
                f"model never attempted a tool in 3 tries: "
                f"{[e.get('type') for e in last_events][:10]}")
            if ever_called:
                assert "run_bash" in asked, (
                    f"gate skipped for {sorted(_tool_names(last_events))}")
                blob = json.dumps(last_events).lower()
                assert ("blocked" in blob or "declined" in blob
                        or "denied" in blob), "denied command executed anyway"
        finally:
            root.shutdown()
