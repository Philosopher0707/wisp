"""M11 — a node references its work unit.

M9 found the real precondition for M11 and recorded it (§17.6, ADR-0029):

    "The nodes are a count, not an identity — `build_turn_graph(run_id, n)`
     generates `turn:0…turn:n-1` from `len(exchanges) + 1`; a node never
     references its work unit."

and `WISP_TARGET_ARCHITECTURE.md` §14 says why that matters: *"given the
journal, the system can reconstruct the state as of any recorded transition,
and re-executing from that point is **idempotent**."* Re-executing a work unit
idempotently requires knowing *which* work unit a node stands for. An index
cannot answer that.

This file pins the fix, and the fix has two halves that are easy to confuse:

1. **A node gains a reference.** `TaskNode.work_unit` names the work unit the
   node records. It is an *identity*, not content: `call:<protocol id>` for a
   closed tool exchange, `output` for the terminal node.

2. **The M9 ratchet was evadable, and it forbade the fix.** M9's
   `test_task_nodes_carry_no_transcript_payload` was a **name blacklist**, and
   it listed `tool_call_id` as a payload — the very reference M9's own report
   says M11 requires. So the ratchet is replaced by a *classification* of every
   field, which a new field cannot slip past by being named something else.

The RED-first test is `test_a_node_references_its_work_unit`: it fails on the
pre-M11 tree, where the field does not exist and no node names its exchange.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from wisp.core.session import SessionEventType
from wisp.core.task_graph import (
    NODE_FIELD_KINDS,
    NodeFieldKind,
    TaskNode,
    build_turn_graph,
    materialize,
    node_field_violations,
    turn_work_units,
)

REPO = Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════════════
# Harness — one real turn, driven through the runtime
# ══════════════════════════════════════════════════════════════════════════


class _DictProvider:
    """Scripted provider: one entry per round, the last entry repeats."""

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


def _run_turn(tmp_path, rounds, *, files=None, sid="m11", **cfg_kw):
    """Drive one real turn and return (live session, repository)."""
    from wisp.config import WispConfig
    from wisp.core.engine import WispAgentCore
    from wisp.core.runtime import AgentRuntime
    from wisp.core.session_repo import SessionRepository
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import SecurityPolicy
    from wisp.infra.store import UnifiedStore
    from wisp.infra.telemetry import Telemetry

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
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


def _persisted_graph(repo, sid="m11"):
    events = repo.load_events(sid)
    graphs = [e for e in events if e.event_type == SessionEventType.TASK_GRAPH]
    assert graphs, "no TASK_GRAPH event was journaled"
    return graphs[0].payload["graph"], events


# ══════════════════════════════════════════════════════════════════════════
# 1. A node names the work unit it records
# ══════════════════════════════════════════════════════════════════════════


class TestANodeReferencesItsWorkUnit:
    def test_a_node_references_its_work_unit(self, tmp_path):
        """**The RED-first test.**

        A real turn with two tool exchanges must produce nodes whose
        `work_unit` names the exchanges — the same protocol ids the transcript
        used — plus the terminal output node.
        """
        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("a.txt", "c0")]),
             _tool_round([_read_call("a.txt", "c1")]),
             _content_only("done")],
            files={"a.txt": "hello"}, task_graph=True,
        )
        graph, _ = _persisted_graph(repo)

        # The ids the transcript actually used — read from the transcript, not
        # assumed, so the assertion is about agreement rather than a constant.
        used = [tc["id"] for m in session["messages"]
                for tc in (m.get("tool_calls") or ())]
        assert used == ["c0", "c1"], used

        assert [n["work_unit"] for n in graph["nodes"]] == [
            "call:c0", "call:c1", "output"], (
            "a node does not name its work unit; it is still only an index")

    def test_the_reference_resolves_to_the_journal(self, tmp_path):
        """An identity is only an identity if it can be looked up.

        Each exchange node's reference must name a `tool_call_id` that the
        journal actually recorded, so the graph can be traced back to the work
        it stands for.
        """
        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("a.txt", "c0")]),
             _tool_round([_read_call("a.txt", "c1")]),
             _content_only("done")],
            files={"a.txt": "hello"}, task_graph=True,
        )
        graph, events = _persisted_graph(repo)

        journaled = {
            str(e.payload.get("tool_call_id"))
            for e in events
            if e.event_type == SessionEventType.TOOL_RESULT
            and e.payload.get("tool_call_id")
        }
        assert journaled, "the journal recorded no tool_call_id at all"

        refs = [n["work_unit"] for n in graph["nodes"]
                if n["work_unit"].startswith("call:")]
        assert refs, "no node references an exchange"
        for ref in refs:
            assert ref.removeprefix("call:") in journaled, (
                f"{ref!r} names a work unit the journal never recorded — "
                "the reference is a label, not an identity")

    def test_the_terminal_node_is_the_output_node(self, tmp_path):
        """The last node records the turn's final output, which has no
        exchange — so its reference is a distinct kind rather than a
        fabricated call id."""
        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("a.txt", "c0")]), _content_only("done")],
            files={"a.txt": "hello"}, task_graph=True,
        )
        graph, _ = _persisted_graph(repo)
        assert graph["nodes"][-1]["work_unit"] == "output"
        assert [n["work_unit"] for n in graph["nodes"][:-1]] == ["call:c0"]

    def test_a_content_only_turn_still_has_one_identified_node(self, tmp_path):
        """The degenerate case: no exchange at all. The turn still has one work
        unit — its output — and it is still named."""
        session, repo = _run_turn(
            tmp_path, [_content_only("hi")], task_graph=True)
        graph, _ = _persisted_graph(repo)
        assert [n["work_unit"] for n in graph["nodes"]] == ["output"]

    def test_two_identical_calls_are_distinguishable(self, tmp_path):
        """Two byte-identical tool calls are two work units. A reference built
        from the tool *name* or its arguments would collide on them."""
        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("a.txt", "c0")]),
             _tool_round([_read_call("a.txt", "c1")]),
             _content_only("done")],
            files={"a.txt": "hello"}, task_graph=True,
        )
        graph, _ = _persisted_graph(repo)
        refs = [n["work_unit"] for n in graph["nodes"]]
        assert len(set(refs)) == len(refs), f"references collide: {refs}"

    def test_a_parallel_round_is_journaled_as_one_exchange_per_call(self, tmp_path):
        """A provider round carrying TWO tool calls becomes two work units, not
        one.

        `_group_exchanges` supports a genuine batch (`callA callB replyA
        replyB` → one exchange), and `turn_work_units` names it `call:c0+c1` —
        see `test_a_parallel_batch_names_all_of_its_ids`. But the live engine
        dispatches each call and streams its reply before the next call
        arrives, so the sequence it emits is `callA replyA callB replyB` and
        the grouping rule closes after each reply.

        Asserted rather than assumed: the node count depends on this ordering,
        so the ordering is pinned here too.
        """
        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("a.txt", "c0"), _read_call("a.txt", "c1")]),
             _content_only("done")],
            files={"a.txt": "hello"}, task_graph=True,
        )
        graph, events = _persisted_graph(repo)

        # The ordering this rests on: each reply closes its own exchange.
        replies = [e.payload.get("tool_call_id") for e in events
                   if e.event_type == SessionEventType.TOOL_RESULT]
        assert replies == ["c0", "c1"], (
            "the engine now batches its tool events; the exchange count — and "
            f"so the node count — changed: {replies}")

        assert [n["work_unit"] for n in graph["nodes"]] == [
            "call:c0", "call:c1", "output"]


# ══════════════════════════════════════════════════════════════════════════
# 2. The reference is not content
# ══════════════════════════════════════════════════════════════════════════


class TestTheReferenceCarriesNoContent:
    def test_the_reference_is_the_protocol_id_and_nothing_more(self, tmp_path):
        """The reference is `call:` + the protocol id, verbatim. It is not
        re-encoded, hashed or annotated — so it cannot carry a tool name, an
        argument or a result."""
        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("a.txt", "c0")]), _content_only("done")],
            files={"a.txt": "hello"}, task_graph=True,
        )
        graph, _ = _persisted_graph(repo)
        ref = graph["nodes"][0]["work_unit"]
        assert ref == "call:c0"
        assert "read_file" not in ref and "a.txt" not in ref

    def test_the_graph_still_supplies_no_message_content(self, tmp_path):
        """M9's behavioural probe, now driven by a real turn instead of a
        synthetic count — the reference must not have smuggled content in."""
        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("secret.txt", "c0")]),
             _content_only("the password is hunter2")],
            files={"secret.txt": "hunter2"}, task_graph=True,
        )
        graph, _ = _persisted_graph(repo)
        blob = json.dumps(graph)
        for probe in ("read_file", "secret.txt", "hunter2", "password"):
            assert probe not in blob, (
                f"the graph now contains {probe!r}; the reference is carrying "
                "content and the graph is a second copy of the transcript")


# ══════════════════════════════════════════════════════════════════════════
# 3. A node cannot be built without an identity
# ══════════════════════════════════════════════════════════════════════════


class TestANodeCannotBeBuiltFromACount:
    def test_build_turn_graph_takes_work_units(self):
        g = build_turn_graph("r1", ["call:c0", "call:c1", "output"])
        assert [n.node_id for n in g.nodes] == ["turn:0", "turn:1", "turn:2"]
        assert [n.work_unit for n in g.nodes] == [
            "call:c0", "call:c1", "output"]

    def test_a_bare_string_is_refused(self):
        """A `str` IS a `Sequence[str]`, so `build_turn_graph(r, "abc")` would
        silently build three nodes named after the characters. Refused."""
        with pytest.raises(TypeError):
            build_turn_graph("r1", "abc")

    def test_an_unnamed_work_unit_is_refused(self):
        """The whole point: a node that records no work unit is the pre-M11
        defect, so it must not be constructible from the turn's path."""
        with pytest.raises(ValueError):
            build_turn_graph("r1", ["call:c0", ""])

    def test_zero_work_units_is_an_empty_graph(self):
        g = build_turn_graph("r1", [])
        assert g.nodes == () and g.edges == () and g.entrypoint == ""

    def test_the_structural_key_and_the_reference_are_separate(self):
        """`node_id` is the graph's structural key — edges, deps, transitions
        and supersession all address it, so it must stay stable and unique
        within the graph. `work_unit` is the reference to the journal. Keeping
        them separate is what makes the reference reversible."""
        g = build_turn_graph("r1", ["call:c0", "call:c1"])
        assert g.edges == (("turn:0", "turn:1"),)
        assert g.node("turn:1").deps == ("turn:0",)
        assert g.node("turn:1").work_unit == "call:c1"

    def test_the_identity_survives_a_round_trip(self):
        from wisp.core.task_graph import TaskGraph

        g = materialize(build_turn_graph("r1", ["call:c0", "output"]))
        assert TaskGraph.from_dict(g.to_dict()) == g
        assert [n.work_unit for n in TaskGraph.from_dict(g.to_dict()).nodes] == [
            "call:c0", "output"]


