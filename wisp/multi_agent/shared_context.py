"""SharedContext — inter-subagent communication for parallel execution.

Allows parallel subagents to:
- Share intermediate findings and discoveries
- Coordinate work to avoid duplicate file reads
- Broadcast progress updates to siblings
- Access a shared knowledge store

Thread-safe via asyncio.Lock. Designed for use within a single
``run_parallel()`` or ``run_map_reduce()`` call — not persisted
across orchestrator runs.

Usage:
    ctx = SharedContext()
    contract_a = SubagentContract(task="...", _shared_context=ctx)
    contract_b = SubagentContract(task="...", _shared_context=ctx)

    # Inside subagent A's system prompt:
    # "You can share findings with siblings via the shared context.
    #  Use the share_finding tool to broadcast discoveries."

The SharedContext is injected into the system prompt and made
available to the subagent as a tool callback.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Finding:
    """A single finding shared by a subagent."""

    agent_id: str
    key: str
    value: str
    timestamp: float = field(default_factory=time.monotonic)
    tags: list[str] = field(default_factory=list)


class SharedContext:
    """Thread-safe shared knowledge store for parallel subagents.

    Each subagent in a parallel run can:
    - ``post(key, value)`` — share a finding with siblings
    - ``get(key)`` — retrieve a finding by key
    - ``query(tag)`` — find all findings with a given tag
    - ``all_findings()`` — get everything posted so far
    - ``claim_file(path)`` — claim a file for reading (avoid duplicate reads)
    - ``is_claimed(path)`` — check if a file is already claimed

    The context is read-only from the subagent's perspective — it can
    read all findings but the system prompt instructs it to check
    before duplicating work.
    """

    def __init__(self):
        self._findings: dict[str, Finding] = {}
        self._file_claims: dict[str, str] = {}  # path -> agent_id
        self._lock = asyncio.Lock()
        self._subscribers: list[asyncio.Queue] = []

    async def post(self, agent_id: str, key: str, value: str, tags: list[str] | None = None) -> None:
        """Share a finding with all sibling subagents."""
        finding = Finding(
            agent_id=agent_id,
            key=key,
            value=value,
            tags=tags or [],
        )
        async with self._lock:
            self._findings[key] = finding
            # Notify subscribers
            for queue in self._subscribers:
                try:
                    queue.put_nowait(finding)
                except asyncio.QueueFull:
                    pass

    async def get(self, key: str) -> Finding | None:
        """Retrieve a finding by key."""
        async with self._lock:
            return self._findings.get(key)

    async def query(self, tag: str) -> list[Finding]:
        """Find all findings with a given tag."""
        async with self._lock:
            return [f for f in self._findings.values() if tag in f.tags]

    async def all_findings(self) -> list[Finding]:
        """Get all findings posted so far."""
        async with self._lock:
            return list(self._findings.values())

    async def claim_file(self, path: str, agent_id: str) -> bool:
        """Claim a file for reading. Returns True if claim succeeded, False if already claimed."""
        async with self._lock:
            if path in self._file_claims:
                return False
            self._file_claims[path] = agent_id
            return True

    async def is_claimed(self, path: str) -> bool:
        """Check if a file is already claimed by another agent."""
        async with self._lock:
            return path in self._file_claims

    async def file_claims(self) -> dict[str, str]:
        """Get all file claims (path -> agent_id)."""
        async with self._lock:
            return dict(self._file_claims)

    def subscribe(self) -> asyncio.Queue:
        """Subscribe to new findings. Returns a queue that receives Finding objects."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Unsubscribe from findings."""
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def format_for_prompt(self, agent_id: str) -> str:
        """Format the current shared context for injection into a subagent's system prompt.

        Shows findings from OTHER agents (not self) and file claims.
        """
        lines: list[str] = []

        # Findings from siblings
        sibling_findings = [
            f for f in self._findings.values() if f.agent_id != agent_id
        ]
        if sibling_findings:
            lines.append("## Shared Findings from Sibling Agents")
            for f in sibling_findings:
                value_preview = f.value[:500]
                if len(f.value) > 500:
                    value_preview += "..."
                lines.append(f"- **{f.key}** (from {f.agent_id}): {value_preview}")
        else:
            lines.append("## Shared Context")
            lines.append("(no findings shared yet by sibling agents)")

        # File claims
        other_claims = {
            path: aid for path, aid in self._file_claims.items() if aid != agent_id
        }
        if other_claims:
            lines.append("\n## Files Already Being Read by Siblings")
            for path, aid in other_claims.items():
                lines.append(f"- `{path}` (claimed by {aid})")
            lines.append("Avoid re-reading these files — use the shared findings instead.")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize for logging/debugging."""
        return {
            "findings": {
                k: {
                    "agent_id": f.agent_id,
                    "key": f.key,
                    "value": f.value[:200],
                    "tags": f.tags,
                }
                for k, f in self._findings.items()
            },
            "file_claims": dict(self._file_claims),
        }


def build_shared_context_tool_schema() -> dict:
    """Return the tool schema for sharing findings via the shared context."""
    return {
        "type": "function",
        "function": {
            "name": "share_finding",
            "description": (
                "Share a finding or discovery with sibling subagents running in parallel. "
                "Use this to broadcast useful information like file contents, patterns found, "
                "or conclusions reached. Siblings can read this instead of duplicating work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "A short identifier for the finding (e.g., 'auth_module_structure', 'error_in_utils')",
                    },
                    "value": {
                        "type": "string",
                        "description": "The finding content — can be a summary, code snippet, or conclusion",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags for categorization (e.g., ['bug', 'auth'])",
                    },
                },
                "required": ["key", "value"],
            },
        },
    }


def build_shared_context_tool_impl(agent_id: str, ctx: SharedContext):
    """Build the tool implementation for sharing findings.

    Returns an async function that can be called by the tool executor.
    """
    async def _share_finding(key: str, value: str, tags: list[str] | None = None) -> dict:
        await ctx.post(agent_id, key, value, tags)
        return {
            "status": "ok",
            "data": f"Finding '{key}' shared with sibling agents.",
        }

    return _share_finding
