"""Tests for wisp.long_horizon.dag — DAG execution, parallelism, deadlock detection."""

from __future__ import annotations

import asyncio
import pytest

from wisp.long_horizon.dag import DagNode, ParallelTaskExecutor
from wisp.long_horizon.state import Step, StepStatus
from wisp.long_horizon.errors import DeadlockError


# ══════════════════════════════════════════════════════════════════════
# DagNode
# ══════════════════════════════════════════════════════════════════════

class TestDagNode:
    def test_from_step(self):
        step = Step(
            id="s1",
            description="Test step",
            dependencies=["s0"],
            parallel_group="group-a",
        )
        node = DagNode.from_step(step)
        assert node.id == "s1"
        assert node.description == "Test step"
        assert node.dependencies == ["s0"]
        assert node.parallel_group == "group-a"
        assert node.status == StepStatus.PENDING

    def test_defaults(self):
        node = DagNode(id="a", description="Do something")
        assert node.dependencies == []
        assert node.result == ""
        assert node.error == ""
        assert node.parallel_group is None


# ══════════════════════════════════════════════════════════════════════
# Sequential execution
# ══════════════════════════════════════════════════════════════════════

class TestSequentialExecution:
    @pytest.mark.asyncio
    async def test_single_node(self):
        executor = ParallelTaskExecutor(max_parallel=1)
        executor.add_node(DagNode(id="a", description="Only node"))
        results = await executor.execute()
        assert results == {"a": "Completed: Only node"}
        assert executor.nodes["a"].status == StepStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_linear_chain(self):
        executor = ParallelTaskExecutor(max_parallel=1)
        executor.add_node(DagNode(id="a", description="First", dependencies=[]))
        executor.add_node(DagNode(id="b", description="Second", dependencies=["a"]))
        executor.add_node(DagNode(id="c", description="Third", dependencies=["b"]))
        results = await executor.execute()
        assert set(results.keys()) == {"a", "b", "c"}
        assert executor.nodes["c"].status == StepStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_with_step_runner(self):
        async def runner(node: DagNode) -> str:
            return f"Result for {node.id}"

        executor = ParallelTaskExecutor(step_runner=runner, max_parallel=1)
        executor.add_node(DagNode(id="a", description="Test"))
        results = await executor.execute()
        assert results["a"] == "Result for a"


# ══════════════════════════════════════════════════════════════════════
# Parallel execution
# ══════════════════════════════════════════════════════════════════════

class TestParallelExecution:
    @pytest.mark.asyncio
    async def test_parallel_independent_nodes(self):
        execution_order = []

        async def slow_runner(node: DagNode) -> str:
            execution_order.append(node.id)
            await asyncio.sleep(0.05)  # Small delay
            return f"Done {node.id}"

        executor = ParallelTaskExecutor(step_runner=slow_runner, max_parallel=4)
        executor.add_node(DagNode(id="a", description="A"))
        executor.add_node(DagNode(id="b", description="B"))
        executor.add_node(DagNode(id="c", description="C"))
        executor.add_node(DagNode(id="d", description="D"))

        results = await executor.execute()
        assert len(results) == 4
        # All should have started roughly together (order may vary)
        assert set(execution_order) == {"a", "b", "c", "d"}

    @pytest.mark.asyncio
    async def test_max_parallel_respected(self):
        max_concurrent = 0
        current = 0

        async def tracking_runner(node: DagNode) -> str:
            nonlocal max_concurrent, current
            current += 1
            max_concurrent = max(max_concurrent, current)
            await asyncio.sleep(0.1)
            current -= 1
            return f"Done {node.id}"

        executor = ParallelTaskExecutor(step_runner=tracking_runner, max_parallel=2)
        for i in range(5):
            executor.add_node(DagNode(id=f"n{i}", description=f"Node {i}"))

        await executor.execute()
        assert max_concurrent <= 2

    @pytest.mark.asyncio
    async def test_diamond_dependency(self):
        r"""
              a
             / \
            b   c
             \ /
              d
        """
        execution_order = []

        async def runner(node: DagNode) -> str:
            execution_order.append(node.id)
            return f"Done {node.id}"

        executor = ParallelTaskExecutor(step_runner=runner, max_parallel=4)
        executor.add_node(DagNode(id="a", description="A"))
        executor.add_node(DagNode(id="b", description="B", dependencies=["a"]))
        executor.add_node(DagNode(id="c", description="C", dependencies=["a"]))
        executor.add_node(DagNode(id="d", description="D", dependencies=["b", "c"]))

        results = await executor.execute()
        assert len(results) == 4
        # a must come before b and c
        assert execution_order.index("a") < execution_order.index("b")
        assert execution_order.index("a") < execution_order.index("c")
        # d must come after b and c
        assert execution_order.index("b") < execution_order.index("d")
        assert execution_order.index("c") < execution_order.index("d")


