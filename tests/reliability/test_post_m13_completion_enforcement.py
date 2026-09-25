"""ADR-0036 — the stagnation completion gate, implemented.

M13 owns stagnation. `core.turn`'s pre-`done` gate may *withhold* `done` for a
bounded number of replan interventions, and then it surrenders honestly. It never
vetoes the turn, and the engine is handed only a **read-only predicate** — never
the detector, whose `observe()` mutates.

The tests are organised around the contract:

* **S1–S14**   the required matrix (bounded budget, last-iteration rule, fail
               open, the F35 replay fix, `UNKNOWN` staying non-blocking)
* **latch**    the property that makes the intervention's *reach* honest: on the
               live path a flat observation always fires the trap, and
               `trap_fired` latches — so the predicate cannot reopen
* **adversarial** the ways the seam could become a second authority
* **no-second-authority**  AST ratchets: no detector internals in the engine, and
               the counter is a local

The live tests drive the real `AgentRuntime.run_turn`; the direct ones drive
`core.turn` with no runtime, which is also the subagent/background shape (§17:
no detector ⇒ no intervention authority).
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from wisp.core.goal import GoalState, derive_goal_state
from wisp.core.stagnation import (
    ProgressSignal,
    StagnationDetector,
    compose_replan_nudge,
)

REPO = Path(__file__).resolve().parents[2]


# ══════════════════════════════════════════════════════════════════════════
# Harness
# ══════════════════════════════════════════════════════════════════════════


class _DictProvider:
    """Scripted provider: one entry per round, the last entry repeating.

    The repeat is what makes the bounded gate observable: after the gate
    withholds `done`, the next round is scripted again, so the turn keeps
    reaching the same gate until the budget is spent.
    """

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages, tools=None):
        self.calls += 1
        script = self._rounds[min(self.calls - 1, len(self._rounds) - 1)]
        yield from script()


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


def _call(name: str, args: dict, cid: str) -> dict:
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": args}}


def _read_call(path: str, cid: str) -> dict:
    return _call("read_file", {"path": path}, cid)


def _write_call(path: str, cid: str) -> dict:
    return _call("write_file", {"path": path, "content": "x"}, cid)


def _repeat(path: str, n: int = 2) -> list:
    """`n` rounds asking for the SAME work, each with a fresh call id.

    Two is enough to close the predicate: the second observation is flat *and*
    repeats the previous state digest, so the reused `OscillationTrap` fires
    while `consecutive_flat` is still below `min_consecutive` (F35's case).
    """
    return [_tool_round([_read_call(path, f"c{i}")]) for i in range(n)]


def _boom(message="provider exploded"):
    def _g():
        raise RuntimeError(message)
        yield  # pragma: no cover - keeps this a generator
    return _g


def _build(tmp_path, rounds, *, sid="pce", files=None, provider=None, **cfg_kw):
    """Build a runtime and a session; return them with the repo and provider."""
    from wisp.config import WispConfig
    from wisp.core.engine import WispAgentCore
    from wisp.core.runtime import AgentRuntime
    from wisp.core.session_repo import SessionRepository
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import SecurityPolicy
    from wisp.infra.store import UnifiedStore
    from wisp.infra.telemetry import Telemetry

    cfg_kw.setdefault("goal_state", True)
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        (ws / name).write_text(text)

    config = WispConfig().replace(workspace=str(ws), **cfg_kw)
    store = UnifiedStore(tmp_path / "wisp.db")
    repo = SessionRepository(store)
    provider = provider or _DictProvider(rounds)

    def factory():
        return WispAgentCore(config=config, provider=provider,
                             security=SecurityPolicy(), tool_executor=None)

    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
        telemetry=Telemetry(), core_factory=factory, session_repo=repo,
        config=config)
    session = {"id": sid, "model": "mock", "workspace": str(ws), "messages": []}
    return runtime, session, repo, provider


def _drain(runtime, session):
    async def _main():
        return [ev async for ev in runtime.run_turn(session, prompt="go")]
    return asyncio.run(_main())


def _run_turn(tmp_path, rounds, **kw):
    runtime, session, repo, provider = _build(tmp_path, rounds, **kw)
    return session, repo, _drain(runtime, session), provider


def _drive_core(rounds, *, max_iterations=30, **turn_kw):
    """Drive `core.turn` with no runtime — the subagent/background shape."""
    from wisp.config import WispConfig
    from wisp.core.engine import WispAgentCore
    from wisp.infra.security import SecurityPolicy

    config = WispConfig().replace(max_iterations=max_iterations)
    core = WispAgentCore(config=config, provider=_DictProvider(rounds),
                         security=SecurityPolicy(), tool_executor=None)
    session = {"id": "direct", "model": "mock", "workspace": ".", "messages": []}

    async def _main():
        return [ev async for ev in core.turn(session, "go", **turn_kw)]
    return session, asyncio.run(_main())


def _message_of(ev):
    """The `[SYSTEM]` text of a `system` event, wherever the flattener put it."""
    msg = ev.get("message")
    if msg is None and isinstance(ev.get("data"), dict):
        msg = ev["data"].get("message")
    return msg if isinstance(msg, str) else ""


def _replans(events):
    """The replan interventions the engine emitted this turn."""
    return [m for ev in events
            if ev.get("type") == "system"
            and (m := _message_of(ev)).startswith("[SYSTEM] Stagnation loop")]


def _goal_records(repo, sid="pce"):
    return (repo.reconstruct(sid).get("_journal") or {}).get("goal_states") or []


def _turn_inner_node():
    """The `_turn_inner` AST — for ordering claims the runtime cannot observe."""
    tree = ast.parse((REPO / "wisp" / "core" / "stateless.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_turn_inner":
            return node
    raise AssertionError("_turn_inner not found in stateless.py")


def _call_lines(node, matcher):
    return sorted(n.lineno for n in ast.walk(node)
                  if isinstance(n, ast.Call) and matcher(n.func))


def _turn_loop():
    """The `for iteration in range(max_iterations)` loop body of `_turn_inner`."""
    for node in ast.walk(_turn_inner_node()):
        if isinstance(node, ast.For) and any(
                isinstance(t, ast.Name) and t.id == "iteration"
                for t in ast.walk(node.target)):
            return node
    raise AssertionError("the turn iteration loop was not found")


def _replay(record):
    """Rebuild the goal state from the record ALONE (ADR-0036's replay contract).

    Uses `stagnation_allows_goal_met` — the predicate the arbiter actually
    consumed — and **not** the human-readable `stagnation_verdict`. Before F35
    was fixed this reconstructed from the verdict string, which ignores
    `trap_fired`, so a trap-only stagnation replayed as `GOAL_MET`.
    """
    return derive_goal_state(
        terminal_outcome=record["terminal_outcome"],
        acceptance_verdict=record["acceptance_verdict"] or None,
        stagnating=not record["stagnation_allows_goal_met"],
        turn_succeeded=record["turn_succeeded"],
        cancelled=record["cancelled"],
        escalated=record["escalated"],
    )


# ══════════════════════════════════════════════════════════════════════════
# S1–S5 — the bounded budget, on the real runtime path
# ══════════════════════════════════════════════════════════════════════════


class TestTheBudgetIsBounded:
    def test_s1_an_open_predicate_emits_done_with_no_intervention(self, tmp_path):
        """Nothing observed ⇒ the predicate is open ⇒ today's path exactly."""
        session, repo, events, _p = _run_turn(
            tmp_path, [_content_only("done")], stagnation_gate=True)
        assert _replans(events) == []
        assert [e for e in events if e.get("type") == "done"]
        record = _goal_records(repo)[0]
        assert record["stagnation_allows_goal_met"] is True
        assert record["turn_succeeded"] is True

    def test_s2_s3_s4_the_gate_withholds_twice_then_surrenders(self, tmp_path):
        """**The whole decision, end to end.** A closed predicate withholds
        `done` exactly twice; on the third pass the gate surrenders and the turn
        finishes. The turn still succeeded — it just is not `GOAL_MET`."""
        session, repo, events, provider = _run_turn(
            tmp_path, _repeat("a.txt", 2) + [_content_only("done")],
            files={"a.txt": "hello"}, stagnation_gate=True)

        replans = _replans(events)
        assert len(replans) == 2, (
            f"expected exactly two interventions, got {len(replans)}")
        # S3: the second intervention is a distinct message (a repeat of the
        # first would carry no new signal after a full round).
        assert replans[0] != replans[1]
        assert "Stagnation loop (repeat)" in replans[1]

        # S4: exhausted → done, and the turn SUCCEEDED (13-H5 unchanged).
        assert [e for e in events if e.get("type") == "done"]
        assert not [e for e in events
                    if e.get("type") == "error"
                    and not e.get("recoverable", True)]
        record = _goal_records(repo)[0]
        assert record["turn_succeeded"] is True
        assert record["terminal_outcome"] == "succeeded"
        assert record["stagnation_allows_goal_met"] is False
        assert record["goal_state"] == "goal_stagnated"
        # The intervention is not a retry: it never names the repeated action.
        assert "a.txt" not in replans[0]

    def test_s5_the_final_iteration_cannot_be_withheld(self, tmp_path):
        """`iteration + 1 == max_iterations` ⇒ surrender.

        Withholding here would end the loop, run the iteration-budget wrap-up,
        and turn an honest surrender into a fatal `CODE_ITERATION_BUDGET` — the
        A2 outcome ADR-0036 rejects. Three iterations: two tool rounds close the
        predicate, the third is the last one."""
        session, repo, events, _p = _run_turn(
            tmp_path, _repeat("a.txt", 2) + [_content_only("done")],
            files={"a.txt": "hello"}, stagnation_gate=True, max_iterations=3)

        assert _replans(events) == [], (
            "the gate withheld on the final iteration — its surrender would "
            "become a budget failure")
        assert [e for e in events if e.get("type") == "done"]
        assert not [e for e in events
                    if e.get("type") == "error"
                    and not e.get("recoverable", True)]
        record = _goal_records(repo)[0]
        assert record["turn_succeeded"] is True
        assert record["goal_state"] == "goal_stagnated"

    def test_the_intervention_is_persisted_in_the_transcript(self, tmp_path):
        """The runtime persists every `[SYSTEM]` injection so resume/replay sees
        what the model saw (issue #2B). The replan nudge must not be the one
        injection that goes missing."""
        session, repo, _events, _p = _run_turn(
            tmp_path, _repeat("a.txt", 2) + [_content_only("done")],
            files={"a.txt": "hello"}, stagnation_gate=True)
        persisted = [m for m in session.get("messages", [])
                     if isinstance(m.get("content"), str)
                     and "Stagnation loop" in m["content"]]
        assert len(persisted) == 2, (
            "the replan interventions were not persisted into the transcript")


# ══════════════════════════════════════════════════════════════════════════
# S6–S7 — the floor guard is untouched, and is consulted first
# ══════════════════════════════════════════════════════════════════════════


class TestTheFloorGuardIsUntouched:
    """S6/S7 are proven **structurally and by contract**, not behaviourally.

    The floor guard cannot be made to reject on a live turn in this
    environment: `_validate_tool_args` needs `jsonschema`, which is absent
    (F8), so every call is refused *before* dispatch — and the refusal is
    yielded from the pre-dispatch path (`stateless.py`, the `_blocked` branch),
    which never calls `guard.note_tool_result`. `wrote_code` therefore stays
    False and `rejection()` always returns None.

    So the claim "M13 cannot bypass the floor guard" is proven three ways
    instead: the ordering in the source, the floor guard's own unchanged
    contract, and a negative control showing the stagnation gate *is* reachable
    on the same turn shape once the floor guard is switched off.
    """

    def test_s6_the_floor_guard_is_consulted_first(self):
        """Ordering is the whole proof: the floor guard's block ends in
        `continue`, so while it rejects, control can never fall into the
        stagnation block."""
        body = _turn_inner_node()
        rejection_lines = _call_lines(
            body, lambda f: isinstance(f, ast.Attribute)
            and f.attr == "rejection")
        gate_lines = _call_lines(
            body, lambda f: isinstance(f, ast.Name) and f.id == "completion_gate")
        assert rejection_lines, "the floor guard is no longer consulted"
        assert gate_lines, "the stagnation gate is missing"
        assert max(rejection_lines) < min(gate_lines), (
            "the stagnation gate is evaluated before the verification floor — "
            "it could bypass it")

    def test_s7_the_floor_guard_block_ends_in_continue(self):
        """No double nudge: the floor guard's rejection branch `continue`s, so
        the two gates cannot both nudge in one pass.

        Bound by the name the rejection is assigned to, not by a literal, so the
        claim survives a rename of the local."""
        loop = _turn_loop()
        name = None
        for stmt in ast.walk(loop):
            if not isinstance(stmt, ast.Assign):
                continue
            if any(isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "rejection"
                   for n in ast.walk(stmt.value)):
                name = next((t.id for t in stmt.targets
                             if isinstance(t, ast.Name)), None)
                break
        assert name, "the `guard.rejection()` call is gone"

        branch = [n for n in ast.walk(loop)
                  if isinstance(n, ast.If)
                  and any(isinstance(x, ast.Name) and x.id == name
                          for x in ast.walk(n.test))]
        assert branch, f"no `if {name} is not None:` branch found"
        assert any(isinstance(s, ast.Continue) for s in ast.walk(branch[0])), (
            "the floor guard's rejection branch no longer continues, so the "
            "stagnation gate could nudge in the same pass")

    def test_s6_the_floor_guard_still_surrenders_honestly(self):
        """The contract the new gate must mirror, pinned on the untouched
        mechanism: a mutated unverified turn is blocked, and once the budget is
        spent it is allowed to finish."""
        from wisp.core.verification import VerificationFloorGuard

        guard = VerificationFloorGuard(min_turns=1, max_nudges=2)
        guard.note_tool_result("write_file", "ok", {"path": "a.txt"})
        assert guard.rejection() is not None, "the floor stopped rejecting"
        assert guard.rejection() is not None
        assert guard.rejection() is None, (
            "the floor guard no longer surrenders honestly — the new gate "
            "inherits this property and must not lose it")
        assert guard.resolved() is False

    def test_the_stagnation_gate_is_reachable_when_the_floor_is_off(
            self, tmp_path):
        """Negative control for S6/S7: on the *same* turn shape (a **mutating**
        call repeated, then content), the stagnation gate does fire — so its
        silence in the tests above is the floor guard's ordering, not an
        unreachable gate."""
        rounds = [_tool_round([_write_call("a.txt", f"c{i}")]) for i in range(2)]
        session, repo, events, _p = _run_turn(
            tmp_path, rounds + [_content_only("done")],
            stagnation_gate=True, verification_loop=False)
        assert len(_replans(events)) == 2
        assert _goal_records(repo)[0]["goal_state"] == "goal_stagnated"


# ══════════════════════════════════════════════════════════════════════════
# S8–S10 — fail open
# ══════════════════════════════════════════════════════════════════════════


class TestFailOpen:
    def test_s8_no_gate_is_todays_behaviour(self):
        """No callable ⇒ no intervention authority. This is also the
        subagent/background shape: they call `core.turn` directly, have no
        detector, and pass nothing."""
        _session, events = _drive_core([_content_only("done")])
        assert [e for e in events if e.get("type") == "done"]
        assert _replans(events) == []

    def test_s9_a_raising_gate_permits_done(self):
        """A broken predicate must not become a hung turn (ADR-0026's named
        failure). Fail open, and the absence stays visible in the record."""
        def _broken():
            raise RuntimeError("predicate exploded")

        _session, events = _drive_core(
            [_content_only("done")], completion_gate=_broken)
        assert [e for e in events if e.get("type") == "done"], (
            "a raising predicate withheld `done` — fail-open is broken")
        assert _replans(events) == []

    def test_s10_the_flag_off_preserves_the_old_path(self, tmp_path):
        """`stagnation_gate` gates ENFORCEMENT only. M13 still observes, the
        goal record still carries the predicate, and the goal state is the same
        — only the transcript differs."""
        off, repo_off, events_off, _p = _run_turn(
            tmp_path / "off", _repeat("a.txt", 2) + [_content_only("done")],
            files={"a.txt": "hello"}, stagnation_gate=False)
        on, repo_on, events_on, _p2 = _run_turn(
            tmp_path / "on", _repeat("a.txt", 2) + [_content_only("done")],
            files={"a.txt": "hello"}, stagnation_gate=True)

        assert _replans(events_off) == [], "the flag did not disable the gate"
        assert len(_replans(events_on)) == 2

        # Observation and recording are unaffected by the flag...
        for repo in (repo_off, repo_on):
            record = _goal_records(repo)[0]
            assert record["stagnation_allows_goal_met"] is False
            assert record["goal_state"] == "goal_stagnated"
        # ...and the flag is off by default.
        from wisp.config import WispConfig
        assert WispConfig().stagnation_gate is False

    def test_the_gate_opens_when_the_predicate_flips(self):
        """The gate is a predicate, not a latch of its own: when it opens, the
        turn finishes on the next pass. (Synthetic, because on the live path the
        predicate cannot reopen — see `TestThePredicateIsALatch`.)"""
        seen = {"n": 0}

        def _flip():
            seen["n"] += 1
            return seen["n"] > 1

        _session, events = _drive_core(
            [_content_only("a"), _content_only("b")], completion_gate=_flip)
        assert len(_replans(events)) == 1
        assert [e for e in events if e.get("type") == "done"]

    def test_a_permanently_closed_predicate_still_finishes(self):
        """No input can make the turn run forever: the budget is finite."""
        _session, events = _drive_core(
            [_content_only("x")], completion_gate=lambda: False,
            max_iterations=10)
        assert len(_replans(events)) == 2
        assert [e for e in events if e.get("type") == "done"]

    def test_a_single_iteration_turn_cannot_be_withheld(self):
        _session, events = _drive_core(
            [_content_only("x")], completion_gate=lambda: False,
            max_iterations=1)
        assert _replans(events) == []
        assert [e for e in events if e.get("type") == "done"]


# ══════════════════════════════════════════════════════════════════════════
# S11–S12 — the counter is per turn and not shared
# ══════════════════════════════════════════════════════════════════════════


class TestTheBudgetIsPerTurn:
    def test_s11_a_second_turn_gets_a_fresh_budget(self, tmp_path):
        """Two turns in one session. The first spends both interventions; the
        second must get two more — the counter is per turn, never per session."""
        # 5 provider calls per turn: tool, tool, content(hold), content(hold),
        # content(surrender) — scripted twice, because `calls` keeps counting.
        per_turn = _repeat("a.txt", 2) + [_content_only("done")] * 3
        runtime, session, repo, _p = _build(
            tmp_path, per_turn * 2, files={"a.txt": "hello"},
            stagnation_gate=True)

        first = _drain(runtime, session)
        second = _drain(runtime, session)

        assert len(_replans(first)) == 2, "turn 1 did not spend its budget"
        assert len(_replans(second)) == 2, (
            "turn 2's budget was not reset — the counter leaked across turns")
        records = _goal_records(repo)
        assert len(records) == 2
        assert all(r["goal_state"] == "goal_stagnated" for r in records)

    def test_s12_concurrent_turns_share_no_state(self, tmp_path):
        """Two independent runtimes, driven concurrently. Neither may see the
        other's budget, and neither transcript may contain the other's nudge."""
        rounds = _repeat("a.txt", 2) + [_content_only("done")]
        rt_a, sess_a, repo_a, _pa = _build(
            tmp_path / "a", rounds, sid="a", files={"a.txt": "hello"},
            stagnation_gate=True)
        rt_b, sess_b, repo_b, _pb = _build(
            tmp_path / "b", rounds, sid="b", files={"a.txt": "hello"},
            stagnation_gate=True)

        async def _both():
            return await asyncio.gather(
                _collect(rt_a, sess_a), _collect(rt_b, sess_b))

        ev_a, ev_b = asyncio.run(_both())
        assert len(_replans(ev_a)) == 2
        assert len(_replans(ev_b)) == 2
        for sess, sid in ((sess_a, "a"), (sess_b, "b")):
            nudges = [m for m in sess["messages"]
                      if isinstance(m.get("content"), str)
                      and "Stagnation loop" in m["content"]]
            assert len(nudges) == 2, f"session {sid} saw the wrong budget"


async def _collect(runtime, session):
    return [ev async for ev in runtime.run_turn(session, prompt="go")]


# ══════════════════════════════════════════════════════════════════════════
# S13 — F35: the trap-fired replay case
# ══════════════════════════════════════════════════════════════════════════


class TestF35TheRecordCarriesThePredicate:
    def test_s13_trap_fired_below_the_flat_threshold(self, tmp_path):
        """**The F35 regression.**

        Two identical observations close the predicate via `trap_fired` while
        `consecutive_flat` is still below `min_consecutive`. The *verdict*
        property ignores `trap_fired`, so it still says `progressing` — while
        the arbiter's input is closed. Replay must follow the arbiter's input,
        so the record must carry it."""
        session, repo, events, _p = _run_turn(
            tmp_path, _repeat("a.txt", 2) + [_content_only("done")],
            files={"a.txt": "hello"}, stagnation_gate=False)

        record = _goal_records(repo)[0]
        # The two facts that disagreed before the fix:
        assert record["stagnation_verdict"] == "progressing", (
            "the scenario no longer reproduces F35 — the trap did not fire "
            "below the flat threshold")
        assert record["stagnation_allows_goal_met"] is False, (
            "the record does not carry the predicate the arbiter consumed")
        assert record["goal_state"] == "goal_stagnated"

        # Replay from the record alone must agree with the live derivation.
        assert _replay(record) is GoalState.GOAL_STAGNATED

    def test_the_predicate_field_is_the_arbiter_input_not_a_second_opinion(
            self, tmp_path):
        """`stagnation_allows_goal_met` must be *derived from the same
        computation* the arbiter used, so it cannot drift from it."""
        for name, rounds in (("open", [_content_only("done")]),
                             ("closed", _repeat("a.txt", 2)
                              + [_content_only("done")])):
            session, repo, _ev, _p = _run_turn(
                tmp_path / name, rounds, files={"a.txt": "hello"})
            record = _goal_records(repo)[0]
            live = derive_goal_state(
                terminal_outcome=record["terminal_outcome"],
                acceptance_verdict=record["acceptance_verdict"] or None,
                stagnating=not record["stagnation_allows_goal_met"],
                turn_succeeded=record["turn_succeeded"],
                cancelled=record["cancelled"],
                escalated=record["escalated"],
            )
            assert str(live) == record["goal_state"], (
                f"{name}: live and replay disagree")

    def test_s13_replay_survives_a_restart(self, tmp_path):
        """A reconstructed run reproduces the trap-fired state — the record is
        the authority, not the live detector (which no longer exists)."""
        session, repo, _ev, _p = _run_turn(
            tmp_path, _repeat("a.txt", 2) + [_content_only("done")],
            files={"a.txt": "hello"}, stagnation_gate=True)
        from wisp.core.session_repo import SessionRepository
        from wisp.infra.store import UnifiedStore

        # A brand-new repository over the same store: the detector is gone.
        reopened = SessionRepository(UnifiedStore(tmp_path / "wisp.db"))
        records = (reopened.reconstruct("pce").get("_journal") or {}) \
            .get("goal_states") or []
        assert records, "the goal record did not survive the restart"
        assert _replay(records[0]) is GoalState.GOAL_STAGNATED


# ══════════════════════════════════════════════════════════════════════════
# S14 + the latch
# ══════════════════════════════════════════════════════════════════════════


class TestUnknownIsNotStagnation:
    def test_s14_unknown_stays_open_and_never_becomes_stagnation(self, tmp_path):
        """An unobserved turn records an OPEN predicate, so the arbiter can
        reach `GOAL_MET` on a `PASS`. `UNKNOWN` is never converted."""
        session, repo, _ev, _p = _run_turn(
            tmp_path, [_content_only("done")])
        record = _goal_records(repo)[0]
        assert record["stagnation_allows_goal_met"] is True
        # The same recorded input, with an acceptance PASS, is GOAL_MET.
        assert derive_goal_state(
            terminal_outcome=record["terminal_outcome"],
            acceptance_verdict="pass",
            stagnating=not record["stagnation_allows_goal_met"],
            turn_succeeded=True) is GoalState.GOAL_MET


class TestThePredicateIsALatch:
    """**Why the intervention's reach is bounded — recorded, not assumed.**

    On the live path a *flat* observation always repeats the previous state
    digest (the digest is built from the accumulating action and artifact sets,
    and any growth in either is progress by `is_progress_from`). A repeated
    digest fires `OscillationTrap`, and `trap_fired` is `bool(trap_verdicts)` —
    a latch that is never cleared. So the predicate is closed **iff** the trap
    has fired, and it cannot reopen, even after real progress.

    ADR-0036 §6 ratified the latch as a bounded, conservative consequence. This
    pins the stronger fact: with the shipped `min_consecutive=2` it is
    *universal*, so the gate's interventions can never convert a
    `GOAL_STAGNATED` into a `GOAL_MET` on the live path.
    """

    def test_progress_does_not_reopen_a_latched_predicate(self):
        det = StagnationDetector()          # min_consecutive=2, as shipped
        s1 = ProgressSignal().with_work(work_unit="read:a", outcome_hash="h1")
        s2 = s1.with_work(work_unit="read:a", outcome_hash="h1")   # identical
        s3 = s2.with_work(work_unit="read:b", outcome_hash="h2")   # progress

        det.observe(s1)
        det.observe(s2)
        assert det.may_report_goal_met() is False
        assert det.verdict == "progressing", (
            "the verdict property reports PROGRESSING while the predicate is "
            "closed — this IS F35's disagreement, and it is why the record "
            "must carry the predicate rather than the verdict")

        det.observe(s3)
        assert det.consecutive_flat == 0, "progress was not registered"
        assert det.may_report_goal_met() is False, (
            "the predicate reopened — the latch assumption is wrong")

    def test_a_flat_run_below_the_threshold_closes_via_the_trap(self):
        det = StagnationDetector()
        s = ProgressSignal().with_work(work_unit="read:a", outcome_hash="h1")
        det.observe(s)
        det.observe(s)
        assert det.consecutive_flat < det.min_consecutive
        assert det.trap_fired is True
        assert det.may_report_goal_met() is False

    def test_a_turn_that_recovers_still_records_goal_stagnated(self, tmp_path):
        """**The finding, on the live path.** A turn repeats one action (the trap
        fires) and *then* does something genuinely new — real progress. The goal
        state is still `GOAL_STAGNATED`, because the predicate is a latch.

        This is why the gate's interventions cannot convert a `GOAL_STAGNATED`
        into a `GOAL_MET`: they cannot reopen the predicate. Recorded as a test
        so the property is asserted rather than folklore — ADR-0036 §6 ratified
        the latch, and `PHASE_POST_M13_COMPLETION_ENFORCEMENT_IMPLEMENTATION.md`
        §15.1 reports the consequence.
        """
        rounds = [_tool_round([_read_call("a.txt", "c0")]),
                  _tool_round([_read_call("a.txt", "c1")]),   # repeat → trap
                  _tool_round([_read_call("b.txt", "c2")]),   # NEW work → progress
                  _content_only("done")]
        for name, gate in (("gate_off", False), ("gate_on", True)):
            session, repo, _ev, _p = _run_turn(
                tmp_path / name, rounds, files={"a.txt": "x", "b.txt": "y"},
                stagnation_gate=gate)
            record = _goal_records(repo)[0]
            assert record["stagnation_allows_goal_met"] is False, (
                f"{name}: the predicate reopened after real progress — the "
                "latch assumption is wrong and §15.1 must be revisited")
            assert record["goal_state"] == "goal_stagnated"
            assert record["turn_succeeded"] is True


# ══════════════════════════════════════════════════════════════════════════
# The intervention is a replan, and it lives in M13's home
# ══════════════════════════════════════════════════════════════════════════


class TestTheInterventionIsAReplan:
    def test_the_text_is_not_a_retry_instruction(self):
        """`FORBIDDEN_RUNGS[STAGNATION]` contains `RETRY`: retrying the same
        action against the same state *is* the loop being detected. Each attempt
        must ask for a change of approach."""
        for attempt in (1, 2):
            text = compose_replan_nudge(attempt).lower()
            assert any(word in text for word in
                       ("reconsider", "different", "change strategy")), (
                f"attempt {attempt} does not ask for a change of approach")
            for forbidden in ("retry", "try again", "run it again",
                              "same command", "repeat the previous"):
                assert forbidden not in text, (
                    f"the intervention instructs a retry: {forbidden!r}")

    def test_the_text_is_generic_and_carries_the_system_marker(self):
        """Generic: it names the condition, never the repeated action (the
        transcript has that). `[SYSTEM]` is required — it is how the runtime
        decides an injection is provider-visible and must be persisted."""
        text = compose_replan_nudge(1)
        assert text.startswith("[SYSTEM]")
        assert compose_replan_nudge(1) != compose_replan_nudge(2)

    def test_the_text_lives_in_m13s_home_module(self):
        """GH#27: the prose comes from the invariant's home module, so it cannot
        drift from the gate that emits it."""
        from wisp.core import stagnation
        assert stagnation.compose_replan_nudge.__module__ \
            == "wisp.core.stagnation"
        assert "stagnation" in compose_replan_nudge(1).lower()


# ══════════════════════════════════════════════════════════════════════════
# Adversarial — the seam must not become a second authority
# ══════════════════════════════════════════════════════════════════════════


class TestAdversarial:
    def test_a_fatal_error_with_a_closed_predicate_stays_goal_failed(
            self, tmp_path):
        """A terminal error outranks stagnation (row 3 > row 4). The gate must
        not soften a real failure into a progress signal."""
        session, repo, events, _p = _run_turn(
            tmp_path, _repeat("a.txt", 2) + [_boom()],
            files={"a.txt": "hello"}, stagnation_gate=True)
        record = _goal_records(repo)[0]
        assert record["terminal_outcome"] == "failed"
        assert record["turn_succeeded"] is False
        assert record["goal_state"] == "goal_failed"

    def test_a_closed_predicate_cannot_promote_a_failed_turn(self, tmp_path):
        """The gate withholds `done`; it never manufactures one. A turn whose
        provider dies stays failed whether the predicate is open or closed."""
        _s1, repo_open, _e1, _p1 = _run_turn(
            tmp_path / "open", [_boom()], stagnation_gate=True)
        _s2, repo_closed, _e2, _p2 = _run_turn(
            tmp_path / "closed", _repeat("a.txt", 2) + [_boom()],
            files={"a.txt": "hello"}, stagnation_gate=True)
        assert _goal_records(repo_open)[0]["goal_state"] == "goal_failed"
        assert _goal_records(repo_closed)[0]["goal_state"] == "goal_failed"

    def test_the_goal_state_record_is_frozen_against_a_duplicate(self, tmp_path):
        """A second GOAL_STATE event must not rewrite the first (ADR-0020)."""
        session, repo, _ev, _p = _run_turn(
            tmp_path, _repeat("a.txt", 2) + [_content_only("done")],
            files={"a.txt": "hello"}, stagnation_gate=True)
        first = _goal_records(repo)[0]
        assert first["goal_state"] == "goal_stagnated"
        # Re-deriving with the frozen state supplied returns it unchanged, even
        # for inputs that would otherwise yield GOAL_MET.
        assert derive_goal_state(
            terminal_outcome="succeeded", acceptance_verdict="pass",
            stagnating=False, turn_succeeded=True,
            already_recorded=first["goal_state"]) \
            is GoalState.GOAL_STAGNATED

    def test_the_engine_never_receives_the_detector(self):
        """Only the predicate crosses. `observe()` mutates, so handing the
        engine the object would give a second party write access to the one
        stagnation authority.

        AST, not a substring search: an explanatory comment naming the symbol
        would make a whole-file grep match its own documentation (the P8 trap),
        and this module's own docstrings do exactly that.
        """
        src = (REPO / "wisp" / "core" / "stateless.py").read_text()
        assert _stagnation_imports(src) == {"compose_replan_nudge"}, (
            "the engine imports more from M13 than its message text: "
            f"{sorted(_stagnation_imports(src))}")

        tree = ast.parse(src)
        forbidden = {"consecutive_flat", "min_consecutive", "OscillationTrap",
                     "ProgressSignal", "may_report_goal_met",
                     "StagnationDetector", "trap_fired", "observe"}
        touched = {n.attr for n in ast.walk(tree)
                   if isinstance(n, ast.Attribute)} & forbidden
        assert not touched, (
            f"the engine references M13 internals ({sorted(touched)}) — it must "
            "receive only the predicate")

    def test_the_gate_is_only_ever_called_never_inspected(self):
        """`completion_gate` is called once, at the gate, and its result is the
        only thing read. No attribute of it is touched, so it cannot be a
        back-door into M13."""
        tree = ast.parse((REPO / "wisp" / "core" / "stateless.py").read_text())
        reads = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute)
                 and isinstance(n.value, ast.Name)
                 and n.value.id == "completion_gate"]
        assert not reads, (
            f"the engine inspects the gate object ({[n.attr for n in reads]})")
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)
                 and n.func.id == "completion_gate"]
        assert len(calls) == 1, (
            f"the gate is called {len(calls)} times — it must be evaluated at "
            "exactly one site")

    def test_the_intervention_counter_is_a_local_not_shared_state(self):
        src = (REPO / "wisp" / "core" / "stateless.py").read_text()
        tree = ast.parse(src)
        stored = {t.id for n in ast.walk(tree)
                  if isinstance(n, ast.Name)
                  and isinstance(n.ctx, ast.Store)
                  for t in [n]}
        assert "stagnation_interventions_used" in stored
        attrs = {n.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute)}
        assert "stagnation_interventions_used" not in attrs, (
            "the counter is an attribute — it would be shared state")

    def test_the_bound_is_two(self):
        """ADR-0036 fixes the bound at two, matching
        `VerificationFloorGuard.max_nudges`."""
        src = (REPO / "wisp" / "core" / "stateless.py").read_text()
        tree = ast.parse(src)
        found = [n.value.value for n in ast.walk(tree)
                 if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "") == "_MAX_STAGNATION_INTERVENTIONS"
                         for t in n.targets)
                 and isinstance(n.value, ast.Constant)]
        assert found == [2], f"the intervention bound is not 2: {found}"


def _stagnation_imports(src: str) -> set[str]:
    """Names `stateless.py` imports from `wisp.core.stagnation`."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.ImportFrom):
            continue
        if (node.module or "") != "wisp.core.stagnation":
            continue
        out |= {a.name for a in node.names}
    return out
