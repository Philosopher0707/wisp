"""Materialized task graph (migration P4).

Turns a turn's work into an explicit, inspectable graph — and makes that graph
**persistent state** rather than a recomputation.

Three things the existing code already had, and one it did not:

| Concern | Before | After |
|---|---|---|
| Node vocabulary | `graph/types.py::NodeType` / `NodeStatus` | reused unchanged |
| Grouping rule | `graph/scheduler.py::ready_nodes` recomputed readiness on every pass and **stored nothing** | `ready` is a field on each node, written by `apply_transition` |
| Write path | ~30 direct status mutations across the codebase, 11 of them unpersisted (audit) | `apply_transition()` is the only writer, and it validates |
| Persistence | `GraphStore` uses its **own** SQLite file | the session journal (`UnifiedStore`), so the graph is a **projection of the durable record** |

**Why the journal and not `GraphStore`.** `WISP_MIGRATION_PLAN.md` says to reuse
`wisp/graph/`'s store. This reuses its *types and legality rules* but journals through
`UnifiedStore`, deliberately: `GraphStore` opens a second SQLite database
(`graph/store.py::_conn`), and a second database would fragment the durable record that P0-P3 spent
four phases consolidating into one append-only journal. A graph that replays from the same log as
everything else is also what lets the message list become a *projection* of the graph — the plan's own
stated mitigation for P4's risk. Recorded as ADR-0019.

**Ready is materialized, and that is the point.** `ready_nodes()` in `graph/scheduler.py` is a pure
function of `(graph, state)`. Materializing it is what makes readiness *state*: it can be inspected
without re-deriving, persisted, and compared against a recomputation — which is how a divergence
between the graph and the scheduler becomes visible instead of silent.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Iterable

from wisp.graph.types import NodeStatus, NodeType


class TaskNodeState(StrEnum):
    """The node state vocabulary, EXTENDED (migration P5).

    A strict **superset** of `graph/types.py::NodeStatus` — every `NodeStatus`
    value appears here with the identical string. That shape is deliberate and
    has precedent in this repo: `RunState` (8) ⊃ `RunStatus` (7) was verified in
    P0 and needed no coercion shim (ADR-0003). The superset is pinned by
    `test_task_node_state_is_a_superset_of_node_status`.

    **Why a superset rather than editing `NodeStatus`.** P5 asks for seven new
    states, and `SKIPPED` conflates "predecessor failed" with "dead branch" —
    a real ambiguity worth resolving. But `NodeStatus` is consumed by
    `graph/executor.py` (1149 lines with join policies, terminality checks and
    retry semantics) and by `graph/scheduler.py::is_finished`, which lists
    terminal statuses **explicitly**. A new terminal state there would make
    `is_finished` return False forever — a run that never completes.

    The plan's own safety net for that change (`test_graph_fuzz.py`,
    `test_graph_races.py`, `test_graph_resume.py`) **does not exist in the
    repository**, and Layer B's executor has **zero** references from
    `core/runtime.py` or `core/stateless.py` — it is not on the live turn path.
    So the states are added where the live loop's graph lives, and Layer B is
    left alone until its safety net is real.
    """

    # ── the NodeStatus values, unchanged ──
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    # ── P5 additions (WISP_GRAPH_DOMAIN_MODEL.md §4) ──
    WAITING = "waiting"            # parked on an external condition
    BLOCKED = "blocked"            # cannot proceed; distinct from SKIPPED
    OBSERVED = "observed"          # acted, outcome recorded, not yet judged
    VERIFYING = "verifying"        # handed to an independent verifier
    INCONCLUSIVE = "inconclusive"  # verification could not decide
    INVALIDATED = "invalidated"    # a dependency changed under it
    SUPERSEDED = "superseded"      # replaced by a new node; retained for history


def coerce_node_state(value: TaskNodeState | NodeStatus | str) -> TaskNodeState:
    """Map any known node-state value to `TaskNodeState`.

    Accepts `NodeStatus` directly (the superset means this always succeeds) and
    raises on anything unknown — fail loud, so a producer that invents a state
    becomes visible rather than being silently treated as non-terminal. Same
    discipline as `runs/record.py::coerce_state`.

    NOTE the `.value` branch. `graph/types.py::NodeStatus` is a `str, Enum`,
    **not** a `StrEnum`, so `str(NodeStatus.SUCCESS)` is `"NodeStatus.SUCCESS"`
    — not `"success"`. Calling `str()` on it would reject every `NodeStatus`
    member, which is exactly what happened the first time this ran. `StrEnum`
    members are handled by the same branch.
    """
    if isinstance(value, TaskNodeState):
        return value
    raw = getattr(value, "value", value)
    try:
        return TaskNodeState(str(raw))
    except ValueError:
        raise ValueError(f"unknown node state: {value!r}") from None


#: The node state machine. `apply_transition` refuses anything not listed here,
#: so an illegal move is a loud error rather than a status that quietly means
#: two things. Terminal states have no outgoing edges — the same discipline as
#: `runs/record.py::LEGAL_TRANSITIONS`.
#:
#: PENDING may settle DIRECTLY (→ SUCCESS/FAILURE/TIMEOUT) as well as via
#: RUNNING. That is not laxity: a graph materialized retroactively from a
#: completed turn, or a node whose work was instantaneous, has no observed
#: RUNNING step to record, and refusing the settlement would force a caller to
#: write a transition that never happened. What the machine still catches is the
#: error class that matters — anything leaving a TERMINAL state, and a stale
#: `from_status`.
LEGAL_NODE_TRANSITIONS: dict[TaskNodeState, tuple[TaskNodeState, ...]] = {
    TaskNodeState.PENDING: (
        TaskNodeState.RUNNING, TaskNodeState.WAITING, TaskNodeState.BLOCKED,
        TaskNodeState.SUCCESS, TaskNodeState.FAILURE, TaskNodeState.TIMEOUT,
        TaskNodeState.SKIPPED, TaskNodeState.CANCELLED,
        TaskNodeState.SUPERSEDED, TaskNodeState.INVALIDATED,
    ),
    TaskNodeState.RUNNING: (
        TaskNodeState.SUCCESS, TaskNodeState.FAILURE, TaskNodeState.TIMEOUT,
        TaskNodeState.CANCELLED, TaskNodeState.SKIPPED,
        TaskNodeState.OBSERVED, TaskNodeState.BLOCKED,
        TaskNodeState.INVALIDATED, TaskNodeState.SUPERSEDED,
    ),
    # P5: the observe → verify → judge leg, and the recovery exits from it.
    TaskNodeState.WAITING: (
        TaskNodeState.RUNNING, TaskNodeState.BLOCKED, TaskNodeState.TIMEOUT,
        TaskNodeState.CANCELLED, TaskNodeState.INVALIDATED,
        TaskNodeState.SUPERSEDED,
    ),
    TaskNodeState.OBSERVED: (
        TaskNodeState.VERIFYING, TaskNodeState.SUCCESS, TaskNodeState.FAILURE,
        TaskNodeState.INVALIDATED, TaskNodeState.SUPERSEDED,
    ),
    TaskNodeState.VERIFYING: (
        TaskNodeState.SUCCESS, TaskNodeState.FAILURE,
        TaskNodeState.INCONCLUSIVE, TaskNodeState.INVALIDATED,
        TaskNodeState.SUPERSEDED,
    ),
    # BLOCKED and INCONCLUSIVE are RESUMABLE — that is the point of separating
    # them from FAILURE. A blocked node is not a failed one, and an
    # undecidable verdict is not a rejection.
    TaskNodeState.BLOCKED: (
        TaskNodeState.RUNNING, TaskNodeState.PENDING,
        TaskNodeState.CANCELLED, TaskNodeState.INVALIDATED,
        TaskNodeState.SUPERSEDED,
    ),
    TaskNodeState.INCONCLUSIVE: (
        TaskNodeState.RUNNING, TaskNodeState.VERIFYING,
        TaskNodeState.CANCELLED, TaskNodeState.INVALIDATED,
        TaskNodeState.SUPERSEDED,
    ),
    TaskNodeState.SUCCESS: (TaskNodeState.INVALIDATED,
                            TaskNodeState.SUPERSEDED),
    TaskNodeState.FAILURE: (TaskNodeState.INVALIDATED,
                            TaskNodeState.SUPERSEDED),
    TaskNodeState.TIMEOUT: (TaskNodeState.INVALIDATED,
                            TaskNodeState.SUPERSEDED),
    TaskNodeState.SKIPPED: (TaskNodeState.INVALIDATED,
                            TaskNodeState.SUPERSEDED),
    TaskNodeState.CANCELLED: (TaskNodeState.INVALIDATED,
                              TaskNodeState.SUPERSEDED),
    # INVALIDATED and SUPERSEDED are final: a node that was invalidated or
    # replaced is history, and history is not rewritten.
    TaskNodeState.INVALIDATED: (),
    TaskNodeState.SUPERSEDED: (),
}

#: States a node can be in and still be considered settled for the purpose of
#: releasing downstream work. NOTE this is NOT the same as "has no outgoing
#: edges": P5 requires that a settled node can still be INVALIDATED (a stale
#: success is the thing invalidation exists to demote), so SUCCESS and FAILURE
#: have outgoing edges. `FINAL_NODE_STATES` below is the no-outgoing-edge set.
TERMINAL_NODE_STATES = frozenset({
    TaskNodeState.SUCCESS, TaskNodeState.FAILURE, TaskNodeState.TIMEOUT,
    TaskNodeState.CANCELLED, TaskNodeState.SKIPPED,
    TaskNodeState.INVALIDATED, TaskNodeState.SUPERSEDED,
})

#: Retained under the old name for callers that only care about the settled set.
TERMINAL_NODE_STATUSES = TERMINAL_NODE_STATES

#: States with no outgoing edges — history, which is not rewritten.
FINAL_NODE_STATES = frozenset({
    TaskNodeState.INVALIDATED, TaskNodeState.SUPERSEDED,
})


# ══════════════════════════════════════════════════════════════════════════
# The work-unit reference (migration M11)
#
# "A node never references its work unit" — M9 §17.6. Nodes were generated
# from a COUNT (`turn:0 … turn:n-1`), so nothing connected a node to the work
# it recorded, and `WISP_TARGET_ARCHITECTURE.md` §14's replay guarantee —
# *"re-executing from that point is idempotent"* — had nothing to key on.
#
# `TaskNode.work_unit` is that reference. It is an **identity**, not content:
# it names the work unit and carries none of it. The distinction is enforced,
# not merely documented — see `NODE_FIELD_KINDS` below.
# ══════════════════════════════════════════════════════════════════════════

#: The reference used for a closed tool exchange: this prefix + the protocol
#: ids the exchange's calls carry, joined by `+` for a parallel batch. The id
#: is the one `runtime._exchange_parts` mints and stamps on the assistant
#: `tool_calls` block, the tool reply, the TOOL_RESULT event, the proposal and
#: the outcome — so the reference and the transcript cannot disagree.
WORK_UNIT_CALL_PREFIX = "call:"

#: The reference used for the terminal node: the turn's final output, which has
#: no exchange and therefore no protocol id. A distinct kind rather than a
#: fabricated call id.
TERMINAL_WORK_UNIT = "output"

#: The closed vocabulary. A reference's kind is readable without a lookup, and
#: a new kind is a deliberate addition rather than an accident.
WORK_UNIT_PREFIXES = (WORK_UNIT_CALL_PREFIX,)


def turn_work_units(
    exchange_call_ids: Iterable[Iterable[str]],
) -> tuple[str, ...]:
    """The identity of every work unit in one turn, in order.

    One entry per closed tool exchange — identified by the protocol ids its
    calls carry — plus the terminal output node. **The single authority for
    "what are a turn's work units"**, so the runtime cannot build a node for
    something the journal would not recognise.
    """
    units = [WORK_UNIT_CALL_PREFIX + "+".join(ids) for ids in exchange_call_ids]
    return tuple(units) + (TERMINAL_WORK_UNIT,)


class NodeFieldKind(StrEnum):
    """What a `TaskNode` field is FOR. The classification *is* the design.

    M9's payload ratchet was a **name blacklist** (`{"tool_name", "arguments",
    "result", ...}`), which fails in both directions:

    * it is **evadable by naming** — a payload field called `body` passes;
    * it was **wrong about `tool_call_id`**, listed as a payload when it is the
      reference M9's own report says M11 requires (ADR-0033).

    Classifying every field closes the evasion: a new field is a test failure
    until it is classified deliberately.
    """

    #: The graph's own bookkeeping — ids, state, readiness, a human note.
    STRUCTURAL = "structural"
    #: An opaque pointer at something outside the node: another node, or a work
    #: unit in the journal. Carries no content.
    REFERENCE = "reference"
    #: Transcript content. **Forbidden.** A node holding tool content makes the
    #: graph a second copy of the transcript, which can diverge from it.
    #: Declared so the prohibition is expressible; no field may use it.
    PAYLOAD = "payload"


#: Every field on `TaskNode`, classified. `node_field_violations()` is the
#: ratchet that reads this; `test_every_node_field_is_classified` drives it.
NODE_FIELD_KINDS: dict[str, NodeFieldKind] = {
    "node_id": NodeFieldKind.STRUCTURAL,
    "kind": NodeFieldKind.STRUCTURAL,
    "iteration": NodeFieldKind.STRUCTURAL,
    "status": NodeFieldKind.STRUCTURAL,
    "ready": NodeFieldKind.STRUCTURAL,
    "detail": NodeFieldKind.STRUCTURAL,
    "deps": NodeFieldKind.REFERENCE,
    "superseded_by": NodeFieldKind.REFERENCE,
    "work_unit": NodeFieldKind.REFERENCE,
}


def node_field_violations(names: Iterable[str]) -> list[str]:
    """The field ratchet, as a function so a test can drive it.

    Returns the violations for a set of field names: **unclassified** (a field
    was added without a decision), **stale** (a classification outlived its
    field), and **payload** (a field that would make the graph a second copy of
    the transcript). Exposed rather than inlined so
    `test_the_ratchet_is_not_vacuous` can prove it fires.
    """
    names = set(names)
    out = [f"unclassified field: {n}"
           for n in sorted(names - set(NODE_FIELD_KINDS))]
    out += [f"stale classification: {n}"
            for n in sorted(set(NODE_FIELD_KINDS) - names)]
    out += [f"payload field: {n}"
            for n, k in sorted(NODE_FIELD_KINDS.items())
            if k is NodeFieldKind.PAYLOAD]
    return out


def is_legal_node_transition(from_status: TaskNodeState | str,
                             to_status: TaskNodeState | str) -> bool:
    return coerce_node_state(to_status) in \
        LEGAL_NODE_TRANSITIONS[coerce_node_state(from_status)]


@dataclass(frozen=True)
class TaskNode:
    """One unit of a turn's work.

    `ready` is MATERIALIZED — written by `apply_transition`, never derived at
    read time. A caller asking "what can run now?" reads a stored field.
    """

    node_id: str
    kind: NodeType = NodeType.AGENT
    iteration: int = 0
    status: TaskNodeState = TaskNodeState.PENDING
    ready: bool = False
    deps: tuple[str, ...] = ()
    detail: str = ""
    #: Set when this node was replaced by another (migration P5). The node is
    #: RETAINED with a pointer rather than deleted — replay needs the history,
    #: and a task is never mutated into a different task.
    superseded_by: str = ""
    #: **The work unit this node records** (migration M11). An opaque reference
    #: — `call:<protocol id>` for a closed tool exchange, `output` for the
    #: terminal node — never the work itself. It is what makes "re-executing
    #: from this point is idempotent" (target architecture §14) answerable: an
    #: index cannot say *which* action already ran.
    #:
    #: NOTE the split from `node_id`. `node_id` is the graph's **structural
    #: key**: edges, `deps`, transitions and supersession all address it, so it
    #: must be stable and unique within the graph. Deriving it from a
    #: provider-supplied id would put the topology at the mercy of transcript
    #: data (and of `_exchange_parts`'s random fallback for id-less traffic).
    #: The reference is a separate field on purpose — see ADR-0033.
    work_unit: str = ""

    def __post_init__(self) -> None:
        """Coerce `status` to `TaskNodeState` on construction.

        Without this, a caller passing `NodeStatus.PENDING` stores a
        `NodeStatus` — and `NodeStatus` is a `str, Enum`, not a `StrEnum`, so
        `node.status is TaskNodeState.PENDING` is **False** for the same
        semantic value. Identity comparisons then silently fail: a node reads
        as non-pending, readiness is skipped, and `apply_transition` reports a
        stale transition for a node nobody touched.

        Coercing at the boundary means the rest of the module can compare with
        `is` safely.
        """
        if not isinstance(self.status, TaskNodeState):
            object.__setattr__(self, "status", coerce_node_state(self.status))

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "kind": self.kind.value,
                "iteration": self.iteration, "status": self.status.value,
                "ready": self.ready, "deps": list(self.deps),
                "detail": self.detail, "superseded_by": self.superseded_by,
                "work_unit": self.work_unit}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskNode":
        return cls(node_id=d["node_id"],
                   kind=NodeType(d.get("kind", "agent")),
                   iteration=int(d.get("iteration", 0)),
                   status=coerce_node_state(d.get("status", "pending")),
                   ready=bool(d.get("ready", False)),
                   deps=tuple(d.get("deps") or ()),
                   detail=d.get("detail", ""),
                   superseded_by=d.get("superseded_by", ""),
                   work_unit=d.get("work_unit", ""))


@dataclass(frozen=True)
class NodeTransition:
    """The ONLY write path for node state.

    Carries the reason and the sequence number, so the journal can answer not
    just *what* a node's status is but *why* it changed and *when* — which the
    ~30 direct mutations it replaces could not.
    """

    run_id: str
    node_id: str
    from_status: TaskNodeState
    to_status: TaskNodeState
    seq: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "node_id": self.node_id,
                "from_status": self.from_status.value,
                "to_status": self.to_status.value, "seq": self.seq,
                "reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NodeTransition":
        return cls(run_id=d["run_id"], node_id=d["node_id"],
                   from_status=coerce_node_state(d["from_status"]),
                   to_status=coerce_node_state(d["to_status"]),
                   seq=int(d.get("seq", 0)), reason=d.get("reason", ""))


@dataclass(frozen=True)
class TaskGraph:
    """A materialized view of one turn's work."""

    run_id: str
    entrypoint: str = ""
    nodes: tuple[TaskNode, ...] = ()
    edges: tuple[tuple[str, str], ...] = ()

    def node(self, node_id: str) -> TaskNode | None:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def ready_ids(self) -> list[str]:
        """Read the MATERIALIZED readiness — no recomputation."""
        return sorted(n.node_id for n in self.nodes if n.ready)

    def with_node(self, node: TaskNode) -> "TaskGraph":
        return replace(self, nodes=tuple(
            node if n.node_id == node.node_id else n for n in self.nodes))

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "entrypoint": self.entrypoint,
                "nodes": [n.to_dict() for n in self.nodes],
                "edges": [list(e) for e in self.edges]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskGraph":
        return cls(run_id=d["run_id"], entrypoint=d.get("entrypoint", ""),
                   nodes=tuple(TaskNode.from_dict(n)
                               for n in d.get("nodes") or ()),
                   edges=tuple(tuple(e) for e in d.get("edges") or ()))


# ── Construction ────────────────────────────────────────────────────────


def build_turn_graph(run_id: str,
                     work_units: Iterable[str]) -> TaskGraph:
    """One `AGENT` node per work unit, chained in order.

    The plan's mapping: *"the turn becomes a graph with one AGENT node per
    iteration."* Iteration 0 is the entrypoint; each later node depends on the
    one before it, so the chain is a real dependency structure rather than a
    list — which is what makes readiness meaningful rather than trivially
    "everything".

    **It takes the work units, not a count** (migration M11). It used to take
    an `int` and generate `turn:0 … turn:n-1`, which is how a node ended up
    with no connection to the work it recorded. The caller supplies the
    identities — `turn_work_units()` is the one authority for deriving them
    from a turn — so a node that records nothing is not constructible.

    `node_id` remains the structural key (`turn:i`); the identity travels in
    `work_unit`. See ADR-0033 for why those are separate fields.
    """
    if isinstance(work_units, (str, bytes)):
        # A `str` IS a `Sequence[str]`, so `build_turn_graph(r, "abc")` would
        # silently build three nodes named after the characters. Refused rather
        # than guessed at.
        raise TypeError(
            "work_units must be a sequence of work-unit references, not a "
            f"string: {work_units!r}")
    units = tuple(work_units)
    for u in units:
        if not u:
            raise ValueError(
                "a work unit must be named; an empty reference is the pre-M11 "
                "defect (a node that records nothing)")
    nodes: list[TaskNode] = []
    edges: list[tuple[str, str]] = []
    for i, unit in enumerate(units):
        nid = f"turn:{i}"
        deps = (f"turn:{i - 1}",) if i else ()
        nodes.append(TaskNode(node_id=nid, kind=NodeType.AGENT, iteration=i,
                              deps=deps, work_unit=unit))
        if i:
            edges.append((f"turn:{i - 1}", nid))
    return TaskGraph(run_id=run_id,
                     entrypoint="turn:0" if nodes else "",
                     nodes=tuple(nodes), edges=tuple(edges))


def _compute_ready(graph: TaskGraph) -> set[str]:
    """Derive readiness. Used ONLY to materialize it, never at read time.

    A node with no incoming edges is READY, entrypoint or not. This deliberately
    differs from `graph/scheduler.py:41`, which leaves a non-entry root pending
    as a "dead definition" — correct for a *compiled* graph, where a root with no
    edges is a mistake. P5 introduces runtime-created independent roots, where it
    is intentional: `create_node(graph, node, deps=[])` asks for a parallel task,
    and parking it forever would be the footgun rather than the safeguard.
    """
    statuses = {n.node_id: n.status for n in graph.nodes}
    incoming: dict[str, list[str]] = {n.node_id: [] for n in graph.nodes}
    for src, dst in graph.edges:
        if dst in incoming:
            incoming[dst].append(src)
    ready: set[str] = set()
    for n in graph.nodes:
        if n.status is not TaskNodeState.PENDING:
            continue
        preds = incoming[n.node_id]
        if not preds:
            ready.add(n.node_id)      # a root: entrypoint, or a runtime-created
            continue
        if all(statuses.get(p) in TERMINAL_NODE_STATES for p in preds):
            ready.add(n.node_id)
    return ready


def materialize(graph: TaskGraph) -> TaskGraph:
    """Write the derived readiness back onto the nodes.

    Idempotent: materializing twice yields the same graph. Returns a new graph;
    never mutates in place.
    """
    ready = _compute_ready(graph)
    return replace(graph, nodes=tuple(
        replace(n, ready=n.node_id in ready) for n in graph.nodes))


def apply_transition(graph: TaskGraph,
                     transition: NodeTransition) -> TaskGraph:
    """The single write path for node state.

    Validates legality, refuses an unknown node, refuses a stale
    `from_status`, and **re-materializes readiness** so the stored `ready`
    flags cannot go stale after a status change.

    Raises `ValueError` rather than returning a sentinel: an illegal
    transition is a defect, and a silently-ignored one is how a node ends up
    in a state nobody can explain.
    """
    node = graph.node(transition.node_id)
    if node is None:
        raise ValueError(f"unknown node: {transition.node_id}")
    _to = coerce_node_state(transition.to_status)
    _from = coerce_node_state(transition.from_status)
    if node.status is not _from:
        raise ValueError(
            f"stale transition: {transition.node_id} is {node.status.value}, "
            f"not {_from.value}")
    if not is_legal_node_transition(_from, _to):
        raise ValueError(
            f"illegal node transition: {_from.value} -> {_to.value}")
    return materialize(graph.with_node(replace(node, status=_to)))


def replay_transitions(graph: TaskGraph,
                       transitions: Iterable[NodeTransition]) -> TaskGraph:
    """Rebuild a graph by applying transitions in sequence order.

    This is what makes the graph a *projection of the journal*: a fresh replay
    of the same rows produces the same graph, so the persisted graph is a
    cache and the journal is the truth.
    """
    out = materialize(graph)
    for t in sorted(transitions, key=lambda t: t.seq):
        out = apply_transition(out, t)
    return out


def divergences(graph: TaskGraph) -> list[str]:
    """Node ids whose materialized `ready` disagrees with a fresh derivation.

    A cheap integrity check. Materializing readiness buys inspectability at the
    cost of a value that can go stale; this is how staleness becomes *visible*
    rather than a silent difference between what the graph says and what the
    scheduler would do.
    """
    derived = _compute_ready(graph)
    return sorted(
        n.node_id for n in graph.nodes if n.ready != (n.node_id in derived))


# ══════════════════════════════════════════════════════════════════════════
# Runtime graph mutation (migration P5)
#
# "This is the phase that creates the Persistent Graph Loop property."
#
# Every function here is PURE: it returns a new `TaskGraph` and never mutates
# its input. That is not stylistic. `graph/types.py` is built on frozen
# dataclasses precisely so history is preserved, and P5's fourth item requires
# that a task is NEVER mutated into a different task — a replan creates a NEW
# node and marks the old SUPERSEDED with a pointer. Immutability is what makes
# replay possible: a mutated-in-place graph cannot be reconstructed from a log.
# ══════════════════════════════════════════════════════════════════════════


class GrowthBudgetExceeded(RuntimeError):
    """Raised when an expansion would exceed the graph-growth budget.

    The audit found graph growth **unbounded** (there was no replanning to
    bound). A non-terminating expansion is the plan's first named risk for P5,
    so the budget is mandatory rather than optional — a bound that is not
    enforced is a comment.
    """


@dataclass(frozen=True)
class GraphGrowthBudget:
    """A hard ceiling on the number of nodes a run may materialize."""

    max_nodes: int = 64
    max_edges: int = 256

    def check(self, graph: "TaskGraph", adding_nodes: int = 0,
              adding_edges: int = 0) -> None:
        if len(graph.nodes) + adding_nodes > self.max_nodes:
            raise GrowthBudgetExceeded(
                f"node budget exceeded: {len(graph.nodes)} + {adding_nodes} "
                f"> {self.max_nodes}")
        if len(graph.edges) + adding_edges > self.max_edges:
            raise GrowthBudgetExceeded(
                f"edge budget exceeded: {len(graph.edges)} + {adding_edges} "
                f"> {self.max_edges}")


def _reachable(edges: Iterable[tuple[str, str]], start: str) -> set[str]:
    """Every node reachable from `start` by following edges forward."""
    adj: dict[str, list[str]] = {}
    for src, dst in edges:
        adj.setdefault(src, []).append(dst)
    seen: set[str] = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        for nxt in adj.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _find_cycle(nodes: Iterable[str],
                edges: Iterable[tuple[str, str]]) -> list[str] | None:
    """Return a cycle as a node-id path, or None. Iterative, deterministic.

    Deterministic by construction: neighbours are visited in sorted order, so
    the same graph always yields the same cycle (or none). The plan's
    `test_graph_expand_acyclic` requires that expansion cannot create a cycle,
    and a non-deterministic detector would make the failure unreproducible.
    """
    adj: dict[str, list[str]] = {}
    for src, dst in edges:
        adj.setdefault(src, []).append(dst)
    for k in adj:
        adj[k].sort()

    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {n: WHITE for n in nodes}
    for root in sorted(colour):
        if colour[root] != WHITE:
            continue
        path: list[str] = []
        stack: list[tuple[str, int]] = [(root, 0)]
        while stack:
            node, i = stack.pop()
            if i == 0:
                colour[node] = GREY
                path.append(node)
            nxts = adj.get(node, ())
            if i < len(nxts):
                stack.append((node, i + 1))
                nxt = nxts[i]
                if colour.get(nxt, WHITE) == GREY:
                    return path[path.index(nxt):] + [nxt]
                if colour.get(nxt, WHITE) == WHITE:
                    stack.append((nxt, 0))
            else:
                colour[node] = BLACK
                if path and path[-1] == node:
                    path.pop()
    return None


def create_node(graph: "TaskGraph", node: "TaskNode",
                deps: Iterable[str] = (),
                budget: "GraphGrowthBudget | None" = None) -> "TaskGraph":
    """Add one node mid-run (`NodeCreate`, WISP_PROPOSAL_PROTOCOL §3.1).

    Refuses a duplicate id, an unknown dependency, and a budget overrun. An
    unknown dependency is refused rather than silently dropped: a node whose
    declared prerequisite does not exist can never become ready, and silently
    accepting it would produce a node that hangs forever with no diagnostic.
    """
    budget = budget or GraphGrowthBudget()
    budget.check(graph, adding_nodes=1, adding_edges=len(tuple(deps)))
    if graph.node(node.node_id) is not None:
        raise ValueError(f"duplicate node id: {node.node_id}")
    deps = tuple(deps)
    known = {n.node_id for n in graph.nodes}
    unknown = [d for d in deps if d not in known]
    if unknown:
        raise ValueError(f"unknown dependency: {sorted(unknown)}")
    new_edges = tuple((d, node.node_id) for d in deps)
    cycle = _find_cycle(known | {node.node_id}, tuple(graph.edges) + new_edges)
    if cycle:
        raise ValueError(f"edge would create a cycle: {cycle}")
    return materialize(replace(
        graph,
        entrypoint=graph.entrypoint or node.node_id,
        nodes=tuple(graph.nodes) + (replace(node, deps=deps),),
        edges=tuple(graph.edges) + new_edges,
    ))


def expand(graph: "TaskGraph",
           nodes: Iterable["TaskNode"],
           edges: Iterable[tuple[str, str]] = (),
           budget: "GraphGrowthBudget | None" = None) -> "TaskGraph":
    """Grow the graph by several nodes and edges (`GraphExpand`, §3.3).

    **Acyclic by construction.** The candidate topology is checked before it is
    adopted, so an expansion that would introduce a cycle is refused with the
    cycle named. A cyclic graph is not merely wrong — the scheduler would
    deadlock on it, and a deadlock is indistinguishable from slowness.
    """
    budget = budget or GraphGrowthBudget()
    nodes = tuple(nodes)
    edges = tuple(edges)
    budget.check(graph, adding_nodes=len(nodes), adding_edges=len(edges))
    existing = {n.node_id for n in graph.nodes}
    for n in nodes:
        if n.node_id in existing:
            raise ValueError(f"duplicate node id: {n.node_id}")
    candidate_nodes = existing | {n.node_id for n in nodes}
    all_edges = tuple(graph.edges) + edges
    for src, dst in all_edges:
        if src not in candidate_nodes or dst not in candidate_nodes:
            raise ValueError(f"edge references an unknown node: {src} -> {dst}")
    cycle = _find_cycle(candidate_nodes, all_edges)
    if cycle:
        raise ValueError(f"expansion would create a cycle: {cycle}")
    return materialize(replace(
        graph,
        entrypoint=graph.entrypoint or (nodes[0].node_id if nodes else ""),
        nodes=tuple(graph.nodes) + nodes,
        edges=all_edges,
    ))


def invalidate(graph: "TaskGraph", node_id: str, *,
               reason: str = "",
               budget: "GraphGrowthBudget | None" = None) -> "TaskGraph":
    """Invalidate a node and **cascade** to everything that depends on it
    (`GraphInvalidate`, §3.4).

    The cascade is transitive: invalidating `a` invalidates every node
    reachable from it, because a conclusion drawn from an invalidated premise
    is itself invalid. A node already settled `SUCCESS` is demoted to
    `INVALIDATED` — a stale success is exactly what this exists to prevent.

    Returns the new graph; the caller journals one `NodeTransition` per changed
    node, so the cascade is visible in the log rather than implied by it.
    """
    budget = budget or GraphGrowthBudget()
    if graph.node(node_id) is None:
        raise ValueError(f"unknown node: {node_id}")
    doomed = {node_id} | _reachable(graph.edges, node_id)
    out = graph
    for nid in sorted(doomed):
        node = out.node(nid)
        if node is None or node.status in (TaskNodeState.INVALIDATED,
                                           TaskNodeState.SUPERSEDED):
            continue
        out = materialize(out.with_node(replace(
            node, status=TaskNodeState.INVALIDATED,
            detail=reason or node.detail)))
    return out


def supersede(graph: "TaskGraph", old_id: str, new_node: "TaskNode",
              *, reason: str = "",
              budget: "GraphGrowthBudget | None" = None) -> "TaskGraph":
    """Replace a node with a NEW one, retaining the old (P5 item 4).

    *"A task is never mutated into a different task. A replan creates a new
    node and marks the old SUPERSEDED with a pointer."*

    So the old node is **kept**, with `superseded_by` set and its status moved
    to `SUPERSEDED`; the new node takes over its dependencies and inherits the
    dependents that pointed at it. Both directions of the pointer are written,
    which is what makes the history navigable rather than merely present.

    Raises if the new node would introduce a cycle — a replan that reconnects
    a dependency to something downstream of it is a real mistake, not a
    topology to accept.
    """
    budget = budget or GraphGrowthBudget()
    old = graph.node(old_id)
    if old is None:
        raise ValueError(f"unknown node: {old_id}")
    budget.check(graph, adding_nodes=1, adding_edges=len(old.deps) + 1)
    if graph.node(new_node.node_id) is not None:
        raise ValueError(f"duplicate node id: {new_node.node_id}")

    # Rewire BOTH directions. Incoming edges (those pointing AT the old node)
    # move to the replacement, and the old node's own outgoing edges (its
    # dependents) now originate from the replacement. Rewiring only one
    # direction leaves the dependents hanging off a superseded node — they
    # would never see the replanned work, which is the whole point of
    # replanning.
    kept_edges: list[tuple[str, str]] = []
    for src, dst in graph.edges:
        if dst == old_id:
            continue                      # replaced by the inherited edges
        kept_edges.append((new_node.node_id if src == old_id else src, dst))
    inherited = tuple((d, new_node.node_id) for d in old.deps)
    candidate_edges = tuple(kept_edges) + inherited
    candidate_nodes = {n.node_id for n in graph.nodes} | {new_node.node_id}
    cycle = _find_cycle(candidate_nodes, candidate_edges)
    if cycle:
        raise ValueError(f"supersession would create a cycle: {cycle}")

    new = replace(new_node, deps=tuple(old.deps))
    out = replace(graph,
                  nodes=tuple(graph.nodes) + (new,),
                  edges=candidate_edges)
    out = out.with_node(replace(old, status=TaskNodeState.SUPERSEDED,
                                ready=False,
                                superseded_by=new_node.node_id,
                                detail=reason or old.detail))
    return materialize(out)
