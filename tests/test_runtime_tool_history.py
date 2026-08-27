"""Regression pins: session-persisted tool history must be protocol-consistent.

BUG PINNED HERE (found via live REPL trace, 2026-08-27):

    AgentRuntime.run_turn used to re-serialize tool history from the flat
    event stream as ONE assistant message PER tool call — all assistant
    messages first, then all role:"tool" replies. With N>1 parallel calls
    in a single provider iteration, the first tool reply answered the
    SECOND-to-last assistant message:

        user
        assistant {tool_calls:[read_file]}     <- own message  \\
        assistant {tool_calls:[write_file]}    <- own message   | crossed:
        tool reply(read_file)                  ----------------/ first reply
        tool reply(write_file)                                    answers #1

    OpenAI-compatible endpoints reject that sequence with HTTP 400
    ("tool message must follow the assistant message it belongs to").
    Single-call turns were unaffected, which is why mocks never caught it.

CORRECT SHAPE per provider boundary (pinned below):

        user
        assistant {tool_calls:[A, B]}          <- ONE grouped message
        tool reply(A)                          <- its own replies, in order
        tool reply(B)
        assistant "final"

These tests drive the REAL WispAgentCore + AgentRuntime with a scripted
MockProvider (network layer only) so event production and persistence are
authentic. read_file is read-only, so no approval gate interferes; with
tool_executor=None the core's fallback dispatch runs the real registry.
"""

from __future__ import annotations

import json
import pytest

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.core.runtime import AgentRuntime
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import SecurityPolicy
from wisp.infra.store import UnifiedStore
from wisp.infra.telemetry import Telemetry
from wisp.providers.mock import MockProvider


def _make_runtime(provider: MockProvider, tmp_path) -> tuple[AgentRuntime, str]:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "a.txt").write_text("alpha")
    config = WispConfig().replace(workspace=str(ws))

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


async def _run_turn(runtime: AgentRuntime, session: dict) -> list[dict]:
    return [ev async for ev in runtime.run_turn(session, prompt="go")]


def _tool_call(name: str, args: dict) -> dict:
    return {"function": {"name": name, "arguments": args}}

# ── strict validator + regression tests appended below ──────────────────


def assert_strict_openai_history(msgs: list[dict]) -> None:
    """Replicate strict provider validation of interleaved tool history.

    Rule enforced by OpenAI/vLLM-class endpoints: every role:"tool"
    message must reference a tool_call_id declared on the IMMEDIATELY
    preceding assistant tool_calls message. Several consecutive replies
    may consume ids from that same message; any other role resets it.
    """
    expected_ids: set[str] = set()
    for i, m in enumerate(msgs):
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            expected_ids = {tc["id"] for tc in m["tool_calls"]}
        elif role == "tool":
            assert m.get("tool_call_id") in expected_ids, (
                f"messages[{i}] role='tool' tool_call_id="
                f"{m.get('tool_call_id')!r} does not answer the immediately "
                f"preceding assistant tool_calls {sorted(expected_ids)} — "
                f"strict providers (OpenAI/vLLM) reject this with HTTP 400. "
                f"History: {json.dumps(msgs)[:600]}"
            )
        elif role in ("user", "assistant"):
            expected_ids = set()


