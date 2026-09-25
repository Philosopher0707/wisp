"""ADR-0039 — provider event normalization boundary (F40, F42).

Every test here drives REAL production code. The provider doubles emit the
**typed** representation that `OllamaClient` and `MockProvider` actually
produce, because that is the representation that exposed F40 — a dict-shaped
double cannot see any of these defects.

The load-bearing test is `TestWrapUpF40::test_typed_provider_summary_is_delivered`:
if a future change routes the wrap-up back onto the raw provider stream, that
test fails. That is the architectural invariant under test, not the function
names.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.infra.security import SecurityPolicy
from wisp.stream_events import StreamComplete, TokenBatch, ToolCallBatch

REPO = pathlib.Path(__file__).resolve().parents[2]


# ── provider doubles (typed = the Ollama/MockProvider shape) ──────────
def _read_call(path: str, cid: str) -> dict:
    return {"id": cid, "type": "function",
            "function": {"name": "read_file", "arguments": {"path": path}}}


class TypedProvider:
    """Emits REAL typed stream events, like OllamaClient and MockProvider."""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages=None, tools=None,
                               checkpoint_every=50):
        self.calls += 1
        yield from self._rounds[min(self.calls - 1, len(self._rounds) - 1)](tools)


def _typed_tool_round(path="x.py", cid="c0"):
    def _g(tools):
        yield ToolCallBatch(phase="tool_calls", calls=[_read_call(path, cid)])
        yield StreamComplete(phase="complete", final_thinking="",
                             final_content="", total_tokens=1, tool_calls=None,
                             validation_hash="h", done_reason="tool_calls")
    return _g


def _typed_content_round(text, done_reason="stop"):
    def _g(tools):
        yield TokenBatch(phase="content", text=text, batch_index=0)
        yield StreamComplete(phase="complete", final_thinking="",
                             final_content=text, total_tokens=1, tool_calls=None,
                             validation_hash="h", done_reason=done_reason)
    return _g


def _typed_empty_round():
    def _g(tools):
        return
        yield  # pragma: no cover — makes this a generator
    return _g


def _dict_content_round(text, done_reason="stop"):
    def _g(tools):
        yield {"type": "content", "text": text}
        yield {"type": "done", "done_reason": done_reason}
    return _g


def _core(provider, tmp_path, **cfg_kw):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    config = WispConfig().replace(workspace=str(ws), **cfg_kw)
    return WispAgentCore(config=config, provider=provider,
                         security=SecurityPolicy(), tool_executor=None), ws


async def _allow(ev):
    return True


def _run_turn(core, prompt="go", **kw):
    kw.setdefault("approval_handler", _allow)
    session = {"id": "f40", "messages": [], "model": "m", "workspace": "/tmp"}

    async def _main():
        return [ev async for ev in core.turn(session, prompt, **kw)]

    return asyncio.run(_main())


def _types(evs):
    return [e.get("type") for e in evs]


# ══════════════════════════════════════════════════════════════════════
# §16 — boundary tests
# ══════════════════════════════════════════════════════════════════════
class TestNormalizationBoundary:
    """A: typed normalization."""

    def test_typed_event_becomes_a_canonical_dict(self, tmp_path):
        core, _ = _core(TypedProvider([]), tmp_path)
        core.provider = TypedProvider([_typed_content_round("hello")])
        got = asyncio.run(_drain(core._normalized_provider_stream("s", [], None)))
        assert got == [{"type": "content", "text": "hello"},
                       {"type": "complete", "done_reason": "stop"}]

    def test_dict_passthrough_is_a_copy_not_the_same_object(self, tmp_path):
        """B: a canonical dict passes through — and the copy is load-bearing.

        The main loop mutates what it receives (`normalized["type"] =
        "tool_call"`), so handing it the provider's own dict would corrupt
        provider state.
        """
        core, _ = _core(TypedProvider([]), tmp_path)
        original = {"type": "content", "text": "x"}

        class _P:
            def generate_stream_events(self, system_prompt, messages=None,
                                       tools=None, checkpoint_every=50):
                yield original

        core.provider = _P()
        got = asyncio.run(_drain(core._normalized_provider_stream("s", [], None)))
        assert got == [{"type": "content", "text": "x"}]
        assert got[0] is not original

    def test_terminal_event_is_forwarded_not_consumed(self, tmp_path):
        """C + E: the boundary yields the terminal marker."""
        core, _ = _core(TypedProvider([_typed_content_round("hi")]), tmp_path)
        got = asyncio.run(_drain(core._normalized_provider_stream("s", [], None)))
        assert got[-1]["type"] == "complete", (
            "the normalization boundary must not consume terminal markers — "
            "the wrap-up needs to see them (ADR-0039 R6)")

    def test_empty_attempt_is_not_retried(self, tmp_path):
        """D: no retry in the normalization-only boundary."""
        prov = TypedProvider([_typed_empty_round()])
        core, _ = _core(prov, tmp_path)
        got = asyncio.run(_drain(core._normalized_provider_stream("s", [], None)))
        assert got == []
        assert prov.calls == 1, "the boundary retried; retry is the guard's job (R5)"

    def test_unrecognised_shape_is_total_not_raising(self, tmp_path):
        """ADR-0039 R3: canonicalization is total."""
        core, _ = _core(TypedProvider([]), tmp_path)

        class _Alien:
            pass

        class _P:
            def generate_stream_events(self, system_prompt, messages=None,
                                       tools=None, checkpoint_every=50):
                yield _Alien()
                yield object()

        core.provider = _P()
        got = asyncio.run(_drain(core._normalized_provider_stream("s", [], None)))
        assert [g["type"] for g in got] == ["unknown", "unknown"]


class TestGuardKeepsRecovery:
    """F: the guard still performs its own recovery."""

    def test_guard_retries_an_empty_attempt(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "3")
        prov = TypedProvider([_typed_empty_round(), _typed_content_round("recovered")])
        core, _ = _core(prov, tmp_path)
        got = asyncio.run(_drain(core._guarded_provider_stream("s", [], None)))
        assert prov.calls >= 2, "the guard lost its empty-stream retry"
        assert any(g.get("text") == "recovered" for g in got)

    def test_guard_still_consumes_the_terminal_marker(self, tmp_path):
        """The guard's own bookkeeping is unchanged (ADR-0039 §8)."""
        core, _ = _core(TypedProvider([_typed_content_round("hi")]), tmp_path)
        got = asyncio.run(_drain(core._guarded_provider_stream("s", [], None)))
        assert "complete" not in [g["type"] for g in got], (
            "the guard must keep consuming terminal markers for its own "
            "bookkeeping — only the normalization boundary forwards them")


