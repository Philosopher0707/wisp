"""Unified task and result types for all multi-agent systems in Wisp.

This is the single source of truth for SubagentContract, SubagentResult, and
OrchestratorEvent. All other modules (protocol.py, subagent.py, subagent_runner.py)
alias their types here.

Migration Guide (v3)
--------------------
**Old (deprecated):**
    from wisp.subagent import SubagentTask, SubagentResult
    from wisp.subagent_runner import SubagentRunner

**New (unified):**
    from wisp.multi_agent import SubagentContract, SubagentResult, SubagentOrchestrator

    orch = SubagentOrchestrator(parent_agent=my_agent)
    result = await orch.run(SubagentContract(task="Audit auth.py"))
    results = await orch.run_parallel([contract1, contract2])
    result = await orch.run_map_reduce(task="Review", items=[...], mapper=..., reducer=...)
    result = await orch.run_vote(task="Is this safe?", agents=[...])
    result = await orch.run_chain([contract1, contract2])

Key Changes
-----------
- ``SubagentTask`` → ``SubagentContract`` (same fields, clearer name)
- ``SubagentRunner`` → ``SubagentOrchestrator`` (unified API)
- ``run_parallel`` replaces ``run_subagents`` and ``run_swarm``
- Token budget tracking: ``set_global_token_budget()``, ``get_tokens_consumed()``
- Composable patterns: ``run_map_reduce``, ``run_vote``, ``run_chain``
"""

from __future__ import annotations

import uuid
import time as _time_module
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


#: Absolute ceiling for SubagentContract.max_retries (G1E §28). A retry
#: re-executes a whole agent task; unbounded or huge values are an
#: adversarial multiplier. Callers declare within [0, cap]; anything
#: outside is clamped fail-closed (garbage coerces to 0 = no retry).
MAX_SUBAGENT_RETRIES = 5


def _new_id() -> str:    return str(uuid.uuid4())[:8]


def _now_ts() -> float:
    return _time_module.monotonic()


# ── Contract ──────────────────────────────────────────────────────────────


@dataclass
class SubagentContract:
    """Single source of truth for all subagent invocations.

    Merges fields from the legacy subagent system (subagent.py),
    the parallel runner (subagent_runner.py), and the swarm orchestrator.
    """

    # ── Identity ──
    name: str = "subagent"
    """Human-readable identifier for this subagent instance."""

    role: str = "generalist"
    """Agent role — maps to ROLE_CONFIGS in roles.py."""

    # ── Metadata (P9) ──
    metadata: dict[str, Any] = field(default_factory=dict)
    """Free-form per-invocation metadata.

    Added in migration P9. `subagent_orchestrator.py:1316` writes
    `task.metadata["_budget"]` and `_runner._budget_from_contract` reads
    `contract.metadata["_budget"]`, but this class had **no `metadata` field**
    — so the *read* on line 1316 raised `AttributeError` the first time a DAG
    node declared a budget, and the declared budget never applied. That is
    Phase 10's F1 (audit-2026-08-24 item 11, "honor metadata budget")
    reappearing in a second location.
    """

    # ── Task ──
    task: str = ""
    """The instruction / prompt given to the subagent."""

    system_prompt: Optional[str] = None
    """Override the default role-based system prompt."""

    # ── Tools & Capabilities ──
    tools: list[str] = field(default_factory=lambda: ["all"])
    """Allowed tool names. ["all"] means inherit parent's full toolset."""

    allowed_skills: list[str] = field(default_factory=list)
    """Skill names the subagent may use."""

    # ── Budgets ──
    max_iterations: int = 15
    """Maximum agent loop iterations before forced stop."""

    timeout_seconds: float = 120.0
    """Hard wall-clock timeout."""

    max_tokens: Optional[int] = None
    """Hard token budget (enforced by context trim). None = inherit from parent."""

    max_input_tokens: Optional[int] = None
    """Per-contract input token limit. None = no limit."""

    max_output_tokens: Optional[int] = None
    """Per-contract output token limit. None = no limit."""

    max_output_chars: int = 8000
    """Truncate subagent output to this length before returning to parent."""

    # ── Output ──
    output_format: str = "text"
    """How the subagent should format its final answer: text | json | markdown | report."""

    output_schema: Optional[dict] = None
    """JSON schema for validating structured output."""

    auto_retry_parse: bool = True
    """If True and output_schema is set, retry once on schema validation failure."""

    # ── Environment ──
    model: Optional[str] = None
    """Ollama model override. None = inherit parent's model."""

    workspace: Optional[str] = None
    """Working directory. None = inherit parent's workspace."""

    worktree_isolated: bool = False  # role gating decides actual isolation

    """Run in an isolated git worktree. When False the subagent shares the workspace."""

    auto_approve: bool = False
    """Auto-approve dangerous tool calls without user consent.

    Security: defaults to False so subagents spawned by untrusted
    input (e.g. LLM-generated plans) require human approval.
    Only set to True when the caller explicitly opts in.
    """

    # ── Observability ──
    progress_callback: Optional[Callable[[OrchestratorEvent], None]] = None
    """Optional callback receiving real-time progress events."""

    # ── Backward compat: subagent.py fields ──
    system_prompt_extra: str = ""
    """Additional system prompt text appended after the default."""

    # ── Backward compat: subagent_runner.py fields ──
    prompt: str = ""
    """Alias for task — kept for backward compatibility."""

    context_files: list[str] = field(default_factory=list)
    """Specific file paths to mention in the subagent's context."""

    _subagent_depth: int = 0
    """Current nesting depth. Prevents recursive subagent spawning beyond max_depth."""

    _subagent_branch_count: int = 0
    """Number of subagents spawned by this agent. Prevents exponential explosion."""

    retry_count: int = 0
    """Logical retry budget CONSUMED by this task (G1E §5). Every
    subagent-layer re-execution (parallel-guard, timeout, validation,
    outer retry, pattern retry) reconstructs the contract with +1 and
    refuses when retry_count >= max_retries. Monotonic: no path resets,
    reconstructs, or increments the cap to evade the limit."""

    max_retries: int = 0
    """Logical retry budget for this task: max EXTRA executions beyond the
    first (0 = run once, no automatic retry). Clamped to
    [0, MAX_SUBAGENT_RETRIES] in __post_init__."""

    max_memory_mb: int = 2048
    """Maximum memory limit for process subagents (Unix only)."""

    _cache_context: str = ""
    """Session-scoped context for cache key isolation. Set by orchestrator."""

    _shared_context: Any = None
    """SharedContext instance for inter-subagent communication in parallel runs."""

    def __post_init__(self):
        """Normalize backward-compat aliases; clamp retry budget (G1E §28).

        max_retries is strictly typed and bounded: only real numbers
        coerce (bool/int/float); strings, None, and objects coerce
        fail-closed to 0 (so "false"/"10" can never mint budget, §28).
        Negatives clamp to 0, huge values to MAX_SUBAGENT_RETRIES.
        retry_count floors at 0 (a child must never inherit credit).
        """
        if self.prompt and not self.task:
            self.task = self.prompt
        if isinstance(self.max_retries, bool):
            coerced = int(self.max_retries)
        elif isinstance(self.max_retries, (int, float)):
            coerced = int(self.max_retries)
        else:
            coerced = 0
        object.__setattr__(self, "max_retries",
                           max(0, min(coerced, MAX_SUBAGENT_RETRIES)))
        used = self.retry_count
        if isinstance(used, bool):
            used = int(used)
        elif not isinstance(used, (int, float)):
            used = 0
        object.__setattr__(self, "retry_count", max(0, int(used)))
        if self.system_prompt_extra and self.system_prompt is None:
            # Build a default system prompt from role + extra
            pass  # Will be handled by orchestrator


