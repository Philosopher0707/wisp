"""Session aggregate + event types for event-sourced session management.

Session state is derived by replaying an append-only event log. This gives us:
  - Crash recovery: replay from last snapshot
  - Full audit: every state change is an event
  - Time travel: reconstruct session at any point
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable

logger = logging.getLogger(__name__)


class SessionEventType(StrEnum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    # Migration P2 — the proposal boundary. Both are AUDIT-ONLY: they record
    # what was proposed and what validation did with it, and deliberately do
    # NOT contribute to `messages`. The transcript is built from
    # ASSISTANT_MESSAGE(tool_calls=...) + TOOL_RESULT, and adding a second
    # path into it would duplicate every tool reply on replay.
    PROPOSAL = "proposal"
    OUTCOME = "outcome"
    # Migration P3 (stage 3a) — the recorded completion verdict. AUDIT-ONLY,
    # like PROPOSAL/OUTCOME: it reports what verification concluded without
    # becoming part of the transcript.
    VERDICT = "verdict"
    # Migration P4 — the materialized task graph and its transitions.
    # AUDIT-ONLY. `TASK_GRAPH` records the structure; `NODE_TRANSITION`
    # records every node state change, so the graph is a PROJECTION of the
    # journal and can be rebuilt by replay rather than trusted as a cache.
    TASK_GRAPH = "task_graph"
    NODE_TRANSITION = "node_transition"
    # Migration P6 — the recovery ladder. AUDIT-ONLY. `RECOVERY` records one
    # rung decision; `ESCALATION` records a `HumanIntervention`, which is
    # durable STATE rather than a blocking call, so a run can be parked and
    # resumed when an answer arrives.
    RECOVERY = "recovery"
    ESCALATION = "escalation"
    COMPACTED = "compacted"
    ERROR = "error"
    DONE = "done"


#: Event kinds whose loss is a **correctness** precondition rather than an
#: observability one (ADR-0028).
#:
#: ADR-0004 made every durable write best-effort, on the rule that a turn must
#: never be broken by a failed write. That rule is right for a *record of what
#: happened* and wrong for a *state transition*: if the write that establishes a
#: state is lost, continuing as though it landed is not best-effort — it is a
#: false record.
#:
#: `ESCALATION` is the only such kind, and ADR-0027's classification already
#: carved it out. A `HumanIntervention` is not a description of a parked run; it
#: **is** the parked run's state, and `resumable` reads it to decide whether to
#: resume. The other audit kinds (`PROPOSAL`/`OUTCOME`, `VERDICT`,
#: `TASK_GRAPH`/`NODE_TRANSITION`, `RECOVERY`) are records *about* a turn whose
#: own behaviour is unaffected by their loss, so best-effort stands for them.
STATE_BEARING_EVENT_TYPES: frozenset[SessionEventType] = frozenset({
    SessionEventType.ESCALATION,
})

#: The records a replayed `Session` carries that `UnifiedStore.load_session`
#: (the blob) does **not** (ADR-0028). One authority, because two places need
#: the same list: `reconstruct()` salvages them on the blob path, and the tests
#: assert the blob genuinely lacks each one.
JOURNAL_ONLY_RECORDS: tuple[str, ...] = (
    "proposals", "outcomes", "verdicts", "task_graph", "node_transitions",
    "recovery", "escalation",
)


#: The value shape of each `JOURNAL_ONLY_RECORDS` entry. Here, beside the name
#: list, so an empty template is built from one authority rather than restating
#: the keys and their types at every call site.
JOURNAL_ONLY_SHAPES: dict[str, type] = {
    "proposals": list,
    "outcomes": list,
    "verdicts": list,
    "task_graph": dict,
    "node_transitions": list,
    "recovery": list,
    "escalation": dict,
}


def empty_journal_records() -> dict:
    """The shape `Session.journal_records()` returns, holding nothing.

    Used when a session cannot be replayed at all, so a caller reads `_journal`
    unconditionally instead of branching on whether the journal was available.
    """
    return {name: JOURNAL_ONLY_SHAPES[name]() for name in JOURNAL_ONLY_RECORDS}


def is_state_bearing(events: "Iterable[SessionEvent]") -> bool:
    """True when any event's loss would leave a state transition unrecorded.

    The single authority for the question, so the runtime's write policy and
    the read-side salvage cannot disagree about which records are special.
    """
    return any(e.event_type in STATE_BEARING_EVENT_TYPES for e in events)


@dataclass(frozen=True)
class SessionEvent:
    """A single immutable event in a session's lifecycle."""

    event_type: SessionEventType
    sequence_num: int
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def user_message(cls, seq: int, content: str) -> SessionEvent:
        return cls(SessionEventType.USER_MESSAGE, seq, {"content": content})

    @classmethod
    def assistant_message(cls, seq: int, content: str, tool_calls: list[dict] | None = None) -> SessionEvent:
        return cls(SessionEventType.ASSISTANT_MESSAGE, seq, {"content": content, "tool_calls": tool_calls or []})

    @classmethod
    def tool_call_event(cls, seq: int, name: str, args: dict,
                        action_key: str = "") -> SessionEvent:
        """A tool invocation. Written BEFORE dispatch (migration P1).

        `action_key` is the canonical digest of (tool, arguments) — see
        `wisp.core.action_key`. Paired with the same key on the matching
        `TOOL_RESULT`, it makes "dispatched but never resolved" a queryable
        property of the log rather than an inference.
        """
        payload: dict[str, Any] = {"name": name, "arguments": args}
        if action_key:
            payload["action_key"] = action_key
        return cls(SessionEventType.TOOL_CALL, seq, payload)

    @classmethod
    def tool_result_event(cls, seq: int, name: str, result: str,
                          duration_ms: float = 0.0,
                          tool_call_id: str = "",
                          synthesized: bool = False,
                          action_key: str = "") -> SessionEvent:
        """A tool reply.

        `tool_call_id` is what pairs this reply to its call. It is optional
        only for backward compatibility with pre-migration events: the live
        transcript pairs by id (see `_serialize_tool_exchanges`, GH#6), so a
        replay that cannot see the id cannot reproduce the pairing. New
        writers always supply it.

        `synthesized=True` marks the placeholder reply written for a call
        that was interrupted before it returned. It is journaled so replay
        reproduces the transcript exactly, but the flag keeps the record
        honest: this result was never produced by the tool.

        `action_key` mirrors the call's key so a resolved action is provable.
        """
        payload: dict[str, Any] = {
            "name": name, "result": result, "duration_ms": duration_ms,
        }
        if tool_call_id:
            payload["tool_call_id"] = tool_call_id
        if synthesized:
            payload["synthesized"] = True
        if action_key:
            payload["action_key"] = action_key
        return cls(SessionEventType.TOOL_RESULT, seq, payload)

    @classmethod
    def proposal_event(cls, seq: int, request: dict) -> SessionEvent:
        """A `ToolRequest` proposal, recorded BEFORE dispatch (migration P2).

        Carries the wire form of `contracts/tool.py::ToolRequest`, including
        the P1 idempotency key, so the proposal is joinable to its outcome.
        """
        return cls(SessionEventType.PROPOSAL, seq, {"request": request})

    @classmethod
    def outcome_event(cls, seq: int, result: dict) -> SessionEvent:
        """A `ToolResult` outcome for a proposal — including rejections.

        A rejection is a first-class observable event, not an absence: the
        whole point of the boundary is that validation's disposition is
        recorded even when nothing executed.
        """
        return cls(SessionEventType.OUTCOME, seq, {"result": result})

    @classmethod
    def verdict_event(cls, seq: int, verdict: dict) -> SessionEvent:
        """The recorded completion verdict (migration P3, stage 3a).

        Recorded, not enforced: nothing on the completion path consumes this
        yet. Stage 3a exists to measure what the gate *would* say before
        letting it say it, because P3 changes completion semantics.
        """
        return cls(SessionEventType.VERDICT, seq, {"verdict": verdict})

    @classmethod
    def task_graph_event(cls, seq: int, graph: dict) -> SessionEvent:
        """The materialized task graph's STRUCTURE (migration P4).

        Structure only. Node state arrives as `NODE_TRANSITION` events, so the
        graph is rebuilt by replay and the persisted copy is a cache — never a
        second truth that could drift from the log.
        """
        return cls(SessionEventType.TASK_GRAPH, seq, {"graph": graph})

    @classmethod
    def node_transition_event(cls, seq: int, transition: dict) -> SessionEvent:
        """One node state change — the ONLY write path for node state (P4)."""
        return cls(SessionEventType.NODE_TRANSITION, seq,
                   {"transition": transition})

    @classmethod
    def recovery_event(cls, seq: int, decision: dict) -> SessionEvent:
        """One rung decision (migration P6). Audit-only."""
        return cls(SessionEventType.RECOVERY, seq, {"decision": decision})

    @classmethod
    def escalation_event(cls, seq: int, intervention: dict) -> SessionEvent:
        """A `HumanIntervention` (migration P6). Audit-only.

        Durable state, not a blocking call: the run can be parked here and
        resumed when an answer arrives, which a blocking call cannot express.
        """
        return cls(SessionEventType.ESCALATION, seq,
                   {"intervention": intervention})

    @classmethod
    def compacted(cls, seq: int, before_count: int, after_count: int, summary: str = "") -> SessionEvent:
        return cls(SessionEventType.COMPACTED, seq, {"before_count": before_count, "after_count": after_count, "summary": summary})

    @classmethod
    def error(cls, seq: int, message: str, recoverable: bool = True) -> SessionEvent:
        return cls(SessionEventType.ERROR, seq, {"message": message, "recoverable": recoverable})

    @classmethod
    def done(cls, seq: int, turns: int, reason: str = "natural") -> SessionEvent:
        return cls(SessionEventType.DONE, seq, {"turns": turns, "reason": reason})