async def _drain(agen):
    return [ev async for ev in agen]


# ══════════════════════════════════════════════════════════════════════
# §15 — F40 closure
# ══════════════════════════════════════════════════════════════════════
class TestWrapUpF40:
    def test_typed_provider_summary_is_delivered(self, tmp_path):
        """F40-1 + F40-2. THE architectural regression test.

        A typed provider reaches the wrap-up. Before ADR-0039 this raised
        `AttributeError: 'TokenBatch' object has no attribute 'get'` (F40-1),
        and even normalised a `StreamComplete` was missed because the wrap-up
        checked only `== "done"` (F40-2). Revert the wrap-up to the raw stream
        and this fails.
        """
        prov = TypedProvider([_typed_tool_round(), _typed_content_round("SUMMARY-MARKER")])
        core, ws = _core(prov, tmp_path, max_iterations=1)
        (ws / "x.py").write_text("pass\n")
        evs = _run_turn(core, "loop")

        texts = [e.get("text", "") for e in evs if e.get("type") == "content"]
        assert any("SUMMARY-MARKER" in t for t in texts), (
            f"the wrap-up summary was discarded; content={texts!r}")
        assert not any("Max iterations reached" in str(e.get("message", ""))
                       for e in evs if e.get("type") == "error"), (
            "the spurious 'Max iterations reached' is still emitted (F40-2)")

    def test_dict_provider_still_works(self, tmp_path):
        """The dict control — unchanged behaviour (ADR-0039 §12)."""
        prov = TypedProvider([_dict_tool_round(), _dict_content_round("SUMMARY-MARKER")])
        core, ws = _core(prov, tmp_path, max_iterations=1)
        (ws / "x.py").write_text("pass\n")
        evs = _run_turn(core, "loop")
        texts = [e.get("text", "") for e in evs if e.get("type") == "content"]
        assert any("SUMMARY-MARKER" in t for t in texts)
        assert not any("Max iterations reached" in str(e.get("message", ""))
                       for e in evs if e.get("type") == "error")

    def test_typed_and_dict_providers_agree(self, tmp_path):
        """The invariant: a consumer never needs to know which provider it has."""
        (tmp_path / "a").mkdir(exist_ok=True)
        (tmp_path / "b").mkdir(exist_ok=True)

        def _outcomes(sub, rounds):
            prov = TypedProvider(rounds)
            core, ws = _core(prov, tmp_path / sub, max_iterations=1)
            (ws / "x.py").write_text("pass\n")
            evs = _run_turn(core, "loop")
            return ([e.get("text") for e in evs if e.get("type") == "content"],
                    any("Max iterations reached" in str(e.get("message", ""))
                        for e in evs if e.get("type") == "error"))

        typed = _outcomes("a", [_typed_tool_round(), _typed_content_round("S")])
        dicts = _outcomes("b", [_dict_tool_round(), _dict_content_round("S")])
        assert typed[0] == dicts[0], f"typed={typed!r} dict={dicts!r}"
        assert typed[1] == dicts[1] is False

    def test_genuine_provider_failure_still_reports_the_error(self, tmp_path):
        """The real failure path is preserved (ADR-0039 §22)."""
        class _Broken:
            def generate_stream_events(self, system_prompt, messages=None,
                                       tools=None, checkpoint_every=50):
                if tools is None:
                    raise RuntimeError("provider died")
                yield ToolCallBatch(phase="tool_calls",
                                    calls=[_read_call("x.py", "c0")])
                yield StreamComplete(phase="complete", final_thinking="",
                                     final_content="", total_tokens=1,
                                     tool_calls=None, validation_hash="h")

        core, ws = _core(_Broken(), tmp_path, max_iterations=1)
        (ws / "x.py").write_text("pass\n")
        evs = _run_turn(core, "loop")
        assert any("Max iterations reached" in str(e.get("message", ""))
                   for e in evs if e.get("type") == "error"), (
            "a genuinely dead provider must still surface the error")


