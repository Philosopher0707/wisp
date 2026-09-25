"""Final execution-semantics closure — ADR-0043 / ADR-0044 + the semantic invariants.

Three classes, per the closure mission:

A. **Semantic invariants** — the non-collapse rules, asserted directly.
B. **Differential** — equivalent typed/dict streams produce equivalent results.
C. **Adversarial** — attempts to break the authority model by construction.

Every test drives real production code. Typed doubles emit real
`wisp.stream_events` dataclasses, because that is the representation that
exposed F40/F43/F44 and a dict-shaped double cannot see them.
"""
from __future__ import annotations

import asyncio
import pathlib
import tempfile

import pytest

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.core.goal import GoalState, TerminalOutcome, terminal_outcome_from_evidence
from wisp.core.runtime import AgentRuntime
from wisp.core.session_repo import SessionRepository
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import PermissionMode, SecurityPolicy
from wisp.infra.store import UnifiedStore
from wisp.infra.telemetry import Telemetry
from wisp.stream_events import StreamComplete, TokenBatch, ToolCallBatch
from wisp.tool_executor import ToolExecutor

REPO = pathlib.Path(__file__).resolve().parents[2]
ALLOW = PermissionMode.ASK_ALL


def _call(name, args, cid="c0"):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": args}}


class Typed:
    def __init__(self, rounds):
        self._r = list(rounds)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages=None, tools=None,
                               checkpoint_every=50):
        self.calls += 1
        yield from self._r[min(self.calls - 1, len(self._r) - 1)](tools)


class Dicts:
    def __init__(self, rounds):
        self._r = list(rounds)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages=None, tools=None,
                               checkpoint_every=50):
        self.calls += 1
        yield from self._r[min(self.calls - 1, len(self._r) - 1)](tools)


class Raw:
    """Emits literal event lists — for adversarial stream shapes."""

    def __init__(self, events):
        self._e = list(events)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages=None, tools=None,
                               checkpoint_every=50):
        self.calls += 1
        yield from self._e


async def _drain(agen):
    return [ev async for ev in agen]


def _guard(provider, tmp_path, attempts="3"):
    import os
    os.environ["WISP_STREAM_ATTEMPTS"] = attempts
    ws = pathlib.Path(tempfile.mkdtemp(dir=tmp_path))
    cfg = WispConfig().replace(workspace=str(ws), permission_mode=ALLOW)
    core = WispAgentCore(config=cfg, provider=provider,
                         security=SecurityPolicy(permission_mode=ALLOW),
                         tool_executor=None)
    try:
        evs = asyncio.run(_drain(core._guarded_provider_stream("s", [], None)))
    except BaseException as exc:  # a non-transient raise propagates by design
        return provider.calls, f"<raised {type(exc).__name__}>"
    return provider.calls, [e.get("type") for e in evs]


def _runtime(provider, ws, **cfg):
    cfg.setdefault("goal_state", True)
    config = WispConfig().replace(workspace=str(ws), permission_mode=ALLOW, **cfg)
    store = UnifiedStore(ws / "s.db")
    repo = SessionRepository(store)
    sec = SecurityPolicy(permission_mode=ALLOW)

    def _cf():
        return WispAgentCore(config=config, provider=provider, security=sec,
                             tool_executor=ToolExecutor(config))

    rt = AgentRuntime(store=store, security=sec, extensions=ExtensionHost(),
                      telemetry=Telemetry(), core_factory=_cf,
                      session_repo=repo, config=config)
    return rt, repo


def _run(rt, sid, ws, prompt="go"):
    session = {"id": sid, "model": "m", "workspace": str(ws), "messages": []}

    async def _allow(ev):
        return True

    async def _main():
        return [e async for e in rt.run_turn(session, prompt=prompt,
                                             approval_handler=_allow)]

    return asyncio.run(_main()), session


