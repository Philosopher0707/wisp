"""AgentRuntime — stateful session lifecycle manager.

Replaces: scattered session management in WispAgentCore.

Design:
  - Owns sessions, compaction, background runs
  - Delegates turn loop to injected stateless core
  - Uses injected store, security, extensions, telemetry
  - Caches core instance for performance (warm-start)
  - Thread-safe and async-safe for concurrent turns
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, ClassVar

from wisp.agent_memory import SessionSummary
from wisp.approval_state import ApprovalSessionState, SessionPolicy
from wisp.core.compaction_record import attach_record
from wisp.core.events import normalize_event, nudge_message, steering_message

logger = logging.getLogger(__name__)
logger = logging.getLogger(__name__)


def _group_exchanges(
    tool_sequence: list[tuple[str, dict[str, Any]]],
    closed_only: bool = False,
) -> list[dict[str, list[Any]]]:
    """Group arrival-ordered tool events into provider-boundary exchanges.

    THE grouping rule (GH#6). Event arrival order encodes the boundaries::

        callA callB  replyA replyB  callC  replyC ...

    An exchange closes once its replies catch up to its calls; the
    ``max(..., 1)`` also closes reply-only groups left by gate-refused
    calls, which stream no call event. Emission order is deterministic:
    calls in arrival order, then unmatched replies in arrival order.

    ``closed_only=True`` omits a trailing exchange that has not closed yet.
    Migration P1 uses that to journal each exchange the moment it completes,
    so a crash mid-turn leaves the completed exchanges on disk. Both callers
    — the turn-end serializer and the incremental journal — go through this
    one function on purpose: the rule is subtle enough (see the GH#6 note
    above) that a second copy would drift, and a drift here corrupts the
    provider transcript rather than merely the log.
    """
    exchanges: list[dict[str, list[Any]]] = []
    cur: dict[str, list[Any]] = {"calls": [], "replies": []}

    def _close() -> None:
        if cur["calls"] or cur["replies"]:
            # Store copies — resetting `cur` below must not reach back into
            # already-recorded exchanges.
            exchanges.append({"calls": list(cur["calls"]),
                              "replies": list(cur["replies"])})
        cur["calls"], cur["replies"] = [], []

    for kind, ev in tool_sequence:
        if kind == "call":
            cur["calls"].append(ev)
        else:
            cur["replies"].append(ev)
            if len(cur["replies"]) >= max(len(cur["calls"]), 1):
                _close()
    if not closed_only:
        _close()
    return exchanges


def _exchange_parts(
    exchange: dict[str, list[Any]],
    field_reader: Callable[[dict[str, Any], str], Any],
    journal: bool = True,
    proposal: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Any]]:
    """Compute one exchange's (tool_calls blocks, reply messages, events).

    The single authority for pairing. Pairing is BY tool_call_id inside a
    boundary (GH#6): positional pairing corrupts mixed batches, because a
    gate-refused call streams NO call event while its denial reply still
    arrives — sequence call_B, reply_A, reply_B paired positionally as
    (call_B<->reply_A), attaching A's refusal content to B's call id in the
    persisted transcript. Identity comes from the reply's tool_call_id
    (falling back to its own id), matched against the call event's id;
    id-less traffic matches first-unmatched in arrival order, which degrades
    exactly to the old positional behavior. Missing replies (turn
    interrupted mid-execute) get an honest placeholder; reply-only groups
    synthesize their block.

    Returns events in the same order as the messages the caller will append,
    with `sequence_num=0` placeholders for the caller to stamp.
    """
    calls, replies = exchange["calls"], exchange["replies"]
    if not calls and not replies:
        return [], [], []

    def _reply_key(rp: dict[str, Any]) -> Any:
        return rp.get("tool_call_id") or field_reader(rp, "id")

    remaining = list(replies)
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []
    for c in calls:
        key = field_reader(c, "id")
        match: dict[str, Any] | None = None
        for i, rp in enumerate(remaining):
            if _reply_key(rp) == key:
                match = remaining.pop(i)
                break
        pairs.append((c, match))
    for rp in remaining:
        pairs.append((None, rp))

    blocks: list[dict[str, Any]] = []
    reply_msgs: list[dict[str, Any]] = []
    # Parallel event list for this exchange, appended in the same order
    # as the messages so replay reproduces the transcript.
    ex_events: list[Any] = []
    ex_call_events: list[Any] = []
    ex_proposal_events: list[Any] = []
    ex_outcome_events: list[Any] = []
    for c, rp in pairs:
        name = ""
        raw_args: Any = {}
        if c is not None:
            name = field_reader(c, "name") or ""
            args = field_reader(c, "arguments") or {}
            raw_args = args
            if not isinstance(args, str):
                args = json.dumps(args)
        else:
            name = field_reader(rp, "name") if rp is not None else ""
            args = "{}"

        reply_id = None
        if rp is not None:
            reply_id = rp.get("tool_call_id") or field_reader(rp, "id")
        call_id = None
        if c is not None:
            call_id = field_reader(c, "id")
        shared_id = call_id or reply_id or f"call_{uuid.uuid4().hex[:8]}"

        blocks.append({
            "id": shared_id,
            "type": "function",
            "function": {"name": name, "arguments": args},
        })

        if rp is not None:
            result = field_reader(rp, "result")
            if result is None:
                result = rp.get("data", "")
            content = (json.dumps(result) if isinstance(result, dict)
                       else str(result))
        else:
            content = "[no result recorded before turn ended]"
        reply_msgs.append({
            "role": "tool",
            "tool_call_id": shared_id,
            "content": content,
        })

        # Journal the invocation when a real call event streamed, and the
        # reply whenever a reply MESSAGE was appended — including the
        # placeholder for an interrupted call.
        #
        # Why the placeholder must be journaled: replay replaces the live
        # transcript (`runtime.py`, crash-recovery branch), so the log has
        # to reproduce `messages` exactly. A call journaled without its
        # reply replays into an assistant `tool_calls` block with no
        # following tool message — which strict providers reject. The
        # event is flagged `synthesized` so a reader can still tell a
        # placeholder from a real result.
        #
        # A synthesized block (c is None: a gate-refused call that never
        # streamed an event) still records NO invocation — there was
        # none — but its denial reply is real and is recorded.
        # Canonical idempotency key for this invocation (migration P1). One
        # computation, stamped on both the call and its result so "resolved"
        # is provable from the log rather than inferred.
        _akey = ""
        if journal and c is not None:
            from wisp.core.action_key import action_key
            _akey = action_key(
                name, raw_args if isinstance(raw_args, dict)
                else {"raw": str(raw_args)})

        if journal and c is not None:
            from wisp.core.session import SessionEvent
            ex_call_events.append(SessionEvent.tool_call_event(
                0, name,
                raw_args if isinstance(raw_args, dict)
                else {"raw": str(raw_args)},
                action_key=_akey,
            ))
        # Migration P2 — the proposal record. Emitted for every REAL call,
        # before dispatch in journal order (the call event precedes it in the
        # stream; the record is derived at exchange close). A reply-only group
        # has no proposal because nothing was proposed — a gate refused the
        # call before it became one, and fabricating a proposal for it would
        # invent an intent the turn never expressed.
        if proposal and c is not None:
            from wisp.core.proposal import build_proposal
            from wisp.core.session import SessionEvent
            ex_proposal_events.append(SessionEvent.proposal_event(
                0,
                build_proposal(shared_id, name,
                               raw_args if isinstance(raw_args, dict)
                               else {"raw": str(raw_args)},
                               action_key=_akey).to_dict(),
            ))
        if journal:
            from wisp.core.session import SessionEvent
            ex_events.append(SessionEvent.tool_result_event(
                0, name, content, tool_call_id=shared_id,
                synthesized=rp is None,
                # A synthesized placeholder still carries the key: the action
                # existed, we simply never learned its outcome — which is
                # exactly what `Session.unresolved_actions()` reports. A
                # reply-only group (c is None) has no invocation to key.
                action_key=_akey,
            ))
        # Migration P2 — the outcome record, for EVERY proposal including
        # rejections. A refusal produces an outcome with no execution, and
        # that is the record's whole point: validation's disposition is
        # observable even when nothing ran.
        if proposal:
            from wisp.core.proposal import build_outcome
            from wisp.core.session import SessionEvent
            ex_outcome_events.append(SessionEvent.outcome_event(
                0,
                build_outcome(shared_id, name, content,
                              synthesized=rp is None).to_dict(),
            ))

    events: list[Any] = []
    if (journal or proposal) and blocks:
        from wisp.core.session import SessionEvent
        # The assistant_message EVENT is a transcript record, so it is gated
        # on `journal` — not on `(journal or proposal)`. Emitting it whenever
        # a proposal existed would put a transcript event into the log while
        # `session_event_fidelity` is off, and (worse) would leave an
        # assistant `tool_calls` block in the log with no reply beside it
        # whenever `journal` is off, which replay turns into a
        # provider-invalid transcript.
        if journal:
            events.append(SessionEvent.assistant_message(0, "", blocks))
        events.extend(ex_call_events)
        events.extend(ex_proposal_events)
        events.extend(ex_events)
        events.extend(ex_outcome_events)
    return blocks, reply_msgs, events


def _serialize_tool_exchanges(
    session: dict[str, Any],
    exchanges: list[dict[str, list[Any]]],
    field_reader: Callable[[dict[str, Any], str], Any],
    journal: bool = True,
    proposal: bool = True,
) -> tuple[list[Any], list[list[str]]]:
    """Append protocol-consistent assistant/tool messages for one turn.

    Per provider boundary: ONE assistant message holding every tool_calls
    block, IMMEDIATELY followed by that boundary's role:"tool" replies.

    Pairing and event derivation live in `_exchange_parts` — one authority
    for both, so the transcript and the journal cannot disagree.

    With `journal=False` no event objects are built; the message-writing
    behavior is identical either way, so the rollback flag cannot change
    the transcript.

    Returns `(events, exchange_call_ids)`. The second value is migration M11's
    work-unit identity: the protocol ids each exchange's blocks carry, **read
    back from the blocks `_exchange_parts` just built** rather than recomputed.
    Recomputing them would be wrong, not merely redundant — an exchange whose
    events carry no id gets a fresh `uuid4` (`_exchange_parts`), so a second
    pass would mint a different id and the graph's reference would name a work
    unit the transcript never recorded.
    """
    events: list[Any] = []
    exchange_call_ids: list[list[str]] = []
    for ex in exchanges:
        blocks, reply_msgs, ex_events = _exchange_parts(
            ex, field_reader, journal, proposal)
        if not blocks and not reply_msgs:
            continue
        events.extend(ex_events)
        exchange_call_ids.append([str(b["id"]) for b in blocks])
        session["messages"].append({
            "role": "assistant",
            "content": "",
            "tool_calls": blocks,
        })
        session["messages"].extend(reply_msgs)

    return events, exchange_call_ids


def _closed_exchange_events(
    tool_sequence: list[tuple[str, dict[str, Any]]],
    field_reader: Callable[[dict[str, Any], str], Any],
    journal: bool = True,
    proposal: bool = True,
) -> list[Any]:
    """Events for every exchange that has CLOSED so far.

    Migration P1's incremental journal. Uses `_group_exchanges(closed_only=True)`
    — the same rule the turn-end serializer uses — so the exchange boundaries
    the journal sees mid-turn are exactly the ones the transcript will see at
    turn end. Returns a list that only ever grows, which is what lets the
    caller slice off the already-written prefix.

    `journal` and `proposal` are threaded through rather than hardcoded: the
    two concerns have independent rollback flags, and hardcoding either one
    made the incremental writer ignore its flag — writing transcript events
    with `session_event_fidelity` off.
    """
    events: list[Any] = []
    for ex in _group_exchanges(tool_sequence, closed_only=True):
        _blocks, _reply_msgs, ex_events = _exchange_parts(
            ex, field_reader, journal=journal, proposal=proposal)
        events.extend(ex_events)
    return events





def _ev_get(event: dict[str, Any], key: str, default: Any = None) -> Any:
    """Read an event field across flat and canonical ({type, data}) shapes.

    Flat (engine wire shape) wins on conflict; mirrors the `_field` reader
    used at persist time so intake and persistence never disagree.
    """
    if key in event:
        return event[key]
    data = event.get("data")
    if isinstance(data, dict) and key in data:
        return data[key]
    return default


def _evict_session_state_maps(maps: list[dict[str, Any]], cap: int,
                                access: dict[str, float],
                                skip: set[str] | None = None) -> None:
    """Evict coldest session ids across auxiliary maps, bounded at cap.

    Module-level so it works on partial test doubles (stubs binding single
    methods, bare __new__ instances): only the maps actually present are
    considered. Fires at == cap since the caller inserts right after,
    guaranteeing post-insert <= cap. Ids in `skip` are never evicted
    (live turns holding their session lock).
    """
    keys: set[str] = set()
    for table in maps:
        keys.update(table)
    if skip:
        keys -= set(skip)
    if len(keys) < cap:
        return
    overflow = len(keys) - cap + 1
    to_evict = max(int(cap * 0.2) or 1, overflow)
    for key in sorted(keys, key=lambda sid: access.get(sid, 0.0))[:to_evict]:
        for table in maps:
            table.pop(key, None)


def _maybe_evict_session_state(runtime: Any) -> None:
    """Best-effort bound for per-session auxiliary maps; never raises."""
    try:
        maps: list[dict[str, Any]] = []
        for name in ("_steering_inbox", "_approval_states",
                     "_touched_files", "_turn_counts"):
            table = getattr(runtime, name, None)
            if isinstance(table, dict):
                maps.append(table)
        # Never evict a session whose turn is live: same held-lock
        # exemption as _evict_old_session_locks — dropping mid-turn
        # steering/approval/touched-file state would corrupt the live turn.
        live: set[str] = set()
        try:
            locks = getattr(runtime, "_session_locks", None)
            if isinstance(locks, dict):
                for sid, lock in locks.items():
                    try:
                        if lock is not None and lock.locked():
                            live.add(sid)
                    except Exception:
                        continue
        except Exception:
            live = set()
        _evict_session_state_maps(
            maps,
            cap=getattr(runtime, "_max_session_state", 1000),
            access=getattr(runtime, "_session_access", {}),
            skip=live,
        )
    except Exception:
        pass


def _stringify_tool_call_arguments(messages: list[dict[str, Any]]) -> None:
    """Heal sessions persisted before tool-call arguments were stored as JSON strings."""
    import json

    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict) and not isinstance(function.get("arguments"), str):
                function["arguments"] = json.dumps(function.get("arguments", {}))


@dataclass
class AgentRuntime:
    """Stateful runtime that owns sessions and delegates turns.

    Thread-safe: _core_cache is protected by _core_lock.
    Async-safe: per-session locks prevent concurrent turns on same session.
    """

    store: Any
    security: Any
    extensions: Any
    telemetry: Any
    core_factory: Callable[[], Any]
    session_repo: Any = None
    compactor: Any = None
    orchestrator: Any = None  # SubagentOrchestrator — set by CompositionRoot

    config: Any = None

    # Durable span sink (migration P0). None keeps the pre-migration
    # behavior exactly: no spans are written. Constructed by the composition
    # root from the same UnifiedStore (ADR-0006) and gated by `turn_spans`.
    trace_store: Any = None

    # Cores scoped per (session_id, config fingerprint) — one shared core
    # meant one shared CircuitBreaker, so a session's failing model opened
    # the circuit for EVERY other session for the whole recovery window.
    # Cores are cheap value objects; the (None, fp) slot serves
    # session-less introspection like get_core_provider().
    MAX_SESSION_CORES: ClassVar[int] = 32
    _session_cores: dict[Any, Any] = field(default_factory=dict, repr=False)
    _core_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # Per-session locks — prevents concurrent turns on same session
    _session_locks: dict[str, asyncio.Lock] = field(default_factory=dict, repr=False)
    _session_access: dict[str, float] = field(default_factory=dict, repr=False)
    _max_session_locks: int = field(default=1000, repr=False)

    # Mid-turn steering inbox (M3): lines typed during a turn land here
    # and are drained by the engine at the next tool boundary.
    _steering_inbox: dict[str, list[str]] = field(default_factory=dict, repr=False)

    # Per-session approval memory (y/Y/a/n/N/d/c): lives here because the
    # session does — CLI/TUI keep their own only for same-process turns.
    _approval_states: dict[str, ApprovalSessionState] = field(
        default_factory=dict, repr=False
    )

    # Files each session has touched, folded into its memory summary.
    _touched_files: dict[str, set[str]] = field(default_factory=dict, repr=False)
    _turn_counts: dict[str, int] = field(default_factory=dict, repr=False)

    # Cap for the per-session auxiliary maps above (+ inbox/approvals).
    # Mirrors _max_session_locks: all four are best-effort caches, so
    # dropping a cold session's entries is loss-safe (steering re-queues,
    # approval memory rebuilds, touched files/turn counts re-accumulate
    # and merge with persisted agent_memory). Phase 3.2 (D10).
    _max_session_state: int = field(default=1000, repr=False)

    # Configurable thresholds (can be overridden)
    max_messages: int = field(default=50, repr=False)
    max_context_tokens: int = field(default=128000, repr=False)

    async def get_or_create_session(
        self,
        session_id: str,
        model: str,
        workspace: str,
    ) -> dict[str, Any]:
        """Load existing session or create new one.

        Validates inputs to prevent crashes deep in the stack.
        """
        # Input validation
        if not session_id or not isinstance(session_id, str):
            raise ValueError(f"Invalid session_id: {session_id!r}")
        if not isinstance(model, str):
            raise ValueError(f"Invalid model: {model!r}")
        # Empty model = unset — legal; provider_catalog resolves it to a
        # served model when the core builds. Only non-strings are garbage.
        if not workspace or not isinstance(workspace, str):
            raise ValueError(f"Invalid workspace: {workspace!r}")

        # Store boundary is unannotated; validate shape before trusting it.
        loaded: Any = self.store.load_session(session_id)
        if isinstance(loaded, dict) and "messages" in loaded:
            session: dict[str, Any] = loaded
            _stringify_tool_call_arguments(session["messages"])
            # Latest selection wins: a resumed session must serve with the
            # CURRENTLY chosen model, not the one baked in when it was
            # created — otherwise /model switches silently never reach old
            # sessions and users see the stale default "come online".
            if model and session.get("model") != model:
                session["model"] = model
                self.store.save_session(session)
            return session

        now = datetime.now(timezone.utc).isoformat()
        session = {
            "id": session_id,
            "model": model,
            "workspace": workspace,
            "messages": [],
            "compaction_history": [],
            "created_at": now,
            "updated_at": now,
        }
        self.store.save_session(session)
        return session

    async def run_turn(self, session: dict[str, Any], prompt: str, approval_handler: Any = None) -> AsyncIterator[dict[str, Any]]:
        """Run one turn, yielding events.

        Guarantees session consistency even if the turn aborts:
        - User message is always added
        - Assistant + tool messages are added on success
        - Session is saved after every turn
        - Compaction runs automatically before turn if needed
        - Concurrent turns on same session are serialized
        """
        start = time.time()

        # Input validation
        if not prompt or not isinstance(prompt, str):
            raise ValueError(f"Invalid prompt: {prompt!r}")

        sid = session.get("id", "unknown")

        # Get or create per-session lock (LRU-tracked)
        if sid not in self._session_locks:
            self._evict_old_session_locks()
            self._session_locks[sid] = asyncio.Lock()
        self._session_access[sid] = time.monotonic()
        session_lock = self._session_locks[sid]

        # Start trace context for this turn.
        #
        # Migration P0: the trace id was previously generated and discarded,
        # and `new_span()` — which exists for exactly this purpose — had no
        # caller anywhere in the tree, so the trace store was always empty.
        # Capturing both here gives the span writer below its lineage.
        from wisp.infra.tracing import new_span, new_trace
        trace_id = new_trace(session_id=sid)
        turn_span_id = new_span()
        turn_span_started = time.time()

        # Events are NOT accumulated into a turn-wide list — transports
        # consume them live and the grouped serializer in the finally block
        # tracks only the tool-call subset it needs. Holding every streamed
        # event used to cost ~1.5MB + 30ms of dead retention per long
        # streaming turn.
        seq_num = 0
        if self.session_repo is not None:
            try:
                seq_num = self.session_repo.get_last_sequence(sid)
            except Exception:
                pass

        async with session_lock:
            # Crash recovery: the last turn did not reach DONE, so the saved
            # transcript may lag the journal. Rebuild from the journal only when
            # it can be trusted; otherwise keep the saved transcript, and say which.
            if self.session_repo is not None:
                try:
                    if not self.session_repo.was_last_turn_complete(sid):
                        self._recover_unfinished_turn(sid, session)
                except Exception:
                    logger.debug("Session %s: turn recovery skipped", sid, exc_info=True)

            # Auto-compact before turn to prevent context overflow
            await self.maybe_compact(session)

            # Live byte budget: condense historical tool payloads in the
            # in-memory session so provider dispatch cost stays flat between
            # compactions. No-op when under budget (single O(n) byte walk);
            # persisted history keeps full fidelity. See context_manager.
            try:
                from wisp.core.context_manager import prune_live_session

                prune_live_session(session)
            except Exception:
                logger.debug("live context prune failed — continuing unpruned", exc_info=True)

            # ── Autonomous mode: synthesize approval handler so safe coding
            # turns run fully autonomously (Cursor/Aider-like) while dangerous
            # commands still hit the hard block. (Legacy GraphState hydration
            # removed in 12.5A: nothing read session["graph_state"].)
            if self.config is not None and bool(getattr(self.config, "autonomous", False)):
                if approval_handler is None:
                    approval_handler = self._autonomous_approval_handler()

            # Where this turn's own messages begin: the spend estimate charges this turn's provider calls, while the
            # earlier history still counts as input to them.
            turn_start_index = len(session["messages"])

            # Add user message
            session["messages"].append({"role": "user", "content": prompt})
            seq_num += 1
            if self.session_repo is not None:
                try:
                    from wisp.core.session import SessionEvent
                    self.session_repo.append_event(sid, SessionEvent.user_message(seq_num, prompt))
                except Exception:
                    pass

            # Session-scoped core (own circuit breaker), warm per session
            core = self._get_core(sid)

            assistant_content: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []
            # Arrival-ordered view of the same events: needed to group
            # calls into provider-boundary exchanges when persisting
            # (see finally block — one assistant message with ALL of an
            # iteration's tool_calls blocks, then exactly ITS replies).
            tool_sequence: list[tuple[str, dict[str, Any]]] = []
            # (tool name, arrival timestamp) for calls that actually streamed
            # a call event — the span input for this turn (migration P0).
            tool_call_spans: list[tuple[str, float]] = []
            # Migration P1: how many journal events the incremental writer has
            # already committed. The turn-end writer slices this prefix off so
            # no event is written twice.
            journaled_event_count = 0
            # The three durable-record flags, read ONCE here rather than at
            # each use site: the stream loop and the `finally` block must
            # agree, and a flag read in two places is a flag that can
            # disagree with itself.
            journal_fidelity = bool(getattr(
                getattr(self, "config", None), "session_event_fidelity", True))
            journal_incremental = bool(
                getattr(getattr(self, "config", None), "turn_journal", True)
                and journal_fidelity
                and self.session_repo is not None)
            # Migration P2: record a proposal + outcome for every tool call.
            # Gated independently so the boundary can be rolled back without
            # losing the P0/P1 journal.
            proposal_boundary = bool(
                getattr(getattr(self, "config", None), "proposal_boundary", True)
                and self.session_repo is not None)
            # Migration P3 stage 3a: record the completion verdict. Defaults
            # OFF — unlike the P0-P2 flags, this one adds a record to the log
            # of every existing caller, so it is opt-in until 3b measures it.
            verdict_recording = bool(
                getattr(getattr(self, "config", None), "record_verdict", False)
                and self.session_repo is not None)
            # Migration P4: materialize the turn as a task graph. Defaults OFF
            # for the same reason as `record_verdict` — it adds records to the
            # log of every existing caller — and because the message list
            # remains authoritative, which is the plan's rollback contract.
            task_graph_recording = bool(
                getattr(getattr(self, "config", None), "task_graph", False)
                and self.session_repo is not None)
            # Migration POST-M13 (ADR-0035): consult P6's recovery ladder at the
            # turn boundary and journal the rung it chooses. Defaults OFF — this
            # is the name ADR-0026's reversal condition reserved, and the
            # production path must be unchanged until it is explicitly enabled.
            # It gates the RECOVERY track only: completion semantics are
            # deliberately not coupled to it (ADR-0035's two-authority split).
            recovery_ladder_enabled = bool(
                getattr(getattr(self, "config", None), "recovery_ladder", False)
                and self.session_repo is not None)
            # Migration POST-M13 (ADR-0035), completion side: derive and record
            # the goal state. Defaults OFF for the same reason as
            # `record_verdict`/`task_graph` — it adds a record to the log of
            # every existing caller — which is ADR-0035 clause 9's
            # "recorded first, enforced later" staging. Nothing acts on the
            # state, so turn completion is unchanged either way.
            goal_state_recording = bool(
                getattr(getattr(self, "config", None), "goal_state", False)
                and self.session_repo is not None)
            # ADR-0053: let the turn's required-criteria set carry the objective's
            # DECLARED criteria (ADR-0050), so the verdict stops being a projection of
            # the floor guard — ADR-0051 R1's precondition. Defaults OFF, and it is
            # deliberately NOT gated on `WISP_CRITERIA_STRUCTURED_DECLARATION`: that
            # flag gates the OBJECTIVE-LEVEL derivation, and coupling them would make
            # the turn path's behaviour depend on a flag read at another composition
            # point (ADR-0002 — one flag per concern, read once). With this off, the
            # verdict site below is byte-for-byte today's code.
            #
            # A MODEL-authored prompt (a delegated subagent's task) is never read for a
            # declaration: a `command_succeeds` spec is run by the host's probe, outside
            # ToolExecutor's approval and sandbox, and ADR-0050 R6's "the objective comes from
            # the caller" does not hold when the caller is the model (PR #30 review).
            from wisp.core.turn_criteria import MODEL_AUTHORED_PROMPT_KEY

            turn_criteria_source_enabled = bool(
                getattr(getattr(self, "config", None), "turn_criteria_source", False)
                and not session.get(MODEL_AUTHORED_PROMPT_KEY))
            # ADR-0054: the ACCEPTANCE GATE — withholds `done` (bounded, by ADR-0036's
            # model) when the objective's declared criteria are not satisfied. This is
            # the consumer ADR-0053 §10 recorded as missing.
            #
            # DEPENDENT on `turn_criteria_source`, deliberately: the gate withholds on
            # the criteria the source produces, and the verdict the RECORD carries must
            # be the one the gate acted on. With the source off there are no declared
            # criteria in the set, so the gate would withhold on a verdict the record
            # does not contain — two answers to one question.
            #
            # Defaults OFF. ADR-0051 R2's measurement contract is NOT satisfied (see
            # ADR-0054), so nothing is enabled by default. Read once, here.
            acceptance_gate_enabled = bool(
                getattr(getattr(self, "config", None), "acceptance_gate", False)
                and turn_criteria_source_enabled)
            # Migration POST-M13 (ADR-0036), ENFORCEMENT: let M13's predicate
            # withhold `done` for a bounded replan. Defaults OFF, and separate
            # from `graph_oscillation_guard` (which disables the detector
            # itself) because recording and enforcing are different concerns —
            # so the rollback has two levels. This needs no session repo: it is
            # control, not a record.
            stagnation_gate_enabled = bool(
                getattr(getattr(self, "config", None), "stagnation_gate", False))
            # Migration M13 — the stagnation detector, on the live turn path.
            #
            # Constructed per TURN, not per session: the question it answers is
            # "is this turn going round in circles?", and a detector that spans
            # turns would compare one user request against the next, which is
            # not the same task.
            #
            # `from_config` reads `config.graph_oscillation_guard` — the flag
            # that has existed since before this migration and was read by
            # nothing (P7's explicit completion criterion). A disabled detector
            # returns PROGRESSING from `observe`, so the flag is the rollback
            # switch and no separate check is needed.
            stagnation_detector = None
            stagnation_signal = None
            stagnation_seen = False
            #: The arguments of each call that streamed, keyed by its id — so an
            #: observation can name the ACTION, not just the tool. Empty when the
            #: engine refused a call before dispatch (it emits no call event
            #: then, and the arguments never reach the runtime): the outcome
            #: hash carries the signal instead. See ADR-0034 / F33.
            call_args_by_id: dict[str, tuple[str, Any]] = {}
            try:
                from wisp.core.stagnation import (
                    ProgressSignal as _ProgressSignal,
                    StagnationDetector as _StagnationDetector,
                    StagnationVerdict as _StagnationVerdict,
                )
                stagnation_detector = _StagnationDetector.from_config(
                    getattr(self, "config", None))
                stagnation_signal = _ProgressSignal()
            except Exception:
                logger.debug("stagnation detector unavailable", exc_info=True)
            # Provider-visible injections the core appended to its LOCAL
            # messages mid-turn (verification nudges, steering notes,
            # budget notice). Persisted in the finally block so
            # resume/replay sees exactly what the model saw (issue #2B).
            injected_context: list[dict[str, Any]] = []
            # Terminal-evidence tracking (13-H5): repository completion
            # derives from the observed terminal outcome below, never from
            # bare generator exhaustion. A fatal error is an error event
            # with recoverable falsy; recoverable mid-turn diagnostics
            # (denials, recovered transients) do not poison a later clean
            # done — otherwise the crash-recovery replay branch would wipe
            # live tool history on the next turn.
            saw_done = False
            saw_fatal_error = False
            terminal_error_message: str | None = None
            #: ADR-0035 — the engine error code, kept because it is what
            #: distinguishes a budget exhaustion from a timeout inside
            #: `GOAL_FAILED`, and it is the durable input M12 classifies from.
            terminal_error_code: str = ""
            turn_succeeded = False
            #: ADR-0044 — the turn's terminal evidence, computed ONCE at the end
            #: of the turn loop below from the final `saw_done`/`saw_fatal_error`.
            #: `turn_succeeded` is a projection of it and `derive_goal_state`
            #: reads both, so they must come from one computation: two
            #: predicates feeding one arbiter could disagree, and rows 3/5 key
            #: on the outcome while row 6 keys on the flag.
            _goal_outcome: Any = None

            # ADR-0036's seam: a READ-ONLY predicate over the per-turn detector,
            # handed to the engine so it may withhold `done` for a bounded
            # replan. The engine never receives the detector itself —
            # `observe()` mutates, and handing it over would give a second party
            # write access to the one stagnation authority. The closure is made
            # here and dies with the turn, so it is per-turn by construction:
            # nothing to cache, nothing to leak between turns or sessions.
            #
            # `None` when the flag is off or there is no detector — exactly
            # today's behaviour. No detector, no intervention authority: the
            # subagent/background paths drive `core.turn` directly and pass
            # nothing (ADR-0036's explicit non-goal).
            completion_gate = (
                (lambda: bool(stagnation_detector.may_report_goal_met()))
                if (stagnation_gate_enabled and stagnation_detector is not None)
                else None)

            # ADR-0054: the declared-criteria gate, built BEFORE the turn because the
            # engine asks it at its pre-`done` gate. A malformed declaration raises HERE
            # (ADR-0050 R4 — loud, never a fallback), outside any handler, so it cannot
            # become a silent floor-only run (ADR-0053 R6).
            declared_gate = None
            if acceptance_gate_enabled:
                from wisp.core.turn_criteria import declared_criteria_gate

                declared_gate = declared_criteria_gate(
                    prompt, session.get("workspace", "."))

            try:
                # The completion gate is passed ONLY when there is one. This is
                # not tidiness: `turn()` is an implementation point as well as a
                # call site, and an alternative core — every `_MockCore` in the
                # suite — implements the pre-ADR-0036 signature. Passing
                # `completion_gate=None` unconditionally made those cores raise
                # `TypeError`, i.e. it widened a pinned signature for a new
                # concern (ADR-0009). With the flag off (the default) the call
                # below is byte-for-byte the old call.
                turn_kwargs: dict[str, Any] = {
                    "approval_handler": approval_handler,
                    "steering_drain": lambda: self.drain_steering(sid),
                }
                if completion_gate is not None:
                    turn_kwargs["completion_gate"] = completion_gate
                if declared_gate is not None:
                    turn_kwargs["declared_gate"] = declared_gate
                async for raw_event in core.turn(session, prompt, **turn_kwargs):
                    # Engine already yields flat dicts — normalize only if needed
                    if isinstance(raw_event, dict) and "type" in raw_event:
                        event = dict(raw_event)
                    else:
                        canonical = normalize_event(raw_event).to_dict()
                        event = dict(canonical.get("data", {}))
                        event["type"] = canonical["type"]
                        event["timestamp"] = canonical.get("timestamp", 0.0)
                    yield event

                    etype = event.get("type")
                    if etype == "content":
                        assistant_content.append(_ev_get(event, "text", ""))
                    elif etype == "tool_call":
                        tool_calls.append(event)
                        tool_sequence.append(("call", event))
                        # Migration M13 — remember this call's action identity
                        # so its reply can be observed as a *named* action.
                        _cid = str(event.get("id") or _ev_get(event, "id", "") or "")
                        if _cid:
                            call_args_by_id[_cid] = (
                                str(_ev_get(event, "name", "") or ""),
                                _ev_get(event, "arguments", {}) or {},
                            )
                        _data = event.get("data")
                        self._note_touched_file(
                            sid, _data if isinstance(_data, dict) else event)
                        tool_call_spans.append((
                            str(_ev_get(event, "name", "") or ""),
                            time.time(),
                        ))
                    elif etype == "tool_result":
                        tool_results.append(event)
                        tool_sequence.append(("reply", event))
                        # Migration M13 — observe one closed exchange.
                        #
                        # The signal is built from what is ALWAYS present here
                        # — the action (when the call streamed) and the outcome
                        # — not from the opt-in records `from_verdict_and_graph`
                        # reads. That distinction is the whole point: both of
                        # those default off, so a turn-end signal would be empty
                        # on every default configuration and the detector would
                        # read "no information" as "no progress" (F32).
                        #
                        # Accumulated immutably (`with_work`), because progress
                        # is "we have learned something new at some point", and
                        # a delta comparison would call an A→B→A oscillation
                        # progress on every step.
                        if (stagnation_detector is not None
                                and stagnation_signal is not None):
                            try:
                                from wisp.core.action_key import action_key
                                from wisp.core.oscillation import diff_hash
                                # The action identity, from whichever producer
                                # saw the call: the engine stamps it on a
                                # refusal (a refused call emits no call event),
                                # and for an allowed call the runtime already
                                # read the call event above. Two producers for
                                # disjoint cases, not two copies of one — and
                                # both go through the same `action_key`.
                                _unit = str(event.get("action_key") or "")
                                if not _unit:
                                    _args = call_args_by_id.get(
                                        str(event.get("tool_call_id") or ""))
                                    if _args:
                                        _unit = action_key(_args[0], _args[1])
                                _raw = _ev_get(event, "result", None)
                                if _raw is None:
                                    _raw = _ev_get(event, "data", "")
                                _text = (json.dumps(_raw, default=str)
                                         if isinstance(_raw, dict)
                                         else str(_raw or ""))
                                stagnation_signal = stagnation_signal.with_work(
                                    work_unit=_unit,
                                    outcome_hash=diff_hash(_text) if _text else "")
                                if (stagnation_detector.observe(stagnation_signal)
                                        is _StagnationVerdict.STAGNATING):
                                    stagnation_seen = True
                            except Exception:
                                logger.debug("stagnation observation failed",
                                             exc_info=True)
                        # Migration P1: journal each exchange the moment it
                        # closes, so a crash mid-turn leaves the completed
                        # exchanges on disk instead of nothing at all.
                        # Before P1 the whole turn body was written once, at
                        # turn end inside the `finally` block — which never
                        # runs on SIGKILL, so a killed turn left only the
                        # user_message and the turn interior was a black box.
                        #
                        # The closed set is recomputed rather than tracked
                        # incrementally: it goes through the SAME
                        # `_group_exchanges` rule the turn-end serializer
                        # uses, so the two can never disagree about where an
                        # exchange ended. The already-written prefix is
                        # sliced off, which is sound because grouping is
                        # sequential — later events never re-open an earlier
                        # exchange.
                        if journal_incremental or proposal_boundary:
                            _closed = _closed_exchange_events(
                                tool_sequence, _ev_get,
                                journal=journal_fidelity,
                                proposal=proposal_boundary)
                            _fresh = _closed[journaled_event_count:]
                            if _fresh:
                                from dataclasses import replace as _stamp
                                _stamped = []
                                for _ev in _fresh:
                                    seq_num += 1
                                    _stamped.append(_stamp(
                                        _ev, sequence_num=seq_num))
                                await asyncio.to_thread(
                                    self._journal_turn_events, sid, _stamped)
                                journaled_event_count += len(_fresh)
                    elif etype == "system":
                        # Persist provider-visible [SYSTEM] injections
                        # (verification nudge, budget notice) in the exact
                        # shape the core used for its local messages.
                        # Turn-ephemeral advice (truncation / retry notices)
                        # carries no [SYSTEM] prefix and stays unpersisted.
                        _msg = event.get("message")
                        if _msg is None:
                            _d = event.get("data")
                            _msg = _d.get("message") if isinstance(_d, dict) else None
                        if isinstance(_msg, str) and _msg.startswith("[SYSTEM]"):
                            injected_context.append(nudge_message(_msg))
                    elif etype in ("steering_inject", "steering_feedback"):
                        # steering_feedback() factory emits type
                        # "steering_inject" ("steering_feedback" is the
                        # function name); accept both. Mirrors the core's
                        # local f"[steering] {note}" user message exactly.
                        _note = event.get("text")
                        if _note is None:
                            _d = event.get("data")
                            _note = _d.get("text") if isinstance(_d, dict) else None
                        if isinstance(_note, str) and _note.strip():
                            injected_context.append(steering_message(_note))
                    elif etype == "done":
                        saw_done = True
                    elif etype == "error":
                        if not _ev_get(event, "recoverable", True):
                            saw_fatal_error = True
                        terminal_error_message = str(
                            _ev_get(event, "message") or "turn failed")
                        terminal_error_code = str(
                            _ev_get(event, "code", "") or "")

                # 13-H5 / ADR-0044 R1-R2: completion derives from terminal
                # evidence — a done with no fatal error. Bare exhaustion,
                # partial output, or fatal diagnostics never count as success.
                #
                # The evidence is turned into its outcome HERE, once, and the
                # turn-level flag is a PROJECTION of it. `turn_succeeded` used
                # to re-implement the same predicate, and `derive_goal_state`
                # reads both inputs — so the two could have disagreed about the
                # same turn.
                from wisp.core.goal import TerminalOutcome, terminal_outcome_from_evidence
                _goal_outcome = terminal_outcome_from_evidence(
                    saw_done=saw_done, saw_fatal_error=saw_fatal_error)
                turn_succeeded = _goal_outcome is TerminalOutcome.SUCCEEDED

            except Exception as exc:
                logger.exception("Turn failed for session %s", sid)
                error_event = {
                    "type": "error",
                    "message": f"Turn aborted: {exc}",
                    "recoverable": True,
                }
                yield error_event
                seq_num += 1
                if self.session_repo is not None:
                    try:
                        from wisp.core.session import SessionEvent
                        self.session_repo.append_event(sid, SessionEvent.error(seq_num, str(exc)))
                    except Exception:
                        pass

            finally:
                # Always record what happened in the session
                #
                # Migration P0: journal the tool exchanges as session events.
                # Before this, the append-only log held only user_message /
                # error / done — `load_session()` (the documented crash-
                # recovery replay source, ~40 lines above) could therefore
                # never reconstruct a turn, and would replace a live session's
                # messages with a user-message-only list. Events are derived
                # from the SAME pairing walk that writes the messages, so the
                # two cannot drift.
                journal_events: list[Any] = []
                # Migration M11: the identity of each work unit this turn
                # performed, in order — read back from the blocks the one
                # pairing authority just built. Empty when no exchange closed,
                # which is the honest answer for a content-only turn.
                exchange_call_ids: list[list[str]] = []
                if tool_sequence:

                    # ── Group into provider-boundary exchanges ──────────
                    # REGRESSION GUARD (2026-08-27): this block used to emit
                    # ONE assistant message PER tool call — every assistant
                    # first, then every role:"tool" reply. With N>1 parallel
                    # calls in one provider iteration, the first tool reply
                    # answered the second-to-last assistant message, which
                    # OpenAI-compatible endpoints reject with HTTP 400.
                    # The protocol requires: ONE assistant message carrying
                    # ALL of an iteration's tool_calls blocks, IMMEDIATELY
                    # followed by its own replies.
                    #
                    # Migration P1: the grouping rule now lives in
                    # `_group_exchanges` so the incremental journal (which
                    # flushes each exchange as it closes) and this turn-end
                    # serializer share ONE implementation. A second copy of
                    # the rule would drift, and a drift here corrupts the
                    # provider transcript, not merely the log.
                    exchanges = _group_exchanges(tool_sequence)

                    def _field(ev: dict[str, Any], key: str) -> Any:
                        """Read an event field across flat and data-nested shapes."""
                        if key in ev:
                            return ev[key]
                        d = ev.get("data")
                        return d.get(key) if isinstance(d, dict) else None

                    journal_events, exchange_call_ids = _serialize_tool_exchanges(
                        session, exchanges, _field, journal=journal_fidelity,
                        proposal=proposal_boundary)
                    # Migration P1: drop the prefix the incremental writer
                    # already committed. Sound because grouping is sequential
                    # — `_closed_exchange_events` returns a growing prefix of
                    # exactly this list, so the slice cannot skip or reorder
                    # an event. When the incremental flag is off the count is
                    # 0 and this is a no-op.
                    if journaled_event_count:
                        journal_events = journal_events[journaled_event_count:]

                if assistant_content:
                    session["messages"].append({
                        "role": "assistant",
                        "content": "".join(assistant_content),
                    })
                    if journal_fidelity:
                        from wisp.core.session import SessionEvent
                        journal_events.append(SessionEvent.assistant_message(
                            0, "".join(assistant_content)))

                # Migration P4 — materialize the turn as a task graph.
                #
                # RECORDED, not enforced: the message list remains
                # authoritative, exactly as the plan's rollback strategy
                # requires. The graph is built from what the turn OBSERVABLY
                # did — one node per closed tool exchange, plus a terminal
                # node for the final assistant output.
                #
                # It is deliberately a faithful record of observable work
                # units rather than a claim about the model's internal loop
                # count: a content-only iteration leaves no exchange, so the
                # node count is a lower bound on iterations and is described
                # as one. Inventing nodes for iterations nobody observed would
                # be the graph equivalent of a fabricated success.
                if task_graph_recording and journal_fidelity:
                    try:
                        from wisp.core.session import SessionEvent
                        from wisp.core.task_graph import (
                            NodeTransition, TaskNodeState, apply_transition,
                            build_turn_graph, materialize, turn_work_units,
                        )
                        # Migration M11 — the nodes name the work units they
                        # record. `exchange_call_ids` comes from the SAME walk
                        # that wrote the transcript and the journal (see
                        # `_serialize_tool_exchanges`), so a node's reference is
                        # the protocol id the transcript actually used. It used
                        # to be `len(exchanges) + 1`, a count, which left every
                        # node an index with nothing to trace back to.
                        _units = turn_work_units(exchange_call_ids)
                        _graph = materialize(build_turn_graph(sid, _units))
                        journal_events.append(SessionEvent.task_graph_event(
                            0, _graph.to_dict()))
                        _seq = 0
                        for _i in range(len(_units)):
                            _seq += 1
                            _to = (TaskNodeState.SUCCESS if turn_succeeded
                                   else TaskNodeState.FAILURE)
                            _node = _graph.node(f"turn:{_i}")
                            if _node is None or _node.status is _to:
                                continue
                            _graph = apply_transition(_graph, NodeTransition(
                                run_id=sid, node_id=f"turn:{_i}",
                                from_status=_node.status, to_status=_to,
                                seq=_seq,
                                reason=("settled at turn end"
                                        if turn_succeeded
                                        else "turn did not succeed"),
                            ))
                            journal_events.append(
                                SessionEvent.node_transition_event(
                                    0, NodeTransition(
                                        run_id=sid, node_id=f"turn:{_i}",
                                        from_status=TaskNodeState.PENDING,
                                        to_status=_to, seq=_seq,
                                        reason=("settled at turn end"
                                                if turn_succeeded
                                                else "turn did not succeed"),
                                    ).to_dict()))
                    except Exception:
                        logger.debug("task graph materialization failed",
                                     exc_info=True)

                # Migration P3 (stage 3a) — RECORD the completion verdict.
                #
                # Recorded, not enforced. The completion rule is unchanged:
                # `turn_succeeded` still derives from terminal evidence (13-H5)
                # and the floor guard still decides nudges. What is new is that
                # the verdict is *written down*, so stage 3b can be justified by
                # a measured INCONCLUSIVE rate instead of a guess — which is the
                # staging the plan prescribes for the highest-risk phase.
                #
                # `record_verdict` is read once, here, and defaults OFF: an
                # audit record must not appear in a caller's log because it was
                # not asked for.
                #
                # The guard lives on the CORE, not here — it is the engine that
                # owns the completion invariant. Reading it via getattr keeps
                # ONE floor implementation (a second one here would be a second
                # authority for "was this verified"), and keeps this to a read.
                if verdict_recording:
                    _guard = getattr(core, "_last_guard", None)
                    if _guard is not None:
                        try:
                            from wisp.core.session import SessionEvent
                            from wisp.core.verification import floor_guard_verdict
                            journal_events.append(SessionEvent.verdict_event(
                                0, floor_guard_verdict(_guard).to_dict()))
                        except Exception:
                            logger.debug("verdict recording failed",
                                         exc_info=True)

                # Migration M13 — record the stagnation verdict.
                #
                # RECORDED, not enforced: the detector observes and reports, and
                # nothing acts on the report. Routing it to the recovery ladder
                # would change this turn's control flow, which is the risk
                # ADR-0026 named and the same deferral M12 made for the ladder's
                # enforcement. A tripwire asserts the ladder is still unconsulted.
                #
                # Written only when the verdict was reached, so a normal turn
                # adds no record to any caller's log — which is what lets this
                # run under the flag's declared default (`True`) rather than
                # being opt-in like `record_verdict`/`task_graph`.
                if (stagnation_seen and stagnation_detector is not None
                        and journal_fidelity and self.session_repo is not None):
                    try:
                        from wisp.core.session import SessionEvent
                        journal_events.append(SessionEvent.stagnation_event(
                            0, stagnation_detector.to_dict()))
                    except Exception:
                        logger.debug("stagnation recording failed",
                                     exc_info=True)

                # ── ADR-0035 — the completion authority, and the recovery
                # track behind its own flag. ────────────────────────────────
                #
                # ORDERING is taken from ADR-0035's **normative precedence
                # table**; its flow diagram is illustrative. The completion
                # GATE is first — it lives in the engine, before `done` is
                # emitted (`stateless.py:761-806`), which is the only place
                # completion can be *prevented*. The DERIVATION below runs last
                # because precedence row 2 (`ESCALATED_TO_HUMAN`) is a recovery
                # outcome: the escalation fact must exist before the state is
                # derived.
                #
                # Nothing is re-derived here. The acceptance verdict comes from
                # the floor guard (P3), the stagnation predicate from M13, the
                # failure class from M12. This block only arbitrates.
                from wisp.core.goal import (
                    already_recorded_from,
                    derive_goal_state,
                    terminal_outcome_from_evidence,
                )

                # ADR-0044 R3: the outcome was computed ONCE, at the end of the
                # turn loop above. Re-deriving it unconditionally here is the
                # second implementation this ADR removes. The guard covers the
                # one path where that computation did not happen — the turn body
                # raised before its evidence was final, and this `finally` still
                # ran. Same function; never a second predicate.
                if _goal_outcome is None:
                    _goal_outcome = terminal_outcome_from_evidence(
                        saw_done=saw_done, saw_fatal_error=saw_fatal_error)

                # P3's verdict, read from the guard the engine published
                # (ADR-0018) — the ONE floor implementation, not a second one.
                #
                # ADR-0053: with `turn_criteria_source` on, the criteria set is the
                # floor guard's criterion UNIONED with the objective's declared
                # criteria (ADR-0050) and the declaration's own probe evidence. The
                # parse and the probe sit OUTSIDE the guard below on purpose: a
                # rejected declaration must propagate (ADR-0050 R4 — loud, never a
                # fallback) rather than be swallowed as "verdict unavailable", which
                # would silently measure the floor while the caller declared more.
                _acceptance = None
                _guard_for_goal = getattr(core, "_last_guard", None)
                if _guard_for_goal is not None and turn_criteria_source_enabled:
                    from wisp.core.turn_criteria import turn_acceptance_verdict

                    _turn_verdict, _turn_criteria = turn_acceptance_verdict(
                        _guard_for_goal, prompt, session.get("workspace", "."),
                        enabled=True,
                        # Reuse the gate's probe when it ran: the declared command is the
                        # expensive part, and one turn pays for it once (ADR-0054 R3).
                        # Only a probe that let `done` through: a withheld one is stale.
                        measurement=(declared_gate.final_measurement
                                     if declared_gate is not None else None))
                    _acceptance = _turn_verdict.verdict
                    if _turn_criteria.declared:
                        logger.debug("turn criteria: floor + declared %s",
                                     _turn_criteria.declared_ids)
                elif _guard_for_goal is not None:
                    try:
                        from wisp.core.verification import floor_guard_verdict
                        _acceptance = floor_guard_verdict(
                            _guard_for_goal).verdict
                    except Exception:
                        logger.debug("acceptance verdict unavailable",
                                     exc_info=True)

                # M13's established predicate — consumed, not re-inferred.
                _stagnating = bool(
                    stagnation_detector is not None
                    and not stagnation_detector.may_report_goal_met())

                # ── The recovery track (ADR-0035), behind `recovery_ladder`. ──
                #
                # Consulted only when the turn did not succeed — there is
                # nothing to recover from otherwise — and emitted only when a
                # rung is actually chosen. Never because a detector exists, and
                # never merely because a failure occurred.
                _escalated = False
                if recovery_ladder_enabled and not turn_succeeded:
                    try:
                        from wisp.core.recovery import (
                            RecoveryLadder, classify_failure_signal,
                        )
                        from wisp.core.session import SessionEvent as _SEv
                        from wisp.core.stagnation import route_to_recovery

                        _ladder = RecoveryLadder()
                        _evidence = (
                            f"terminal={_goal_outcome.value}",
                            f"code={terminal_error_code or 'none'}",
                        )
                        if _stagnating and stagnation_detector is not None:
                            # P7's existing routing, reused — not reimplemented.
                            _decision = route_to_recovery(
                                stagnation_detector, _ladder, _evidence)
                        else:
                            _failure_class = classify_failure_signal(
                                terminal_error_message,
                                not saw_fatal_error,
                                terminal_error_code or None)
                            _decision = _ladder.decide(
                                _failure_class, _evidence,
                                reason=str(terminal_error_message or ""))
                        if _decision is not None:
                            journal_events.append(
                                _SEv.recovery_event(0, _decision.to_dict()))
                        if _ladder.escalated and _ladder.escalation is not None:
                            _escalated = True
                            journal_events.append(_SEv.escalation_event(
                                0, _ladder.escalation.to_dict()))
                    except Exception:
                        logger.debug("recovery ladder unavailable",
                                     exc_info=True)

                # ── The goal state: derived, then recorded. ──────────────────
                #
                # The record carries the INPUTS as well as the answer, which is
                # what closes ADR-0035's two durability gaps: the acceptance
                # verdict is present whenever the goal state is authoritative
                # (without switching the diagnostic `VERDICT` record on), and
                # every evaluated turn has an explicit stagnation outcome — so a
                # missing stagnation record can no longer be ambiguous between
                # "not evaluated" and "evaluated and not stagnating".
                _stagnation_verdict = (
                    str(getattr(stagnation_detector.verdict, "value",
                                stagnation_detector.verdict))
                    if stagnation_detector is not None else "not_evaluated")
                _goal_state = derive_goal_state(
                    terminal_outcome=_goal_outcome,
                    acceptance_verdict=_acceptance,
                    stagnating=_stagnating,
                    turn_succeeded=turn_succeeded,
                    cancelled=False,
                    escalated=_escalated,
                    already_recorded=already_recorded_from(
                        session.get("goal_states")
                        if isinstance(session, dict) else None),
                )
                if goal_state_recording:
                    try:
                        from wisp.core.session import SessionEvent
                        journal_events.append(SessionEvent.goal_state_event(
                            0, {
                                "goal_state": str(_goal_state),
                                "terminal_outcome": _goal_outcome.value,
                                "acceptance_verdict": str(
                                    getattr(_acceptance, "value", _acceptance)
                                    or ""),
                                "stagnation_verdict": _stagnation_verdict,
                                # F35 / ADR-0036 §6 — the predicate the arbiter
                                # ACTUALLY consumed, taken from the same
                                # computation (`not _stagnating`) so the record
                                # and the arbitration cannot drift. The
                                # `stagnation_verdict` above is M13's
                                # human-readable verdict, which ignores
                                # `trap_fired`; replay must read THIS field or a
                                # trap-only stagnation reconstructs as GOAL_MET
                                # and live and replay disagree.
                                "stagnation_allows_goal_met": not _stagnating,
                                "turn_succeeded": bool(turn_succeeded),
                                "cancelled": False,
                                "escalated": bool(_escalated),
                                "failure_code": terminal_error_code,
                            }))
                    except Exception:
                        logger.debug("goal state recording failed",
                                     exc_info=True)

                # Transcript unification (issue #2, part B): record the
                # injected context above so the persisted transcript shows
                # the model exactly what it saw mid-turn.
                # ORDERING: pure append AFTER exchanges + assistant content.
                # Intra-turn interleaving (e.g. a steering note between two
                # exchanges) is normalized on persist. This keeps the
                # positional exchange serializer above untouched — strict
                # providers require each tool reply to immediately follow
                # its assistant tool_calls block, and splicing user
                # messages between exchanges risks orphaning ids.
                #
                # Journaled beside the append, under the digest's gate: the model
                # saw these, so a replay without them is a different transcript.
                _journal_injected = (journal_fidelity
                                     and self.session_repo is not None)
                for ctx_msg in injected_context:
                    session["messages"].append(ctx_msg)
                    if _journal_injected:
                        from wisp.core.session import SessionEvent
                        journal_events.append(
                            SessionEvent.injected_context_event(0, ctx_msg))

                # The transcript's digest, recorded so that replay can CHECK the
                # transcript it rebuilds rather than assume it — F25's defect
                # class, where `Session.apply` produced a transcript the turn had
                # not run on and nothing said so. Appended last, so it covers
                # every message this turn added, and before stamping so it takes a
                # sequence number with the rest of the turn's events.
                #
                # Gated on `journal_fidelity` alone: that is the flag that
                # journals the transcript (assistant_message / tool_call /
                # tool_result). With it off there is no transcript in the log to
                # rebuild, so a digest would be a claim replay could never check.
                if journal_fidelity and self.session_repo is not None:
                    from wisp.core.replay_digest import projection_digest
                    from wisp.core.session import SessionEvent
                    journal_events.append(SessionEvent.replay_digest_event(
                        0, projection_digest(session["messages"])))

                # Stamp the journaled events into the session's sequence space
                # BEFORE the terminal event, so the log stays gap-free and
                # strictly increasing. Dropped entirely when the repository is
                # absent or both writers are off.
                if (journal_fidelity or proposal_boundary or verdict_recording
                        or task_graph_recording or recovery_ladder_enabled
                        or goal_state_recording) \
                        and journal_events and self.session_repo is not None:
                    from dataclasses import replace as _replace_event
                    stamped: list[Any] = []
                    for _ev in journal_events:
                        seq_num += 1
                        stamped.append(_replace_event(_ev, sequence_num=seq_num))
                    journal_events = stamped
                else:
                    journal_events = []

                # Persist the turn OFF the event loop: saving the full session
                # blob, writing the DONE event, and folding turn memory are all
                # blocking SQLite/JSONL writes. Running them on the loop stalls
                # every concurrent transport/subagent turn.
                #
                # The journal write is a separate hop that runs FIRST, so a
                # reader can never observe a terminal DONE whose turn body is
                # missing — the failure mode that made the pre-migration log
                # useless for recovery.
                if journal_events:
                    await asyncio.to_thread(
                        self._journal_turn_events, sid, journal_events)
                seq_num += 1
                await asyncio.to_thread(
                    self._persist_turn_state,
                    session, sid, prompt, turn_succeeded, seq_num,
                    terminal_error_message,
                )

                # Trace spans last: they describe the turn that just finished,
                # so they must not be written before its terminal state exists.
                if getattr(getattr(self, "config", None), "turn_spans", True):
                    await asyncio.to_thread(
                        self._record_turn_spans,
                        trace_id, turn_span_id, sid, turn_span_started,
                        time.time(), turn_succeeded, tool_call_spans,
                        str(getattr(self, "_model", "") or ""),
                    )

                # Record telemetry.
                #
                # (A stale comment claiming "Cache result for idempotency
                # (1h TTL)" sat here with no code beneath it. Migration P1
                # removed it rather than implementing it: a process-local TTL
                # cache cannot survive the crash it would need to protect
                # against, so idempotency now lives where it can actually
                # work — a canonical action key journaled on the TOOL_CALL
                # before dispatch and on its TOOL_RESULT after, queryable via
                # `Session.unresolved_actions()`. See
                # WISP_ARCHITECTURE_DECISIONS.md ADR-0010.)
                latency_ms = (time.time() - start) * 1000
                from wisp.infra.token_counter import TokenCounter
                model = getattr(self, "_model", None)
                chars_per_token = getattr(self, "_chars_per_token", 4)
                counter = TokenCounter(chars_per_token=chars_per_token)
                # `model=` is what lets the wired cost meter charge. Without it the
                # meter is inert and `max_cost_usd` cannot fire — the last link in
                # the chain, and the reason it is named here rather than assumed.
                #
                # NOTE: the counts come from `TokenCounter`, which ESTIMATES from
                # characters — the providers do not report usage. So a cost is an
                # estimate of a cost, and the bound is as sharp as the estimate.
                #
                # What is charged is every provider call the turn made, not the size of what the user typed: each call
                # re-sends the system prompt, the tool schemas and the history (`wisp.core.spend`). The old figure
                # (`count(prompt)`) is kept as a floor, so a turn is never charged less than before.
                spent_in = spent_out = 0
                try:
                    from wisp.core.spend import estimate_spend

                    spent_in, spent_out = estimate_spend(
                        session["messages"], self.prompt_overhead_chars(session),
                        from_index=turn_start_index, chars_per_token=chars_per_token)
                except Exception:
                    logger.debug("spend estimate failed; charging the prompt and reply only", exc_info=True)
                self.telemetry.record_turn(
                    latency_ms=latency_ms,
                    prompt_tokens=max(counter.count(prompt, model=model), spent_in),
                    completion_tokens=max(counter.count("".join(assistant_content), model=model), spent_out),
                    model=model,
                )

    # ── Mid-turn steering (M3) ─────────────────────────────────────

    _FILE_ARG_KEYS = ("path", "file_path", "notebook", "file")

    def _journal_turn_events(self, sid: str, events: list[Any]) -> None:
        """Write this turn's assistant/tool events in one transaction.

        Runs on a worker thread (see the caller) because it is a blocking
        SQLite write.

        **Best-effort, except for a state-bearing event (ADR-0028).** ADR-0004's
        rule — a turn that ran correctly must not be reported as failed because
        journaling failed — is right for a *record of what happened*, whose loss
        is observable as a gap in the session's sequence numbers. It is wrong for
        a *state transition*: `ESCALATION` is the record that a run parked itself
        and why, and a resume reads it to decide whether to resume. Swallowing
        that write would let the turn continue as though the park had been
        recorded, which is not a lost observation but a **false record** — so it
        propagates instead.

        A raise is not silent. Inside the stream loop the turn's own handler
        turns it into a recoverable error event the transport sees; on the
        turn-end path (a `finally`) it reaches the caller of `run_turn`. Either
        way a caller learns the escalation was not durable, which is exactly what
        it needs to retry or tell an operator directly.
        """
        if self.session_repo is None or not events:
            return
        from wisp.core.session import is_state_bearing
        try:
            self.session_repo.append_events(sid, events)
        except Exception:
            if is_state_bearing(events):
                logger.error(
                    "Session %s: failed to journal a STATE-BEARING event "
                    "(%d event(s)); propagating — the state transition was "
                    "not recorded", sid, len(events), exc_info=True)
                raise
            logger.warning(
                "Session %s: failed to journal %d turn event(s)",
                sid, len(events), exc_info=True)

    def _record_turn_spans(
        self,
        trace_id: str,
        turn_span_id: str,
        sid: str,
        started_at: float,
        finished_at: float,
        turn_succeeded: bool,
        tool_calls: list[tuple[str, float]],
        model: str = "",
    ) -> None:
        """Append this turn's spans to the trace store.

        Runs on a worker thread (blocking SQLite write). Best-effort by
        design, like every other durable write on this path (ADR-0004):
        observability must never fail a turn.

        `tool_calls` is a list of (name, started_at) for calls that actually
        streamed an event — a refused call has no span, for the same reason
        it has no journaled TOOL_CALL event: there was no invocation.
        """
        if self.trace_store is None:
            return
        try:
            from wisp.trace.span import Span, SpanStatus

            status = SpanStatus.OK if turn_succeeded else SpanStatus.ERROR
            spans = [Span(
                trace_id=trace_id, span_id=turn_span_id, kind="turn",
                name="run_turn", started_at=started_at,
                finished_at=finished_at, status=status,
                attrs={"session_id": sid, "model": model or ""},
            )]
            for name, call_started in tool_calls:
                from wisp.infra.tracing import uuid7
                spans.append(Span(
                    trace_id=trace_id, span_id=str(uuid7()),
                    parent_span_id=turn_span_id, kind="tool_call",
                    name=name or "unknown_tool", started_at=call_started,
                    finished_at=finished_at, status=status,
                    attrs={"session_id": sid, "model": model or ""},
                ))
            for span in spans:
                self.trace_store.append(span)
        except Exception:
            logger.warning(
                "Session %s: failed to record %d span(s)",
                sid, 1 + len(tool_calls), exc_info=True)

    def _persist_turn_state(
        self,
        session: dict[str, Any],
        sid: str,
        prompt: str,
        turn_succeeded: bool,
        seq_num: int,
        terminal_error: str | None = None,
    ) -> None:
        """Run on a worker thread at turn end: DONE event + memory fold +
        full session save. Groups the blocking SQLite/JSONL writes that
        used to run inline on the asyncio loop.

        The turn's journaled events are NOT written here — they go through
        `_journal_turn_events`, a separate off-loop step that runs first.
        Keeping them apart leaves this signature untouched, which matters:
        it is a pinned contract (`test_13h5_success_derivation.py`
        TestFlagCompatibility wraps it positionally to observe
        `turn_succeeded`), and widening it would break that guard rather
        than extend it.
        """
        # Repository completion mirrors the observed terminal outcome
        # (13-H5): DONE only on success; the terminal error row on failure
        # so history stays append-only and recovery can see it. Never both.
        if self.session_repo is not None:
            try:
                from wisp.core.session import SessionEvent
                if turn_succeeded:
                    self.session_repo.append_event(
                        sid,
                        SessionEvent.done(seq_num, self.telemetry.turns_total),
                    )
                elif terminal_error:
                    self.session_repo.append_event(
                        sid,
                        SessionEvent.error(seq_num, terminal_error),
                    )
            except Exception:
                pass

        self._record_session_memory(sid, session, prompt)

        # Update timestamp and save
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            self.store.save_session(session)
        except Exception:
            logger.exception("Failed to save session %s", sid)

    def _note_touched_file(self, sid: str, data: dict[str, Any]) -> None:
        name = str(data.get("name", ""))
        if name not in (
            "read_file", "write_file", "edit_file", "create_file",
            "apply_patch", "read_notebook", "overwrite_notebook",
        ):
            return
        for key in self._FILE_ARG_KEYS:
            val = (data.get("arguments") or {}).get(key)
            if isinstance(val, str) and val:
                if sid not in self._touched_files:
                    _maybe_evict_session_state(self)
                self._touched_files.setdefault(sid, set()).add(val)
                return

    def _record_session_memory(
        self, sid: str, session: dict[str, Any], prompt: str
    ) -> None:
        """Fold this turn into the session's cross-memory summary.

        Best-effort: memory must never break a turn.
        """
        try:
            if sid not in self._turn_counts:
                _maybe_evict_session_state(self)
            self._turn_counts[sid] = self._turn_counts.get(sid, 0) + 1
            from wisp.agent_memory import get_agent_memory
            from datetime import datetime, timezone

            mem = get_agent_memory()
            existing = {
                s.session_id: s for s in mem.load_all()
            }
            prev = existing.get(sid)
            files = sorted(self._touched_files.get(sid, set()))[:10]
            if prev is not None:
                merged_files = sorted(set(prev.files_touched) | set(files))[:10]
            else:
                merged_files = files
            turns = self._turn_counts[sid]
            summary = SessionSummary(
                session_id=sid,
                timestamp=datetime.now(timezone.utc).isoformat(),
                workspace=str(session.get("workspace", "")),
                summary=f"{turns} turn(s); latest request: {prompt[:160]}",
                files_touched=merged_files,
            )
            mem.upsert(summary)
        except Exception:
            logger.debug("session-memory recording failed", exc_info=True)

    def approval_state(self, session_id: str) -> ApprovalSessionState:
        """Session-scoped approval memory, created on first access."""
        state = self._approval_states.get(session_id)
        if state is None:
            _maybe_evict_session_state(self)
            state = ApprovalSessionState()
            self._approval_states[session_id] = state
        return state

    def apply_approval_decision(
        self, session_id: str, tool_name: str, key: str
    ) -> bool:
        """Fold an approval key into session memory; return the verdict.

        Same precedence as CLITransport.approve: policy short-circuits,
        then per-tool sets. y/n answer once and mutate nothing.
        """
        key = str(key).strip()
        state = self.approval_state(session_id)
        if key == "a":
            state.set_auto()
        elif key == "d":
            state.set_block()
        elif key == "Y":
            state.allow_tool(tool_name)
        elif key == "N":
            state.deny_tool(tool_name)

        if state.session_policy is SessionPolicy.AUTO:
            return True
        if state.session_policy is SessionPolicy.BLOCK:
            return False
        if tool_name in state.allowed_tools:
            return True
        if tool_name in state.denied_tools:
            return False
        return key in ("y", "Y", "a")

    def get_doctor_report(self) -> dict[str, Any]:
        """Public pre-flight report summary for UI layers (/doctor, banner).

        Returns the last DoctorRunner report as plain data, or a degraded
        sentinel when no check has run yet in this process. Never raises —
        diagnostics must not break the REPL.
        """
        try:
            from wisp.core.doctor import last_report

            report = last_report()
        except ImportError:
            report = None
        if report is None:
            return {"healthy": False, "passed": 0, "total": 0,
                    "banner": "pre-flight has not run yet", "checks": []}
        try:
            checks = [
                f"{getattr(c, 'symbol', '?')} {getattr(c, 'name', '?')}: {getattr(c, 'message', '')}"
                for c in (getattr(report, "checks", None) or [])
            ]
            return {
                "healthy": bool(getattr(report, "healthy", False)),
                "passed": int(getattr(report, "passed", 0) or 0),
                "total": int(getattr(report, "total", 0) or 0),
                "banner": str(getattr(report, "banner", "") or ""),
                "checks": checks,
            }
        except Exception:
            return {"healthy": False, "passed": 0, "total": 0,
                    "banner": "pre-flight report unreadable", "checks": []}

    def drain_steering(self, session_id: str) -> list[str]:
        """Remove and return pending steering notes for *session_id*."""
        return self._steering_inbox.pop(session_id, [])

    def inject_steering(self, session_id: str, text: str) -> None:
        """Queue a mid-course correction typed during an active turn.

        Thread-safe: called from the typeahead reader thread; drained on
        the loop thread at the next tool boundary.
        """
        text = str(text).strip()
        if not text:
            return
        if session_id not in self._steering_inbox:
            _maybe_evict_session_state(self)
        self._steering_inbox.setdefault(session_id, []).append(text)


    def clear_steering(self, session_id: str) -> None:
        self._steering_inbox.pop(session_id, None)

    def _autonomous_approval_handler(self) -> Any:
        """Synthetic handler for autonomous mode — auto-approves safe tools.

        Dangerous commands (`check_dangerous_command`) are still denied and
        surface as `NEEDS_HUMAN_REVIEW` in the graph layer, so the agent
        remains safe even in fully autonomous mode.
        """
        from wisp.tools._utils import check_dangerous_command

        async def handler(event: dict[str, Any]) -> bool:
            try:
                name = str(event.get("name", "") or "")
                args = event.get("arguments", {}) or {}
                # Hard block dangerous bash regardless of autonomy
                if name == "run_bash":
                    cmd = str(args.get("command", "") or "")
                    danger = check_dangerous_command(cmd)
                    if danger:
                        logger.warning("Autonomous gate blocked dangerous command: %s (%s)", cmd[:80], danger)
                        return False
                # In autonomous mode, all non-dangerous tools auto-approve
                return True
            except Exception as e:
                logger.warning("Autonomous handler failed — denying: %s", e, exc_info=True)
                return False

        return handler

    def _get_core(self, session_id: str | None = None) -> Any:
        """Get the core for *session_id*, creating if needed.

        Thread-safe: uses _core_lock. Keyed by (session_id, config
        fingerprint); passing None shares one introspection slot.
        """
        with self._core_lock:
            current_fp = None
            if self.config is not None and hasattr(self.config, "fingerprint"):
                current_fp = self.config.fingerprint()
            key = (session_id, current_fp)
            core = self._session_cores.get(key)
            if core is None:
                core = self.core_factory()
                self._session_cores[key] = core
                # Long-lived servers accumulate dead sessions; FIFO keeps
                # the map bounded without needing lifecycle hooks.
                while len(self._session_cores) > self.MAX_SESSION_CORES:
                    self._session_cores.pop(next(iter(self._session_cores)))
            return core

    def prompt_overhead_chars(self, session: dict[str, Any]) -> int:
        """Fixed per-call overhead (system prompt + tool schemas) of the core that serves *session*."""
        return int(self._get_core(session.get("id")).prompt_overhead_chars(session))

    def get_core_provider(self) -> Any:
        """Return the provider from the cached core, if available."""
        core = self._get_core()
        return getattr(core, "provider", None)

    def invalidate_core_cache(self) -> None:
        """Invalidate cached cores — call when config/workspace changes.

        Thread-safe: uses _core_lock.
        """
        with self._core_lock:
            self._session_cores.clear()

    def _recover_unfinished_turn(self, sid: str, session: dict[str, Any]) -> None:
        """The last turn did not reach DONE: rebuild from the journal, but only a trustworthy one.

        This used to log *"has incomplete turn — replaying"* and then assign whatever the journal
        replayed to, inside `except Exception: pass`. Two failures followed. A journal whose replay
        diverged from its recorded digest raised `ReplayDivergence`, which was swallowed, so the
        log claimed a replay that never happened, on every resume. A journal with a GAP replayed
        without error and REPLACED a good saved transcript with a provider-invalid one. The
        journal is now used only when it replays consistently and has no gap (M4's rule in
        `SessionRepository.reconstruction_source`). Otherwise the saved transcript stays, and the
        log says which history the turn runs on and why. A journal holding only user messages is
        still replayed, as before (`test_replay_holds_session_lock` depends on it).
        """
        from wisp.core.replay_digest import ReplayDivergence

        assert self.session_repo is not None
        try:
            replayed = self.session_repo.load_session(sid)
            unusable = ""
        except ReplayDivergence as exc:
            replayed, unusable = None, f"its journal does not replay consistently ({exc})"
        if replayed is not None and replayed.gap_detected:
            replayed, unusable = None, "its journal has a gap (a lost event)"
        if replayed is None:
            logger.warning(
                "Session %s: the previous turn did not finish; keeping the saved transcript "
                "because %s", sid, unusable or "its journal is empty")
            return
        session["messages"] = replayed.messages
        _stringify_tool_call_arguments(session["messages"])
        logger.info("Session %s: the previous turn did not finish; history rebuilt from the "
                    "journal", sid)

    async def maybe_compact(self, session: dict[str, Any], max_messages: int | None = None, force: bool = False) -> dict[str, Any] | None:
        """Compact session if it exceeds max_messages.

        Uses LLM-powered summarization when compactor is configured,
        falls back to simple truncation otherwise.

        Returns a dict with compaction results if compaction was performed,
        or None if skipped.
        """
        threshold = max_messages or self.max_messages
        if not force and len(session["messages"]) <= threshold:
            return None

        old_count = len(session["messages"])
        keep = threshold // 2

        # Preserve existing system messages (persona, delegation context, etc.)
        existing_system = [m for m in session["messages"] if m.get("role") == "system"]

        def _snap_kept(messages: list[dict[str, Any]], keep_n: int) -> list[dict[str, Any]]:
            """Slice the kept window on a safe boundary.

            - Never start on a role="tool" result whose assistant tool_calls
              head was summarized away (strict providers reject orphans).
            - Drop mid-history system copies; they are preserved up front.
            """
            n = len(messages)
            start = max(0, n - keep_n)
            while start < n and messages[start].get("role") == "tool":
                start += 1
            return [m for m in messages[start:] if m.get("role") != "system"]

        summary = ""
        if self.compactor is not None:
            try:
                result = await self.compactor.compact(
                    messages=session["messages"],
                    keep_recent=keep,
                )
                summary = result.summary
                fallback = result.fallback_truncation
            except Exception:
                logger.warning("Compactor failed, using truncation fallback")
                result = None
                fallback = True

            kept = _snap_kept(session["messages"], keep)
            summarizer_ran = not (result is None or fallback)
        else:
            kept = _snap_kept(session["messages"], keep)
            summary, summarizer_ran = "", False

        # What was dropped is recorded by the harness whether or not a model summarized it (wisp/core/compaction_record.py).
        kept_ids = {id(m) for m in kept}
        compacted = [m for m in session["messages"] if id(m) not in kept_ids and m.get("role") != "system"]
        summary = attach_record(summary, compacted, summarizer_ran)

        # Build new messages: existing system messages + summary + kept messages
        session["messages"] = existing_system + [{"role": "system", "content": summary}] + kept

        session["compaction_history"].append({
            "before_count": old_count,
            "after_count": len(session["messages"]),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.store.save_session(session)
        logger.info("Compacted session %s: %d → %d messages",
                    session.get("id"), old_count, len(session["messages"]))

        return {
            "compacted": True,
            "before_count": old_count,
            "after_count": len(session["messages"]),
            "summary": summary,
        }

    def _evict_old_session_state(self) -> None:
        """Evict coldest sessions' auxiliary maps if we exceed the limit.

        Kept for API compatibility; delegates to :func:`_maybe_evict_session_state`.
        Evict-before-insert keeps the just-created entry alive.
        """
        _maybe_evict_session_state(self)

    def _evict_old_session_locks(self) -> None:
        """Evict least-recently-used session locks if we exceed the limit."""
        if len(self._session_locks) <= self._max_session_locks:
            return
        # Evict oldest 20% by last access time (true LRU)
        to_evict = int(self._max_session_locks * 0.2)
        # Sort by access time, oldest first; missing entries get epoch 0
        sorted_keys = sorted(
            self._session_locks.keys(),
            key=lambda k: self._session_access.get(k, 0.0),
        )
        evicted = 0
        for k in sorted_keys:
            if evicted >= to_evict:
                break
            lock = self._session_locks.get(k)
            if lock is not None and lock.locked():
                # A held lock must never leave the map: eviction would let a
                # later arrival mint a fresh lock and run concurrently with
                # the turn still holding the old one.
                continue
            self._session_locks.pop(k, None)
            self._session_access.pop(k, None)
            evicted += 1

    async def start_background_run(
        self,
        session_id: str,
        prompt: str,
        model: str,
    ) -> str:
        """Start a background run. Returns run ID."""
        await self.get_or_create_session(session_id, model, "/tmp")

        run_id = f"bg-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        run = {
            "id": run_id,
            "session_id": session_id,
            "prompt": prompt,
            "model": model,
            "status": "pending",
            "events": [],
            "created_at": now,
        }
        self.store.save_run(run)
        return run_id

    async def update_run_status(self, run_id: str, status: str) -> None:
        """Update background run status."""
        run = self.store.load_run(run_id)
        if run is not None:
            run["status"] = status
            self.store.save_run(run)

    async def list_background_runs(self, session_id: str) -> list[dict[str, Any]]:
        """List background runs for a session."""
        runs: Any = self.store.list_runs(session_id=session_id)
        return runs  # type: ignore[no-any-return]  # store boundary is unannotated
