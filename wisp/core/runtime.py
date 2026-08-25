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

from wisp.approval_state import ApprovalSessionState, SessionPolicy
from wisp.core.events import normalize_event

logger = logging.getLogger(__name__)


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

    # Cached core instance — avoids rebuilding system prompt caches every turn
    _core_cache: Any = field(default=None, repr=False)
    _core_fingerprint: str | None = field(default=None, repr=False)
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
        if not model or not isinstance(model, str):
            raise ValueError(f"Invalid model: {model!r}")
        if not workspace or not isinstance(workspace, str):
            raise ValueError(f"Invalid workspace: {workspace!r}")

        # Store boundary is unannotated; validate shape before trusting it.
        loaded: Any = self.store.load_session(session_id)
        if isinstance(loaded, dict) and "messages" in loaded:
            session: dict[str, Any] = loaded
            _stringify_tool_call_arguments(session["messages"])
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

            # ── Auto-delegation check ─────────────────────────────────────
            delegation_context: str | None = None
            if (
                self.orchestrator is not None
                and getattr(self._get_core().config, "auto_delegate", True)
            ):
                sub_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
                delegate_task = asyncio.create_task(
                    self._maybe_delegate(
                        prompt, session, self._get_core().config,
                        progress_queue=sub_events,
                    )
                )
                # Interleave child lifecycle events into the turn stream
                # while delegation runs, so the terminal shows 🧬/✓ lines
                # as children start and finish instead of nothing.
                while not delegate_task.done():
                    try:
                        yield await asyncio.wait_for(sub_events.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                while not sub_events.empty():
                    yield sub_events.get_nowait()
                try:
                    delegation_context = delegate_task.result()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # The delegation probe must never abort the turn before
                    # persistence — degrade to a direct answer instead.
                    logger.warning("Delegation probe failed: %s", exc)
                    yield {
                        "type": "system",
                        "message": "Subagent delegation crashed — answering directly",
                        "timestamp": time.time(),
                    }
                    delegation_context = None

                if delegation_context:
                    failed = delegation_context.startswith("[DELEGATION FAILED]")
                    if failed:
                        yield {
                            "type": "system",
                            "message": (
                                "Subagent delegation failed — answering directly"
                            ),
                            "timestamp": time.time(),
                        }

            # Get cached core (warm-start, thread-safe)
            core = self._get_core()

            # Inject delegation results as system context
            if delegation_context:
                session["messages"].append({
                    "role": "system",
                    "content": delegation_context,
                })

            assistant_content: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []
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
                    import json

                    # Providers require assistant tool_calls arguments as a
                    # JSON string; ids must survive persist/reload so each
                    # result matches its call.
                    pending_call_ids: list[str] = []
                    for tc in tool_calls:
                        args = tc.get("arguments", {})
                        if not isinstance(args, str):
                            args = json.dumps(args)
                        call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                        pending_call_ids.append(call_id)
                        session["messages"].append({
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": tc.get("name", ""),
                                    "arguments": args,
                                },
                            }],
                        })

                    for tr in tool_results:
                        result = tr.get("result", "")
                        if isinstance(result, dict):
                            content = json.dumps(result)
                        else:
                            content = str(result)
                        call_id = tr.get("tool_call_id") or (
                            pending_call_ids.pop(0) if pending_call_ids else ""
                        )
                        session["messages"].append({
                            "role": "tool",
                            "tool_call_id": call_id,
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

    async def _maybe_delegate(
        self, prompt: str, session: dict[str, Any], config: Any,
        progress_queue: "asyncio.Queue[dict[str, Any]] | None" = None,
    ) -> str | None:
        """Analyze prompt and auto-delegate to subagents if warranted.

        When *progress_queue* is given, child lifecycle events are pushed
        onto it as flattened dicts so the turn stream can render them live.

        Returns delegation context string to inject as system message,
        or None if delegation is not needed or fails.
        """
        if not prompt or len(prompt) < 20:
            return None

        try:
            from wisp.multi_agent.delegation import get_delegation_analyzer
            from wisp.multi_agent.task import SubagentContract

            threshold = getattr(config, "delegation_threshold", 0.45)
            analyzer = get_delegation_analyzer()

            # Build LLM call wrapper for hybrid classification.
            # Bounded: a stalled classify must not hang the whole turn
            # before the main stream even starts. On timeout we skip
            # delegation and answer directly.
            classify_timeout = float(
                getattr(config, "delegation_classify_timeout", 10.0)
            )

            async def _llm_classify(classify_prompt: str) -> str:
                core = self._get_core()
                messages = [{"role": "user", "content": classify_prompt}]
                events = []

                async def _collect() -> str:
                    async for event in core.provider.generate_stream_events(messages=messages):
                        events.append(event)
                        if event.get("type") == "done":
                            break
                    return "".join(
                        e.get("text", "") for e in events if e.get("type") == "content"
                    )

                try:
                    return await asyncio.wait_for(_collect(), timeout=classify_timeout)
                except asyncio.TimeoutError:
                    logger.info(
                        "Delegation classify timed out after %.0fs — skipping delegation",
                        classify_timeout,
                    )
                    return ""

            # Dead-air rule: analysis can take seconds on a slow endpoint;
            # after 1s tell the user it's thinking, not hung.
            if progress_queue is not None:
                async def _analyze_indicator() -> None:
                    await asyncio.sleep(1.0)
                    progress_queue.put_nowait({
                        "type": "system",
                        "message": "Analyzing whether to delegate...",
                        "timestamp": time.time(),
                    })

                indicator_task = asyncio.create_task(_analyze_indicator())
            else:
                indicator_task = None

            try:
                signal = await analyzer.analyze_with_llm(prompt, _llm_classify)
            finally:
                if indicator_task is not None:
                    indicator_task.cancel()

            if not signal.should_delegate or signal.confidence < threshold:
                return None

            logger.info(
                "Auto-delegating: %s (confidence=%.2f, reason=%s)",
                prompt[:80], signal.confidence, signal.reason,
            )

            # Build contracts from suggested contracts
            contracts = []
            for sc_dict in signal.suggested_contracts:
                sc_dict = dict(sc_dict)
                sc_dict.setdefault("_cache_context", session.get("id", ""))
                contracts.append(SubagentContract(**sc_dict))

            if not contracts:
                return None

            # Announce now — after the verdict, before children launch.
            if progress_queue is not None:
                progress_queue.put_nowait({
                    "type": "system",
                    "message": "Auto-delegating to subagents...",
                    "timestamp": time.time(),
                })

            # Surface child lifecycle on the terminal: same conversion the
            # explicit spawn path uses, pushed onto the caller's queue.
            if progress_queue is not None:
                from wisp.tool_executor import orchestrator_event_to_agent_event

                def _stream_progress(orch_ev: Any) -> None:
                    agent_ev = orchestrator_event_to_agent_event(orch_ev)
                    flat = dict(agent_ev.data)
                    flat["type"] = str(agent_ev.type)
                    flat["timestamp"] = time.time()
                    progress_queue.put_nowait(flat)

                for contract in contracts:
                    contract.progress_callback = _stream_progress

            # Run subagents — each contract enforces its own deadline (Phase 1A)
            results = await self.orchestrator.run_parallel(
                contracts, max_concurrent=3,
            )

            # Build context from results
            succeeded = [r for r in results if r.success]
            failed = [r for r in results if not r.success]

            if not succeeded:
                first_err = failed[0].error if failed else "unknown"
                logger.warning(
                    "All %d delegation subagents failed", len(results),
                )
                # A marker string, not None: the caller surfaces it on the
                # terminal and the model learns why no subagent results
                # arrived — instead of silently re-answering alone.
                return (
                    f"[DELEGATION FAILED] All {len(results)} subagents failed "
                    f"({first_err}). Answer the user directly from your own "
                    "knowledge; do not wait for subagent results."
                )

            parts = [
                "[AUTO-DELEGATION RESULTS]",
                f"The following research was completed by specialized subagents "
                f"({len(succeeded)} succeeded, {len(failed)} failed):\n",
            ]
            for r in succeeded:
                parts.append(f"## {r.task_id}")
                parts.append(r.output[:3000])
                parts.append("")

            if failed:
                parts.append("## Failures (ignore these approaches)")
                for r in failed:
                    parts.append(f"- {r.task_id}: {r.error or 'unknown'}")

            context = "\n".join(parts)
            logger.info(
                "Delegation complete: %d/%d succeeded, %d chars of context",
                len(succeeded), len(results), len(context),
            )
            return context

        except Exception:
            logger.exception("Auto-delegation failed, proceeding without")
            return None

    def _get_core(self) -> Any:
        """Get cached core instance, creating if needed.

        Thread-safe: uses _core_lock to prevent race conditions.
        Invalidates cache when config fingerprint changes.
        """
        with self._core_lock:
            current_fp = None
            if self.config is not None and hasattr(self.config, "fingerprint"):
                current_fp = self.config.fingerprint()
            if self._core_cache is None or self._core_fingerprint != current_fp:
                self._core_cache = self.core_factory()
                self._core_fingerprint = current_fp
            return self._core_cache

    def get_core_provider(self) -> Any:
        """Return the provider from the cached core, if available."""
        core = self._get_core()
        return getattr(core, "provider", None)

    def invalidate_core_cache(self) -> None:
        """Invalidate cached core — call when config/workspace changes.

        Thread-safe: uses _core_lock.
        """
        with self._core_lock:
            self._core_cache = None

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