# ══════════════════════════════════════════════════════════════════════════
# 4. The work-unit vocabulary is closed
# ══════════════════════════════════════════════════════════════════════════


class TestTheWorkUnitVocabulary:
    def test_turn_work_units_names_every_unit_in_order(self):
        assert turn_work_units([["c0"], ["c1"]]) == (
            "call:c0", "call:c1", "output")

    def test_a_parallel_batch_names_all_of_its_ids(self):
        assert turn_work_units([["c0", "c1"]]) == ("call:c0+c1", "output")

    def test_a_turn_with_no_exchange_still_has_its_output(self):
        assert turn_work_units([]) == ("output",)

    def test_every_reference_uses_a_declared_prefix(self):
        """A closed vocabulary, so a reader can tell the kind of work unit a
        node records without a lookup."""
        from wisp.core.task_graph import WORK_UNIT_PREFIXES, TERMINAL_WORK_UNIT

        refs = turn_work_units([["c0"], ["c1", "c2"]])
        for ref in refs:
            assert ref == TERMINAL_WORK_UNIT or ref.startswith(
                tuple(WORK_UNIT_PREFIXES)), (
                f"{ref!r} uses no declared prefix; the vocabulary is not closed")


# ══════════════════════════════════════════════════════════════════════════
# 5. The ratchet: every field classified, no payload permitted
# ══════════════════════════════════════════════════════════════════════════


