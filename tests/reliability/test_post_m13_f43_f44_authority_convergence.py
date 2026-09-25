"""ADR-0041 / ADR-0042 — F43 recovery classification, F44 completion authorities.

Two contracts under test:

**ADR-0041 (F43)** — recovery classification is semantic and terminal-first, so
*provider representation must not determine recovery*. A typed `StreamComplete`
and a canonical `{"type": "complete"}` take identical paths; likewise `done`.

**ADR-0042 (F44)** — five distinct authorities, none derived from a lower one:

    provider terminal  ->  turn state  ->  verification  ->  goal state  ->  recovery

These tests deliberately assert the authorities **separately** so that a future
change which collapses two of them fails here rather than silently changing
meaning.
"""
from __future__ import annotations

import asyncio
import pathlib
import tempfile

import pytest

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.core.runtime import AgentRuntime
from wisp.core.session_repo import SessionRepository
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import PermissionMode, SecurityPolicy
from wisp.infra.store import UnifiedStore
from wisp.infra.telemetry import Telemetry
from wisp.stream_events import StreamComplete, TokenBatch, ToolCallBatch
from wisp.tool_executor import ToolExecutor

ALLOW = PermissionMode.ASK_ALL


def _call(name, args, cid="c0"):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": args}}


# ── provider doubles ──────────────────────────────────────────────────
class Typed:
    """Typed events — the OllamaClient / MockProvider shape."""

    def __init__(self, rounds):
        self._r = list(rounds)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages=None, tools=None,
                               checkpoint_every=50):
        self.calls += 1
        yield from self._r[min(self.calls - 1, len(self._r) - 1)](tools)


class Dicts:
    """Canonical dicts — the OpenAIProvider shape."""

    def __init__(self, rounds):
        self._r = list(rounds)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages=None, tools=None,
                               checkpoint_every=50):
        self.calls += 1
        yield from self._r[min(self.calls - 1, len(self._r) - 1)](tools)


def t_content(text):
    def g(tools):
        yield TokenBatch(phase="content", text=text, batch_index=0)
        yield StreamComplete(phase="complete", final_thinking="", final_content=text,
                             total_tokens=1, tool_calls=None, validation_hash="h",
                             done_reason="stop")
    return g


def t_terminal(phase="complete"):
    def g(tools):
        yield StreamComplete(phase=phase, final_thinking="", final_content="",
                             total_tokens=0, tool_calls=None, validation_hash="h",
                             done_reason="stop")
    return g


def t_tool(name, args, cid="c0"):
    def g(tools):
        yield ToolCallBatch(phase="tool_calls", calls=[_call(name, args, cid)])
        yield StreamComplete(phase="complete", final_thinking="", final_content="",
                             total_tokens=1, tool_calls=None, validation_hash="h",
                             done_reason="tool_calls")
    return g


def t_raises(exc):
    def g(tools):
        raise exc
        yield  # pragma: no cover
    return g


def d_content(text):
    def g(tools):
        yield {"type": "content", "text": text}
        yield {"type": "done", "done_reason": "stop"}
    return g


def d_terminal(typ="done"):
    def g(tools):
        yield {"type": typ, "done_reason": "stop"}
    return g


def d_tool(name, args, cid="c0"):
    def g(tools):
        yield {"type": "tool_calls", "calls": [_call(name, args, cid)]}
        yield {"type": "done", "done_reason": "tool_calls"}
    return g


def d_raises(exc):
    def g(tools):
        raise exc
        yield  # pragma: no cover
    return g


# ── harness ───────────────────────────────────────────────────────────
def _core(provider, tmp_path, **cfg):
    ws = pathlib.Path(tempfile.mkdtemp(dir=tmp_path))
    config = WispConfig().replace(workspace=str(ws), permission_mode=ALLOW, **cfg)
    return WispAgentCore(config=config, provider=provider,
                         security=SecurityPolicy(permission_mode=ALLOW),
                         tool_executor=None), ws


async def _allow(ev):
    return True


