"""AgentRuntime — stateful session lifecycle manager.

Replaces: scattered session management in WispAgentCore.

Design:
  - Owns sessions, compaction, background runs
  - Delegates turn loop to injected stateless core
  - Uses injected store, security, extensions, telemetry
  - Caches core instance for performance (warm-start)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable

from wisp.core.events import normalize_event

logger = logging.getLogger(__name__)


@dataclass
class AgentRuntime:
    """Stateful runtime that owns sessions and delegates turns."""

    store: Any
    security: Any
    extensions: Any
    telemetry: Any
    core_factory: Callable[[], Any]

    # Cached core instance — avoids rebuilding system prompt caches every turn
    _core_cache: Any = field(default=None, repr=False)

    async def get_or_create_session(
        self,
        session_id: str,
        model: str,
        workspace: str,
    ) -> dict:
        """Load existing session or create new one."""
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

    async def run_turn(self, session: dict, prompt: str) -> AsyncIterator[dict]:
        """Run one turn, yielding events.

        Guarantees session consistency even if the turn aborts:
        - User message is always added
        - Assistant + tool messages are added on success
        - Session is saved after every turn
        - Compaction runs automatically before turn if needed
        """
        import time
        start = time.time()

        # Auto-compact before turn to prevent context overflow
        await self.maybe_compact(session)

        # Add user message
        session["messages"].append({"role": "user", "content": prompt})

        # Get cached core (warm-start)
        core = self._get_core()

        assistant_content: list[str] = []
        tool_calls: list[dict] = []
        tool_results: list[dict] = []
        turn_succeeded = False

        try:
            async for raw_event in core.turn(session, prompt):
                # Normalize to canonical AgentEvent, then flatten for backward compatibility
                canonical = normalize_event(raw_event).to_dict()
                # Flatten: merge data fields into top level for easy access
                event = dict(canonical.get("data", {}))
                event["type"] = canonical["type"]
                event["timestamp"] = canonical.get("timestamp", 0.0)
                yield event

                etype = event.get("type")
                if etype == "content":
                    assistant_content.append(event.get("text", ""))
                elif etype == "tool_call":
                    tool_calls.append(event)
                elif etype == "tool_result":
                    tool_results.append(event)

            turn_succeeded = True

        except Exception as exc:
            logger.exception("Turn failed for session %s", session.get("id"))
            yield {
                "type": "error",
                "message": f"Turn aborted: {exc}",
                "recoverable": True,
            }

        finally:
            # Always record what happened in the session, even on failure
            if tool_calls or tool_results:
                # Build OpenAI-style tool_calls + tool result messages
                for tc in tool_calls:
                    session["messages"].append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                            "type": "function",
                            "function": {
                                "name": tc.get("name", ""),
                                "arguments": str(tc.get("arguments", {})),
                            },
                        }],
                    })

                for tr in tool_results:
                    session["messages"].append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_call_id", ""),
                        "content": str(tr.get("result", "")),
                    })

            if assistant_content:
                session["messages"].append({
                    "role": "assistant",
                    "content": "".join(assistant_content),
                })

            # Update timestamp and save
            session["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.store.save_session(session)

            # Record telemetry
            latency_ms = (time.time() - start) * 1000
            self.telemetry.record_turn(
                latency_ms=latency_ms,
                prompt_tokens=len(prompt) // 4,
                completion_tokens=len("".join(assistant_content)) // 4,
            )

    def _get_core(self) -> Any:
        """Get cached core instance, creating if needed."""
        if self._core_cache is None:
            self._core_cache = self.core_factory()
        return self._core_cache

    def invalidate_core_cache(self) -> None:
        """Invalidate cached core — call when config/workspace changes."""
        self._core_cache = None

    async def maybe_compact(self, session: dict, max_messages: int = 50) -> None:
        """Compact session if it exceeds max_messages."""
        if len(session["messages"]) <= max_messages:
            return

        old_count = len(session["messages"])
        keep = max_messages // 2
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