class TestTheFieldRatchet:
    def test_every_node_field_is_classified(self):
        """**The ratchet, made total.**

        M9's ratchet was a name blacklist, so a payload field could evade it by
        being called something else — and it listed `tool_call_id`, the very
        reference M11 needs, as a payload. Classifying every field closes the
        evasion: a new field is a failure until it is classified deliberately.
        """
        names = {f.name for f in dataclasses.fields(TaskNode)}
        assert not node_field_violations(names), (
            "a TaskNode field is unclassified, stale, or a payload — see "
            "NODE_FIELD_KINDS. Adding a field is a decision, not an edit.")

    def test_the_ratchet_is_not_vacuous(self):
        """A guard nobody can see firing is a guard nobody can trust. Drive it
        against a synthetic offender and require it to fail."""
        names = {f.name for f in dataclasses.fields(TaskNode)} | {"tool_name"}
        violations = node_field_violations(names)
        assert any("tool_name" in v for v in violations), (
            "the ratchet accepted a transcript payload field; it is vacuous")

    def test_no_field_is_a_payload(self):
        """`PAYLOAD` is a declared kind with no member. A node holding tool
        content would make the graph a second copy of the transcript."""
        payloads = sorted(n for n, k in NODE_FIELD_KINDS.items()
                          if k is NodeFieldKind.PAYLOAD)
        assert not payloads, (
            f"TaskNode carries transcript payload: {payloads}. That makes the "
            "graph a second copy of the transcript, which can diverge from it. "
            "If this is intended it needs an ADR first — see PHASE_M11_REPORT.md")

    def test_the_reference_fields_are_the_ones_that_point_outward(self):
        """The classification is the design, so assert the design: only the
        reference fields point at anything outside the node."""
        refs = {n for n, k in NODE_FIELD_KINDS.items()
                if k is NodeFieldKind.REFERENCE}
        assert refs == {"deps", "superseded_by", "work_unit"}