async def _drain(agen):
    return [ev async for ev in agen]


# ══════════════════════════════════════════════════════════════════════
# ADR-0041 — F43: representation must not determine recovery
# ══════════════════════════════════════════════════════════════════════
class TestF43Vocabulary:
    def test_the_classifier_owns_one_vocabulary_only(self):
        """ADR-0043 R2/R3 — no non-terminal vocabulary exists to drift.

        ADR-0041 reduced the bookkeeping set to the non-terminal payload-less
        types, but the closure audit found the mechanism still live: an
        *unrecognised* payload-less event was treated as meaningful and could
        bless an empty attempt. ADR-0043 removed the mechanism — meaningfulness
        is the payload question, asked the same way for every type.
        """
        import wisp.core.provider_stream as ps

        assert {"done", "complete", "stream_complete"} == set(ps.TERMINAL_TYPES)
        assert not hasattr(ps, "NON_PAYLOAD_TYPES"), (
            "a non-terminal vocabulary reappeared in the classifier")
        assert not hasattr(WispAgentCore, "_BOOKKEEPING_TYPES")
        assert hasattr(ps, "_event_has_payload")

    def test_no_consumer_re_spells_the_vocabulary(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        src = (root / "wisp/core/provider_stream.py").read_text()
        assert src.count("TERMINAL_TYPES = frozenset(") == 1
        assert "NON_PAYLOAD_TYPES" not in src
        stateless = (root / "wisp/core/stateless.py").read_text()
        # the old re-spelling must not come back in any form. Assert the
        # ASSIGNMENT, not the bare name: the file's comment explains why the
        # constant was removed and legitimately names it.
        assert '"stream_complete", "checkpoint"' not in stateless
        assert "_BOOKKEEPING_TYPES = " not in stateless
        assert "NON_PAYLOAD_TYPES" not in stateless


class TestF43RepresentationEquivalence:
    """R6 — the whole point of F43."""

    @pytest.mark.parametrize("typed_round,dict_round", [
        (t_terminal(), d_terminal("done")),
        (t_terminal(), d_terminal("complete")),
        (t_content("hi"), d_content("hi")),
    ])
    def test_equivalent_semantics_give_equivalent_recovery(
            self, tmp_path, monkeypatch, typed_round, dict_round):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "3")
        out = {}
        for label, prov in (("typed", Typed([typed_round])),
                            ("dict", Dicts([dict_round]))):
            core, _ = _core(prov, tmp_path)
            evs = asyncio.run(_drain(core._guarded_provider_stream("s", [], None)))
            out[label] = (prov.calls, [e.get("type") for e in evs])
        assert out["typed"] == out["dict"], (
            f"representation determined recovery: typed={out['typed']} "
            f"dict={out['dict']}")

    def test_bare_terminal_is_empty_on_both_representations(self, tmp_path, monkeypatch):
        """R5: a stream that produced only bare markers is EMPTY, not success."""
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "3")
        for label, prov in (("typed", Typed([t_terminal()])),
                            ("dict", Dicts([d_terminal("done")])),
                            ("dict-complete", Dicts([d_terminal("complete")])),
                            ("dict-legacy", Dicts([d_terminal("stream_complete")]))):
            core, _ = _core(prov, tmp_path)
            evs = asyncio.run(_drain(core._guarded_provider_stream("s", [], None)))
            types = [e.get("type") for e in evs]
            assert prov.calls == 3, f"{label}: not retried ({prov.calls} calls)"
            assert types and types[-1] == "error", f"{label}: {types}"

    def test_terminal_carrying_payload_is_still_meaningful(self, tmp_path):
        """R2 — the payload check must survive the reorder."""
        def _rich():
            def _g(tools):
                yield {"type": "done", "text": "final answer"}
            return _g

        prov = Dicts([_rich()])
        core, _ = _core(prov, tmp_path)
        evs = asyncio.run(_drain(core._guarded_provider_stream("s", [], None)))
        assert prov.calls == 1, "a payload-carrying terminal was treated as empty"
        assert [e.get("type") for e in evs] == [] or evs[-1].get("type") != "error"

    def test_content_carrying_streams_are_unchanged(self, tmp_path):
        for label, prov in (("typed", Typed([t_content("hi")])),
                            ("dict", Dicts([d_content("hi")]))):
            core, _ = _core(prov, tmp_path)
            evs = asyncio.run(_drain(core._guarded_provider_stream("s", [], None)))
            assert prov.calls == 1, label
            assert any(e.get("text") == "hi" for e in evs), label