def _observe(tmp_path, rounds, sid, *, typed=False, **cfg):
    ws = pathlib.Path(tempfile.mkdtemp(dir=tmp_path))
    (ws / "a.txt").write_text("alpha")
    prov = Typed(rounds) if typed else Dicts(rounds)
    rt, repo = _runtime(prov, ws, **cfg)
    evs, _ = _run(rt, sid, ws)
    rec = repo.reconstruct(sid)
    states = (rec.get("_journal") or {}).get("goal_states") or []
    g = states[-1] if states else {}
    return {"types": [e.get("type") for e in evs], "repo": repo, "goal": g,
            "was": repo.was_last_turn_complete(sid), "rt": rt, "ws": ws,
            "evs": evs}


# ── round builders ────────────────────────────────────────────────────
def t_content(text):
    def g(tools):
        yield TokenBatch(phase="content", text=text, batch_index=0)
        yield StreamComplete(phase="complete", final_thinking="", final_content=text,
                             total_tokens=1, tool_calls=None, validation_hash="h")
    return g


def t_tool(name, args, cid="c0"):
    def g(tools):
        yield ToolCallBatch(phase="tool_calls", calls=[_call(name, args, cid)])
        yield StreamComplete(phase="complete", final_thinking="", final_content="",
                             total_tokens=1, tool_calls=None, validation_hash="h")
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


# ══════════════════════════════════════════════════════════════════════
# A. ADR-0043 — the classifier owns no vocabulary but the terminal authority
# ══════════════════════════════════════════════════════════════════════
class TestClassifierOwnsOneVocabulary:
    def test_no_non_terminal_vocabulary_exists(self):
        import wisp.core.provider_stream as ps
        assert {"done", "complete", "stream_complete"} == set(ps.TERMINAL_TYPES)
        for gone in ("NON_PAYLOAD_TYPES", "_terminal_has_payload"):
            assert not hasattr(ps, gone), f"{gone} is back in the classifier"
        assert not hasattr(WispAgentCore, "_BOOKKEEPING_TYPES")
        assert hasattr(ps, "_event_has_payload")

    def test_the_guard_takes_no_vocabulary_argument(self):
        import inspect

        from wisp.core.provider_stream import guarded_provider_stream
        params = inspect.signature(guarded_provider_stream).parameters
        assert "bookkeeping_types" not in params
        assert list(params)[:2] == ["open_stream", "normalize_event"]

    def test_the_classifier_has_no_vocabulary_in_the_meaningfulness_decision(self):
        """Structural: meaningfulness must be the payload call, not a lookup.

        Checked over IDENTIFIERS, not raw text: the function's docstring and
        comments legitimately explain why the vocabulary was removed, and a
        substring scan would flag that explanation (the "a scanner reporting
        absent" trap).
        """
        import ast
        import inspect

        from wisp.core.provider_stream import guarded_provider_stream
        fn = ast.parse(inspect.getsource(guarded_provider_stream)).body[0]
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
        assert "bookkeeping" not in names | attrs, (
            "a vocabulary list re-entered the classifier")
        # the only type-string gate left is terminal detection
        assert "if _event_has_payload(normalized):" in inspect.getsource(
            guarded_provider_stream)


class TestUnknownEventCannotBless:
    """The hole the closure audit found — and its closure."""

    def test_unknown_payload_less_event_does_not_rescue_an_empty_attempt(self, tmp_path):
        calls, types = _guard(Raw([{"type": "weird_thing"}, {"type": "done"}]), tmp_path)
        assert calls == 3, f"an unrecognised payload-less event blessed the attempt: {calls} calls"
        assert types[-1] == "error"

    def test_unknown_payload_less_event_alone_is_empty(self, tmp_path):
        calls, types = _guard(Raw([{"type": "weird_thing"}]), tmp_path)
        assert calls == 3 and types[-1] == "error"

    def test_unknown_event_carrying_payload_still_counts(self, tmp_path):
        calls, types = _guard(Raw([{"type": "weird_thing", "text": "hi"}, {"type": "done"}]), tmp_path)
        assert calls == 1 and types == ["weird_thing"]

    def test_every_payload_key_counts(self, tmp_path):
        for key, val in (("text", "x"), ("content", "x"), ("final_content", "x"),
                         ("tool_calls", [{"id": "c"}]), ("calls", [{"id": "c"}])):
            calls, _ = _guard(Raw([{"type": "done", key: val}]), tmp_path)
            assert calls == 1, f"a terminal carrying {key!r} was treated as empty"

    def test_final_content_is_a_deliberate_compatibility_key(self, tmp_path):
        """ADR-0043 R6 — reachable for dict providers, kept on purpose."""
        from wisp.core.events import canonical_event
        assert canonical_event({"type": "done", "final_content": "x"})["final_content"] == "x"
        calls, _ = _guard(Raw([{"type": "done", "final_content": "x"}]), tmp_path)
        assert calls == 1, "a dict provider's final_content payload was ignored"


