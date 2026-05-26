"""Task DAG — directed acyclic graph of subagent tasks with topological scheduling.

Level-by-level parallel execution. Each level's tasks run concurrently
up to max_parallelism. Dependencies must complete before dependents start.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class TaskNode:
    """A node in the task DAG.

    Each node wraps a SubagentContract or callable and declares
    dependencies that must complete before it can execute.
    """

    name: str
    task: Any  # SubagentContract or callable
    dependencies: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(self.name)


@dataclass
class TaskDAG:
    """A directed acyclic graph of tasks.

    Nodes are added with named dependencies. The DAG validates
    acyclicity before execution.
    """

    nodes: dict[str, TaskNode] = field(default_factory=dict)
    _edges: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _reverse_edges: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add_node(self, node: TaskNode) -> None:
        if node.name in self.nodes:
            raise ValueError(f"Duplicate node name: {node.name}")
        self.nodes[node.name] = node
        if node.name not in self._edges:
            self._edges[node.name] = set()
        if node.name not in self._reverse_edges:
            self._reverse_edges[node.name] = set()
        # Process declared dependencies
        for dep in node.dependencies:
            self.add_edge(dep, node.name)

    def add_edge(self, from_node: str, to_node: str) -> None:
        """Declare that 'to_node' depends on 'from_node'.

        Nodes must exist by validation time, not necessarily at edge creation.
        """
        self._edges[from_node].add(to_node)
        self._reverse_edges[to_node].add(from_node)

    def dependencies_of(self, name: str) -> set[str]:
        """Nodes that must complete before 'name' can run."""
        return set(self._reverse_edges.get(name, set()))

    def dependents_of(self, name: str) -> set[str]:
        """Nodes that depend on 'name'."""
        return set(self._edges.get(name, set()))

    def roots(self) -> list[str]:
        """Nodes with no dependencies."""
        return [name for name in self.nodes if not self.dependencies_of(name)]

    def validate(self) -> list[str]:
        """Check for cycles. Returns list of errors (empty = valid)."""
        errors: list[str] = []

        # Check all referenced dependencies exist
        for node in self.nodes.values():
            for dep in node.dependencies:
                if dep not in self.nodes:
                    errors.append(f"Node '{node.name}' depends on unknown '{dep}'")

        # Cycle detection via Kahn's algorithm
        in_degree = {name: len(self.dependencies_of(name)) for name in self.nodes}
        q = deque([name for name, deg in in_degree.items() if deg == 0])
        visited = 0

        while q:
            current = q.popleft()
            visited += 1
            for dependent in self.dependents_of(current):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    q.append(dependent)

        if visited != len(self.nodes):
            remaining = [name for name, deg in in_degree.items() if deg > 0]
            errors.append(f"Cycle detected involving: {', '.join(remaining)}")

        return errors

    def topological_levels(self) -> list[list[str]]:
        """Group nodes into levels for parallel execution.

        Level 0 = roots (no deps). Level N = nodes whose deps are all in levels < N.
        """
        in_degree = {name: len(self.dependencies_of(name)) for name in self.nodes}
        remaining = dict(in_degree)
        levels: list[list[str]] = []

        while remaining:
            current_level = sorted(
                name for name, deg in remaining.items() if deg == 0
            )
            if not current_level:
                break  # Cycle — caller should validate first
            levels.append(current_level)
            for name in current_level:
                del remaining[name]
                for dependent in self.dependents_of(name):
                    if dependent in remaining:
                        remaining[dependent] -= 1

        return levels


@dataclass
class DAGResult:
    """Result of executing a task DAG."""

    node_results: dict[str, Any]  # name → SubagentResult
    level_order: list[list[str]]
    total_elapsed: float
    success: bool = True
    errors: list[str] = field(default_factory=list)


@dataclass
class DAGScheduler:
    """Execute a TaskDAG level-by-level with parallel execution within each level.

    Each level's tasks run concurrently up to max_parallelism.
    """

    max_parallelism: int = 4
    timeout_per_node: float = 300.0

    async def execute(
        self,
        dag: TaskDAG,
        executor: Callable[[Any], Any],
    ) -> DAGResult:
        """Execute all nodes in topological order.

        Args:
            dag: The task DAG to execute.
            executor: Async callable that takes a TaskNode and returns a result.
                      Called as: await executor(node)

        Returns:
            DAGResult with per-node results and timing.
        """
        errors = dag.validate()
        if errors:
            return DAGResult(
                node_results={},
                level_order=[],
                total_elapsed=0.0,
                success=False,
                errors=errors,
            )

        levels = dag.topological_levels()
        all_results: dict[str, Any] = {}
        start = time.monotonic()

        semaphore = asyncio.Semaphore(self.max_parallelism)

        async def _run_node(node: TaskNode) -> None:
            async with semaphore:
                try:
                    result = await asyncio.wait_for(
                        executor(node),
                        timeout=self.timeout_per_node,
                    )
                    all_results[node.name] = result
                except asyncio.TimeoutError:
                    logger.error("Node %s timed out after %.1fs", node.name, self.timeout_per_node)
                    all_results[node.name] = _timeout_result(node.name)
                except Exception as exc:
                    logger.exception("Node %s failed", node.name)
                    all_results[node.name] = _error_result(node.name, str(exc))

        for level_idx, level in enumerate(levels):
            logger.info("DAG level %d: %s", level_idx, level)
            tasks = [asyncio.create_task(_run_node(dag.nodes[name])) for name in level]
            await asyncio.gather(*tasks)

        elapsed = time.monotonic() - start
        success = all(
            getattr(r, "success", True) for r in all_results.values()
        )

        return DAGResult(
            node_results=all_results,
            level_order=levels,
            total_elapsed=elapsed,
            success=success,
        )


def _timeout_result(name: str) -> Any:
    """Create a timeout error result compatible with SubagentResult."""
    return _make_fallback(name, False, "Timed out", "timeout")


def _error_result(name: str, error: str) -> Any:
    """Create an error result compatible with SubagentResult."""
    return _make_fallback(name, False, f"Error: {error}", error)


def _make_fallback(name: str, success: bool, output: str, error: str) -> Any:
    """Create a result-like object without importing SubagentResult.

    Uses duck-typing so callers can check .success, .output, .error.
    """
    return type("_FallbackResult", (), {
        "task_id": name,
        "success": success,
        "output": output,
        "error": error,
        "elapsed_seconds": 0.0,
    })()