@pytest.mark.asyncio
async def test_parallel_tool_calls_persist_as_one_grouped_exchange(tmp_path):
    """THE PIN: N calls in one iteration → exactly ONE assistant message."""
    provider = MockProvider(
        responses=["", "final answer"],
        tool_calls=[[
            _tool_call("read_file", {"path": "a.txt"}),
            _tool_call("list_files", {"path": "."}),
        ]],
    )
    runtime, ws = _make_runtime(provider, tmp_path)
    session = await runtime.get_or_create_session(
        "pin-parallel", model="mock-model", workspace=ws)

    await _run_turn(runtime, session)
    msgs = session["messages"]

    assistants_with_calls = [m for m in msgs if m.get("tool_calls")]
    tools = [m for m in msgs if m.get("role") == "tool"]

    # THE REGRESSION: old code emitted one assistant PER call (= 2 here).
    assert len(assistants_with_calls) == 1, (
        f"expected ONE grouped assistant message carrying both tool_calls "
        f"blocks; got {len(assistants_with_calls)} split messages — "
        f"history: {json.dumps(msgs)[:500]}"
    )
    blocks = assistants_with_calls[0]["tool_calls"]
    assert [b["function"]["name"] for b in blocks] == [
        "read_file", "list_files"]

    # The single assistant message must IMMEDIATELY precede its replies.
    a_idx = msgs.index(assistants_with_calls[0])
    t_idxs = [msgs.index(t) for t in tools]
    assert t_idxs == [a_idx + 1, a_idx + 2], (
        f"replies at {t_idxs} must sit directly after the grouped "
        f"assistant at {a_idx} — history: {json.dumps(msgs)[:500]}"
    )

    # Pairing survives persist/reload (ids generated pre-persist).
    assert [b["id"] for b in blocks] == [t["tool_call_id"] for t in tools]

    assert_strict_openai_history(msgs)

# ── sequential + single-call pins appended below ─────────────────────────


@pytest.mark.asyncio
async def test_sequential_iterations_persist_as_ordered_exchanges(tmp_path):
    """Two SEPARATE iterations → two exchanges, never crossed."""
    provider = MockProvider(
        responses=["", "", "done"],
        tool_calls=[
            [_tool_call("read_file", {"path": "a.txt"})],
            [_tool_call("list_files", {"path": "."})],
        ],
    )
    runtime, ws = _make_runtime(provider, tmp_path)
    session = await runtime.get_or_create_session(
        "pin-sequential", model="mock-model", workspace=ws)

    await _run_turn(runtime, session)
    msgs = session["messages"]

    assistants = [m for m in msgs if m.get("tool_calls")]
    tools = [m for m in msgs if m.get("role") == "tool"]
    assert len(assistants) == 2 and len(tools) == 2

    a0, a1 = (msgs.index(m) for m in assistants)
    t0, t1 = (msgs.index(t) for t in tools)

    # Each exchange is locally well-formed...
    assert t0 == a0 + 1
    assert t1 == a1 + 1
    # ...and globally ordered: A's reply precedes B's request — under the
    # old split-serialization both assistant msgs came FIRST, so t0 > a1.
    assert t0 < a1, (
        f"first exchange's reply (idx {t0}) must precede the second "
        f"exchange's request (idx {a1}) or replies answer the wrong "
        f"assistant call. History: {json.dumps(msgs)[:500]}"
    )
    assert tools[0]["tool_call_id"] in {
        tc["id"] for tc in assistants[0]["tool_calls"]}
    assert tools[1]["tool_call_id"] in {
        tc["id"] for tc in assistants[1]["tool_calls"]}

    assert_strict_openai_history(msgs)


@pytest.mark.asyncio
async def test_single_tool_call_shape_is_unchanged(tmp_path):
    """Common path stays compatible with pre-fix persisted behaviour."""
    provider = MockProvider(
        responses=["", "ok"],
        tool_calls=[[_tool_call("read_file", {"path": "a.txt"})]],
    )
    runtime, ws = _make_runtime(provider, tmp_path)
    session = await runtime.get_or_create_session(
        "pin-single", model="mock-model", workspace=ws)

    await _run_turn(runtime, session)
    msgs = session["messages"]

    assert [m.get("role") for m in msgs][:4] == [
        "user", "assistant", "tool", "assistant"]
    asst = msgs[1]
    assert len(asst["tool_calls"]) == 1
    block = asst["tool_calls"][0]
    assert block["type"] == "function"
    assert isinstance(block["function"]["arguments"], str), (
        "arguments must stay a JSON string after persist/reload")
    assert msgs[2]["tool_call_id"] == block["id"]
    # The read really ran through the registry (fallback dispatch).
    assert "alpha" in str(msgs[2].get("content", ""))

    assert_strict_openai_history(msgs)


