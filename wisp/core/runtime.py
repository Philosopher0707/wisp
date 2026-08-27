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
from wisp.core.events import normalize_event

logger = logging.getLogger(__name__)
logger = logging.getLogger(__name__)


def _serialize_tool_exchanges(
    session: dict[str, Any],
    exchanges: list[dict[str, list]],
    field_reader,
) -> None:
    """Append protocol-consistent assistant/tool messages for one turn.

    Per provider boundary: ONE assistant message holding every tool_calls
    block, IMMEDIATELY followed by that boundary's role:"tool" replies.

    Pairing is POSITIONAL inside a boundary (calls[i] <-> replies[i]):
    streamed tool_call events may carry no stable id while tool_result
    events get one independently, so identity comes from the call event,
    falling back to its paired reply's id, falling back to a fresh id
    shared by both sides. Missing replies (turn interrupted mid-execute)
    get an honest placeholder; reply-only groups (gate-refused calls that
    never streamed a call event) synthesize their block.
    """
    for ex in exchanges:
        calls, replies = ex["calls"], ex["replies"]
        total = max(len(calls), len(replies))
        if total == 0:
            continue
        blocks: list[dict[str, Any]] = []
        reply_msgs: list[dict[str, Any]] = []
        for i in range(total):
            c = calls[i] if i < len(calls) else None
            rp = replies[i] if i < len(replies) else None

            name = ""
            if c is not None:
                name = field_reader(c, "name") or ""
                args = field_reader(c, "arguments") or {}
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

        session["messages"].append({
            "role": "assistant",
            "content": "",
            "tool_calls": blocks,
        })
        session["messages"].extend(reply_msgs)





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

        # Start trace context for this turn
        from wisp.infra.tracing import new_trace
        new_trace(session_id=sid)

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

        # Track events for session persistence
        emitted_events: list[dict[str, Any]] = []
        seq_num = 0
        if self.session_repo is not None:
            try:
                seq_num = self.session_repo.get_last_sequence(sid)
            except Exception:
                pass

        async with session_lock:
            # Auto-compact before turn to prevent context overflow
            await self.maybe_compact(session)

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
                    emitted_events.append(dict(event))

                    etype = event.get("type")
                    if etype == "content":
                        assistant_content.append(event.get("text", ""))
                    elif etype == "tool_call":
                        tool_calls.append(event)
                        tool_sequence.append(("call", event))
                        self._note_touched_file(sid, event.get("data") or {})
                    elif etype == "tool_result":
                        tool_results.append(event)
                        tool_sequence.append(("reply", event))

                turn_succeeded = True
                self._record_session_memory(sid, session, prompt)

            except Exception as exc:
                logger.exception("Turn failed for session %s", sid)
                error_event = {
                    "type": "error",
                    "message": f"Turn aborted: {exc}",
                    "recoverable": True,
                }
                yield error_event
                emitted_events.append(error_event)
                seq_num += 1
                if self.session_repo is not None:
                    try:
                        from wisp.core.session import SessionEvent
                        self.session_repo.append_event(sid, SessionEvent.error(seq_num, str(exc)))
                    except Exception:
                        pass

            finally:
                # Always record what happened in the session
                if tool_sequence:
                    import json

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
                    # Event arrival order encodes the boundaries:
                    #   callA callB  replyA replyB  callC  replyC ...
                    # An exchange closes once its replies catch up to its
                    # calls; max(...,1) also closes reply-only groups left
                    # by gate-refused calls (they stream no call event).
                    exchanges: list[dict[str, list]] = []
                    cur: dict[str, list] = {"calls": [], "replies": []}

                    def _close_exchange() -> None:
                        if cur["calls"] or cur["replies"]:
                            # Store copies — resetting `cur` below must not
                            # reach back into already-recorded exchanges.
                            exchanges.append({"calls": list(cur["calls"]),
                                              "replies": list(cur["replies"])})
                        cur["calls"], cur["replies"] = [], []

                    for kind, ev in tool_sequence:
                        if kind == "call":
                            cur["calls"].append(ev)
                        else:
                            cur["replies"].append(ev)
                            if len(cur["replies"]) >= max(len(cur["calls"]), 1):
                                _close_exchange()
                    _close_exchange()

                    def _field(ev: dict[str, Any], key: str) -> Any:
                        """Read an event field across flat and data-nested shapes."""
                        if key in ev:
                            return ev[key]
                        d = ev.get("data")
                        return d.get(key) if isinstance(d, dict) else None

                    _serialize_tool_exchanges(session, exchanges, _field)

                if assistant_content:
                    session["messages"].append({
                        "role": "assistant",
                        "content": "".join(assistant_content),
                    })

                # Persist DONE event to session event log
                seq_num += 1
                if self.session_repo is not None and turn_succeeded:
                    try:
                        from wisp.core.session import SessionEvent
                        self.session_repo.append_event(
                            sid,
                            SessionEvent.done(seq_num, self.telemetry.turns_total),
                        )
                    except Exception:
                        pass

                # Update timestamp and save
                session["updated_at"] = datetime.now(timezone.utc).isoformat()
                self.store.save_session(session)

                # Cache result for idempotency (1h TTL)
                # Record telemetry
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
                self._touched_files.setdefault(sid, set()).add(val)
                return

    def _record_session_memory(
        self, sid: str, session: dict[str, Any], prompt: str
    ) -> None:
        """Fold this turn into the session's cross-memory summary.

        Best-effort: memory must never break a turn.
        """
        try:
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
        self._steering_inbox.setdefault(session_id, []).append(text)


    def clear_steering(self, session_id: str) -> None:
        self._steering_inbox.pop(session_id, None)

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