def _dict_tool_round(path="x.py", cid="c0"):
    def _g(tools):
        yield {"type": "tool_calls", "calls": [_read_call(path, cid)]}
        yield {"type": "done", "done_reason": "tool_calls"}
    return _g


class TestCompactionF40:
    def test_typed_provider_summary_is_not_the_error_fallback(self, tmp_path):
        """F40-3. Before: AttributeError → '[ERROR: …]' → silent truncation."""
        from wisp.core.compaction import Compactor

        comp = Compactor(provider_factory=lambda m: TypedProvider(
            [_typed_content_round("REAL-SUMMARY")]),
            compaction_model="some-model", chars_per_token=4)

        async def _main():
            return await comp._llm_summarize([{"role": "user", "content": "old"}], [])

        out = asyncio.run(_main())
        assert out is not None, (
            "compaction silently degraded to truncation on a typed provider")
        assert "REAL-SUMMARY" in out.summary
        assert "[ERROR" not in out.summary

    def test_compaction_error_path_still_works(self, tmp_path):
        from wisp.core.compaction import Compactor

        def _g(tools):
            yield {"type": "error", "message": "boom"}

        class _P:
            def generate_stream_events(self, system_prompt, messages=None,
                                       tools=None, checkpoint_every=50):
                yield from _g(tools)

        comp = Compactor(provider_factory=lambda m: _P(),
                         compaction_model="m", chars_per_token=4)

        async def _main():
            return await comp._llm_summarize([{"role": "user", "content": "o"}], [])

        assert asyncio.run(_main()) is None