# ── direct unit pins on _serialize_tool_exchanges (edge cases) ──────────


def _flat_call(name: str, args: dict) -> dict:
    return {"type": "tool_call", "name": name, "arguments": args}


def _flat_reply(result: str, tcid: str = "") -> dict:
    ev = {"type": "tool_result", "name": "x", "result": result}
    if tcid:
        ev["tool_call_id"] = tcid
    return ev


def test_reply_only_group_synthesizes_matching_block():
    """Gate-refused call streams NO call event — its reply must still get
    an owning tool_calls block or providers see an orphan tool id."""
    from wisp.core.runtime import _serialize_tool_exchanges

    session: dict = {"messages": []}
    field_reader = lambda ev, key: ev.get(key)
    _serialize_tool_exchanges(
        session,
        [{"calls": [], "replies": [_flat_reply("[Blocked: user declined]")]}],
        field_reader,
    )
    msgs = session["messages"]
    assert [m.get("role") for m in msgs] == ["assistant", "tool"]
    assert len(msgs[0]["tool_calls"]) == 1  # block synthesized from the reply
    assert msgs[1]["tool_call_id"] == msgs[0]["tool_calls"][0]["id"]
    assert_strict_openai_history(msgs)


def test_interrupted_call_gets_honest_placeholder_reply():
    """Call recorded but turn died before its result — history must stay
    answerable (no dangling tool_calls without replies) for crash replay."""
    import uuid

    from wisp.core.runtime import _serialize_tool_exchanges

    session: dict = {"messages": []}
    field_reader = lambda ev, key: ev.get(key)
    _serialize_tool_exchanges(
        session,
        [{"calls": [_flat_call("write_file", {"path": "x"})], "replies": []}],
        field_reader,
    )
    msgs = session["messages"]
    assert [m.get("role") for m in msgs] == ["assistant", "tool"]
    block = msgs[0]["tool_calls"][0]
    # Ids must be shared between request and synthesized reply...
    assert msgs[1]["tool_call_id"] == block["id"]
    # ...and must NEVER collide across independent boundaries.
    seen: set[str] = set()
    for m in msgs:
        for tc in m.get("tool_calls", []):
            assert tc["id"] not in seen
            seen.add(tc["id"])
    assert "[no result recorded" in str(msgs[1]["content"])
    assert_strict_openai_history(msgs)


def test_ids_never_collide_across_parallel_boundaries():
    """Parallel calls whose events lack ids each need their own fresh id."""
    from wisp.core.runtime import _serialize_tool_exchanges

    session: dict = {"messages": []}
    field_reader = lambda ev, key: ev.get(key)
    _serialize_tool_exchanges(
        session,
        [{"calls": [_flat_call("read_file", {"path": "a"}),
                    _flat_call("list_files", {"path": "."})],
          "replies": [_flat_reply("ra"), _flat_reply("rb")]}],
        field_reader,
    )
    msgs = session["messages"]
    blocks = msgs[0]["tool_calls"]
    ids = [b["id"] for b in blocks]
    assert len(set(ids)) == 2, f"id collision between parallel calls: {ids}"
    assert [m["tool_call_id"] for m in msgs[1:]] == ids  # positional pairing
    assert_strict_openai_history(msgs)


def test_result_dict_survives_as_json_and_args_stay_strings():
    from wisp.core.runtime import _serialize_tool_exchanges

    session: dict = {"messages": []}
    field_reader = lambda ev, key: ev.get(key)
    _serialize_tool_exchanges(
        session,
        [{"calls": [_flat_call("read_file", {"path": "a"})],
          "replies": [{"type": "tool_result", "name": "read_file",
                       "result": {"status": "ok", "data": "z"}}]}],
        field_reader,
    )
    msgs = session["messages"]
    args = msgs[0]["tool_calls"][0]["function"]["arguments"]
    assert isinstance(args, str) and json.loads(args) == {"path": "a"}
    assert json.loads(msgs[1]["content"]) == {"status": "ok", "data": "z"}

