"""Parent coordinator: decomposition → frames → pool → atomic reduction.

The coordinator is the single parent-graph node for subagent work:
  - Task frames are assembled zero-shot: objective + role allowlist +
    explicit context chunks only. Parent conversation history NEVER enters
    a frame (see :meth:`Coordinator.build_frame`).
  - Fanout runs through :class:`BoundedWorkerPool` (semaphore, timeouts,
    cascade cancellation, telemetry).
  - Results failing :class:`SubagentResult` validation are retried once
    with a repair note; persistent garbage becomes FAILED without touching
    parent state (reduction is pure).
  - :class:`Reducer` atomically merges findings (dedupe by identity),
    collects patches, and flags overlapping-patch conflicts.

Legacy backend: :func:`legacy_orchestrator_worker` adapts a
``SubagentOrchestrator`` (``wisp.multi_agent``) to the pool's worker
contract by requesting JSON output against the result schema.
"""

from __future__ import annotations

import itertools
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from wisp.core.subagent.pool import BoundedWorkerPool, WorkerFn
from wisp.core.subagent.protocol import (
    ConflictPair,
    ContextChunk,
    ExecutionPolicy,
    Finding,
    PatchProposal,
    ReducedResult,
    SubagentResult,
    TaskFrame,
    TaskStatus,
    TelemetrySink,
)

logger = logging.getLogger(__name__)

# Role → tool allowlist. Unknown roles fail closed to the read-only set;
# "generalist" inherits the legacy ["all"] contract explicitly.
ROLE_TOOLS: dict[str, list[str]] = {
    "explorer": ["read_file", "list_files", "search_codebase", "search_symbols"],
    "auditor": ["read_file", "list_files", "search_codebase", "search_symbols",
                "lsp_diagnostics", "lsp_references"],
    "patcher": ["read_file", "list_files", "search_codebase", "edit_file",
                "edit_file_multi", "git_diff", "git_status"],
    "generalist": ["all"],
}
READ_ONLY_TOOLS = ["read_file", "list_files", "search_codebase", "search_symbols"]


def tools_for_role(role: str) -> list[str]:
    """Allowlist for a role; unknown roles fail closed to read-only."""
    return list(ROLE_TOOLS.get(role, READ_ONLY_TOOLS))


@dataclass
class CoordinatorConfig:
    """Tuning for one coordinator instance."""

    default_policy: ExecutionPolicy = field(default_factory=ExecutionPolicy)
    default_token_budget: int = 4000
    global_token_budget: int | None = None
    validation_retries: int = 1


class Coordinator:
    """Builds frames, fans out through the pool, reduces atomically."""

    def __init__(
        self,
        worker_fn: WorkerFn,
        config: CoordinatorConfig | None = None,
        telemetry: TelemetrySink | None = None,
    ) -> None:
        self._config = config or CoordinatorConfig()
        self._pool = BoundedWorkerPool(worker_fn=worker_fn, telemetry=telemetry)
        self._telemetry = telemetry

    def build_frame(
        self,
        objective: str,
        role: str = "generalist",
        context: list[ContextChunk] | None = None,
        task_id: str | None = None,
        token_budget: int | None = None,
        allowed_tools: list[str] | None = None,
    ) -> TaskFrame:
        """Assemble an isolated task frame.

        Takes ONLY explicit inputs — there is deliberately no
        ``parent_messages`` parameter, so full history inheritance is a
        type error, not a discipline problem. Callers pass pre-selected
        chunks (file slices, AST excerpts); the frame carries nothing else.
        """
        budget = token_budget or self._config.default_token_budget
        frame = TaskFrame(
            task_id=task_id or uuid.uuid4().hex[:12],
            task=objective,
            role=role,
            allowed_tools=allowed_tools or tools_for_role(role),
            context=list(context or []),
            token_budget=budget,
            policy=self._config.default_policy,
        )
        if frame.estimated_tokens() > budget:
            raise ValueError(
                f"frame exceeds token budget ({frame.estimated_tokens()} > {budget}); "
                "narrow the objective or drop context chunks"
            )
        return frame

    async def fanout(self, frames: list[TaskFrame]) -> ReducedResult:
        """Dispatch frames, retry validation failures once, reduce."""
        started = time.monotonic()
        if not frames:
            return ReducedResult(elapsed_s=0.0)
        results = await self._pool.run(frames)
        results = await self._repair_invalid(frames, results)
        return Reducer.reduce(
            results,
            elapsed_s=time.monotonic() - started,
            global_budget=self._config.global_token_budget,
        )

    async def _repair_invalid(
        self, frames: list[TaskFrame], results: list[SubagentResult]
    ) -> list[SubagentResult]:
        """One repair retry for validation FAILED results.

        Rebuilds the frame with a repair note appended to the objective
        (bounded: original task text is preserved, note is fixed-size).
        Anything still invalid stays FAILED — parent state untouched.
        """
        if self._config.validation_retries < 1:
            return results
        by_id = {f.task_id: f for f in frames}
        repaired: list[SubagentResult] = []
        for result in results:
            if (result.status is TaskStatus.FAILED
                    and "validation" in result.error
                    and result.task_id in by_id):
                frame = by_id[result.task_id]
                retry_frame = frame.model_copy(update={
                    "task": frame.task
                    + "\n\nYour previous reply was not valid SubagentResult JSON. "
                      "Reply with ONLY the JSON object matching the schema.",
                })
                retry = await self._pool.run([retry_frame])
                repaired.append(retry[0])
                logger.info("validation-retry for %s -> %s", frame.task_id, retry[0].status.value)
            else:
                repaired.append(result)
        return repaired