# ── Backward compat alias ──
SubagentTask = SubagentContract


# ── Result ─────────────────────────────────────────────────────────────────


@dataclass
class SubagentResult:
    """Structured output from a completed (or failed/timed-out) agent task."""

    task_id: str = ""
    success: bool = False
    output: str = ""
    error: Optional[str] = None
    files_changed: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    iterations_used: int = 0
    retry_count: int = 0
    timed_out: bool = False
    hit_iteration_limit: bool = False
    worktree_patch: Optional[str] = None
    """git diff patch of uncommitted changes made in the isolated worktree before it was destroyed."""

    patch_applied: bool = False
    """Whether the worktree patch was successfully applied to the parent workspace."""

    # ── Audit trail ──
    messages: list[dict] = field(default_factory=list)
    """Full conversation history for replay / debugging."""

    tool_calls: list[dict] = field(default_factory=list)
    """Summary of tool calls made (name + arg preview per call)."""

    # ── Token usage ──
    tokens_used: int = 0
    """Total tokens consumed (input + output)."""

    input_tokens: int = 0
    """Input tokens consumed."""

    output_tokens: int = 0
    """Output tokens consumed."""

    model_used: str = ""
    """Model identifier used for this task."""

    # ── Structured output ──
    validated_output: Optional[Any] = None
    """Parsed JSON object if output_schema was provided and validation succeeded."""

    # ── Backward compat: subagent_runner.py fields ──
    spec: Any = None
    """The contract/spec that produced this result."""

    duration_seconds: float = 0.0
    """Alias for elapsed_seconds — kept for backward compatibility."""

    session_id: str = ""
    """Session ID persisted to the session store for audit."""

    def __post_init__(self):
        """Normalize backward-compat aliases."""
        if self.duration_seconds and not self.elapsed_seconds:
            self.elapsed_seconds = self.duration_seconds


# ── Orchestrator Event (for streaming) ────────────────────────────────────


class EventKind:
    """Orchestrator event type constants."""
    PLANNING = "planning"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_RETRY = "task_retry"
    DONE = "done"


@dataclass
class OrchestratorEvent:
    """Streaming progress event emitted by the orchestrator.

    Consumers (CLI, WebSocket, HTTP polling) receive these via the
    progress_callback and convert them to their native formats.
    """

    task_id: str = ""
    event_type: str = EventKind.TASK_STARTED
    payload: dict[str, Any] = field(default_factory=dict)

    def to_ws_message(self) -> dict[str, Any] | None:
        """Convert to a WebSocket message dict, or None if not needed."""
        kind = self.event_type
        p = self.payload

        if kind == EventKind.TASK_STARTED:
            return {
                "type": "subagent_start",
                "subagent_id": self.task_id,
                "name": p.get("role", p.get("name", "")),
                "description": p.get("description", ""),
                # The child's real authority surface, as computed by the
                # runner at spawn time. Absent payload keys degrade to an
                # honest "unknown" rather than a permissive default: an
                # emitter that predates this field must not read as
                # "unconstrained".
                "capabilities": p.get("capabilities"),
                "unbounded": p.get("unbounded", False),
            }
        elif kind == EventKind.TASK_PROGRESS:
            return {
                "type": "subagent_progress",
                "subagent_id": self.task_id,
                "progress": p.get("progress", ""),
            }
        elif kind == EventKind.TASK_COMPLETED:
            return {
                "type": "subagent_complete",
                "subagent_id": self.task_id,
                "files_changed": p.get("files_changed", []),
                "duration_ms": int(p.get("elapsed", 0) * 1000),
            }
        elif kind == EventKind.TASK_FAILED:
            return {
                "type": "subagent_fail",
                "subagent_id": self.task_id,
                "error": p.get("error", ""),
            }
        return None
