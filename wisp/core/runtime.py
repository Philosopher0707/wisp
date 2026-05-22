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
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable

from wisp.core.events import normalize_event

logger = logging.getLogger(__name__)


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

    # Cached core instance — avoids rebuilding system prompt caches every turn
    _core_cache: Any = field(default=None, repr=False)
    _core_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # Per-session locks — prevents concurrent turns on same session
    _session_locks: dict[str, asyncio.Lock] = field(default_factory=dict, repr=False)
    _session_access: dict[str, float] = field(default_factory=dict, repr=False)
    _max_session_locks: int = field(default=1000, repr=False)

    # Configurable thresholds (can be overridden)
    max_messages: int = field(default=50, repr=False)
    max_context_tokens: int = field(default=128000, repr=False)

    async def get_or_create_session(
        self,
        session_id: str,
        model: str,
        workspace: str,
    ) -> dict:
        """Load existing session or create new one.

        Validates inputs to prevent crashes deep in the stack.
        """
        # Input validation
        if not session_id or not isinstance(session_id, str):
            raise ValueError(f"Invalid session_id: {session_id!r}")
        if not model or not isinstance(model, str):
            raise ValueError(f"Invalid model: {model!r}")
        if not workspace or not isinstance(workspace, str):
            raise ValueError(f"Invalid workspace: {workspace!r}")

        session = self.store.load_session(session_id)
        if session is not None:
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

    async def run_turn(self, session: dict, prompt: str, approval_handler=None) -> AsyncIterator[dict]:
        """Run one turn, yielding events.

        Guarantees session consistency even if the turn aborts:
        - User message is always added
        - Assistant + tool messages are added on success
        - Session is saved after every turn
        - Compaction runs automatically before turn if needed
        - Concurrent turns on same session are serialized
        """
        import time
        start = time.time()

        # Input validation
        if not prompt or not isinstance(prompt, str):
            raise ValueError(f"Invalid prompt: {prompt!r}")

        sid = session.get("id", "unknown")

        # Idempotency: same prompt within 5s window returns cached result
        import hashlib
        idem_key = hashlib.sha256(
            f"{sid}:{prompt}:{int(time.time() / 5)}".encode()
        ).hexdigest()
        try:
            conn = self.store._get_conn()
            row = conn.execute(
                "SELECT result FROM idempotency WHERE key = ?",
                (idem_key,),
            ).fetchone()
            if row is not None:
                import json as _json
                cached = _json.loads(row["result"])
                for ev in cached:
                    yield ev
                return
        except Exception:
            pass  # idempotency table may not exist yet — not critical

        # Get or create per-session lock (LRU-tracked)
        if sid not in self._session_locks:
            self._evict_old_session_locks()
            self._session_locks[sid] = asyncio.Lock()
        self._session_access[sid] = time.monotonic()
        session_lock = self._session_locks[sid]

        # Start trace context for this turn
        from wisp.infra.tracing import new_trace
        trace_id = new_trace(session_id=sid)

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
            except Exception:
                pass  # table might not exist

        # Track events for idempotency cache
        emitted_events: list[dict] = []
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

            # Get cached core (warm-start, thread-safe)
            core = self._get_core()

            assistant_content: list[str] = []
            tool_calls: list[dict] = []
            tool_results: list[dict] = []
            turn_succeeded = False

            try:
                async for raw_event in core.turn(session, prompt, approval_handler=approval_handler):
                    # Normalize to canonical AgentEvent, then flatten
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
                    elif etype == "tool_result":
                        tool_results.append(event)

                turn_succeeded = True

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
                if tool_calls or tool_results:
                    for tc in tool_calls:
                        import json
                        args = tc.get("arguments", {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {}
                        session["messages"].append({
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{
                                "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                                "type": "function",
                                "function": {
                                    "name": tc.get("name", ""),
                                    "arguments": args,
                                },
                            }],
                        })

                    for tr in tool_results:
                        import json
                        result = tr.get("result", "")
                        if isinstance(result, dict):
                            content = json.dumps(result)
                        else:
                            content = str(result)
                        session["messages"].append({
                            "role": "tool",
                            "tool_call_id": tr.get("tool_call_id", ""),
                            "content": content,
                        })

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
                try:
                    import json as _json
                    self.store._get_conn().execute(
                        "INSERT OR REPLACE INTO idempotency (key, result, created_at) VALUES (?, ?, ?)",
                        (idem_key, _json.dumps(emitted_events, default=str), time.time()),
                    )
                except Exception:
                    pass

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

    def _get_core(self) -> Any:
        """Get cached core instance, creating if needed.

        Thread-safe: uses _core_lock to prevent race conditions.
        """
        with self._core_lock:
            if self._core_cache is None:
                self._core_cache = self.core_factory()
            return self._core_cache

    def invalidate_core_cache(self) -> None:
        """Invalidate cached core — call when config/workspace changes.

        Thread-safe: uses _core_lock.
        """
        with self._core_lock:
            self._core_cache = None

    async def maybe_compact(self, session: dict, max_messages: int | None = None) -> None:
        """Compact session if it exceeds max_messages.

        Uses LLM-powered summarization when compactor is configured,
        falls back to simple truncation otherwise.
        """
        threshold = max_messages or self.max_messages
        if len(session["messages"]) <= threshold:
            return

        old_count = len(session["messages"])
        keep = threshold // 2

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
                kept = session["messages"][-keep:]
                summary = f"[Compacted {len(to_summarize)} messages]"
            else:
                kept = session["messages"][-keep:]
        else:
            to_summarize = session["messages"][:-keep]
            kept = session["messages"][-keep:]
            summary = f"[Compacted {len(to_summarize)} messages]"

        session["messages"] = [{"role": "system", "content": summary}] + kept

        session["compaction_history"].append({
            "before_count": old_count,
            "after_count": len(session["messages"]),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.store.save_session(session)
        logger.info("Compacted session %s: %d → %d messages",
                    session.get("id"), old_count, len(session["messages"]))

    def _evict_old_session_locks(self) -> None:
        """Evict least-recently-used session locks if we exceed the limit."""
        if len(self._session_locks) <= self._max_session_locks:
            return
        # Evict oldest 20% by last access time (true LRU)
        to_evict = int(self._max_session_locks * 0.2)
        now = time.monotonic()
        # Sort by access time, oldest first; missing entries get epoch 0
        sorted_keys = sorted(
            self._session_locks.keys(),
            key=lambda k: self._session_access.get(k, 0.0),
        )
        for k in sorted_keys[:to_evict]:
            self._session_locks.pop(k, None)
            self._session_access.pop(k, None)

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

    async def list_background_runs(self, session_id: str) -> list[dict]:
        """List background runs for a session."""
        return self.store.list_runs(session_id=session_id)