class TestF43BookkeepingPreserved:
    """R7 — the recovery model is preserved, not simplified away."""

    def test_bookkeeping_only_stream_is_still_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "2")

        def _bookkeeping_only():
            def _g(tools):
                yield {"type": "stream_stats", "sse_lines": 1, "usable_deltas": 0}
                yield {"type": "checkpoint", "token_count": 0}
                yield {"type": "done"}
            return _g

        prov = Dicts([_bookkeeping_only()])
        core, _ = _core(prov, tmp_path)
        evs = asyncio.run(_drain(core._guarded_provider_stream("s", [], None)))
        assert prov.calls == 2, "a bookkeeping-only stream must still be retried"
        assert [e.get("type") for e in evs][-1] == "error"

    def test_bookkeeping_events_are_forwarded_not_swallowed(self, tmp_path):
        def _with_checkpoint():
            def _g(tools):
                yield {"type": "checkpoint", "token_count": 3}
                yield {"type": "content", "text": "hi"}
                yield {"type": "done"}
            return _g

        core, _ = _core(Dicts([_with_checkpoint()]), tmp_path)
        evs = asyncio.run(_drain(core._guarded_provider_stream("s", [], None)))
        types = [e.get("type") for e in evs]
        assert "checkpoint" in types, types
        assert "content" in types, types

    def test_bookkeeping_does_not_become_terminal(self, tmp_path, monkeypatch):
        """A checkpoint must not end the stream as if it were terminal."""
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "2")

        def _checkpoint_then_content():
            def _g(tools):
                yield {"type": "checkpoint", "token_count": 1}
                yield {"type": "content", "text": "after checkpoint"}
                yield {"type": "done"}
            return _g

        prov = Dicts([_checkpoint_then_content()])
        core, _ = _core(prov, tmp_path)
        evs = asyncio.run(_drain(core._guarded_provider_stream("s", [], None)))
        assert prov.calls == 1, "the checkpoint truncated the stream"
        assert any(e.get("text") == "after checkpoint" for e in evs)


# ══════════════════════════════════════════════════════════════════════
# ADR-0042 — F44: five distinct authorities
# ══════════════════════════════════════════════════════════════════════
def _runtime(provider, ws, **cfg):
    cfg.setdefault("goal_state", True)
    config = WispConfig().replace(workspace=str(ws), permission_mode=ALLOW, **cfg)
    store = UnifiedStore(ws / "f.db")
    repo = SessionRepository(store)
    sec = SecurityPolicy(permission_mode=ALLOW)

    def _core_factory():
        return WispAgentCore(config=config, provider=provider, security=sec,
                             tool_executor=ToolExecutor(config))

    rt = AgentRuntime(store=store, security=sec, extensions=ExtensionHost(),
                      telemetry=Telemetry(), core_factory=_core_factory,
                      session_repo=repo, config=config)
    return rt, repo


def _run(rt, sid, ws, prompt="go"):
    session = {"id": sid, "model": "m", "workspace": str(ws), "messages": []}

    async def _main():
        return [e async for e in rt.run_turn(session, prompt=prompt,
                                             approval_handler=_allow)]

    return asyncio.run(_main()), session


def _goal(repo, sid):
    rec = repo.reconstruct(sid)
    states = (rec.get("_journal") or {}).get("goal_states") or []
    return states[-1] if states else {}