class TestBookkeepingStillBookkeeping:
    def test_bookkeeping_only_stream_is_still_empty(self, tmp_path):
        for typ in ("checkpoint", "usage", "stream_stats"):
            calls, types = _guard(Raw([{"type": typ}]), tmp_path, attempts="2")
            assert calls == 2 and types[-1] == "error", f"{typ} blessed an empty attempt"

    def test_bookkeeping_does_not_become_terminal(self, tmp_path):
        calls, types = _guard(
            Raw([{"type": "checkpoint"}, {"type": "content", "text": "after"}, {"type": "done"}]),
            tmp_path)
        assert calls == 1 and "content" in types

    def test_bookkeeping_events_are_still_forwarded(self, tmp_path):
        _, types = _guard(
            Raw([{"type": "checkpoint"}, {"type": "usage"}, {"type": "content", "text": "x"},
                 {"type": "done"}]), tmp_path)
        assert "checkpoint" in types and "usage" in types and "content" in types


# ══════════════════════════════════════════════════════════════════════
# A. ADR-0044 — one turn-level predicate
# ══════════════════════════════════════════════════════════════════════
class TestOneTurnPredicate:
    def test_the_predicate_is_implemented_once(self):
        src = (REPO / "wisp/core/runtime.py").read_text()
        assert "turn_succeeded = saw_done and not saw_fatal_error" not in src, (
            "the turn-success predicate was re-implemented (ADR-0044 R2)")
        assert "turn_succeeded = _goal_outcome is TerminalOutcome.SUCCEEDED" in src
        assert src.count("terminal_outcome_from_evidence(") <= 2, (
            "more than one computation path for the turn outcome")

    def test_turn_succeeded_is_the_projection_of_the_outcome(self):
        for saw_done, saw_fatal in ((True, False), (True, True), (False, False), (False, True)):
            outcome = terminal_outcome_from_evidence(saw_done=saw_done,
                                                     saw_fatal_error=saw_fatal)
            assert (outcome is TerminalOutcome.SUCCEEDED) == (saw_done and not saw_fatal), (
                f"the two disagree at saw_done={saw_done} saw_fatal={saw_fatal}")

    def test_the_ladder_state_is_not_named_terminal_outcome(self):
        """ADR-0044 R6 — a recovery state must not share the turn outcome's name."""
        from wisp.core.recovery import RecoveryLadder
        ladder = RecoveryLadder()
        assert not hasattr(ladder, "terminal_outcome")
        assert hasattr(ladder, "ladder_state")
        assert ladder.ladder_state == "IN_PROGRESS"
        # and its values must not collide with GoalState's
        assert ladder.ladder_state not in {s.value for s in GoalState}


