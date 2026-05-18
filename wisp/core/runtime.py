"""AgentRuntime — stateful session lifecycle manager.

Replaces: scattered session management in WispAgentCore.

Design:
  - Owns sessions, compaction, background runs
  - Delegates turn loop to injected stateless core
  - Uses injected store, security, extensions, telemetry
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentRuntime:
    """Stateful runtime that owns sessions and delegates turns."""

    store: Any
    security: Any
    extensions: Any
    telemetry: Any
    core_factory: Callable[[], Any]

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
        """Run one turn, yielding events."""
        import time
        start = time.time()

        # Add user message
        session["messages"].append({"role": "user", "content": prompt})

        # Delegate to stateless core
        core = self.core_factory()
        assistant_content = []

        async for event in core.turn(session, prompt):
            yield event
            if event.get("type") == "content":
                assistant_content.append(event.get("text", ""))

        # Add assistant message
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

    async def maybe_compact(self, session: dict, max_messages: int = 50) -> None:
        """Compact session if it exceeds max_messages."""
        if len(session["messages"]) <= max_messages:
            return

        # Simple compaction: summarize old messages, keep recent
        old_count = len(session["messages"])
        # Keep last max_messages // 2 messages, summarize the rest
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

    async def start_background_run(
        self,
        session_id: str,
        prompt: str,
        model: str,
    ) -> str:
        """Start a background run. Returns run ID."""
        # Ensure session exists
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
