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
) -> list[Any]:
    """Append protocol-consistent assistant/tool messages for one turn.

    Per provider boundary: ONE assistant message holding every tool_calls
    block, IMMEDIATELY followed by that boundary's role:"tool" replies.

    Pairing and event derivation live in `_exchange_parts` — one authority
    for both, so the transcript and the journal cannot disagree.

    With `journal=False` no event objects are built; the message-writing
    behavior is identical either way, so the rollback flag cannot change
    the transcript.
    """
    events: list[Any] = []
    for ex in exchanges:
        blocks, reply_msgs, ex_events = _exchange_parts(
            ex, field_reader, journal, proposal)
        if not blocks and not reply_msgs:
            continue
        events.extend(ex_events)
        session["messages"].append({
            "role": "assistant",
            "content": "",
            "tool_calls": blocks,
        })
        session["messages"].extend(reply_msgs)

    return events


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
            # Crash recovery: if last event in session_events isn't DONE,
            # replay from last UserMessage to rebuild state
            if self.session_repo is not None:
                try:
                    if not self.session_repo.was_last_turn_complete(sid):
                        logger.warning("Session %s has incomplete turn — replaying", sid)
                        last_seq = self.session_repo.get_last_sequence(sid)
                        if last_seq >= 0:
                            replayed = self.session_repo.load_session(sid)
                            if replayed is not None:
                                session["messages"] = replayed.messages
                                _stringify_tool_call_arguments(session["messages"])
                except Exception:
                    pass  # table might not exist

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
            turn_succeeded = False

            try:
                async for raw_event in core.turn(
                    session, prompt, approval_handler=approval_handler,
                    steering_drain=lambda: self.drain_steering(sid),
                ):
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

                # 13-H5: completion derives from terminal evidence — a done
                # with no fatal error. Bare exhaustion, partial output, or
                # fatal diagnostics never count as success.
                turn_succeeded = saw_done and not saw_fatal_error

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

                    journal_events = _serialize_tool_exchanges(
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
                for ctx_msg in injected_context:
                    session["messages"].append(ctx_msg)

                # Stamp the journaled events into the session's sequence space
                # BEFORE the terminal event, so the log stays gap-free and
                # strictly increasing. Dropped entirely when the repository is
                # absent or both writers are off.
                if (journal_fidelity or proposal_boundary or verdict_recording) \
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
                self.telemetry.record_turn(
                    latency_ms=latency_ms,
                    prompt_tokens=counter.count(prompt, model=model),
                    completion_tokens=counter.count("".join(assistant_content), model=model),
                )

    # ── Mid-turn steering (M3) ─────────────────────────────────────

    _FILE_ARG_KEYS = ("path", "file_path", "notebook", "file")

    def _journal_turn_events(self, sid: str, events: list[Any]) -> None:
        """Write this turn's assistant/tool events in one transaction.

        Runs on a worker thread (see the caller) because it is a blocking
        SQLite write. Best-effort by design: a turn that ran correctly must
        not be reported as failed because journaling failed — the loss is
        observable as a gap in the session's sequence numbers, which is the
        honest signal (ADR-0004).
        """
        if self.session_repo is None or not events:
            return
        try:
            self.session_repo.append_events(sid, events)
        except Exception:
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

            if result is None or fallback:
                to_summarize = session["messages"][:-keep]
                kept = _snap_kept(session["messages"], keep)
                summary = f"[Compacted {len(to_summarize)} messages]"
            else:
                kept = _snap_kept(session["messages"], keep)
        else:
            to_summarize = session["messages"][:-keep]
            kept = _snap_kept(session["messages"], keep)
            summary = f"[Compacted {len(to_summarize)} messages]"

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