class TestPlannerF40:
    def test_typed_provider_text_is_used(self, tmp_path):
        """F40-4. Before: isinstance(c, dict) discarded every typed event."""
        from wisp.graph.planner import _parse_text_fallback

        class _TypedNoGenerate:
            """No `generate` — forces the stream branch."""

            def generate_stream_events(self, system_prompt, messages=None,
                                       tools=None, checkpoint_every=50):
                yield TokenBatch(phase="content", text='{"steps": []}',
                                 batch_index=0)
                yield StreamComplete(phase="complete", final_thinking="",
                                     final_content='{"steps": []}',
                                     total_tokens=1, tool_calls=None,
                                     validation_hash="h")

        out = _parse_text_fallback(_TypedNoGenerate(), [])
        assert out == {"steps": []}, (
            f"typed planner events were discarded; got {out!r}")


# ══════════════════════════════════════════════════════════════════════
# §6/§15 — F42: one canonicalization authority
# ══════════════════════════════════════════════════════════════════════
class TestF42SingleAuthority:
    def test_tool_call_batch_calls_survive(self):
        from wisp.core.events import canonical_event
        calls = [_read_call("a.py", "c1")]
        got = canonical_event(ToolCallBatch(phase="tool_calls", calls=calls))
        assert got["calls"] == calls, "F42: ToolCallBatch.calls was dropped"

    def test_done_reason_survives(self):
        from wisp.core.events import canonical_event
        got = canonical_event(StreamComplete(
            phase="complete", final_thinking="", final_content="",
            total_tokens=1, tool_calls=None, validation_hash="h",
            done_reason="stop"))
        assert got["done_reason"] == "stop", "F42: done_reason was dropped"

    def test_the_second_normalizer_preserves_calls_too(self):
        """F42 was the DIVERGENCE, not just the field. Both entry points must
        now agree because both delegate to the one implementation."""
        from wisp.core.events import normalize_event
        calls = [_read_call("a.py", "c1")]
        ev = normalize_event(ToolCallBatch(phase="tool_calls", calls=calls))
        assert ev.data.get("calls") == calls, (
            "events.normalize_event still has its own narrower whitelist")

    def test_only_one_whitelist_definition_exists(self):
        """Structural: the whitelist literal exists in exactly one place."""
        src = (REPO / "wisp/core/events.py").read_text()
        tree = ast.parse(src)
        names = [n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        assert "canonical_event" in names
        # `normalize_event` must not carry a local whitelist any more.
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "normalize_event":
                local = [t.id for s in ast.walk(node) if isinstance(s, ast.Assign)
                         for t in s.targets if isinstance(t, ast.Name)]
                assert "safe_fields" not in local, (
                    "normalize_event re-grew a second whitelist (F42)")
        assert src.count("CANONICAL_EVENT_FIELDS: frozenset[str] =") == 1

    def test_core_delegates_does_not_reimplement(self):
        src = (REPO / "wisp/core/stateless.py").read_text()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name == "_normalize_event":
                body = [s for s in node.body
                        if not isinstance(s, ast.Expr)
                        or not isinstance(s.value, ast.Constant)]
                assigns = [t.id for s in ast.walk(node) if isinstance(s, ast.Assign)
                           for t in s.targets if isinstance(t, ast.Name)]
                assert "safe_fields" not in assigns, (
                    "_normalize_event re-implemented the whitelist (ADR-0039 R2)")
                assert any(isinstance(s, ast.Return) for s in body)
                return
        pytest.fail("_normalize_event not found")


# ══════════════════════════════════════════════════════════════════════
# §17 — consumer-architecture invariants
# ══════════════════════════════════════════════════════════════════════
class TestConsumerInvariants:
    def test_no_consumer_reaches_the_raw_provider_stream(self):
        """ADR-0039 R4. The wrap-up must not call `_stream_events_async`."""
        src = (REPO / "wisp/core/stateless.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                calls = [c.func.attr for c in ast.walk(node)
                         if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)]
                if "_turn_inner" in node.name:
                    assert "_stream_events_async" not in calls, (
                        "the turn loop reached past the normalization boundary")
                if "_normalized_provider_stream" in node.name:
                    assert "_guarded_provider_stream" not in calls
        # The boundary itself is the ONLY caller of the raw stream.
        raw_callers = [
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and "_stream_events_async" in
            [c.func.attr for c in ast.walk(n)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)]
        ]
        assert raw_callers == ["_normalized_provider_stream"], (
            f"more than one caller of the raw provider stream: {raw_callers}")

    def test_wrap_up_uses_the_shared_terminal_authority(self):
        """ADR-0039 R7 — no local terminal spelling."""
        src = (REPO / "wisp/core/stateless.py").read_text()
        assert "TERMINAL_TYPES" in src
        assert 'etype == "done"' not in src, (
            "the wrap-up re-spelled the terminal vocabulary")

    def test_terminal_vocabulary_has_one_definition(self):
        """R7: TERMINAL_TYPES is declared once, and consumers read it.

        The wrap-up used to re-spell it as `etype == "done"`, which could
        never fire on the typed Ollama path (F40-2).
        """
        psrc = (REPO / "wisp/core/provider_stream.py").read_text()
        assert psrc.count("TERMINAL_TYPES = frozenset(") == 1
        assert "stream_complete" in psrc  # legacy alias retained (R8)
        ssrc = (REPO / "wisp/core/stateless.py").read_text()
        assert "TERMINAL_TYPES" in ssrc
        assert 'etype == "done"' not in ssrc, (
            "the wrap-up re-spelled the terminal vocabulary")

    def test_F43_vocabulary_duplication_is_gone(self):
        """F43 CLOSED, and the MECHANISM removed, by ADR-0043 (PM-24).

        The history: `_BOOKKEEPING_TYPES` re-spelled two terminal spellings and
        omitted `complete`, so a bare typed `StreamComplete` counted as a
        meaningful — and silent — empty success while a bare `done` was retried
        (F43). ADR-0041 reordered the checks and shrank the set, but kept a
        vocabulary list in a semantic decision; the closure audit then found the
        same defect through a second door (an *unrecognised* payload-less event
        blessed an empty attempt).

        ADR-0043 removed the mechanism: meaningfulness is decided by the event's
        PAYLOAD for every type, so the guard owns no vocabulary but
        `TERMINAL_TYPES`. There is no list left to drift.
        """
        import wisp.core.provider_stream as ps

        assert not hasattr(ps, "NON_PAYLOAD_TYPES")
        assert not hasattr(WispAgentCore, "_BOOKKEEPING_TYPES")
        assert hasattr(ps, "_event_has_payload")
        assert not hasattr(ps, "_terminal_has_payload")
        # the guard owns exactly one vocabulary
        src = pathlib.Path(ps.__file__).read_text()
        assert src.count("TERMINAL_TYPES = frozenset(") == 1

    def test_compaction_and_planner_canonicalize(self):
        for rel in ("wisp/core/compaction.py", "wisp/graph/planner.py"):
            src = (REPO / rel).read_text()
            assert "canonical_event" in src, f"{rel} does not canonicalize"
            assert "isinstance(event, dict)" not in src, (
                f"{rel} still gates on a representation isinstance")
            assert 'if isinstance(c, dict)' not in src, (
                f"{rel} still filters typed events out (F40-4)")