# ══════════════════════════════════════════════════════════════════════
# B. Differential — representation must not matter
# ══════════════════════════════════════════════════════════════════════
class TestRepresentationDifferential:
    @pytest.mark.parametrize("typed_round,dict_round", [
        (t_content("hi"), d_content("hi")),
        (t_tool("read_file", {"path": "a.txt"}), d_tool("read_file", {"path": "a.txt"})),
        (t_raises(RuntimeError("dead")), d_raises(RuntimeError("dead"))),
    ])
    def test_guard_outcome_is_identical(self, tmp_path, typed_round, dict_round):
        a = _guard(Typed([typed_round]), tmp_path)
        b = _guard(Dicts([dict_round]), tmp_path)
        assert a == b, f"typed={a} dict={b}"

    @pytest.mark.parametrize("typed_round,dict_round", [
        (t_content("answer"), d_content("answer")),
        (t_tool("read_file", {"path": "a.txt"}), d_tool("read_file", {"path": "a.txt"})),
        (t_raises(RuntimeError("dead")), d_raises(RuntimeError("dead"))),
    ])
    def test_turn_and_goal_state_are_identical(self, tmp_path, typed_round, dict_round):
        t = _observe(tmp_path, [typed_round], "t", typed=True)
        d = _observe(tmp_path, [dict_round], "d")
        for key in ("turn_succeeded", "acceptance_verdict", "goal_state",
                    "terminal_outcome"):
            assert t["goal"].get(key) == d["goal"].get(key), (
                f"{key}: typed={t['goal'].get(key)} dict={d['goal'].get(key)}")
        assert t["was"] == d["was"]

    def test_exhaustion_row_agrees(self, tmp_path):
        d = _observe(tmp_path, [d_tool("read_file", {"path": "a.txt"}), d_content("s")],
                     "xd", max_iterations=1)
        t = _observe(tmp_path, [t_tool("read_file", {"path": "a.txt"}), t_content("s")],
                     "xt", typed=True, max_iterations=1)
        for key in ("turn_succeeded", "acceptance_verdict", "goal_state", "terminal_outcome"):
            assert d["goal"].get(key) == t["goal"].get(key), key
        assert d["was"] == t["was"] is True

    def test_all_terminal_spellings_agree(self, tmp_path):
        seen = {typ: _guard(Raw([{"type": typ}]), tmp_path)
                for typ in ("done", "complete", "stream_complete")}
        assert len({(c, tuple(t_)) for c, t_ in seen.values()}) == 1, seen


# ══════════════════════════════════════════════════════════════════════
# A. Semantic invariants — the non-collapse rules
# ══════════════════════════════════════════════════════════════════════
class TestSemanticInvariants:
    def test_turn_success_does_not_imply_goal_success(self, tmp_path):
        r = _observe(tmp_path, [d_content("answer")], "i1")
        assert r["goal"]["turn_succeeded"] is True
        assert r["goal"]["goal_state"] != GoalState.GOAL_MET.value

    def test_wrapup_success_does_not_imply_goal_success(self, tmp_path):
        r = _observe(tmp_path, [d_tool("read_file", {"path": "a.txt"}), d_content("summary")],
                     "i2", max_iterations=1)
        assert r["goal"]["goal_state"] == GoalState.GOAL_UNVERIFIED.value

    def test_exhaustion_does_not_imply_goal_failure(self, tmp_path):
        r = _observe(tmp_path, [d_tool("read_file", {"path": "a.txt"}), d_content("summary")],
                     "i3", max_iterations=1)
        assert r["goal"]["goal_state"] != GoalState.GOAL_FAILED.value

    def test_verification_failure_overrides_turn_success(self, tmp_path):
        r = _observe(tmp_path, [
            d_tool("write_file", {"path": "out.txt", "content": "hi"}),
            d_tool("run_bash", {"command": "exit 3"}, "c1"),
            d_content("done")], "i4")
        assert r["goal"]["turn_succeeded"] is True
        assert r["goal"]["goal_state"] == GoalState.GOAL_FAILED.value

    def test_fatal_provider_error_overrides_terminal_presence(self, tmp_path):
        r = _observe(tmp_path, [d_tool("read_file", {"path": "a.txt"}),
                                d_raises(RuntimeError("dead"))], "i5", max_iterations=1)
        assert "done" in r["types"]
        assert r["goal"]["turn_succeeded"] is False

    def test_replay_equals_live(self, tmp_path):
        from wisp.core.goal import terminal_outcome_from_evidence as f
        r = _observe(tmp_path, [d_tool("read_file", {"path": "a.txt"}), d_content("s")],
                     "i6", max_iterations=1)
        g = r["goal"]
        replayed = str(__import__("wisp.core.goal", fromlist=["x"]).derive_goal_state(
            terminal_outcome=f(saw_done=True, saw_fatal_error=False),
            acceptance_verdict=g.get("acceptance_verdict"),
            stagnating=not bool(g.get("stagnation_allows_goal_met", True)),
            turn_succeeded=bool(g.get("turn_succeeded")),
            cancelled=bool(g.get("cancelled")),
            escalated=bool(g.get("escalated")),
            already_recorded=None))
        assert replayed == g.get("goal_state")

    def test_repo_complete_is_not_a_success_verdict(self, tmp_path):
        r = _observe(tmp_path, [
            d_tool("write_file", {"path": "out.txt", "content": "hi"}),
            d_tool("run_bash", {"command": "exit 3"}, "c1"),
            d_content("done")], "i7")
        assert r["was"] is True
        assert r["goal"]["goal_state"] == GoalState.GOAL_FAILED.value