class Reducer:
    """Pure fanin: merge, dedupe, conflict-check. No parent mutation."""

    @staticmethod
    def reduce(
        results: list[SubagentResult],
        elapsed_s: float = 0.0,
        global_budget: int | None = None,
    ) -> ReducedResult:
        findings: dict[tuple[str, str, str, int, int], Finding] = {}
        patches: list[PatchProposal] = []
        prompt = completion = 0
        succeeded = failed = timed_out = 0
        for result in results:
            prompt += result.token_usage.prompt
            completion += result.token_usage.completion
            if result.status is TaskStatus.SUCCESS:
                succeeded += 1
            elif result.status is TaskStatus.TIMEOUT:
                timed_out += 1
            else:
                failed += 1
            # Only SUCCESS findings enter the parent graph; FAILED/TIMEOUT
            # carry no findings by construction, so nothing pollutes.
            for finding in result.findings:
                findings.setdefault(finding.identity(), finding)
            patches.extend(result.patches)
        conflicts = [
            ConflictPair(first=a, second=b)
            for a, b in itertools.combinations(patches, 2)
            if _patches_conflict(a, b)
        ]
        exceeded = global_budget is not None and (prompt + completion) > global_budget
        return ReducedResult(
            findings=list(findings.values()),
            patches=patches,
            conflicts=conflicts,
            prompt_tokens=prompt,
            completion_tokens=completion,
            succeeded=succeeded,
            failed=failed,
            timed_out=timed_out,
            budget_exceeded=bool(exceeded),
            elapsed_s=elapsed_s,
        )


def _patches_conflict(a: PatchProposal, b: PatchProposal) -> bool:
    from wisp.core.subagent.protocol import patches_conflict

    return patches_conflict(a, b)


def legacy_orchestrator_worker(orchestrator: Any) -> WorkerFn:
    """Adapt a legacy SubagentOrchestrator to the pool worker contract.

    Requests JSON output against the SubagentResult schema and parses it
    with the existing balanced-JSON extractor; token counts come from the
    legacy result record. Any failure mode degrades to a raw FAILED dict
    (the pool/coordinator type it from there).
    """

    async def _work(frame: TaskFrame, emit: Any) -> dict[str, Any]:
        from wisp.multi_agent.schema_validator import extract_json_from_markdown
        from wisp.multi_agent.task import SubagentContract

        schema = SubagentResult.model_json_schema()
        contract = SubagentContract(
            name=f"{frame.role}-{frame.task_id}",
            role=frame.role,
            task=frame.render_prompt(),
            tools=list(frame.allowed_tools),
            timeout_seconds=frame.policy.timeout_s,
            max_output_chars=16000,
            output_format="json",
            output_schema=schema,
        )
        result = await orchestrator.run(contract)
        parsed: dict[str, Any] | None = None
        try:
            parsed = extract_json_from_markdown(result.output or "")
        except Exception:
            parsed = None
        if not isinstance(parsed, dict):
            return {
                "task_id": frame.task_id,
                "status": "FAILED",
                "findings": [],
                "token_usage": {
                    "prompt": int(getattr(result, "input_tokens", 0) or 0),
                    "completion": int(getattr(result, "output_tokens", 0) or 0),
                },
                "error": f"legacy output unparseable: {(result.output or '')[:200]}",
            }
        parsed.setdefault("task_id", frame.task_id)
        try:
            usage = parsed.get("token_usage", {}) or {}
            usage["prompt"] = int(usage.get("prompt", 0) or getattr(result, "input_tokens", 0) or 0)
            usage["completion"] = int(usage.get("completion", 0) or getattr(result, "output_tokens", 0) or 0)
            parsed["token_usage"] = usage
        except Exception:
            pass
        return parsed

    return _work
