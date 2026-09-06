"""Pins for issue #2, part B (transcript unification).

Every provider-visible context message the core injects mid-turn must also
land in the persisted session transcript, so resume/replay sees exactly
what the model saw:

  1. Verification nudge  -> user "[SYSTEM] Verification loop: ..."
  2. Steering note       -> user "[steering] <note>"
  3. Budget-exhausted notice -> user "[SYSTEM] Iteration budget exhausted..."

Turn-ephemeral advice (truncation / retry notices) carries no "[SYSTEM]"
prefix and must stay OUT of the transcript.

These tests drive the REAL WispAgentCore + AgentRuntime (same harness as
tests/test_runtime_tool_history.py) so event production and persistence
are authentic.
"""

from __future__ import annotations

import pytest

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.core.runtime import AgentRuntime
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import SecurityPolicy
from wisp.infra.store import UnifiedStore
from wisp.infra.telemetry import Telemetry
from wisp.providers.mock import MockProvider


def _make_runtime(provider: MockProvider, tmp_path, **config_kw) -> tuple[AgentRuntime, str]:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "a.txt").write_text("alpha")
    config = WispConfig().replace(workspace=str(ws), **config_kw)

    def factory():
        return WispAgentCore(
            config=config,
            provider=provider,
            security=SecurityPolicy(),
            tool_executor=None,  # core fallback dispatch -> real registry
        )

    runtime = AgentRuntime(
        store=UnifiedStore(tmp_path / "wisp.db"),
        security=SecurityPolicy(),
        extensions=ExtensionHost(),
        telemetry=Telemetry(),
        core_factory=factory,
        config=config,
    )
    return runtime, str(ws)


def _tool_call(name: str, args: dict) -> dict:
    return {"function": {"name": name, "arguments": args}}


def _user_texts(msgs: list[dict]) -> list[str]:
    return [str(m.get("content", "")) for m in msgs if m.get("role") == "user"]


@pytest.mark.asyncio
async def test_verification_nudge_persisted_in_transcript(tmp_path):
    """Full turn: write_file with no verification -> 2 bounded nudges.

    Both nudges were provider-visible (appended to the core's local
    messages AND yielded as system events); both must survive in
    session["messages"] with the exact user-role shape the model saw.
    """
    provider = MockProvider(
        responses=["", "almost done", "still working", "final answer"],
        tool_calls=[[_tool_call("write_file", {"path": "b.txt", "content": "hello"})]],
    )
    runtime, ws = _make_runtime(provider, tmp_path)
    session = await runtime.get_or_create_session(
        "pin-nudge", model="mock-model", workspace=ws)

    events = [ev async for ev in runtime.run_turn(session, prompt="go")]

    # Sanity: the turn really nudged (bounded self-correction, then done).
    nudges_yielded = [
        e for e in events
        if e.get("type") == "system" and "Verification loop" in str(e.get("message", ""))
    ]
    assert len(nudges_yielded) == 2
    assert any(e.get("type") == "done" for e in events)

    msgs = session["messages"]
    persisted = [t for t in _user_texts(msgs) if t.startswith("[SYSTEM] Verification loop")]
    assert len(persisted) == 2, (
        f"both provider-visible nudges must persist; got {len(persisted)} — "
        f"history roles: {[m.get('role') for m in msgs]}"
    )
    # Pure-append ordering: context lands after exchanges + assistant
    # content, leaving the positional exchange serializer undisturbed.
    asst_idxs = [i for i, m in enumerate(msgs) if m.get("role") == "assistant"]
    nudge_idxs = [i for i, m in enumerate(msgs)
                  if m.get("role") == "user" and str(m.get("content", "")).startswith("[SYSTEM]")]
    assert nudge_idxs and min(nudge_idxs) > max(asst_idxs)
    # Exchange grouping untouched: tool reply still immediately follows
    # its assistant tool_calls block.
    first_tc = next(i for i, m in enumerate(msgs) if m.get("tool_calls"))
    assert msgs[first_tc + 1].get("role") == "tool"


