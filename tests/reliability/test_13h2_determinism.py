"""Phase 13-H2 determinism forensics: terminality, context accounting, delivery.

AUDIT ONLY. Every test drives REAL production code (WispAgentCore.turn,
AgentRuntime.run_turn, prune_messages, meter, CLITransport) with deterministic
fakes. Tests that pin defects assert the defect's EXACT shape — they PASS by
documenting current behavior. Nothing here changes production semantics.

Provenance (production paths pinned):
  turn loop / exits ......... wisp/core/stateless.py:325,578-630,641-688,779-844
  turn timeout .............. wisp/core/stateless.py:211,282-296
  stall guard ............... wisp/core/provider_stream.py + stateless.py:1912-1947
  pruner ceilings ........... wisp/core/context_pruner.py:70-99,588-656
  live prune (pre-turn) ..... wisp/core/runtime.py:371-376
  success flag .............. wisp/core/runtime.py:463,588-596
  meter ..................... wisp/transport/cli.py:579-581, wisp/entry.py:171-184,
                              wisp/transport/renderer.py:358-396
  thinking/content flush .... wisp/transport/cli.py:1298-1374,1469-1648
"""

from __future__ import annotations

import asyncio
import io
import json
import threading
import time
from unittest.mock import patch

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.core.events import AgentEvent
from wisp.core.runtime import AgentRuntime
from wisp.core.session_repo import SessionRepository
from wisp.infra.circuit_breaker import CircuitState
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import SecurityPolicy
from wisp.infra.store import UnifiedStore
from wisp.infra.telemetry import Telemetry
from wisp.providers.mock import MockProvider
from wisp.core.context_pruner import PrunerConfig, prune_messages
from wisp.transport.cli import AgentAdapter, CLITransport
from wisp.transport.progress import ProgressTracker
from wisp.transport.renderer import render_turn_stats


# ── Shared deterministic harness ──────────────────────────────────────

async def _allow(ev) -> bool:
    return True


def _read_call(path: str, cid: str) -> dict:
    return {"id": cid, "type": "function",
            "function": {"name": "read_file", "arguments": {"path": path}}}


def _core(provider, tmp_path, ws_files=None, **cfg_kw):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    for name, text in (ws_files or {}).items():
        (ws / name).write_text(text)
    config = WispConfig().replace(workspace=str(ws), **cfg_kw)
    core = WispAgentCore(config=config, provider=provider,
                         security=SecurityPolicy(), tool_executor=None)
    session = {"id": "h2", "messages": [], "workspace": str(ws)}
    return core, session, config


def _run(core, session, prompt="go", **kw):
    kw.setdefault("approval_handler", _allow)

    async def _main():
        return [ev async for ev in core.turn(session, prompt, **kw)]

    return asyncio.run(_main())


def _types(evs) -> list:
    return [e.get("type") for e in evs]


def _texts(evs, etype="content") -> str:
    return "".join(e.get("text", "") for e in evs if e.get("type") == etype)


def _to_agent_event(e: dict) -> AgentEvent:
    data = {k: v for k, v in e.items() if k != "type"}
    return AgentEvent(type=e.get("type", ""), data=data)


class _DictProvider:
    """OpenAI-shaped dict stream. Rounds are thunks so failures raise mid-iteration.

    Records the (already pruned) provider payload bytes per round.
    """

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0
        self.payload_bytes: list[int] = []

    def generate_stream_events(self, system_prompt, messages, tools=None):
        self.calls += 1
        self.payload_bytes.append(len(json.dumps(messages, default=str)))
        script = self._rounds[min(self.calls - 1, len(self._rounds) - 1)]
        yield from script()