# ══════════════════════════════════════════════════════════════════════
# C. Adversarial
# ══════════════════════════════════════════════════════════════════════
class TestAdversarialStreamShapes:
    def test_terminal_before_content(self, tmp_path):
        """A terminal ends the attempt; post-terminal bytes cannot rescue it."""
        calls, types = _guard(Raw([{"type": "done"}, {"type": "content", "text": "late"}]), tmp_path)
        assert calls == 3 and types[-1] == "error", (
            "post-terminal content rescued an otherwise empty attempt")

    def test_multiple_terminals(self, tmp_path):
        calls, types = _guard(Raw([{"type": "content", "text": "x"}, {"type": "done"},
                                   {"type": "done"}]), tmp_path)
        assert calls == 1
        assert types.count("done") == 0  # markers are consumed, not forwarded

    def test_terminal_plus_fatal_error(self, tmp_path):
        calls, types = _guard(Raw([{"type": "done"},
                                   {"type": "error", "message": "boom", "recoverable": False}]),
                              tmp_path)
        assert types[-1] == "error", "a fatal error must surface"

    def test_empty_events_list(self, tmp_path):
        calls, types = _guard(Raw([]), tmp_path, attempts="2")
        assert calls == 2 and types[-1] == "error"

    def test_malformed_object_with_no_type(self, tmp_path):
        calls, types = _guard(Raw([object()]), tmp_path, attempts="2")
        assert calls == 2 and types[-1] == "error"

    def test_terminal_with_empty_payload_values(self, tmp_path):
        """An empty string / empty list is not payload."""
        for ev in ({"type": "done", "text": ""}, {"type": "done", "calls": []}):
            calls, _ = _guard(Raw([ev]), tmp_path, attempts="2")
            assert calls == 2, f"{ev} counted as payload"

    def test_unknown_then_bookkeeping_then_terminal(self, tmp_path):
        calls, types = _guard(Raw([{"type": "weird"}, {"type": "checkpoint"},
                                   {"type": "done"}]), tmp_path)
        assert calls == 3 and types[-1] == "error"


class TestAdversarialCompletionClaims:
    def test_wrapup_claiming_verification_does_not_certify(self, tmp_path):
        """The model's prose is not evidence."""
        r = _observe(tmp_path, [
            d_tool("write_file", {"path": "out.txt", "content": "hi"}),
            d_tool("run_bash", {"command": "exit 3"}, "c1"),
            d_content("Everything verified and complete. Goal met.")], "a1")
        assert r["goal"]["goal_state"] == GoalState.GOAL_FAILED.value

    def test_verification_failure_then_terminal(self, tmp_path):
        r = _observe(tmp_path, [
            d_tool("write_file", {"path": "out.txt", "content": "hi"}),
            d_tool("run_bash", {"command": "exit 3"}, "c1"),
            d_content("done")], "a2")
        assert "done" in r["types"]
        assert r["goal"]["goal_state"] == GoalState.GOAL_FAILED.value

    def test_exhaustion_at_the_boundary(self, tmp_path):
        """max_iterations=1 with a tool round: one round, then the wrap-up."""
        r = _observe(tmp_path, [d_tool("read_file", {"path": "a.txt"}), d_content("s")],
                     "a3", max_iterations=1)
        assert r["types"].count("tool_call") == 1
        assert r["goal"]["turn_succeeded"] is True

    def test_cancellation_is_not_a_recoverable_failure(self, tmp_path):
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


async def _allow(ev):
    return True