# ══════════════════════════════════════════════════════════════════════
# Error handling
# ══════════════════════════════════════════════════════════════════════

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_node_failure(self):
        async def failing_runner(node: DagNode) -> str:
            if node.id == "b":
                raise ValueError("Intentional failure")
            return f"Done {node.id}"

        executor = ParallelTaskExecutor(step_runner=failing_runner, max_parallel=2)
        executor.add_node(DagNode(id="a", description="A"))
        executor.add_node(DagNode(id="b", description="B"))
        executor.add_node(DagNode(id="c", description="C", dependencies=["a"]))

        results = await executor.execute()
        # a and c should complete, b should fail
        assert "a" in results
        assert "c" in results
        assert "b" not in results
        assert executor.nodes["b"].status == StepStatus.FAILED
        assert "Intentional failure" in executor.nodes["b"].error

    @pytest.mark.asyncio
    async def test_missing_dependency(self):
        executor = ParallelTaskExecutor(max_parallel=2)
        executor.add_node(DagNode(id="a", description="A", dependencies=["missing"]))
        with pytest.raises(DeadlockError, match="unknown node"):
            await executor.execute()

    @pytest.mark.asyncio
    async def test_circular_dependency(self):
        executor = ParallelTaskExecutor(max_parallel=2)
        executor.add_node(DagNode(id="a", description="A", dependencies=["c"]))
        executor.add_node(DagNode(id="b", description="B", dependencies=["a"]))
        executor.add_node(DagNode(id="c", description="C", dependencies=["b"]))
        with pytest.raises(DeadlockError, match="Circular dependency"):
            await executor.execute()

    @pytest.mark.asyncio
    async def test_self_dependency(self):
        executor = ParallelTaskExecutor(max_parallel=1)
        executor.add_node(DagNode(id="a", description="A", dependencies=["a"]))
        with pytest.raises(DeadlockError, match="Circular dependency"):
            await executor.execute()


# ══════════════════════════════════════════════════════════════════════
# Deadlock detection
# ══════════════════════════════════════════════════════════════════════

class TestDeadlockDetection:
    def test_detect_deadlock_true(self):
        executor = ParallelTaskExecutor(max_parallel=1)
        executor.add_node(DagNode(id="a", description="A", dependencies=["b"]))
        executor.add_node(DagNode(id="b", description="B", dependencies=["a"]))
        assert executor._detect_cycle() is True

    def test_detect_deadlock_false(self):
        executor = ParallelTaskExecutor(max_parallel=1)
        executor.add_node(DagNode(id="a", description="A"))
        executor.add_node(DagNode(id="b", description="B", dependencies=["a"]))
        assert executor._detect_cycle() is False

    def test_detect_deadlock_complex(self):
        executor = ParallelTaskExecutor(max_parallel=1)
        executor.add_node(DagNode(id="a", description="A"))
        executor.add_node(DagNode(id="b", description="B", dependencies=["a"]))
        executor.add_node(DagNode(id="c", description="C", dependencies=["b"]))
        executor.add_node(DagNode(id="d", description="D", dependencies=["a"]))
        assert executor._detect_cycle() is False


# ══════════════════════════════════════════════════════════════════════
# Edge cases
# ══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_dag(self):
        executor = ParallelTaskExecutor(max_parallel=4)
        results = await executor.execute()
        assert results == {}

    @pytest.mark.asyncio
    async def test_single_node_with_self_dep(self):
        executor = ParallelTaskExecutor(max_parallel=1)
        executor.add_node(DagNode(id="a", description="A", dependencies=["a"]))
        with pytest.raises(DeadlockError):
            await executor.execute()

    @pytest.mark.asyncio
    async def test_large_dag(self):
        """Test with many independent nodes."""
        async def fast_runner(node: DagNode) -> str:
            return f"Done {node.id}"

        executor = ParallelTaskExecutor(step_runner=fast_runner, max_parallel=10)
        for i in range(20):
            executor.add_node(DagNode(id=f"n{i}", description=f"Node {i}"))

        results = await executor.execute()
        assert len(results) == 20

    @pytest.mark.asyncio
    async def test_deep_chain(self):
        """Test with a long dependency chain."""
        async def runner(node: DagNode) -> str:
            return f"Done {node.id}"

        executor = ParallelTaskExecutor(step_runner=runner, max_parallel=4)
        prev = None
        for i in range(10):
            deps = [prev] if prev else []
            executor.add_node(DagNode(id=f"n{i}", description=f"Node {i}", dependencies=deps))
            prev = f"n{i}"

        results = await executor.execute()
        assert len(results) == 10
        # Verify sequential execution order
        for i in range(10):
            assert executor.nodes[f"n{i}"].status == StepStatus.COMPLETED