class _RecordingMock(MockProvider):
    """MockProvider that records pruned payload bytes per round."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.payload_bytes: list[int] = []
        self.rounds = 0

    def generate_stream_events(self, system_prompt, messages, tools=None):
        self.rounds += 1
        self.payload_bytes.append(len(json.dumps(messages, default=str)))
        yield from super().generate_stream_events(system_prompt, messages, tools)


# Round script thunks (dict-shaped, like providers/openai.py:280-356).
def _content_only(text, reason="stop"):
    def _g():
        yield {"type": "content", "text": text}
        yield {"type": "done", "done_reason": reason}
    return _g


def _tool_round(calls):
    def _g():
        yield {"type": "tool_calls", "calls": calls}
        yield {"type": "done", "done_reason": "tool_calls"}
    return _g


def _think_content_fail(thinking, content, exc):
    def _g():
        if thinking:
            yield {"type": "thinking", "text": thinking}
        if content:
            yield {"type": "content", "text": content}
        raise exc
    return _g


def _think_then_hang(thinking):
    def _g():
        yield {"type": "thinking", "text": thinking}
        threading.Event().wait(30)
        yield {"type": "done", "done_reason": "stop"}  # unreachable pre-stall
    return _g


def _hang():
    def _g():
        threading.Event().wait(30)
        yield {"type": "done", "done_reason": "stop"}
    return _g


def _garbage():
    def _g():
        yield object()
    return _g


class _MockRuntime:
    def __init__(self):
        self.sessions = {}
        self.turns = []


def _history(n, result_size=10000, prefix="f"):
    """Protocol-shaped history: 1 user + N assistant/tool pairs of read_file."""
    msgs = [{"role": "user", "content": "go"}]
    for i in range(n):
        cid = f"c{i}"
        msgs.append({"role": "assistant", "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": "read_file", "arguments": '{"path": "x"}'}}]})
        msgs.append({"role": "tool", "tool_call_id": cid,
                     "content": f"--- FILE: {prefix}{i}.txt | LINES: 300 | "
                                f"SHOWING: 1-300 ---\n" + "x" * result_size})
    return msgs


# ── A. Normal completion (D1) ─────────────────────────────────────────

class TestExitNormal:
    def test_content_only_completes_with_single_done(self, tmp_path):
        core, session, _ = _core(_DictProvider([_content_only("final answer")]), tmp_path)
        evs = _run(core, session)
        assert _types(evs).count("done") == 1
        assert "error" not in _types(evs)
        assert _texts(evs) == "final answer"


# ── B. G1B bare-return reproduction (H1-H1) ───────────────────────────

class TestBareReturn:
    def test_stall_after_thinking_ends_turn_without_done(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        rounds = [
            _tool_round([_read_call("a.txt", "c0")]),
            _think_then_hang("We need now craft final answer"),
        ]
        prov = _DictProvider(rounds)
        core, session, _ = _core(prov, tmp_path, ws_files={"a.txt": "alpha"})
        core.FIRST_TOKEN_DEADLINE_S = 0.2
        core.CHUNK_DEADLINE_S = 0.2
        evs = _run(core, session)
        types = _types(evs)
        # tool round executed …
        assert "tool_call" in types and "tool_result" in types
        # … thinking of the final round was produced …
        assert "We need now craft final answer" in _texts(evs, "thinking")
        # … the stall was reported …
        assert any(e.get("type") == "provider_status"
                   and e.get("status") == "chunk_stall" for e in evs)
        # H3 terminal closure: the turn ends with exactly one terminal error
        # (never done — G1B honesty kept), naming the missing answer.
        assert "done" not in types
        assert types.count("error") == 1
        closure = evs[-1]
        assert closure.get("type") == "error"
        assert "no final answer" in closure.get("message", "")
        assert "reasoning" in closure.get("message", "")
        # stats still computable from the stream (matches incident stats line)
        tracker = ProgressTracker()
        for e in evs:
            tracker.on_event(_to_agent_event(e))
        stats = tracker.on_done()
        assert stats["tools_run"] == 1 and stats["tools_succeeded"] == 1


# ── C. Final content vs thinking ──────────────────────────────────────

class TestThinkingVsContent:
    def test_c1_thinking_only_then_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        core, session, _ = _core(_DictProvider(
            [_think_content_fail("ruminating", None, ConnectionResetError("down"))]),
            tmp_path)
        evs = _run(core, session)
        assert "ruminating" in _texts(evs, "thinking")
        assert _texts(evs, "content") == ""
        assert "tool_call" not in _types(evs)
        assert "done" not in _types(evs)
        # mid-stream diagnostic + one terminal closure naming the missing answer
        assert _types(evs).count("error") == 2
        assert "reasoning" in evs[-1].get("message", "")
        assert "no final answer" in evs[-1].get("message", "")

    def test_c2_thinking_plus_content_then_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        core, session, _ = _core(_DictProvider(
            [_think_content_fail("plan", "partial answer", ConnectionResetError("down"))]),
            tmp_path)
        evs = _run(core, session)
        assert "plan" in _texts(evs, "thinking")
        assert "partial answer" in _texts(evs, "content")  # rendered live …
        assert "done" not in _types(evs)  # … but never completed …
        # … and the terminal closure names the retained partial explicitly.
        assert _types(evs).count("error") == 2
        assert "partial response" in evs[-1].get("message", "")
        assert "no final answer" in evs[-1].get("message", "")

    def test_c3_thinking_content_plus_tools(self, tmp_path):
        prov = MockProvider(
            thinking=["work plan"],
            responses=["progress note"],
            tool_calls=[[_read_call("a.txt", "c0")]])
        core, session, _ = _core(prov, tmp_path, ws_files={"a.txt": "alpha"})
        evs = _run(core, session)
        assert "work plan" in _texts(evs, "thinking")
        assert "tool_result" in _types(evs)
        assert prov._index == 2  # second generation delivered the close
        assert _types(evs).count("done") == 1  # turn continued past content

    def test_c4_content_only_clean(self, tmp_path):
        core, session, _ = _core(_DictProvider([_content_only("the answer")]), tmp_path)
        evs = _run(core, session)
        assert _texts(evs) == "the answer"
        assert _types(evs).count("done") == 1
        assert "error" not in _types(evs)

    def test_c5_content_and_tool_same_generation(self, tmp_path):
        def _both():
            def _g():
                yield {"type": "content", "text": "here is something"}
                yield {"type": "tool_calls", "calls": [_read_call("a.txt", "c0")]}
                yield {"type": "done", "done_reason": "tool_calls"}
            return _g
        core, session, _ = _core(
            _DictProvider([_both(), _content_only("final")]),
            tmp_path, ws_files={"a.txt": "alpha"})
        evs = _run(core, session)
        # valid final TEXT was generated first …
        assert "here is something" in _texts(evs, "content")
        # … and a tool still executed afterwards: content is non-terminal.
        assert "tool_result" in _types(evs)
        assert _types(evs).count("done") == 1


# ── D. Terminal-event uniqueness ──────────────────────────────────────

class TestTerminalUniqueness:
    def test_d2_nontransient_stream_error_single_error_no_done(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")

        def _boom():
            def _g():
                raise ValueError("boom")
                yield {"type": "done", "done_reason": "stop"}
            return _g

        core, session, _ = _core(_DictProvider([_boom()]), tmp_path)
        evs = _run(core, session)
        assert _types(evs).count("done") == 0
        assert _types(evs).count("error") == 1
        assert "Provider stream failed: boom" in (evs[-1].get("message", ""))

    def test_d3_cancelled_turn_no_terminal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        core, session, _ = _core(
            _DictProvider([_think_then_hang("partial")]), tmp_path)
        core.FIRST_TOKEN_DEADLINE_S = 5.0
        core.CHUNK_DEADLINE_S = 5.0

        async def _drain(gen, out):
            async for e in gen:
                out.append(e)

        async def _main():
            evs: list = []
            gen = core.turn(session, "go", approval_handler=_allow)
            task = asyncio.create_task(_drain(gen, evs))
            while not evs:
                await asyncio.sleep(0.01)  # condition wait, not ordering
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return evs

        evs = asyncio.run(_main())
        assert "done" not in _types(evs)
        assert "error" not in _types(evs)

    def test_d4_timeout_error_plus_done_once(self, tmp_path):
        core, session, _ = _core(_DictProvider([_hang()]), tmp_path, turn_timeout=2)
        evs = _run(core, session)
        assert _types(evs).count("done") == 1
        assert _types(evs).count("error") == 1
        assert any("Turn timed out" in e.get("message", "")
                   for e in evs if e.get("type") == "error")

    def test_d6_wrapup_success_summary_plus_done_once_each(self, tmp_path):
        """REWRITTEN by ADR-0039 (PM-21) — it used to assert the defect.

        The previous body asserted F40-1 as the contract: *"MockProvider
        yields objects; the wrap-up path consumes RAW events (stateless.py:
        826-834, ev.get) so it cannot see them: honest error."* That is a
        description of the bug, and it passed because the wrap-up raised
        `AttributeError` on the first typed event and the model's wrap-up
        text was discarded behind a spurious `Max iterations reached`.

        The wrap-up now consumes canonical events from the normalization
        boundary, so the summary is delivered and no error is emitted. What
        this class exists to assert — terminal uniqueness — is unchanged.
        """
        prov = MockProvider(responses=["", ""], tool_calls=[
            [_read_call("a.txt", "c0")], [_read_call("a.txt", "c1")]])
        core, session, _ = _core(
            prov, tmp_path, ws_files={"a.txt": "alpha"}, max_iterations=2)
        evs = _run(core, session)
        # F40-1/F40-2 closed: the wrap-up's text reaches the consumer.
        # (TokenBatch chunks arrive separately — join before matching.)
        joined = "".join(_texts(evs, "content"))
        assert "no more responses" in joined, (
            f"the wrap-up summary was discarded: {_texts(evs, 'content')!r}")
        # No spurious budget error — the provider DID complete the wrap-up.
        assert _types(evs).count("error") == 0, (
            [e for e in evs if e.get("type") == "error"])
        assert _types(evs).count("done") == 1


# ── Verification floor + steering (non-exits that extend turns) ──────

class TestCompletionGateAndSteering:
    def test_verification_floor_forces_two_nudges_then_done(self, tmp_path):
        def _write(n):
            def _g():
                yield {"type": "tool_calls", "calls": [{
                    "id": f"w{n}", "type": "function",
                    "function": {"name": "write_file", "arguments": {
                        "path": f"out{n}.txt", "content": "x"}}}]}
                yield {"type": "done", "done_reason": "tool_calls"}
            return _g

        rounds = [_write(i) for i in range(5)] + [
            _content_only("draft one"), _content_only("draft two"),
            _content_only("verified final")]
        prov = _DictProvider(rounds)
        core, session, _ = _core(prov, tmp_path)
        evs = _run(core, session)
        nudges = [e for e in evs if e.get("type") == "system"
                  and "Verification loop" in e.get("message", "")]
        assert len(nudges) == 2  # grind floor: min_turns=5 tool results, 2 nudges
        assert _types(evs).count("done") == 1
        assert prov.calls == 8  # 5 tool rounds + 3 content rounds

    def test_steering_injection_extends_turn(self, tmp_path):
        drained: list = []

        def _drain_once():
            if not drained:
                drained.append(1)
                return ["stay on target"]
            return []

        rounds = [_tool_round([_read_call("a.txt", "c0")]),
                  _content_only("steered answer")]
        core, session, _ = _core(
            _DictProvider(rounds), tmp_path, ws_files={"a.txt": "alpha"})
        evs = _run(core, session, steering_drain=_drain_once)
        assert any("steer" in t for t in _types(evs))
        assert _types(evs).count("done") == 1


# ── Wrap-up success variant (dict-shaped provider) ────────────────────

class TestWrapUp:
    def test_budget_notice_then_summary_content(self, tmp_path):
        rounds = [_tool_round([_read_call("a.txt", "c0")]),
                  _tool_round([_read_call("a.txt", "c1")]),
                  _content_only("gathered summary")]
        core, session, _ = _core(
            _DictProvider(rounds), tmp_path,
            ws_files={"a.txt": "alpha"}, max_iterations=2)
        evs = _run(core, session)
        assert any("Iteration budget exhausted" in e.get("message", "")
                   for e in evs if e.get("type") == "system")
        assert "gathered summary" in _texts(evs, "content")
        assert _types(evs).count("done") == 1
        assert "error" not in _types(evs)


# ── Circuit-open stream start ─────────────────────────────────────────

class TestCircuitOpen:
    def test_open_breaker_short_circuits_provider(self, tmp_path):
        prov = _DictProvider([_content_only("never reached")])
        core, session, _ = _core(prov, tmp_path)
        core._circuit_breaker._state = CircuitState.OPEN  # test-only poke
        core._circuit_breaker._last_failure_time = time.monotonic()
        evs = _run(core, session)
        assert prov.calls == 0
        assert any(e.get("status") == "circuit_open" for e in evs
                   if e.get("type") == "provider_status")
        assert "done" not in _types(evs)
        # H3 closure: the open round also ends classified, last event first.
        assert _types(evs).count("error") == 2
        assert "no final answer" in evs[-1].get("message", "")


# ── Empty / malformed / immediate-failure rounds ──────────────────────

class TestDegenerateRounds:
    def test_empty_object_stream_surfaces_error_without_done(self, tmp_path, monkeypatch):
        """REWRITTEN by ADR-0041 (PM-23) — it used to assert the defect.

        The previous body documented F43 as current behaviour: *"MockProvider's
        bare StreamComplete is NOT in the core's _BOOKKEEPING_TYPES … the marker
        alone therefore counts as 'meaningful' and the turn ends done(natural)
        with EMPTY content — a silent empty success. Recorded, not fixed."*

        The guard now classifies terminal FIRST and decides meaningfulness from
        the event's payload (ADR-0041 R1/R2), so a bare terminal marker is an
        empty attempt whichever provider emitted it — and the typed path takes
        the same honest path as its dict twin below.
        """
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        core, session, _ = _core(MockProvider(responses=[""]), tmp_path)
        evs = _run(core, session)
        assert "done" not in _types(evs)
        assert any("no usable response" in e.get("message", "")
                   for e in evs if e.get("type") == "error")
        assert _types(evs).count("error") == 2
        assert "no final answer" in evs[-1].get("message", "")

    def test_empty_object_and_empty_dict_streams_agree(self, tmp_path, monkeypatch):
        """ADR-0041 R6: representation must not decide recovery.

        The typed and dict bare-marker streams must produce the same event
        types. Before ADR-0041 they produced `["done"]` and an error
        respectively — the same semantics, two outcomes.
        """
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")

        def _empty():
            def _g():
                yield {"type": "done", "done_reason": "stop"}
            return _g

        core_t, session_t, _ = _core(MockProvider(responses=[""]), tmp_path)
        typed = _types(_run(core_t, session_t))
        core_d, session_d, _ = _core(_DictProvider([_empty()]), tmp_path)
        dicts = _types(_run(core_d, session_d))
        assert typed == dicts, f"typed={typed} dict={dicts}"
        assert "done" not in typed

    def test_empty_dict_stream_surfaces_error_without_done(self, tmp_path, monkeypatch):
        # Dict-shaped empty round ({"type":"done"} only, "done" IS bookkeeping)
        # takes the honest path: error, no done, silent end.
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")

        def _empty():
            def _g():
                yield {"type": "done", "done_reason": "stop"}
            return _g

        core, session, _ = _core(_DictProvider([_empty()]), tmp_path)
        evs = _run(core, session)
        assert "done" not in _types(evs)
        assert any("no usable response" in e.get("message", "")
                   for e in evs if e.get("type") == "error")
        # H3 closure: diagnostic + terminal classification, nothing silent.
        assert _types(evs).count("error") == 2
        assert "no final answer" in evs[-1].get("message", "")

    def test_malformed_event_without_terminal_marker(self, tmp_path, monkeypatch):
        """UPDATED by ADR-0043 (PM-24): the diagnostic changed, the contract did not.

        The fixture yields a bare `object()` — no recognisable type and no
        payload. Before ADR-0043 that counted as response output (it was not in
        the bookkeeping set), so the attempt looked *truncated* and surfaced
        "ended without its terminal marker". Now meaningfulness is decided by
        payload, so an event carrying none is correctly an EMPTY attempt and
        surfaces the empty-stream diagnostic instead.

        The property this test exists for is unchanged: a malformed event must
        never pass silently — no `done`, errors present, and the event is still
        forwarded so the consumer sees what arrived.
        """
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        core, session, _ = _core(_DictProvider([_garbage()]), tmp_path)
        evs = _run(core, session)
        assert "done" not in _types(evs)
        assert any("no usable response" in e.get("message", "")
                   for e in evs if e.get("type") == "error")
        # H3 closure: diagnostic + terminal classification, nothing silent.
        assert _types(evs).count("error") == 2
        assert "no final answer" in evs[-1].get("message", "")

    def test_immediate_transient_failure_single_attempt(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")

        def _down():
            def _g():
                raise ConnectionResetError("down")
                yield {"type": "done", "done_reason": "stop"}
            return _g

        core, session, _ = _core(_DictProvider([_down()]), tmp_path)
        evs = _run(core, session)
        assert "done" not in _types(evs)
        assert any("after 1 attempt" in e.get("message", "")
                   for e in evs if e.get("type") == "error")
        # H3 closure: diagnostic + terminal classification, nothing silent.
        assert _types(evs).count("error") == 2
        assert "no final answer" in evs[-1].get("message", "")


# ── F. Pruner ceiling sweep (N >= 2000 per H1) ────────────────────────

class TestPrunerCeiling:
    NS = [10, 50, 100, 200, 400, 500, 817, 1000, 1500, 2000]

    def _sizes(self, n, result_size=10000):
        msgs = _history(n, result_size)
        raw = sum(len(str(m.get("content", "")).encode()) for m in msgs)
        pruned = prune_messages(msgs, PrunerConfig())
        out = sum(len(str(m.get("content", "")).encode()) for m in pruned)
        return raw, out

    def test_ceiling_holds_for_small_n(self):
        _, out = self._sizes(10)
        assert out <= 200000

    def test_ceiling_broken_once_floor_dominates(self):
        # floor: budget_per_tool = max(500, total // N) (context_pruner.py:594).
        # 500 * N > 200000 for N > 400, so the "total ceiling" cannot hold.
        _, out_500 = self._sizes(500)
        _, out_817 = self._sizes(817)
        assert out_500 > 200000
        assert out_817 > 200000

    def test_pruned_bytes_grow_with_n_not_constant(self):
        outs = [self._sizes(n)[1] for n in (100, 500, 1000, 2000)]
        assert outs == sorted(outs) and len(set(outs)) == len(outs)

    def test_historical_results_condensed_not_full(self):
        pruned = prune_messages(_history(20), PrunerConfig())
        tools = [m for m in pruned if m.get("role") == "tool"]
        olds = tools[:-3]
        assert all(len(str(m["content"]).encode()) <= 2048 for m in olds)
        assert any("pruned" in str(m["content"]).lower() for m in olds)

    def test_recent_results_kept_full(self):
        pruned = prune_messages(_history(20), PrunerConfig())
        tools = [m for m in pruned if m.get("role") == "tool"]
        assert any(len(str(m["content"]).encode()) > 8192 for m in tools[-3:])


# ── G. Pruner failure-open ────────────────────────────────────────────

class TestPrunerFailOpen:
    def _seeded(self, tmp_path, n_old=10):
        ws = tmp_path / "ws"
        ws.mkdir(exist_ok=True)
        (ws / "a.txt").write_text("alpha")
        session = {"id": "h2", "messages": _history(n_old, 10000),
                   "workspace": str(ws)}
        return session

    def test_preflight_failure_falls_back_to_raw(self, tmp_path, monkeypatch):
        import wisp.core.stateless as st
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        session = self._seeded(tmp_path)
        raw = sum(len(str(m.get("content", "")).encode()) for m in session["messages"])
        prov = _RecordingMock(responses=["ok"], tool_calls=[[ _read_call("a.txt", "c9")]])
        config = WispConfig().replace(workspace=session["workspace"])
        core = WispAgentCore(config=config, provider=prov,
                             security=SecurityPolicy(), tool_executor=None)
        with patch.object(st, "prune_messages", side_effect=RuntimeError("pruner down")):
            evs = _run(core, session)
        assert _types(evs).count("done") == 1  # turn survives pruner failure
        assert prov.payload_bytes[0] >= raw  # … by sending raw history

    def test_byte_ceiling_failure_falls_back_to_raw(self, tmp_path, monkeypatch):
        import wisp.core.context_pruner as cp
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        session = self._seeded(tmp_path)
        pruned_now = prune_messages(session["messages"], PrunerConfig())
        small = sum(len(str(m.get("content", "")).encode()) for m in pruned_now)
        assert small < 200000
        prov = _RecordingMock(responses=["ok"], tool_calls=[[_read_call("a.txt", "c9")]])
        config = WispConfig().replace(workspace=session["workspace"])
        core = WispAgentCore(config=config, provider=prov,
                             security=SecurityPolicy(), tool_executor=None)
        with patch.object(cp, "enforce_byte_ceiling", side_effect=RuntimeError("ceil down")):
            evs = _run(core, session)
        assert _types(evs).count("done") == 1
        assert prov.payload_bytes[0] > small  # raw history sent instead


# ── H. History meter vs provider payload ──────────────────────────────

class TestMeterVsPayload:
    NS = [10, 100, 500, 817, 1000]

    def test_retained_history_labeled_not_payload(self, tmp_path):
        adapter = AgentAdapter(_MockRuntime(), WispConfig(), {"messages": []})
        for n in self.NS:
            msgs = _history(n, 4800, prefix="h")
            est = adapter._estimate_tokens(msgs)  # production meter formula
            raw_chars = sum(len(str(m.get("content", ""))) for m in msgs)
            assert est == raw_chars // 4  # meter == raw history, by construction
            pruned = prune_messages(msgs, PrunerConfig())
            payload = sum(len(str(m.get("content", "")).encode()) for m in pruned)
            raw_bytes = sum(len(str(m.get("content", "")).encode()) for m in msgs)
            assert payload < raw_bytes  # PROVIDER_PAYLOAD < RETAINED_HISTORY
            line = render_turn_stats({"turn_number": 6, "tools_run": n,
                                      "tools_succeeded": n, "tools_failed": 0,
                                      "files_changed": [], "elapsed": 1505.0,
                                      "ctx_tokens": est, "ctx_limit": 256000})
            assert "ctx " in line and "%" in line

    def test_incident_scale_meter_shape(self):
        # 970k displayed == 970*1024 est. tokens; /256000 == 388%.
        est, limit = 970 * 1024, 256000
        assert round(100.0 * est / limit) == 388
        line = render_turn_stats({"turn_number": 6, "tools_run": 817,
                                  "tools_succeeded": 801, "tools_failed": 18,
                                  "files_changed": [], "elapsed": 1505.0,
                                  "ctx_tokens": est, "ctx_limit": limit})
        assert "970k" in line and "(388%)" in line


# ── I. Per-round accounting ───────────────────────────────────────────

class TestRoundAccounting:
    def test_payload_grows_across_rounds(self, tmp_path):
        calls = [[_read_call("a.txt", f"c{i}")] for i in range(3)]
        prov = _RecordingMock(
            responses=["", "", ""], tool_calls=calls,
            thinking=["t0", "t1", "t2"])
        # 4th round (final) uses default "[mock: no more responses]" content.
        core, session, _ = _core(prov, tmp_path, ws_files={"a.txt": "alpha"})
        evs = _run(core, session)
        assert prov.rounds == 4
        assert len(prov.payload_bytes) == 4
        assert prov.payload_bytes[-1] > prov.payload_bytes[0]
        assert _types(evs).count("done") == 1
        assert _types(evs).count("tool_result") == 3


# ── J. 817-tool structural reproduction ───────────────────────────────

class TestSynthetic817:
    def test_fifty_rounds_817_calls_reach_budget(self, tmp_path):
        files = {f"f{i}.txt": ("content-%d-" % i) * 20 for i in range(24)}
        per_round = [16] * 49 + [33]  # 784 + 33 = 817
        assert sum(per_round) == 817
        tool_rounds, cid = [], 0
        for width in per_round:
            calls = []
            for _ in range(width):
                calls.append(_read_call(f"f{cid % 24}.txt", f"c{cid}"))
                cid += 1
            tool_rounds.append(calls)
        prov = _RecordingMock(responses=[""] * 50, tool_calls=tool_rounds)
        core, session, _ = _core(
            prov, tmp_path, ws_files=files, max_iterations=50)
        evs = _run(core, session)
        types = _types(evs)
        assert types.count("tool_call") == 817
        assert types.count("tool_result") == 817
        assert prov.rounds == 51  # 50 tool rounds + 1 wrap-up attempt
        # no host per-round cap at 33: the widest round fully executed
        assert max(len(r) for r in tool_rounds) == 33
        # natural budget reach; object-shaped MockProvider cannot feed the
        # raw-event wrap-up (see D6), so the honest budget error closes it
        assert any("Iteration budget exhausted" in e.get("message", "")
                   for e in evs if e.get("type") == "system")
        assert types.count("done") == 1
        # pruned payload grows with history; duplicates dominate (24 files)
        assert prov.payload_bytes[-1] > prov.payload_bytes[0]


# ── K. max_reflections ────────────────────────────────────────────────

class TestMaxReflections:
    def test_no_runtime_call_site(self):
        import pathlib
        hits = [str(p) for p in pathlib.Path("wisp").rglob("*.py")
                if "max_reflections" in p.read_text()]
        assert sorted(hits) == sorted([
            "wisp/config.py", "wisp/transport/renderer.py"])

    def test_identical_rounds_not_stopped(self, tmp_path):
        n = 6
        tool_rounds = [[_read_call("a.txt", f"c{i}")] for i in range(n)]
        prov = MockProvider(responses=[""] * n + ["final summary"],
                            tool_calls=tool_rounds)
        core, session, _ = _core(prov, tmp_path, ws_files={"a.txt": "alpha"})
        evs = _run(core, session)
        assert _types(evs).count("tool_result") == n
        assert _types(evs).count("done") == 1
        assert not any("reflect" in str(e.get("message", "")).lower()
                       or "reflect" in str(e.get("reason", "")).lower()
                       for e in evs)


# ── L. No-progress signal inventory ───────────────────────────────────

class TestNoProgressSignals:
    def test_repetition_signals_extractable_but_unconsumed(self, tmp_path):
        n = 6
        tool_rounds = [[_read_call("same.txt", f"c{i}")] for i in range(n)]
        prov = MockProvider(responses=[""] * n + ["summary"], tool_calls=tool_rounds)
        core, session, _ = _core(prov, tmp_path, ws_files={"same.txt": "same"})
        evs = _run(core, session)
        calls = [e for e in evs if e.get("type") == "tool_call"]
        keys = [(c.get("name"), json.dumps(c.get("arguments", {}), sort_keys=True))
                for c in calls]
        assert len(keys) == n and len(set(keys)) == 1  # same tool+args ×6
        tracker = ProgressTracker()
        for e in evs:
            tracker.on_event(_to_agent_event(e))
        assert tracker.on_done()["files_changed"] == []  # no novelty signaled
        assert not any("no_progress" in str(t) for t in _types(evs))  # no detector


# ── M. Delivery path (renderer buffers) ───────────────────────────────

class TestDeliveryPath:
    def _transport(self):
        return CLITransport(_MockRuntime())

    def test_thinking_buffered_until_boundary(self):
        t = self._transport()
        out = io.StringIO()
        t._render_event(out, {"type": "thinking", "text": "unfinished"})
        assert out.getvalue() == ""  # generated but not delivered

    def test_stall_flushes_thinking_preview_with_line_count(self):
        t = self._transport()
        out = io.StringIO()
        thinking_122 = "\n".join(f"reasoning line {i} with words" for i in range(122))
        t._render_event(out, {"type": "thinking", "text": thinking_122})
        t._render_event(out, {"type": "error", "message": "stalled", "recoverable": True})
        rendered = out.getvalue()
        assert "122 lines" in rendered and "/thinking to expand" in rendered

    def test_done_flushes_content_answer(self):
        t = self._transport()
        out = io.StringIO()
        t._render_event(out, {"type": "thinking", "text": "plan"})
        t._render_event(out, {"type": "content", "text": "the final answer"})
        t._render_event(out, {"type": "done", "session_id": "s"})
        assert "the final answer" in out.getvalue()

    def test_content_streams_live_before_done(self):
        t = self._transport()
        out = io.StringIO()
        t._render_event(out, {"type": "content", "text": "partial live"})
        assert "partial live" in out.getvalue()  # rendered pre-terminal


# ── N. Success honesty (runtime level) ────────────────────────────────

def _runtime(provider, tmp_path, ws_files=None):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    for name, text in (ws_files or {}).items():
        (ws / name).write_text(text)
    config = WispConfig().replace(workspace=str(ws))
    store = UnifiedStore(tmp_path / "wisp.db")

    def factory():
        c = WispAgentCore(config=config, provider=provider,
                          security=SecurityPolicy(), tool_executor=None)
        c.FIRST_TOKEN_DEADLINE_S = 0.2
        c.CHUNK_DEADLINE_S = 0.2
        return c

    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
        telemetry=Telemetry(), core_factory=factory,
        session_repo=SessionRepository(store), config=config)
    return runtime, str(ws)


class TestSuccessHonesty:
    def test_silent_end_recorded_incomplete(self, tmp_path, monkeypatch):
        # H5 compatibility: the H3 closure error is terminal classification,
        # and the derived completion predicate (runtime.py) no longer writes
        # repo-DONE for it. The H1/H2-era repo-complete finding is superseded.
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, ws = _runtime(_DictProvider(
            [_think_then_hang("craft final")]), tmp_path)
        session = {"id": "n-silent", "model": "mock",
                   "workspace": ws, "messages": []}

        async def _main():
            return [ev async for ev in runtime.run_turn(session, prompt="go")]

        evs = asyncio.run(_main())
        assert "done" not in _types(evs)
        assert _types(evs).count("error") == 1  # H3 closure
        assert runtime.session_repo.was_last_turn_complete("n-silent") is False
        assert any(m.get("role") == "user" for m in session["messages"])

    def test_clean_end_recorded_complete(self, tmp_path):
        runtime, ws = _runtime(_DictProvider([_content_only("answer")]), tmp_path)
        session = {"id": "n-clean", "model": "mock",
                   "workspace": ws, "messages": []}

        async def _main():
            return [ev async for ev in runtime.run_turn(session, prompt="go")]

        evs = asyncio.run(_main())
        assert _types(evs).count("done") == 1
        assert runtime.session_repo.was_last_turn_complete("n-clean") is True
