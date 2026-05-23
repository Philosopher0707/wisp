"""SubagentOrchestrator — thin coordinator for subagent execution.

Delegates all responsibilities to focused internal classes:
- ``SubagentRunner`` — direct async execution (no nested loops)
- ``BudgetTracker`` — token accounting
- ``ResultCache`` — result caching with TTL
- ``WorktreeManager`` — git worktree lifecycle
- ``Telemetry`` — metrics collection
- ``Persistence`` — JSONL audit logging
- ``_patterns`` — map-reduce, vote, chain workflows
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from wisp.config import WispConfig

from ._budget_tracker import BudgetTracker
from ._persistence import Persistence
from ._result_cache import ResultCache
from ._runner import SubagentRunner
from ._telemetry import Telemetry
from ._worktree_manager import WorktreeManager
from .roles import ROLE_CONFIGS
from .task import EventKind, OrchestratorEvent, SubagentContract, SubagentResult

logger = logging.getLogger(__name__)

# Fallback defaults — runtime values come from config
_MAX_SUBAGENT_DEPTH_DEFAULT = 2
_MAX_SUBAGENT_BRANCHING_DEFAULT = 3


class SubagentOrchestrator:
    """Unified orchestrator for single, parallel, and composable subagent execution.

    Thin coordinator that composes focused subsystems. All heavy logic
    has been extracted to internal modules.
    """

    def __init__(
        self,
        parent_agent: Optional[Any] = None,
        config: Optional[WispConfig] = None,
        workspace: Optional[Path] = None,
        tool_executor: Any = None,
    ):
        self.parent = parent_agent
        self.config = config or (getattr(parent_agent, "config", None) if parent_agent else WispConfig())
        _ws = (
            workspace
            or (Path(self.config.workspace).resolve() if self.config.workspace else None)
            or Path.cwd().resolve()
        )
        self.workspace = Path(_ws).resolve() if not isinstance(_ws, Path) else _ws.resolve()

        # Unique cache namespace — prevents cross-session cache collisions
        import uuid
        self._cache_namespace = uuid.uuid4().hex[:12]

        # Composed subsystems
        self._budget = BudgetTracker()
        self._cache = ResultCache()
        self._telemetry = Telemetry()
        self._persistence = Persistence(self.workspace / ".wisp" / "subagent_results.jsonl")
        self._worktree_mgr = WorktreeManager(self.workspace)
        self._runner = SubagentRunner(self.config, self.workspace, tool_executor=tool_executor)

        # Config-driven limits
        self._max_depth = getattr(self.config, "max_subagent_depth", _MAX_SUBAGENT_DEPTH_DEFAULT)
        self._max_branching = getattr(self.config, "max_subagent_branching", _MAX_SUBAGENT_BRANCHING_DEFAULT)

        # Concurrency
        self._pool_size = getattr(self.config, "subagent_pool_size", 4)
        self._active = 0
        self._semaphore = asyncio.Semaphore(self._pool_size)

    # ── Token budget API ───────────────────────────────────────────────

    def set_global_token_budget(self, budget: Optional[int]) -> None:
        self._budget.set_budget(budget)

    def get_tokens_consumed(self) -> int:
        return self._budget.get_consumed()

    def get_token_budget_remaining(self) -> Optional[int]:
        return self._budget.get_remaining()

    # ── Cache API ──────────────────────────────────────────────────────

    def get_cache_stats(self) -> dict[str, int | float]:
        return self._cache.stats()

    def clear_cache(self) -> None:
        self._cache.clear()

    # ── Telemetry API ──────────────────────────────────────────────────

    def get_telemetry(self) -> dict[str, list[dict]]:
        return self._telemetry.get()

    def get_telemetry_summary(self) -> dict[str, dict]:
        return self._telemetry.summary()

    def aggregate_telemetry(self, results: list[SubagentResult]) -> dict[str, dict]:
        return self._telemetry.aggregate(results)

    # ── Pool API ───────────────────────────────────────────────────────

    def set_pool_size(self, size: int) -> None:
        if size < 1:
            raise ValueError("Pool size must be >= 1")
        self._pool_size = size
        self._semaphore = asyncio.Semaphore(size)
        logger.info("Subagent pool size set to %d", size)

    def get_pool_status(self) -> dict[str, int]:
        return {
            "pool_size": self._pool_size,
            "active_agents": self._active,
            "available_slots": max(0, self._pool_size - self._active),
        }

    # ── Persistence API ────────────────────────────────────────────────

    def get_persisted_results(self, limit: int = 100) -> list[dict]:
        return self._persistence.load(limit)

    def clear_persisted_results(self) -> None:
        self._persistence.clear()

    # ── Public API ─────────────────────────────────────────────────────

    async def run(
        self,
        contract: SubagentContract,
    ) -> SubagentResult:
        """Run a single subagent and return its result.

        Never raises — failures are returned as ``SubagentResult(success=False)``.
        """
        # ── Depth guard ────────────────────────────────────────────────
        if contract._subagent_depth >= self._max_depth:
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output=f"[DEPTH LIMIT EXCEEDED] Max subagent depth is {self._max_depth}",
                error=f"Subagent depth {contract._subagent_depth} exceeds max {self._max_depth}",
                elapsed_seconds=0.0,
            )

        # ── Role validation ────────────────────────────────────────────
        role_error = self._validate_role(contract)
        if role_error:
            logger.warning("Role validation failed for %s: %s", contract.name, role_error)
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output=f"[ROLE VALIDATION FAILED] {role_error}",
                error=role_error,
                elapsed_seconds=0.0,
            )

        # ── Contract validation ────────────────────────────────────────
        if contract.timeout_seconds <= 0:
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output="[CONTRACT INVALID] timeout_seconds must be > 0",
                error="timeout_seconds must be > 0",
                elapsed_seconds=0.0,
            )
        if contract.max_iterations <= 0:
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output="[CONTRACT INVALID] max_iterations must be > 0",
                error="max_iterations must be > 0",
                elapsed_seconds=0.0,
            )

        # ── Cache check ────────────────────────────────────────────────
        contract._cache_context = self._cache_namespace
        cached = self._cache.get(contract)
        if cached is not None:
            return cached

        # ── Token budget check ─────────────────────────────────────────
        budget_error = self._budget.check()
        if budget_error:
            logger.warning("Token budget check failed for %s: %s", contract.name, budget_error)
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output=f"[TOKEN BUDGET EXCEEDED] {budget_error}",
                error=budget_error,
                elapsed_seconds=0.0,
            )

        # ── Worktree ───────────────────────────────────────────────────
        worktree_path: Path | None = None
        if contract.worktree_isolated:
            try:
                worktree_path = await self._worktree_mgr.create(contract.name)
            except Exception as exc:
                logger.warning(
                    "Worktree creation failed for %s, falling back to shared workspace: %s",
                    contract.name, exc,
                )
                worktree_path = None

        agent_workspace = str(worktree_path or self.workspace)

        # ── System prompt ──────────────────────────────────────────────
        system = contract.system_prompt or self._default_system_prompt(contract)

        # ── Run with concurrency control ─────────────────────────────
        async with self._semaphore:
            self._active += 1
            try:
                result = await self._runner.run(
                    contract=contract,
                    agent_workspace=agent_workspace,
                    system_prompt=system,
                    progress_callback=contract.progress_callback,
                )
            finally:
                self._active -= 1

        # ── Schema validation ────────────────────────────────────────
        if contract.output_schema and result.success:
            result = await self._validate_output(result, contract)

        # ── Post-run bookkeeping ─────────────────────────────────────
        self._cache.set(contract, result)
        self._persistence.save(contract, result)
        self._budget.record(result.tokens_used)
        self._telemetry.record(contract.model or self.config.model or "unknown", result)

        # ── Capture & apply worktree changes ────────────────────────────
        if worktree_path:
            try:
                result.worktree_patch = await self._worktree_mgr.get_patch(worktree_path)
            except Exception as exc:
                logger.warning("Failed to capture worktree patch for %s: %s", worktree_path, exc)

            # Git-based file detection (replaces regex heuristic when worktree available)
            if result.success:
                try:
                    actual_files = await self._worktree_mgr.detect_files_changed(worktree_path)
                    if actual_files:
                        result.files_changed = actual_files
                except Exception as exc:
                    logger.debug("Git file detection failed for %s: %s", worktree_path, exc)

            # Apply patch to parent workspace
            if result.worktree_patch and result.success:
                try:
                    result.patch_applied = await self._worktree_mgr.apply_patch(result.worktree_patch)
                except Exception as exc:
                    logger.warning("Failed to apply worktree patch for %s: %s", worktree_path, exc)

            if not os.environ.get("WISP_KEEP_WORKTREES", "").lower() == "true":
                try:
                    await self._worktree_mgr.cleanup(worktree_path)
                except Exception as exc:
                    logger.warning("Failed to clean up worktree %s: %s", worktree_path, exc)
        return result

    async def run_parallel(
        self,
        contracts: list[SubagentContract],
        max_concurrent: int = 4,
        adaptive: bool = True,
    ) -> list[SubagentResult]:
        """Run multiple subagent contracts concurrently."""
        effective_max = max_concurrent
        if adaptive:
            effective_max = self._adaptive_max_concurrent(max_concurrent, len(contracts))
            if effective_max != max_concurrent:
                logger.info(
                    "Adaptive load balancing: max_concurrent %d → %d",
                    max_concurrent, effective_max,
                )

        semaphore = asyncio.Semaphore(effective_max)

        async def _guarded(contract: SubagentContract) -> SubagentResult:
            async with semaphore:
                return await self.run(contract)

        tasks = [asyncio.create_task(_guarded(c)) for c in contracts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        resolved: list[SubagentResult] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                contract = contracts[i]
                resolved.append(
                    SubagentResult(
                        task_id=contract.name,
                        success=False,
                        output="",
                        elapsed_seconds=0.0,
                        error=f"Unhandled gather exception: {result}",
                        session_id="",
                    )
                )
                logger.error("Subagent %s crashed during gather: %s", contract.name, result)
            else:
                resolved.append(result)

        logger.info(
            "Parallel run complete: %d/%d succeeded",
            sum(1 for r in resolved if r.success),
            len(resolved),
        )
        # Report patch conflicts from concurrent subagents
        failed_patches = [r for r in resolved if r.success and r.worktree_patch and not r.patch_applied]
        if failed_patches:
            logger.warning(
                "Patch conflicts in parallel run: %d/%d patches failed to apply (%s)",
                len(failed_patches),
                sum(1 for r in resolved if r.success and r.worktree_patch),
                ", ".join(r.task_id for r in failed_patches),
            )
        self.aggregate_telemetry(resolved)
        return resolved

    async def run_parallel_streaming(
        self,
        contracts: list[SubagentContract],
        max_concurrent: int = 4,
    ) -> AsyncIterator[SubagentResult]:
        """Run multiple subagent contracts concurrently, yielding results as they finish.

        Uses ``asyncio.as_completed()`` so the caller gets results incrementally
        instead of waiting for the slowest subagent.
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _guarded(contract: SubagentContract) -> SubagentResult:
            async with semaphore:
                return await self.run(contract)

        tasks = [asyncio.create_task(_guarded(c)) for c in contracts]
        completed = 0
        for coro in asyncio.as_completed(tasks):
            try:
                result = await coro
                completed += 1
                logger.debug(
                    "Streaming result %d/%d: %s success=%s",
                    completed, len(contracts), result.task_id, result.success,
                )
                yield result
            except Exception as exc:
                logger.error("Subagent crashed during streaming: %s", exc)

    def _adaptive_max_concurrent(self, requested: int, queue_size: int) -> int:
        """Adjust max_concurrent based on system load and token budget."""
        effective = requested

        budget_ratio = self._budget.get_ratio()
        if budget_ratio is not None:
            if budget_ratio < 0.1:
                effective = min(effective, 1)
            elif budget_ratio < 0.3:
                effective = min(effective, 2)

        try:
            import os
            cpu_count = os.cpu_count() or 4
            load_avg = os.getloadavg()[0]
            if load_avg > cpu_count * 1.5:
                effective = min(effective, 1)
            elif load_avg > cpu_count:
                effective = min(effective, max(1, effective // 2))
        except (AttributeError, OSError):
            pass

        effective = min(effective, queue_size)
        return max(1, effective)

    # ── Composable patterns ────────────────────────────────────────────

    async def run_map_reduce(
        self,
        task: str,
        items: list[str],
        mapper: Any,
        reducer: str,
        max_concurrent: int = 4,
        retry_failed: bool = True,
    ) -> SubagentResult:
        from ._patterns import run_map_reduce
        return await run_map_reduce(self, task, items, mapper, reducer, max_concurrent, retry_failed)

    async def run_vote(
        self,
        task: str,
        agents: list[SubagentContract],
        consensus_threshold: float = 0.6,
        max_concurrent: int = 4,
    ) -> SubagentResult:
        from ._patterns import run_vote
        return await run_vote(self, task, agents, consensus_threshold, max_concurrent)

    async def run_chain(
        self,
        contracts: list[SubagentContract],
        pass_context: bool = True,
        max_concurrent: int = 1,
        continue_on_error: bool = False,
    ) -> SubagentResult:
        from ._patterns import run_chain
        return await run_chain(self, contracts, pass_context, max_concurrent, continue_on_error)

    async def run_dag(
        self,
        dag: Any,  # TaskDAG
        max_parallelism: int = 4,
        timeout_per_node: float = 300.0,
    ) -> Any:  # DAGResult
        """Execute a TaskDAG with topological scheduling.

        Each level of the DAG runs in parallel (up to max_parallelism).
        Dependencies between levels are respected — a node only runs
        after all its dependencies complete.

        Args:
            dag: TaskDAG with nodes and edges.
            max_parallelism: Max concurrent nodes per level.
            timeout_per_node: Per-node timeout in seconds.

        Returns:
            DAGResult with per-node results and timing.
        """
        from .dag import DAGScheduler

        async def _executor(node):
            """Execute a single TaskNode via this orchestrator."""
            task = node.task
            # If it's already a SubagentContract, run it directly
            if isinstance(task, SubagentContract):
                # Apply resource budget from node metadata if present
                budget_dict = node.metadata.get("budget", {})
                if budget_dict:
                    from .resource_budget import ResourceBudget
                    budget = ResourceBudget.from_config(budget_dict)
                    budget.start()
                    # Pass budget to contract metadata for runner
                    if not task.metadata:
                        task.metadata = {}
                    task.metadata["_budget"] = budget
                return await self.run(task)

            # If it's a callable, invoke it
            if callable(task):
                return await task()

            raise ValueError(f"Unsupported task type in DAG node '{node.name}': {type(task)}")

        scheduler = DAGScheduler(
            max_parallelism=max_parallelism,
            timeout_per_node=timeout_per_node,
        )
        return await scheduler.execute(dag, _executor)

    # ── Internal helpers ───────────────────────────────────────────────

    def _validate_role(self, contract: SubagentContract) -> Optional[str]:
        if not contract.role:
            return "Role is required"
        if contract.role not in ROLE_CONFIGS:
            valid_roles = ", ".join(sorted(ROLE_CONFIGS.keys()))
            return f"Unknown role '{contract.role}'. Valid roles: {valid_roles}"
        role_cfg = ROLE_CONFIGS[contract.role]
        if not role_cfg.system_prompt:
            return f"Role '{contract.role}' has no system prompt configured"
        return None

    def _default_system_prompt(self, contract: SubagentContract) -> str:
        """Build a concise default system prompt when none is provided."""
        from .roles import AgentRole

        role_cfg = ROLE_CONFIGS.get(contract.role)
        if role_cfg:
            base = role_cfg.system_prompt
        else:
            base = "\n".join([
                f"You are a specialist subagent: **{contract.name}**.",
                "You have tools to read, write, and edit files, run bash commands, "
                "list directories, and fetch URLs.",
                "",
                "## Rules",
                "1. Focus ONLY on your assigned task.",
                "2. Work efficiently — you have a time budget.",
                "3. When done, provide a clear summary of what you did.",
                "4. If you edit files, list the changed paths.",
                "5. If stuck, explain what blocked you and stop.",
            ])

        parts = [base]

        # Load skills
        workspace = str(contract.workspace or self.workspace)
        try:
            from wisp.skills import discover_skills
            skills = discover_skills(workspace)
            if skills:
                if contract.allowed_skills:
                    filtered = [s for s in skills if s.name in contract.allowed_skills]
                    if filtered:
                        parts.append("")
                        parts.append("## Skills")
                        parts.append("You may use these skills when relevant:")
                        for s in filtered:
                            parts.append(f"- **{s.name}**: {s.description}")
                        for s in filtered:
                            parts.append("")
                            parts.append(f"### {s.name}")
                            parts.append(s.instructions)
                else:
                    parts.append("")
                    parts.append("## Available Skills")
                    parts.append("You can invoke any of these skills when relevant:")
                    for s in skills:
                        parts.append(f"- {s.name}: {s.description}")
        except Exception as exc:
            logger.debug("Failed to load skills for subagent: %s", exc)

        if contract.tools != ["all"]:
            parts.append("")
            parts.append("## Allowed Tools")
            parts.append(", ".join(contract.tools))

        if contract.context_files:
            parts.append("")
            parts.append("## Context Files")
            for f in contract.context_files:
                parts.append(f"- {f}")

        if contract.system_prompt_extra:
            parts.append("")
            parts.append("## Additional Instructions")
            parts.append(contract.system_prompt_extra)

        return "\n".join(parts)

    async def _validate_output(
        self, result: SubagentResult, contract: SubagentContract
    ) -> SubagentResult:
        """Validate subagent output against a JSON schema.

        If validation fails and ``auto_retry_parse`` is True, retry once
        with the validation error injected into the subagent context.
        """
        if not contract.output_schema:
            return result

        from .schema_validator import validate_subagent_output, build_retry_prompt

        is_valid, validated_data, errors = validate_subagent_output(
            result.output, contract.output_schema, auto_retry=True
        )

        if is_valid and validated_data is not None:
            result.validated_output = validated_data
            logger.info("Subagent %s output validated against schema", contract.name)
            return result

        logger.warning(
            "Subagent %s output failed schema validation: %s",
            contract.name, "; ".join(errors)
        )

        if contract.auto_retry_parse and contract.retry_count == 0:
            logger.info("Retrying subagent %s with schema feedback", contract.name)
            retry_dict = {k: v for k, v in contract.__dict__.items()
                          if k in SubagentContract.__dataclass_fields__}
            retry_dict["task"] = build_retry_prompt(
                contract.task, contract.output_schema, result.output, errors
            )
            retry_dict["retry_count"] = contract.retry_count + 1
            retry_contract = SubagentContract(**retry_dict)
            return await self.run(retry_contract)

        result.error = f"Schema validation failed: {'; '.join(errors)}"
        return result

    # ── Spawn with guards (moved from WispAgentCore) ───────────────────

    async def _run_with_retry(self, contract: SubagentContract) -> SubagentResult:
        """Run a contract with retry on failure.

        Failed subagents retry with exponential backoff. Timed-out subagents
        retry once with 1.5x timeout budget (capped at config max).
        """
        max_retries = contract.max_retries
        max_timeout = getattr(self.config, "max_subagent_timeout", 600)
        last_error = ""
        for attempt in range(max_retries + 1):
            try:
                result = await self.run(contract)
                if result.success:
                    return result
                last_error = result.error or "subagent failed"
                if result.timed_out:
                    if attempt < max_retries:
                        new_timeout = min(int(contract.timeout_seconds * 1.5), max_timeout)
                        logger.warning(
                            "Subagent %s timed out — retrying with %ds budget (attempt %d/%d)",
                            contract.name, new_timeout, attempt + 1, max_retries + 1,
                        )
                        contract.timeout_seconds = new_timeout
                        continue
                    logger.warning("Subagent %s timed out — no retries left", contract.name)
                    return result
                if "timeout" in last_error.lower():
                    logger.warning("Subagent %s timed out — not retrying", contract.name)
                    return result
                if attempt < max_retries:
                    backoff = 2 ** attempt
                    logger.warning(
                        "Subagent %s failed (attempt %d/%d), retrying in %ds: %s",
                        contract.name, attempt + 1, max_retries + 1, backoff, last_error,
                    )
                    await asyncio.sleep(backoff)
            except Exception as exc:
                last_error = str(exc)
                if attempt < max_retries:
                    backoff = 2 ** attempt
                    logger.warning(
                        "Subagent %s crashed (attempt %d/%d), retrying in %ds: %s",
                        contract.name, attempt + 1, max_retries + 1, backoff, last_error,
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error("Subagent %s crashed: %s", contract.name, exc, exc_info=True)
                    return SubagentResult(
                        task_id=contract.name,
                        success=False,
                        output=f"[CRASHED after {max_retries + 1} attempts: {exc}]",
                        error=str(exc),
                        elapsed_seconds=0.0,
                    )
        return SubagentResult(
            task_id=contract.name,
            success=False,
            output=f"[FAILED after {max_retries + 1} attempts: {last_error}]",
            error=last_error,
            elapsed_seconds=0.0,
        )

    async def spawn_with_guards(
        self,
        task: str,
        tools: Optional[list[str]] = None,
        max_iterations: int = 30,
        timeout_seconds: float = 300.0,
        output_format: str = "text",
        worktree_isolated: bool = False,
        max_tokens: Optional[int] = None,
        output_schema: Optional[dict] = None,
        auto_retry: bool = True,
        workspace: Optional[str] = None,
        auto_approve: bool = False,
        depth: int = 0,
        branch_count: int = 0,
    ) -> str:
        """Spawn a single subagent with full guard logic.

        Args:
            depth: Current subagent nesting depth (0 = top-level agent).
            branch_count: Current number of sibling subagents spawned.

        Returns the subagent output as a string, or an error message.
        """
        # ── Depth and branching limits ─────────────────────────────────
        if depth >= self._max_depth:
            return f"[Error: subagent depth {depth} exceeds max {self._max_depth}]"

        if branch_count >= self._max_branching:
            return f"[Error: subagent branching {branch_count} exceeds max {self._max_branching}]"

        # ── Build contract ─────────────────────────────────────────────
        contract = SubagentContract(
            task=task,
            tools=tools or ["all"],
            max_iterations=max_iterations,
            timeout_seconds=timeout_seconds,
            output_format=output_format,
            workspace=workspace or str(self.workspace),
            auto_approve=auto_approve,
            worktree_isolated=worktree_isolated,
            max_tokens=max_tokens,
            output_schema=output_schema,
            _subagent_depth=depth + 1,
            _subagent_branch_count=branch_count + 1,
        )

        # ── Local model fallback ───────────────────────────────────────
        local_model = await self._pick_local_model_for_subagent(contract.task)
        if local_model:
            contract.model = local_model
            logger.info("Using local model %s for subagent %s", local_model, contract.name)

        # ── Clamp timeout to operator-set SLA ────────────────────────
        contract.timeout_seconds = self._clamp_subagent_timeout(contract.timeout_seconds)

        # ── Progress callback ──────────────────────────────────────────
        async def _progress(event: OrchestratorEvent) -> None:
            if event.event_type == EventKind.TASK_STARTED:
                logger.info("[sub] %s started", event.task_id)
            elif event.event_type == EventKind.TASK_COMPLETED:
                logger.info("[sub] %s completed", event.task_id)
            elif event.event_type == EventKind.TASK_FAILED:
                logger.warning("[sub] %s failed: %s", event.task_id, event.payload.get("error", ""))

        contract.progress_callback = _progress
        contract.max_retries = int(auto_retry) * 2

        # ── Run with retry ───────────────────────────────────────────
        result = await self._run_with_retry(contract)

        # ── Return output (with size guard) ────────────────────────────
        output = result.output
        if len(output) > 12000:
            output = output[:12000] + f"\n... [truncated: {len(result.output)} total chars]"
        return output

    async def spawn_parallel_with_guards(
        self,
        specs: list[SubagentContract | dict],
        depth: int = 0,
        branch_count: int = 0,
    ) -> list[SubagentResult]:
        """Spawn parallel subagents with optimizations applied to each.

        Args:
            specs: A list of SubagentContract objects or dicts describing each subagent's task.
            depth: Current subagent nesting depth (0 = top-level agent).
            branch_count: Current number of sibling subagents spawned.

        Returns:
            A list of SubagentResult objects, one per spec.
        """
        # ── Depth and branching limits ─────────────────────────────────
        if depth >= self._max_depth:
            return [
                SubagentResult(
                    task_id=getattr(spec, "name", "unknown"),
                    success=False,
                    output=f"[Error: subagent depth {depth} exceeds max {self._max_depth}]",
                    elapsed_seconds=0.0,
                )
                for spec in specs
            ]

        if branch_count >= self._max_branching:
            return [
                SubagentResult(
                    task_id=getattr(spec, "name", "unknown"),
                    success=False,
                    output=f"[Error: subagent branching {branch_count} exceeds max {self._max_branching}]",
                    elapsed_seconds=0.0,
                )
                for spec in specs
            ]

        contracts: list[SubagentContract] = []
        for spec in specs:
            if isinstance(spec, dict):
                # SECURITY FIX: never trust depth/branch values from external dict input.
                # Always override with the orchestrator's computed values.
                spec = dict(spec)
                spec.pop("_subagent_depth", None)
                spec.pop("_subagent_branch_count", None)
                contract = SubagentContract(**spec)
            else:
                contract = spec
            # Enforce depth/branch counters regardless of what the caller passed
            contract._subagent_depth = depth + 1
            contract._subagent_branch_count = branch_count + 1

            # Clamp timeout to operator-set SLA
            contract.timeout_seconds = self._clamp_subagent_timeout(contract.timeout_seconds)
            local_model = await self._pick_local_model_for_subagent(contract.task)
            if local_model:
                contract.model = local_model
            contracts.append(contract)

        results = await self.run_parallel(contracts)
        for r in results:
            logger.info(
                "Subagent %s: success=%s, duration=%.1fs, files=%s, tokens=%d",
                r.task_id, r.success, r.elapsed_seconds, r.files_changed, r.tokens_used,
            )
        return results

    # ── Internal helpers (moved from WispAgentCore) ──────────────────

    async def _pick_local_model_for_subagent(self, task: str) -> Optional[str]:
        """Return a fast local model name if the task is simple enough.

        Uses config.subagent_models as priority list (env: WISP_SUBAGENT_MODELS).
        """
        parent_model = getattr(self.config, "model", "")
        if not parent_model or ":cloud" not in parent_model:
            return None
        fast_locals = getattr(self.config, "subagent_models", ["llama3.2", "llama3.1", "qwen2.5", "phi4", "gemma2"])
        available = await self._list_local_models()
        for candidate in fast_locals:
            for name in available:
                if candidate in name.lower():
                    return name
        return None

    async def _list_local_models(self) -> list[str]:
        """Query Ollama for locally available models. Cached for 60s."""
        now = time.monotonic()
        cache = getattr(self, "_local_model_cache", None)
        if cache and now - cache["ts"] < 60:
            return cache["models"]
        try:
            import aiohttp
            url = getattr(self.config, "ollama_url", "http://localhost:11434")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(f"{url}/api/tags") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        models = [m["name"] for m in data.get("models", [])]
                        self._local_model_cache = {"ts": now, "models": models}
                        return models
        except Exception:
            pass
        return []

    def _clamp_subagent_timeout(self, requested: float) -> float:
        """Clamp timeout to operator-set bounds. Caller's explicit value wins."""
        max_timeout = getattr(self.config, "max_subagent_timeout", 600)
        return max(30.0, min(requested, max_timeout))

    def _research_angles(self, prompt: str) -> list[str]:
        """Break a research prompt into parallel investigation angles."""
        prompt_lower = prompt.lower()
        config_angles = getattr(self.config, "research_angles", {})
        if isinstance(config_angles, dict):
            for keyword, angles in config_angles.items():
                if keyword.lower() in prompt_lower:
                    return [a.format(prompt=prompt) for a in angles]
        return [
            f"Research the core concepts and fundamentals: {prompt}",
            f"Research recent advances and state-of-the-art: {prompt}",
            f"Research practical implementations and tools: {prompt}",
            f"Research limitations, challenges, and future directions: {prompt}",
        ]

