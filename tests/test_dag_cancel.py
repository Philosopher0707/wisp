"""M5 regression pin: outer cancellation must reach every DAG node.

Historical sweep flagged "DAG nodes survive outer cancel" (dag.py gather).
Current chain (scheduler → orchestrator._executor → runner.run) is fully
inline awaits with no detached spawns, so cancellation propagates. These
tests lock that in — if anyone introduces shield/detach/fire-and-forget
into the chain, CI fails here.
"""

import asyncio

import pytest

from wisp.multi_agent.dag import DAGScheduler, TaskDAG, TaskNode
from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator


def _orch() -> SubagentOrchestrator:
    """Real orchestrator via its constructor (parent mocked) — same shape
    as test_spawn_fanout's fixture — then tests swap in a fake runner."""
    from unittest.mock import MagicMock

    from tests.test_subagent_orchestrator import _child_config

    agent = MagicMock()
    agent.config = _child_config({})
    for attr, val in (
        ("model", "test-model"), ("workspace", "/tmp"),
        ("show_thinking", False), ("chars_per_token", 4),
        ("ollama_url", "http://localhost:11434"), ("temperature", 0.2),
        ("max_context_tokens", 128000),
        ("_context_tokens_explicit", True),
        ("permission_mode", "auto"), ("max_iterations", 30),
        ("subagent_pool_size", 4), ("max_subagent_depth", 2),
        ("max_subagent_branching", 3),
    ):
        setattr(agent.config, attr, val)
    return SubagentOrchestrator(parent_agent=agent)


class _Events:
    def __init__(self):
        self.log: list[tuple[str, str]] = []

    def __call__(self, kind, name):
        self.log.append((kind, name))


@pytest.mark.asyncio
async def test_cancel_mid_level_cancels_all_running_nodes():
    events = _Events()

    async def executor(node):
        events("start", node.name)
        try:
            await asyncio.sleep(30)
            events("done", node.name)
        except asyncio.CancelledError:
            events("cancelled", node.name)
            raise

    dag = TaskDAG()
    for i in range(3):
        dag.add_node(TaskNode(name=f"n{i}", task=None))

    sched = DAGScheduler(max_parallelism=4)
    task = asyncio.create_task(sched.execute(dag, executor))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    started = [n for k, n in events.log if k == "start"]
    cancelled = [n for k, n in events.log if k == "cancelled"]
    done = [n for k, n in events.log if k == "done"]
    assert sorted(started) == ["n0", "n1", "n2"]
    assert sorted(cancelled) == started, f"nodes survived cancel: {done=}"
    assert done == [], "completed nodes after outer cancel"


@pytest.mark.asyncio
async def test_cancel_during_level0_never_starts_level1():
    events = _Events()

    async def executor(node):
        events("start", node.name)
        if node.name == "root":
            await asyncio.sleep(30)
        else:
            await asyncio.sleep(0.01)  # fast dependent
        events("done", node.name)

    dag = TaskDAG()
    dag.add_node(TaskNode(name="root", task=None))
    dag.add_node(TaskNode(name="child-a", task=None, dependencies=["root"]))
    dag.add_node(TaskNode(name="child-b", task=None, dependencies=["root"]))

    sched = DAGScheduler()
    task = asyncio.create_task(sched.execute(dag, executor))
    await asyncio.sleep(0.05)  # root parked in its long sleep
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    started = [n for k, n in events.log if k == "start"]
    assert "root" in started
    assert "child-a" not in started and "child-b" not in started, (
        f"dependents started after root's level was cancelled: {started}"
    )


@pytest.mark.asyncio
async def test_orchestrator_run_dag_honors_outer_cancel():
    """Through the REAL orchestrator._executor wrapper (contract handling,
    budget wiring, prompt augmentation): cancel must still reach nodes."""

    orch = _orch()

    class _FakeRunner:
        async def run(self, *args, **kwargs):
            contract = kwargs.get("contract") or args[0]
            orch.events.append(("started", contract.name))
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                orch.events.append(("cancelled", contract.name))
                raise

    orch._runner = _FakeRunner()
    orch.events = []
    orch._resolve_worktree = lambda c: _async_none()
    orch._fire_subagent_hook = lambda *a, **k: _async_none()
    orch._validate_output = lambda r, c: _async_result(r)

    from wisp.multi_agent.task import SubagentContract
    contract = SubagentContract(
        name="leaf", role="generalist", task="do things",
    )

    dag = TaskDAG()
    dag.add_node(TaskNode(name="leaf", task=contract))

    task = asyncio.create_task(orch.run_dag(dag))
    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        if any(k == "started" for k, _ in orch.events):
            break
        await asyncio.sleep(0.02)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    kinds = [k for k, _ in orch.events]
    assert kinds == ["started", "cancelled"], (
        f"node finished or never observed cancellation: {orch.events}"
    )


async def _async_none():
    return None


async def _async_result(value):
    return value