@pytest.mark.asyncio
async def test_steering_note_persisted_in_transcript(tmp_path):
    """Mid-turn steering drained at the tool boundary must persist."""
    provider = MockProvider(
        responses=["", "final"],
        tool_calls=[[_tool_call("read_file", {"path": "a.txt"})]],
    )
    runtime, ws = _make_runtime(provider, tmp_path)
    session = await runtime.get_or_create_session(
        "pin-steering", model="mock-model", workspace=ws)
    runtime.inject_steering("pin-steering", "prefer short answers")

    events = [ev async for ev in runtime.run_turn(session, prompt="go")]

    assert any(e.get("type") == "steering_inject" for e in events), (
        "harness failed: steering note was never drained/yielded"
    )
    msgs = session["messages"]
    assert "[steering] prefer short answers" in _user_texts(msgs), (
        f"steering note missing from transcript: {_user_texts(msgs)}"
    )
    # Pure-append ordering: after the tool exchange, not spliced inside it.
    tool_idx = next(i for i, m in enumerate(msgs) if m.get("role") == "tool")
    steer_idx = next(
        i for i, m in enumerate(msgs)
        if m.get("role") == "user" and str(m.get("content", "")).startswith("[steering]"))
    assert steer_idx > tool_idx


@pytest.mark.asyncio
async def test_budget_notice_persisted_in_transcript(tmp_path):
    """max_iterations=1 forces the budget-exhausted wrap-up path."""
    provider = MockProvider(
        responses=["", "summary of findings"],
        tool_calls=[[_tool_call("read_file", {"path": "a.txt"})]],
    )
    runtime, ws = _make_runtime(provider, tmp_path, max_iterations=1)
    session = await runtime.get_or_create_session(
        "pin-budget", model="mock-model", workspace=ws)

    events = [ev async for ev in runtime.run_turn(session, prompt="go")]

    budget_yielded = [
        e for e in events
        if e.get("type") == "system" and "Iteration budget" in str(e.get("message", ""))
    ]
    assert len(budget_yielded) == 1, "core must mirror the budget notice as a system event"
    assert any(e.get("type") == "done" for e in events)

    msgs = session["messages"]
    persisted = [t for t in _user_texts(msgs)
                 if t.startswith("[SYSTEM] Iteration budget exhausted")]
    assert len(persisted) == 1


@pytest.mark.asyncio
async def test_truncation_warning_not_persisted(tmp_path):
    """System events WITHOUT the [SYSTEM] prefix are turn-ephemeral advice.

    Driven through the same run_turn persist seam with a scripted core:
    the truncation notice must vanish while real content + a [SYSTEM]
    control message persist (positive control proves the seam works).
    """
    from unittest.mock import MagicMock

    scripted = [
        {"type": "system", "message": "Response truncated: max_tokens limit reached. Say 'continue'.",
         "level": "warning"},
        {"type": "system", "message": "[SYSTEM] Verification loop: fake nudge", "level": "warning"},
        {"type": "content", "text": "partial answer"},
        {"type": "done"},
    ]

    class _ScriptedCore:
        async def turn(self, session, prompt, approval_handler=None, steering_drain=None):
            for ev in scripted:
                yield dict(ev)

    store = MagicMock()
    store.load_session.return_value = None
    runtime = AgentRuntime(
        store=store,
        security=SecurityPolicy(),
        extensions=ExtensionHost(),
        telemetry=Telemetry(),
        core_factory=lambda: _ScriptedCore(),
    )
    session = {"id": "pin-ephemeral", "messages": [], "workspace": "/tmp"}

    await _drain(runtime.run_turn(session, "go"))

    texts = [str(m.get("content", "")) for m in session["messages"]]
    assert not any("truncated" in t for t in texts), (
        f"truncation advice leaked into transcript: {texts}"
    )
    assert "[SYSTEM] Verification loop: fake nudge" in texts  # positive control
    assert any("partial answer" in t for t in texts)


async def _drain(aiter):
    out = []
    async for ev in aiter:
        out.append(ev)
    return out
