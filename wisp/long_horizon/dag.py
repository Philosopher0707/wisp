"""Parallel task execution with DAG dependency resolution.

The ParallelTaskExecutor runs independent steps concurrently while
respecting dependency chains. It integrates with SubagentOrchestrator
for actual subagent delegation.

Usage:
    executor = ParallelTaskExecutor(orchestrator=orchestrator, max_parallel=4)
    for step in steps:
        executor.add_node(DagNode.from_step(step))
    results = await executor.execute()
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from wisp.long_horizon.state import Step, StepStatus
from wisp.long_horizon.errors import DeadlockError

logger = logging.getLogger(__name__)


@dataclass
class DagNode:
    """A node in the task dependency graph.

    Attributes:
        id: Unique identifier (matches Step.id).
        description: Natural language instruction.
        dependencies: Node IDs that must complete before this one.
        result: Output from successful execution.
        status: Current execution state.
        error: Failure reason if the node failed.
        parallel_group: Optional group for batched execution.
    """
    id: str
    description: str
    dependencies: list[str] = field(default_factory=list)
    result: str = ""
    error: str = ""
    status: StepStatus = StepStatus.PENDING
    parallel_group: str | None = None

    @classmethod
    def from_step(cls, step: Step) -> DagNode:
        """Create a DagNode from a Step."""
        return cls(
            id=step.id,
            description=step.description,
            dependencies=step.dependencies.copy(),
            parallel_group=step.parallel_group,
        )


class ParallelTaskExecutor:
    """Execute a DAG of tasks with max parallelism and dependency resolution.

    Usage:
        executor = ParallelTaskExecutor(
            step_runner=async_fn,
            max_parallel=4,
        )
        executor.add_node(DagNode(id="a", description="...", dependencies=[]))
        executor.add_node(DagNode(id="b", description="...", dependencies=["a"]))
        results = await executor.execute()
    """

    def __init__(
        self,
        step_runner: Optional[callable] = None,
        max_parallel: int = 4,
    ):
        self.step_runner = step_runner
        self.max_parallel = max_parallel
        self.nodes: dict[str, DagNode] = {}
        self._completed: set[str] = set()
        self._running: set[str] = set()
        self._lock = asyncio.Lock()

    def add_node(self, node: DagNode) -> None:
        """Add a node to the DAG."""
        self.nodes[node.id] = node

    def add_nodes(self, nodes: list[DagNode]) -> None:
        """Add multiple nodes."""
        for node in nodes:
            self.add_node(node)

    async def execute(self) -> dict[str, str]:
        """Execute the DAG with max parallelism.

        Returns a dict mapping node IDs to their results.
        Raises DeadlockError if a circular dependency is detected.
        """
        # Validate all dependencies exist
        for node in self.nodes.values():
            for dep in node.dependencies:
                if dep not in self.nodes:
                    raise DeadlockError(
                        f"Node '{node.id}' depends on unknown node '{dep}'"
                    )

        # Detect cycles before starting
        if self._detect_cycle():
            raise DeadlockError("Circular dependency detected in task DAG")

        pending = {n.id for n in self.nodes.values() if n.status == StepStatus.PENDING}

        while pending or self._running:
            async with self._lock:
                # Find ready nodes (all deps satisfied)
                ready = [
                    nid for nid in pending
                    if all(d in self._completed for d in self.nodes[nid].dependencies)
                ]

                if not ready and not self._running:
                    # Deadlock: pending nodes but none can run
                    if pending:
                        raise DeadlockError(
                            f"Deadlock detected. Pending nodes: {pending}"
                        )
                    break

                # Launch up to max_parallel - running
                slots = self.max_parallel - len(self._running)
                to_launch = ready[:slots]
                for nid in to_launch:
                    pending.discard(nid)
                    self._running.add(nid)
                    self.nodes[nid].status = StepStatus.RUNNING
                    # Fire and forget — we'll await completions
                    asyncio.create_task(self._run_node(nid))

            if self._running:
                # Wait a bit for any running node to complete
                await asyncio.sleep(0.1)
            else:
                # No running and no ready — should have broken above
                break

        # Collect results
        return {
            nid: node.result
            for nid, node in self.nodes.items()
            if node.status == StepStatus.COMPLETED
        }

    async def _run_node(self, node_id: str) -> None:
        """Execute a single node and update state."""
        node = self.nodes[node_id]
        try:
            if self.step_runner:
                result = await self.step_runner(node)
            else:
                result = f"Completed: {node.description}"
            node.result = result
            node.status = StepStatus.COMPLETED
            async with self._lock:
                self._running.discard(node_id)
                self._completed.add(node_id)
            logger.info("Node %s completed", node_id)
        except Exception as e:
            node.error = f"{type(e).__name__}: {e}"
            node.status = StepStatus.FAILED
            async with self._lock:
                self._running.discard(node_id)
            logger.error("Node %s failed: %s", node_id, e)

    def _detect_cycle(self) -> bool:
        """Detect cycles in the dependency graph using DFS."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {nid: WHITE for nid in self.nodes}

        def dfs(nid: str) -> bool:
            color[nid] = GRAY
            for dep in self.nodes[nid].dependencies:
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    return True  # Back edge = cycle
                if color[dep] == WHITE and dfs(dep):
                    return True
            color[nid] = BLACK
            return False

        for nid in self.nodes:
            if color[nid] == WHITE:
                if dfs(nid):
                    return True
        return False

    def _detect_deadlock(self) -> bool:
        """True if no nodes are running and no nodes are ready,
        but pending nodes remain (circular dependencies or missing deps).
        """
        has_pending = any(
            n.status == StepStatus.PENDING for n in self.nodes.values()
        )
        has_running = any(
            n.status == StepStatus.RUNNING for n in self.nodes.values()
        )
        ready = [
            n for n in self.nodes.values()
            if n.status == StepStatus.PENDING
            and all(self.nodes[d].status == StepStatus.COMPLETED for d in n.dependencies)
        ]
        return has_pending and not has_running and not ready