@dataclass
class Session:
    """Session aggregate rebuilt from event replay."""

    session_id: str
    model: str = ""
    workspace: str = ""
    messages: list[dict] = field(default_factory=list)
    compaction_history: list[dict] = field(default_factory=list)
    sequence_num: int = 0
    turn_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Fine-grained tool-call audit trail (migration P0). Kept SEPARATE from
    # `messages` on purpose: the provider protocol requires exactly one
    # assistant message carrying all of an iteration's tool_calls blocks,
    # immediately followed by that iteration's replies. Emitting a message
    # per TOOL_CALL would corrupt that ordering. `messages` is rebuilt from
    # ASSISTANT_MESSAGE(tool_calls=...) + TOOL_RESULT; this list is the
    # per-call record used for audit and for later graph reasoning.
    tool_calls: list[dict] = field(default_factory=list)
    # Replay fidelity canary (ADR-0005): counts events this build does not
    # understand. Non-zero means the log carries data replay discarded.
    unknown_events: int = 0
    # Dispatched-but-unresolved actions, keyed by canonical action key
    # (migration P1). Insertion-ordered; see `unresolved_actions()`.
    _unresolved_actions: dict[str, dict] = field(default_factory=dict)
    # Migration M4: every sequence number applied, so a GAP is detectable.
    # A FIELD, not something `replay` sets: a directly-constructed `Session`
    # must answer `gap_detected` too, and the first version raised
    # AttributeError for exactly that case.
    _seen_sequences: set[int] = field(default_factory=set)
    # The proposal boundary (migration P2). Audit-only records of what was
    # proposed and how validation disposed of it. Never contributes to
    # `messages`; see the PROPOSAL/OUTCOME cases in `apply`.
    proposals: list[dict] = field(default_factory=list)
    outcomes: list[dict] = field(default_factory=list)
    # Recorded completion verdicts (migration P3, stage 3a). Audit-only.
    verdicts: list[dict] = field(default_factory=list)
    # The materialized task graph (migration P4). `task_graph` is the
    # structure; `node_transitions` is the ordered state log it replays from.
    # Audit-only — never contributes to `messages`.
    task_graph: dict = field(default_factory=dict)
    node_transitions: list[dict] = field(default_factory=list)
    # The recovery ladder (migration P6). Audit-only.
    recovery: list[dict] = field(default_factory=list)
    escalation: dict = field(default_factory=dict)

    def journal_records(self) -> dict:
        """The records the journal carries and the blob does not (ADR-0028).

        One accessor rather than seven, because both reconstruction paths need
        the same set and a caller should not have to know which of them a given
        source happens to supply. The keys are `JOURNAL_ONLY_RECORDS`.

        Values are copies: a caller that mutates the result must not be able to
        reach back into the session's state.
        """
        return {
            "proposals": list(self.proposals),
            "outcomes": list(self.outcomes),
            "verdicts": list(self.verdicts),
            "task_graph": dict(self.task_graph),
            "node_transitions": list(self.node_transitions),
            "recovery": list(self.recovery),
            "escalation": dict(self.escalation),
        }

    @property
    def has_escalation(self) -> bool:
        """True when a live `HumanIntervention` was replayed (ADR-0028).

        The escalation's presence is the question a resume asks, so it gets a
        name instead of callers reaching into the dict.
        """
        return bool(self.escalation)

    def rebuild_task_graph(self) -> dict:
        """Rebuild the graph by replaying its transitions (migration P4).

        Demonstrates the projection property: the persisted graph is a cache,
        and this reconstructs the same graph from the log alone.
        """
        from wisp.core.task_graph import (
            NodeTransition, replay_transitions, TaskGraph,
        )

        if not self.task_graph:
            return {}
        graph = TaskGraph.from_dict(self.task_graph)
        transitions = [NodeTransition.from_dict(t)
                       for t in self.node_transitions]
        return replay_transitions(graph, transitions).to_dict()

    @property
    def gap_detected(self) -> bool:
        """True when the journal is missing events (migration M4).

        The journal's invariant is a **contiguous** sequence: `_journal_turn_events`
        stamps events in order with no holes. A hole therefore means a durable
        write was lost — which ADR-0004 explicitly permits, and which nothing
        used to notice.

        Contiguity is measured from the minimum present, not from zero, so
        applying a single event (as tests and `append_events` callers do) is not
        a gap. `[1, 2, 4]` is; `[1, 2, 3]` and `[0, 1, 2]` are not.
        """
        if not self._seen_sequences:
            return False
        seqs = sorted(self._seen_sequences)
        return seqs != list(range(seqs[0], seqs[0] + len(seqs)))

    def unresolved_actions(self) -> list[dict]:
        """Actions dispatched but never resolved (migration P1).

        A key present on a `TOOL_CALL` and absent from every `TOOL_RESULT` is
        an action whose outcome is unknown: the process died between the
        journaled intent and the journaled result, so the side effect may or
        may not have landed. Recovery must surface these rather than silently
        repeating them — repeating is how a crash turns one edit into two.

        Results flagged `synthesized` do NOT count as resolution: a
        placeholder records that we never learned the outcome, which is
        exactly the state this method reports.

        Insertion-ordered, so the report follows dispatch order.
        """
        return list(self._unresolved_actions.values())

    def apply(self, event: SessionEvent) -> None:
        """Apply a single event to mutate session state."""
        self._seen_sequences.add(int(event.sequence_num))
        self.sequence_num = max(self.sequence_num, event.sequence_num)
        self.updated_at = event.timestamp

        match event.event_type:
            case SessionEventType.USER_MESSAGE:
                self.messages.append({"role": "user", "content": event.payload["content"]})
                self.turn_count += 1

            case SessionEventType.ASSISTANT_MESSAGE:
                msg: dict[str, Any] = {"role": "assistant", "content": event.payload["content"]}
                if event.payload.get("tool_calls"):
                    msg["tool_calls"] = event.payload["tool_calls"]
                self.messages.append(msg)

            case SessionEventType.TOOL_CALL:
                # Audit-only: records the invocation without touching
                # `messages` (see the `tool_calls` field docstring above).
                self.tool_calls.append({
                    "name": event.payload.get("name", ""),
                    "arguments": event.payload.get("arguments", {}),
                    "sequence_num": event.sequence_num,
                    "timestamp": event.timestamp,
                })
                # Durable intent (migration P1): remember the dispatch so a
                # log that ends without its result can report the action as
                # unresolved rather than letting recovery repeat it blindly.
                _akey = event.payload.get("action_key")
                if _akey:
                    self._unresolved_actions[_akey] = {
                        "action_key": _akey,
                        "name": event.payload.get("name", ""),
                        "arguments": event.payload.get("arguments", {}),
                        "sequence_num": event.sequence_num,
                    }

            case SessionEventType.TOOL_RESULT:
                # The message shape is the LIVE one, exactly (migration M9).
                #
                # `_exchange_parts` builds a reply as
                # `{role, tool_call_id, content}` and its comment states the
                # invariant: "replay replaces the live transcript, so the log
                # has to reproduce `messages` exactly." This case used to add a
                # `name` key the live path never sets, so it did not — and the
                # blob (saved from `messages`) disagreed with the journal on the
                # same session. `reconstruct()` would then return different
                # messages depending on which source it chose.
                #
                # `name` is not carried here because it is redundant: it is
                # already in the assistant message's `tool_calls` block, in
                # `Session.tool_calls`, and resolvable from `tool_call_id`.
                # Adding it to the live path instead would put a key into
                # `messages` that every provider adapter strips again, so it
                # would be dead data on the hot path.
                #
                # Pinned by `test_the_journal_reproduces_the_transcript_exactly`
                # and `test_a_tool_reply_carries_only_the_protocol_keys`.
                reply: dict[str, Any] = {
                    "role": "tool",
                    "content": event.payload["result"],
                }
                # Pairing id, when the writer supplied one. Without it the
                # replayed transcript cannot be matched to its call by id,
                # which is the only pairing the provider protocol accepts.
                if event.payload.get("tool_call_id"):
                    reply["tool_call_id"] = event.payload["tool_call_id"]
                self.messages.append(reply)
                # Resolution (migration P1): only a REAL result resolves the
                # action. A `synthesized` placeholder means we never learned
                # the outcome, so the action stays unresolved on purpose.
                _akey = event.payload.get("action_key")
                if _akey and not event.payload.get("synthesized"):
                    self._unresolved_actions.pop(_akey, None)

            case SessionEventType.PROPOSAL:
                # Audit-only (migration P2). Deliberately does NOT append to
                # `messages`: the transcript is rebuilt from
                # ASSISTANT_MESSAGE + TOOL_RESULT, and a second path in would
                # duplicate every tool reply on replay.
                self.proposals.append({
                    "sequence_num": event.sequence_num,
                    "timestamp": event.timestamp,
                    **dict(event.payload.get("request") or {}),
                })

            case SessionEventType.OUTCOME:
                # Audit-only, same reason. This is where a REJECTION becomes
                # visible: a denial produces an outcome with no matching
                # execution, and previously left no first-class record.
                self.outcomes.append({
                    "sequence_num": event.sequence_num,
                    "timestamp": event.timestamp,
                    **dict(event.payload.get("result") or {}),
                })

            case SessionEventType.VERDICT:
                # Audit-only (migration P3 3a). The completion verdict is
                # recorded so the 3b gate can be justified by measurement.
                self.verdicts.append({
                    "sequence_num": event.sequence_num,
                    "timestamp": event.timestamp,
                    **dict(event.payload.get("verdict") or {}),
                })

            case SessionEventType.TASK_GRAPH:
                # Audit-only (migration P4). Structure, not state.
                self.task_graph = dict(event.payload.get("graph") or {})

            case SessionEventType.NODE_TRANSITION:
                # Audit-only. The ordered transition log the graph replays
                # from; `task_graph` above is a cache of the same truth.
                self.node_transitions.append({
                    "sequence_num": event.sequence_num,
                    "timestamp": event.timestamp,
                    **dict(event.payload.get("transition") or {}),
                })

            case SessionEventType.RECOVERY:
                # Audit-only (migration P6).
                self.recovery.append({
                    "sequence_num": event.sequence_num,
                    "timestamp": event.timestamp,
                    **dict(event.payload.get("decision") or {}),
                })

            case SessionEventType.ESCALATION:
                # Audit-only. Overwrites: a session has one live escalation,
                # and the full history travels inside it.
                self.escalation = dict(event.payload.get("intervention") or {})

            case SessionEventType.COMPACTED:                self.compaction_history.append({
                    "before_count": event.payload["before_count"],
                    "after_count": event.payload["after_count"],
                    "summary": event.payload.get("summary", ""),
                    "timestamp": event.timestamp,
                })

            case SessionEventType.ERROR:
                self.messages.append({
                    "role": "system",
                    "content": f"[Error] {event.payload['message']}",
                })

            case SessionEventType.DONE:
                pass  # terminal event, no state change

            case _:
                # Fail loud, not silent (ADR-0005). A `match` with no
                # wildcard turns an unrecognized event into a no-op, which
                # is exactly how an append-only log loses data without
                # anyone noticing. The count is the observable signal.
                self.unknown_events += 1
                logger.warning(
                    "Session %s: unknown event type %r at seq %s was not "
                    "applied — replay is incomplete (unknown_events=%d)",
                    self.session_id, event.event_type, event.sequence_num,
                    self.unknown_events,
                )

    def replay(self, events: list[SessionEvent]) -> None:
        """Replay a sequence of events from scratch."""
        self.messages.clear()
        self.compaction_history.clear()
        self.tool_calls.clear()
        self._unresolved_actions.clear()
        self.proposals.clear()
        self.outcomes.clear()
        self.verdicts.clear()
        self.task_graph = {}
        self.node_transitions.clear()
        self.recovery.clear()
        self.escalation = {}
        self.sequence_num = 0
        self.turn_count = 0
        self.unknown_events = 0
        # Migration M4: clear the sequence set so a second replay does not
        # inherit the first's numbers. ADR-0004 permits a durable write to fail
        # silently (best-effort), so the journal can legitimately be missing an
        # event — and before M4 nothing noticed: replay applies what exists, and
        # a lost TOOL_RESULT yields an assistant tool_calls block with no reply,
        # i.e. a provider-invalid transcript that reports no error.
        self._seen_sequences.clear()
        for ev in sorted(events, key=lambda e: e.sequence_num):
            self.apply(ev)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.session_id,
            "model": self.model,
            "workspace": self.workspace,
            "messages": self.messages,
            "compaction_history": self.compaction_history,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