def _observe(tmp_path, rounds, sid, *, typed=False, **cfg):
    ws = pathlib.Path(tempfile.mkdtemp(dir=tmp_path))
    (ws / "a.txt").write_text("alpha")
    prov = Typed(rounds) if typed else Dicts(rounds)
    rt, repo = _runtime(prov, ws, **cfg)
    evs, _ = _run(rt, sid, ws)
    g = _goal(repo, sid)
    return {
        "types": [e.get("type") for e in evs],
        "terminal": ([e.get("type") for e in evs] or [None])[-1],
        "turn_succeeded": g.get("turn_succeeded"),
        "repo_complete": repo.was_last_turn_complete(sid),
        "acceptance": g.get("acceptance_verdict"),
        "goal_state": g.get("goal_state"),
        "outcome": g.get("terminal_outcome"),
    }


class TestF44AuthorityMatrix:
    def test_ordinary_success(self, tmp_path):
        r = _observe(tmp_path, [d_content("answer")], "ok")
        assert r["turn_succeeded"] is True
        assert r["repo_complete"] is True
        # R4: a successful turn alone does NOT reach GOAL_MET — no evidence.
        assert r["goal_state"] != "goal_met"
        assert r["goal_state"] == "goal_unverified"

    def test_provider_terminal_ends_the_turn(self, tmp_path):
        r = _observe(tmp_path, [t_content("answer")], "term", typed=True)
        assert r["turn_succeeded"] is True
        assert r["goal_state"] == "goal_unverified"

    def test_exhaustion_without_wrapup_is_a_failed_turn(self, tmp_path):
        r = _observe(tmp_path,
                     [d_tool("read_file", {"path": "a.txt"}),
                      d_raises(RuntimeError("dead"))],
                     "exn", max_iterations=1)
        assert r["turn_succeeded"] is False
        assert r["repo_complete"] is False
        assert r["goal_state"] == "goal_failed"

    def test_exhaustion_with_wrapup_is_a_completed_turn_not_a_goal_failure(self, tmp_path):
        """R5 — the F44 decision, stated as a test."""
        r = _observe(tmp_path,
                     [d_tool("read_file", {"path": "a.txt"}),
                      d_content("summary")],
                     "exy", max_iterations=1)
        assert r["turn_succeeded"] is True
        assert r["repo_complete"] is True
        assert r["goal_state"] == "goal_unverified", (
            "iteration exhaustion was interpreted as a goal failure")
        assert r["goal_state"] != "goal_met", (
            "a successful wrap-up was interpreted as goal success")
        # the summary is delivered — F40's fix still holds
        assert r["types"].count("content") >= 1

    def test_typed_and_dict_agree_on_the_exhaustion_row(self, tmp_path):
        """F44's original symptom: the two representations must agree."""
        d = _observe(tmp_path,
                     [d_tool("read_file", {"path": "a.txt"}), d_content("summary")],
                     "exd", max_iterations=1)
        t = _observe(tmp_path,
                     [t_tool("read_file", {"path": "a.txt"}), t_content("summary")],
                     "ext", typed=True, max_iterations=1)
        for key in ("turn_succeeded", "repo_complete", "acceptance", "goal_state"):
            assert d[key] == t[key], f"{key}: dict={d[key]} typed={t[key]}"

    def test_next_turn_does_not_replay_a_completed_exhausted_turn(self, tmp_path):
        """R7."""
        ws = pathlib.Path(tempfile.mkdtemp(dir=tmp_path))
        (ws / "a.txt").write_text("alpha")
        prov = Dicts([d_tool("read_file", {"path": "a.txt"}), d_content("summary")])
        rt, repo = _runtime(prov, ws, max_iterations=1)
        evs1, _ = _run(rt, "nxt", ws)
        assert repo.was_last_turn_complete("nxt") is True
        evs2, _ = _run(rt, "nxt", ws, prompt="again")
        assert "done" in [e.get("type") for e in evs2]
        assert not any("replaying" in str(e.get("message", "")) for e in evs2)

    def test_verification_failure_fails_the_goal_despite_a_successful_turn(self, tmp_path):
        """R4/R8 — the sharpest non-collapse: turn_succeeded AND goal_failed."""
        r = _observe(tmp_path, [
            d_tool("write_file", {"path": "out.txt", "content": "hi"}),
            d_tool("run_bash", {"command": "exit 3"}, "c1"),
            d_content("done"),
        ], "vf")
        assert r["acceptance"] == "fail"
        assert r["goal_state"] == "goal_failed"
        assert r["turn_succeeded"] is True, (
            "the turn-level state must not be rewritten by the goal verdict")

    def test_incomplete_evidence_is_not_goal_met(self, tmp_path):
        r = _observe(tmp_path, [
            d_tool("write_file", {"path": "out.txt", "content": "hi"}),
            d_content("finished"), d_content("finished"), d_content("finished"),
        ], "inc")
        assert r["goal_state"] != "goal_met"

    def test_provider_failure_is_a_failed_turn(self, tmp_path):
        r = _observe(tmp_path, [d_raises(RuntimeError("provider died"))], "pf")
        assert r["turn_succeeded"] is False
        assert r["repo_complete"] is False
        assert r["goal_state"] == "goal_failed"

    def test_cancellation_is_its_own_outcome(self, tmp_path):
        ws = pathlib.Path(tempfile.mkdtemp(dir=tmp_path))

        class Hang:
            def generate_stream_events(self, system_prompt, messages=None,
                                       tools=None, checkpoint_every=50):
                def g():
                    yield {"type": "thinking", "text": "partial"}
                    import time
                    time.sleep(30)
                    yield {"type": "done"}
                yield from g()

        rt, repo = _runtime(Hang(), ws)

        async def _cancel():
            session = {"id": "cx", "model": "m", "workspace": str(ws), "messages": []}

            async def _collect():
                return [e async for e in rt.run_turn(session, prompt="go",
                                                     approval_handler=_allow)]

            task = asyncio.create_task(_collect())
            await asyncio.sleep(1.0)
            task.cancel()
            try:
                return await task
            except asyncio.CancelledError:
                return []

        evs = asyncio.run(_cancel())
        assert "done" not in [e.get("type") for e in evs]
        assert repo.was_last_turn_complete("cx") is False
        assert _goal(repo, "cx").get("goal_state") != "goal_met"

    def test_a_terminal_event_does_not_override_a_fatal_error(self, tmp_path):
        """R1/R9 — `provider said done` is not `the turn succeeded`.

        The exhausted-without-wrap-up row emits BOTH a fatal error and a
        `done` event; the turn must still be a failure.
        """
        r = _observe(tmp_path,
                     [d_tool("read_file", {"path": "a.txt"}),
                      d_raises(RuntimeError("dead"))],
                     "ovr", max_iterations=1)
        assert "done" in r["types"], "the turn did terminate"
        assert r["turn_succeeded"] is False, (
            "a terminal event overrode the fatal error")


class TestF44AuthoritiesAreSeparate:
    """ADR-0042 — the collapse checks, as explicit assertions."""

    def test_repo_complete_is_not_a_success_verdict(self, tmp_path):
        """R3 — a completed turn can still be a failed one at goal level."""
        r = _observe(tmp_path, [
            d_tool("write_file", {"path": "out.txt", "content": "hi"}),
            d_tool("run_bash", {"command": "exit 3"}, "c1"),
            d_content("done"),
        ], "sep")
        assert r["repo_complete"] is True
        assert r["goal_state"] == "goal_failed"
        assert r["outcome"] == "succeeded"

    def test_turn_succeeded_never_forces_goal_met(self, tmp_path):
        """R4 — GOAL_MET needs verification evidence, which no row here has."""
        for sid, rounds, kw in (
            ("s1", [d_content("a")], {}),
            ("s2", [t_content("a")], {"typed": True}),
            ("s3", [d_tool("read_file", {"path": "a.txt"}), d_content("s")],
             {"max_iterations": 1}),
        ):
            r = _observe(tmp_path, rounds, sid, **kw)
            if r["turn_succeeded"]:
                assert r["goal_state"] != "goal_met", sid