# ══════════════════════════════════════════════════════════════════════════
# 6. Tripwires — what M11 deliberately did NOT do
# ══════════════════════════════════════════════════════════════════════════


class TestTheDeferralTripwires:
    def test_the_graph_still_does_not_drive_execution(self):
        """M11 was recorded as *"the graph drives execution"*. **That is not
        what landed**, and the ledger must not be read as though it did.

        Node identity is the *precondition* M9 identified; flipping control is
        the change ADR-0029 already recorded as the wrong strong reading (the
        graph cannot project the transcript, so making it the driver means
        copying the transcript into it). Pinned so the gap stays visible.

        AST, not a source grep: an explanatory comment naming the symbol would
        make a whole-file grep match its own documentation — the P8 trap M12
        hit.
        """
        import ast

        src = (REPO / "wisp" / "core" / "runtime.py").read_text(encoding="utf-8")
        consulted = [
            n.attr for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Attribute)
            and n.attr in {"ready_ids", "ready_nodes"}
        ]
        assert not consulted, (
            "the turn loop now asks the graph what to run — the graph drives "
            "execution, which is M11's original wording and not what shipped. "
            "If this is intended, it needs its own ADR and a report.")

    def test_the_progress_signal_now_names_work_units(self):
        """**M11's tripwire fired, and this is its inverse.**

        It read: *"`ProgressSignal` still counts nodes rather than naming
        them ... Pinned so the next phase starts from the fact rather than
        rediscovering it."* M13 landed and the signal now names the work it
        observed, so the tripwire did its job and is replaced by its inverse —
        the P9/M15 pattern.

        The count fields stay: the turn-end signal (`from_verdict_and_graph`)
        still reads them, and M13 did not remove an existing input.
        """
        from wisp.core.stagnation import ProgressSignal

        names = {f.name for f in dataclasses.fields(ProgressSignal)}
        assert {"completed_nodes", "total_nodes"} <= names, (
            "the count-based fields were removed; M13's signal reads them")
        assert "work_units" in names, (
            "the progress signal does not name the work it observed")
