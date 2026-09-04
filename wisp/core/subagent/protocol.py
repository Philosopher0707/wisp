"""Typed IPC protocol for subagent fanout/fanin (Pydantic v2).

This is the invariant boundary between the parent coordinator and child
workers: task frames go out structured, results come back structured.
Free-form conversational markdown never crosses this seam — a child that
cannot produce a valid :class:`SubagentResult` is retried once, then
marked FAILED without touching parent graph state.

Relationship to ``wisp.multi_agent.task``: those dataclasses drive the
legacy runner (full-prompt execution). These models drive the hardened
path: ``TaskFrame`` is assembled zero-shot/minimal-context by the
coordinator, executed by the pool, and reduced by the Reducer Node.
Adapters in ``coordinator.py`` translate between the two worlds.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Coroutine, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class TaskStatus(str, Enum):
    """Terminal states of one child execution."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class ContextChunk(BaseModel):
    """One bounded file/AST slice handed to a child. Nothing else."""

    model_config = {"frozen": True}

    path: str = Field(min_length=1, description="Workspace-relative file path")
    content: str = Field(default="", description="Bounded slice content")
    line_start: int = Field(default=1, ge=1)
    line_end: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _ordered_lines(self) -> "ContextChunk":
        if self.line_end < self.line_start:
            raise ValueError("line_end must be >= line_start")
        return self

    def anchor(self) -> str:
        return f"{self.path}:{self.line_start}-{self.line_end}"


class ExecutionPolicy(BaseModel):
    """Per-task concurrency, time, and retry bounds."""

    model_config = {"frozen": True}

    max_concurrent: int = Field(default=4, ge=1, le=32)
    timeout_s: float = Field(default=60.0, gt=0, le=600)
    max_retries: int = Field(default=1, ge=0, le=3)


class TaskFrame(BaseModel):
    """Zero-shot/minimal-context work order for one child worker."""

    model_config = {"frozen": True}

    task_id: str = Field(min_length=1, description="Unique trace ID")
    task: str = Field(min_length=1, description="Scoped objective, no history")
    role: str = Field(default="generalist", min_length=1)
    allowed_tools: list[str] = Field(default_factory=lambda: ["read_file"])
    context: list[ContextChunk] = Field(default_factory=list)
    token_budget: int = Field(default=4000, gt=0, le=128000)
    policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)

    @field_validator("allowed_tools")
    @classmethod
    def _nonempty_tools(cls, tools: list[str]) -> list[str]:
        if not tools:
            raise ValueError("allowed_tools must list at least one tool")
        return tools

    def estimated_tokens(self) -> int:
        """Chars/4 heuristic over task + chunks (matches repo convention)."""
        total = len(self.task)
        for chunk in self.context:
            total += len(chunk.content)
        return max(1, total // 4)

    def render_prompt(self) -> str:
        """Deterministic zero-shot prompt: objective + allowlist + chunks."""
        lines = [
            f"[Task {self.task_id} | role={self.role}]",
            self.task,
            "",
            "Allowed tools: " + ", ".join(self.allowed_tools),
            "Respond with a SubagentResult JSON object only.",
        ]
        for chunk in self.context:
            lines += ["", f"--- {chunk.anchor()} ---", chunk.content]
        return "\n".join(lines)


class Finding(BaseModel):
    """One typed observation with a verifiable file anchor."""

    model_config = {"frozen": True}

    kind: Literal["vulnerability", "bug", "smell", "note", "patch"] = "note"
    summary: str = Field(min_length=1)
    path: str = Field(min_length=1)
    line_start: int = Field(ge=1, default=1)
    line_end: int = Field(ge=1, default=1)

    @model_validator(mode="after")
    def _ordered_lines(self) -> "Finding":
        if self.line_end < self.line_start:
            raise ValueError("line_end must be >= line_start")
        return self

    def anchor(self) -> str:
        return f"{self.path}:{self.line_start}-{self.line_end}"

    def identity(self) -> tuple[str, str, str, int, int]:
        """Dedup key: same kind+summary+anchor is the same finding."""
        return (self.kind, self.summary, self.path, self.line_start, self.line_end)


class PatchProposal(BaseModel):
    """A bounded replacement proposal over a line range."""

    model_config = {"frozen": True}

    path: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    replacement: str = Field(default="")

    @model_validator(mode="after")
    def _ordered_lines(self) -> "PatchProposal":
        if self.line_end < self.line_start:
            raise ValueError("line_end must be >= line_start")
        return self


def patches_conflict(a: PatchProposal, b: PatchProposal) -> bool:
    """True when two proposals overlap on the same file (both must win check)."""
    if a.path != b.path:
        return False
    return a.line_start <= b.line_end and b.line_start <= a.line_end


class TokenUsage(BaseModel):
    """Exact prompt/completion metrics for one child execution."""

    model_config = {"frozen": True}

    prompt: int = Field(ge=0, default=0)
    completion: int = Field(ge=0, default=0)

    @property
    def total(self) -> int:
        return self.prompt + self.completion


class SubagentResult(BaseModel):
    """Invariant output contract: every child returns exactly this."""

    model_config = {"frozen": True}

    task_id: str = Field(min_length=1)
    status: TaskStatus = TaskStatus.FAILED
    findings: list[Finding] = Field(default_factory=list)
    patches: list[PatchProposal] = Field(default_factory=list)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    error: str = Field(default="")
    elapsed_s: float = Field(ge=0.0, default=0.0)

    @classmethod
    def timeout(cls, task_id: str, elapsed_s: float) -> "SubagentResult":
        return cls(task_id=task_id, status=TaskStatus.TIMEOUT,
                   error=f"wall-clock timeout after {elapsed_s:.1f}s", elapsed_s=elapsed_s)

    @classmethod
    def budget_exceeded(cls, task_id: str, detail: str) -> "SubagentResult":
        return cls(task_id=task_id, status=TaskStatus.BUDGET_EXCEEDED, error=detail)

    @classmethod
    def failure(cls, task_id: str, error: str, elapsed_s: float = 0.0) -> "SubagentResult":
        return cls(task_id=task_id, status=TaskStatus.FAILED, error=error, elapsed_s=elapsed_s)


class ConflictPair(BaseModel):
    """Two patch proposals that overlap on the same file."""

    model_config = {"frozen": True}

    first: PatchProposal
    second: PatchProposal


class ReducedResult(BaseModel):
    """Atomic fanin product: merged findings, patches, conflicts, totals."""

    model_config = {"frozen": True}

    findings: list[Finding] = Field(default_factory=list)
    patches: list[PatchProposal] = Field(default_factory=list)
    conflicts: list[ConflictPair] = Field(default_factory=list)
    prompt_tokens: int = Field(ge=0, default=0)
    completion_tokens: int = Field(ge=0, default=0)
    succeeded: int = Field(ge=0, default=0)
    failed: int = Field(ge=0, default=0)
    timed_out: int = Field(ge=0, default=0)
    budget_exceeded: bool = False
    elapsed_s: float = Field(ge=0.0, default=0.0)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


# ── Telemetry sink ────────────────────────────────────────────────────
# Implemented by wisp/ui/telemetry.py (event bus + worker matrix) and by
# test fakes. The pool/coordinator depend only on this Protocol.


class WorkerEvent(BaseModel):
    """One observable lifecycle moment of a child worker."""

    model_config = {"frozen": True}

    worker_id: str
    role: str
    event: Literal["started", "tool", "progress", "settled"] = "progress"
    detail: str = ""
    elapsed_s: float = 0.0
    tokens: int = 0


TelemetrySink = Callable[[WorkerEvent], Coroutine[Any, Any, None]]
