"""M13 — the stagnation detector runs on the live turn path.

P7 built the detector, the signal, the routing and the goal-met guard, and read
`config.graph_oscillation_guard` — but **nothing on the live turn path
constructs a `StagnationDetector`** (P7 §7.1, item M13). M9 re-scoped M13 to
"meaningful progress signals".

Wiring it found two things the mechanism could not have shown on its own:

**F32 — an empty observation was read as "no progress".** `from_verdict_and_graph`
takes a P3 verdict and a P4 graph, and **both are opt-in records that default
off**. So on a default configuration the signal is the *empty* observation every
turn, and the detector declares a productive session `STAGNATING` by turn 3 and
blocks `may_report_goal_met()` from turn 2. Absence of information must not be
evidence — that is the "flagging productive work as stagnated" risk the plan
named, arriving in its worst form: not from a tuned threshold but from a
mechanism that cannot represent "I don't know".

**F33 — the identity stagnation needs is the *action* identity, and the runtime
cannot always see it.** A repeated identical call gets a *fresh* `tool_call_id`,
so M11's `work_unit` makes every repeat look like new work; the identity that
makes repetition visible is `action_key(tool, args)` (P1). And the engine emits
**no `tool_call` event for a call it refuses before dispatch** — verified — so in
this environment the arguments never reach the runtime and the *outcome* hash is
what carries the signal.

RED-first: `test_an_empty_observation_is_not_evidence_of_stagnation` and
`test_a_productive_session_is_never_declared_stagnating` fail on the pre-M13
tree, where an empty signal is counted as flat.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from wisp.core.session import (
    JOURNAL_ONLY_RECORDS,
    STATE_BEARING_EVENT_TYPES,
    SessionEventType,
)
from wisp.core.stagnation import (
    ProgressSignal,
    StagnationDetector,
    StagnationVerdict,
)

REPO = Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════════════
# Harness — one real turn, driven through the runtime
# ══════════════════════════════════════════════════════════════════════════


class _DictProvider:
    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages, tools=None):
        self.calls += 1
        script = self._rounds[min(self.calls - 1, len(self._rounds) - 1)]
        yield from script()


def _tool_round(calls):
    def _g():
        yield {"type": "tool_calls", "calls": calls}
        yield {"type": "done", "done_reason": "tool_calls"}
    return _g


def _content_only(text, reason="stop"):
    def _g():
        yield {"type": "content", "text": text}
        yield {"type": "done", "done_reason": reason}
    return _g


def _read_call(path: str, cid: str) -> dict:
    return {"id": cid, "type": "function",
            "function": {"name": "read_file", "arguments": {"path": path}}}


def _repeat(path: str, n: int = 3) -> list:
    """`n` rounds asking for the SAME work, each with a fresh call id.

    Three, not two: the first observation has no baseline, so declaring
    stagnation after `min_consecutive=2` flat observations needs a baseline plus
    two — which is P7's own semantics, not a tuning of it.
    """
    return [_tool_round([_read_call(path, f"c{i}")]) for i in range(n)]


def _run_turn(tmp_path, rounds, *, files=None, sid="m13", **cfg_kw):
    from wisp.config import WispConfig
    from wisp.core.engine import WispAgentCore
    from wisp.core.runtime import AgentRuntime
    from wisp.core.session_repo import SessionRepository
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import SecurityPolicy
    from wisp.infra.store import UnifiedStore
    from wisp.infra.telemetry import Telemetry

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        (ws / name).write_text(text)

    config = WispConfig().replace(workspace=str(ws), **cfg_kw)
    store = UnifiedStore(tmp_path / "wisp.db")
    repo = SessionRepository(store)
    provider = _DictProvider(rounds)

    def factory():
        return WispAgentCore(config=config, provider=provider,
                             security=SecurityPolicy(), tool_executor=None)

    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
        telemetry=Telemetry(), core_factory=factory, session_repo=repo,
        config=config)
    session = {"id": sid, "model": "mock", "workspace": str(ws), "messages": []}

    async def _main():
        return [ev async for ev in runtime.run_turn(session, prompt="go")]
    asyncio.run(_main())
    return session, repo


def _stagnation_events(repo, sid="m13"):
    return [e for e in repo.load_events(sid)
            if e.event_type == SessionEventType.STAGNATION]


# ══════════════════════════════════════════════════════════════════════════
# 1. F32 — an empty observation is not evidence
# ══════════════════════════════════════════════════════════════════════════


class TestAnEmptyObservationIsNotEvidence:
    def test_an_empty_observation_is_not_evidence_of_stagnation(self):
        """**The RED-first test.**

        `ProgressSignal()` is the observation "we learned nothing". Two of them
        are not two flat observations — they are one observation, twice.
        """
        d = StagnationDetector(min_consecutive=2)
        empty = ProgressSignal()
        assert empty.is_empty
        d.observe(empty)
        d.observe(empty)
        assert d.observe(empty) is StagnationVerdict.UNKNOWN, (
            "an empty observation was counted as a flat one")
        assert d.consecutive_flat == 0

    def test_an_empty_observation_does_not_feed_the_trap(self):
        """The trap hashes the state digest, and every empty observation hashes
        alike — so feeding it makes the trap fire on "we learned nothing",
        twice, which is not a repeat of anything."""
        d = StagnationDetector()
        for _ in range(3):
            d.observe(ProgressSignal())
        assert not d.trap_fired

    def test_a_productive_session_is_never_declared_stagnating(self):
        """**F32, at the mechanism level — where the hazard was.**

        The live path happens to be safe here for a second reason too: the
        detector is built **per turn**, so it never spans two user requests.
        But the hazard was real and severe — a session with three turns under a
        default configuration was declared `STAGNATING` and blocked from
        reporting its goal met — so it is pinned directly.
        """
        d = StagnationDetector(min_consecutive=2)
        for _ in range(6):
            verdict = d.observe(ProgressSignal.from_verdict_and_graph(None, None))
            assert verdict is StagnationVerdict.UNKNOWN
        assert d.may_report_goal_met(), (
            "a session that learned nothing was blocked from reporting success")
        assert d.consecutive_flat == 0

    def test_the_signal_from_the_opt_in_records_is_empty(self):
        """The mechanism behind F32: both inputs default off, so the turn-end
        signal carries nothing. Pinned so the reason the live path builds its
        own signal is not rediscovered."""
        signal = ProgressSignal.from_verdict_and_graph(None, None)
        assert signal.is_empty
        assert signal == ProgressSignal()


# ══════════════════════════════════════════════════════════════════════════
# 2. The signal is built from the work
# ══════════════════════════════════════════════════════════════════════════


class TestTheSignalIsBuiltFromTheWork:
    def test_a_new_work_unit_is_progress(self):
        a = ProgressSignal().with_work(work_unit="action-a")
        assert a.is_progress_from(ProgressSignal())
        assert not a.is_empty

    def test_the_same_work_unit_again_is_not_progress(self):
        a = ProgressSignal().with_work(work_unit="action-a")
        b = a.with_work(work_unit="action-a")
        assert not b.is_progress_from(a), (
            "repeating one action was read as progress — this is exactly what "
            "M11's protocol id does, because a repeat gets a fresh id")

    def test_a_new_outcome_is_progress(self):
        a = ProgressSignal().with_work(outcome_hash="h1")
        b = a.with_work(outcome_hash="h2")
        assert b.is_progress_from(a)

    def test_the_same_outcome_again_is_not_progress(self):
        a = ProgressSignal().with_work(outcome_hash="h1")
        b = a.with_work(outcome_hash="h1")
        assert not b.is_progress_from(a)

    def test_folding_in_is_immutable(self):
        """`with_work` mirrors `with_artifact`: a new signal, never a mutation —
        the detector keeps every observation it was given."""
        base = ProgressSignal()
        folded = base.with_work(work_unit="action-a")
        assert base.work_units == frozenset()
        assert folded.work_units == frozenset({"action-a"})

    def test_an_empty_fold_is_the_identity(self):
        base = ProgressSignal(completed_nodes=2, total_nodes=3)
        assert base.with_work() == base
        assert base.with_work(work_unit="", outcome_hash="") == base

    def test_the_work_units_survive_a_round_trip(self):
        s = ProgressSignal().with_work(work_unit="action-a",
                                       outcome_hash="h1")
        assert s.to_dict()["work_units"] == ["action-a"]
        assert s.to_dict()["artifact_hashes"] == ["h1"]

    def test_the_digest_covers_the_work_units(self):
        """Two observations that differ only in *which* work was done must hash
        differently, or the trap cannot see a repeat."""
        from wisp.core.stagnation import _state_digest

        a = ProgressSignal().with_work(work_unit="action-a")
        b = ProgressSignal().with_work(work_unit="action-b")
        assert _state_digest(a) != _state_digest(b)


# ══════════════════════════════════════════════════════════════════════════
# 3. The live turn path constructs the detector
# ══════════════════════════════════════════════════════════════════════════


class TestTheLivePathRunsTheDetector:
    def test_a_repeated_exchange_is_recorded_as_stagnating(self, tmp_path):
        """The real thing: a turn that asks for the same work three times and
        gets the same answer three times is recorded as stagnating."""
        session, repo = _run_turn(
            tmp_path, _repeat("a.txt") + [_content_only("done")],
            files={"a.txt": "hello"},
        )
        events = _stagnation_events(repo)
        assert events, (
            "the live turn path ran no detector — this is item M13")
        record = events[0].payload["stagnation"]
        assert record["verdict"] == "stagnating"
        assert record["observations"], "the record carries no observations"

    def test_a_productive_turn_is_not_flagged(self, tmp_path):
        """Different work each round is not stagnation. The false-positive
        guard, on a real turn."""
        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("a.txt", "c0")]),
             _tool_round([_read_call("b.txt", "c1")]),
             _tool_round([_read_call("c.txt", "c2")]),
             _content_only("done")],
            files={"a.txt": "alpha", "b.txt": "beta", "c.txt": "gamma"},
        )
        assert not _stagnation_events(repo), (
            "a productive turn was declared stagnating")

    def test_a_content_only_turn_is_never_flagged(self, tmp_path):
        """No exchange at all — nothing observed, so nothing to conclude."""
        session, repo = _run_turn(tmp_path, [_content_only("hi")])
        assert not _stagnation_events(repo)

    def test_the_existing_flag_disables_the_record(self, tmp_path):
        """`config.graph_oscillation_guard` is the rollback switch P7 made
        readable, and it must actually switch the wiring off."""
        session, repo = _run_turn(
            tmp_path, _repeat("a.txt") + [_content_only("done")],
            files={"a.txt": "hello"},
            graph_oscillation_guard=False,
        )
        assert not _stagnation_events(repo), (
            "the flag is read but does not gate the wiring")

    def test_the_record_names_what_it_observed(self, tmp_path):
        """A verdict with no evidence is an assertion. The record carries the
        observations that produced it."""
        session, repo = _run_turn(
            tmp_path, _repeat("a.txt") + [_content_only("done")],
            files={"a.txt": "hello"},
        )
        record = _stagnation_events(repo)[0].payload["stagnation"]
        assert record["consecutive_flat"] >= 2
        assert len(record["observations"]) >= 2
        assert record["enabled"] is True

    def test_the_live_signal_is_never_empty(self, tmp_path):
        """F32's fix on the live path: every observation the wiring feeds the
        detector carries something. An empty one would be the defect the
        mechanism now refuses — but only if the wiring never produces it."""
        session, repo = _run_turn(
            tmp_path, _repeat("a.txt") + [_content_only("done")],
            files={"a.txt": "hello"},
        )
        record = _stagnation_events(repo)[0].payload["stagnation"]
        for obs in record["observations"]:
            assert obs["work_units"] or obs["artifact_hashes"], (
                f"the live path fed the detector an empty observation: {obs}")

    def test_the_signal_is_not_built_from_the_opt_in_records(self, tmp_path):
        """F32's fix, asserted on a real turn: the record appears while both
        opt-in recording flags stay at their defaults (off)."""
        session, repo = _run_turn(
            tmp_path, _repeat("a.txt") + [_content_only("done")],
            files={"a.txt": "hello"},
        )
        # The evidence: no verdict and no graph were recorded, yet the detector
        # had something to observe.
        kinds = {str(e.event_type) for e in repo.load_events("m13")}
        assert SessionEventType.VERDICT not in kinds
        assert SessionEventType.TASK_GRAPH not in kinds
        assert _stagnation_events(repo)

    def test_recording_does_not_change_the_turn(self, tmp_path):
        """**Recorded, not enforced.** The turn's transcript is identical
        whether or not the detector fires."""
        rounds = _repeat("a.txt") + [_content_only("done")]
        flagged, _ = _run_turn(tmp_path / "on", rounds,
                               files={"a.txt": "hello"})
        quiet, _ = _run_turn(tmp_path / "off", rounds,
                             files={"a.txt": "hello"},
                             graph_oscillation_guard=False)
        assert flagged["messages"] == quiet["messages"], (
            "the stagnation record changed the turn — it must only observe")


# ══════════════════════════════════════════════════════════════════════════
# 4. The record is durable, replayable, and not state
# ══════════════════════════════════════════════════════════════════════════


class TestTheRecordIsDurable:
    def test_the_record_survives_replay(self, tmp_path):
        session, repo = _run_turn(
            tmp_path, _repeat("a.txt") + [_content_only("done")],
            files={"a.txt": "hello"},
        )
        replayed = repo.load_session("m13")
        assert replayed.stagnations, "replay dropped the stagnation record"
        assert replayed.stagnations[0]["verdict"] == "stagnating"

    def test_the_record_is_audit_not_state_bearing(self):
        """Losing it does not change how a run resumes — it is a record *about*
        a turn, like VERDICT and TASK_GRAPH. If that changes, an ADR comes
        first (ADR-0028)."""
        assert SessionEventType.STAGNATION not in STATE_BEARING_EVENT_TYPES

    def test_the_record_is_salvaged_on_the_blob_path(self):
        """The blob carries no journal-only record (ADR-0028), so a new one must
        join `JOURNAL_ONLY_RECORDS` or a reconstruction from the blob silently
        drops it — the F24 shape."""
        assert "stagnations" in JOURNAL_ONLY_RECORDS

    def test_the_blob_does_not_carry_it(self, tmp_path):
        session, repo = _run_turn(
            tmp_path, _repeat("a.txt") + [_content_only("done")],
            files={"a.txt": "hello"},
        )
        blob = repo._store.load_session("m13")
        assert not getattr(blob, "stagnations", []), (
            "the blob now carries the stagnation record; it is journal-only")


# ══════════════════════════════════════════════════════════════════════════
# 5. Tripwires — what M13 deliberately did NOT do
# ══════════════════════════════════════════════════════════════════════════


class TestTheDeferralTripwires:
    """M13's deferral ended with ADR-0035, and both tripwires were **inverted in
    the same phase that wired the consumers** — the P9/M15 and M11/M13 precedent.

    The inverses assert the *presence* of the wiring, so a later phase that
    removes it fails here instead of passing silently. That is the whole point
    of inverting rather than deleting: the tripwire's job changed from "this is
    not wired yet" to "this must stay wired".
    """

    def test_the_ladder_is_consulted_at_the_turn_boundary(self):
        """ADR-0035 makes the recovery track authoritative behind the
        `recovery_ladder` flag, so the turn boundary now consults the ladder.

        Inverted from `test_the_ladder_is_not_consulted_yet`, which M13 wrote
        and which ADR-0035 authorised this phase to replace. AST, not a source
        grep: an explanatory comment naming the symbol would make a whole-file
        grep match its own documentation (the P8 trap).
        """
        import ast

        src = (REPO / "wisp" / "core" / "runtime.py").read_text(encoding="utf-8")
        consulted = [n.attr for n in ast.walk(ast.parse(src))
                     if isinstance(n, ast.Attribute) and n.attr == "decide"]
        assert consulted, (
            "the turn loop no longer consults the recovery ladder — ADR-0035's "
            "recovery track has been unwired, which is a regression")

    def test_goal_met_is_gated_on_the_live_path(self):
        """ADR-0035 row 4: `may_report_goal_met()` is now a completion input.

        Inverted from `test_goal_met_is_not_yet_gated_on_the_live_path`. Note
        precisely what is asserted: the predicate is *consulted*. Whether it
        withholds `done` is enforcement, and enforcement stays staged behind
        ADR-0016's measurement — which is why the goal state is recorded before
        anything acts on it.
        """
        import ast

        src = (REPO / "wisp" / "core" / "runtime.py").read_text(encoding="utf-8")
        called = [n.func.attr for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "may_report_goal_met"]
        assert called, (
            "the live path no longer reads the stagnation predicate — "
            "ADR-0035 row 4 has been unwired, which is a regression")

    def test_the_mechanism_still_has_one_authority(self):
        """The detector reuses `OscillationTrap` rather than defining a second
        one — P7's AST pin, restated here because this phase touches the file."""
        import ast

        src = (REPO / "wisp" / "core" / "stagnation.py").read_text(
            encoding="utf-8")
        defined = [n.name for n in ast.walk(ast.parse(src))
                   if isinstance(n, ast.ClassDef)]
        assert "OscillationTrap" not in defined
