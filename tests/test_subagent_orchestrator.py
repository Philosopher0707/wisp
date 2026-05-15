"""Tests for the unified SubagentOrchestrator.

These tests verify the new orchestrator without hitting real Ollama or
running real git commands.  Worktree creation is mocked where needed.
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from wisp.multi_agent import SubagentOrchestrator, SubagentContract, UnifiedSubagentResult as SubagentResult
from wisp.multi_agent.task import EventKind, OrchestratorEvent


class FakeWispAgentCore:
    """Minimal fake agent core for testing SubagentOrchestrator."""

    def __init__(self, config=None, session=None, role=""):
        self.config = config or MagicMock()
        self.config.workspace = "/tmp"
        self.session = session
        self.role = role
        self.messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Do the task."},
            {"role": "assistant", "content": "Fake output"},
        ]
        self.closed = False

    async def run_task(self, **kwargs):
        # Return a canned result
        return {
            "success": True,
            "output": "Fake output",
        }

    def close(self):
        self.closed = True


@pytest.fixture
def mock_parent_agent():
    agent = MagicMock()
    agent.config.model = "test-model"
    agent.config.workspace = "/tmp"
    agent.config.show_thinking = False
    agent.config.chars_per_token = 4
    agent.config.ollama_url = "http://localhost:11434"
    agent.config.temperature = 0.2
    agent.config.max_context_tokens = 128000
    agent.config._context_tokens_explicit = True
    agent.config.permission_mode = "auto"
    agent.config.max_iterations = 30
    return agent


@pytest.fixture
def orch(mock_parent_agent):
    return SubagentOrchestrator(parent_agent=mock_parent_agent)


# ── Contract tests ───────────────────────────────────────────────────


def test_contract_defaults():
    c = SubagentContract(task="do something")
    assert c.task == "do something"
    assert c.tools == ["all"]
    assert c.max_iterations == 15
    assert c.timeout_seconds == 120.0
    assert c.output_format == "text"
    assert c.model is None
    assert c.workspace is None
    assert c.worktree_isolated is True


def test_contract_overrides():
    c = SubagentContract(
        task="research",
        tools=["read_file", "web_fetch"],
        max_iterations=5,
        timeout_seconds=30.0,
        output_format="json",
        model="qwen2.5",
        workspace="/home",
        worktree_isolated=False,
    )
    assert c.tools == ["read_file", "web_fetch"]
    assert c.max_iterations == 5
    assert c.timeout_seconds == 30.0
    assert c.output_format == "json"
    assert c.model == "qwen2.5"
    assert c.workspace == "/home"
    assert c.worktree_isolated is False


# ── Orchestrator construction ──────────────────────────────────────────


def test_orchestrator_inherits_from_parent(mock_parent_agent):
    o = SubagentOrchestrator(parent_agent=mock_parent_agent)
    assert o.config is mock_parent_agent.config
    assert o.parent is mock_parent_agent


def test_orchestrator_with_explicit_config():
    from wisp.config import WispConfig
    cfg = WispConfig()
    cfg.model = "explicit-model"
    o = SubagentOrchestrator(config=cfg)
    assert o.config.model == "explicit-model"
    assert o.parent is None


def test_orchestrator_workspace_fallback():
    o = SubagentOrchestrator()
    assert o.workspace == Path.cwd().resolve()


# ── run() tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_success(orch):
    with patch("wisp.core.agent.WispAgentCore", FakeWispAgentCore):
        contract = SubagentContract(name="test", task="hello")
        result = await orch.run(contract)
    assert isinstance(result, SubagentResult)
    assert result.success is True
    assert result.output == "Fake output"
    assert result.task_id == "test"


@pytest.mark.asyncio
async def test_run_with_progress_events(orch):
    events = []

    async def callback(event):
        events.append(event)

    with patch("wisp.core.agent.WispAgentCore", FakeWispAgentCore):
        contract = SubagentContract(
            name="test", task="hello", progress_callback=callback
        )
        result = await orch.run(contract)

    assert result.success is True
    assert len(events) >= 2
    assert events[0].event_type == EventKind.TASK_STARTED
    assert events[-1].event_type == EventKind.TASK_COMPLETED


@pytest.mark.asyncio
async def test_run_timeout(orch):
    class SlowAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            await asyncio.sleep(10)
            return {"success": True, "output": "too late"}

    with patch("wisp.core.agent.WispAgentCore", SlowAgent):
        contract = SubagentContract(name="slow", task="sleep", timeout_seconds=0.1)
        result = await orch.run(contract)

    assert result.success is False
    assert result.timed_out is True
    assert "TIMED OUT" in result.output


@pytest.mark.asyncio
async def test_run_crashed_agent(orch):
    class BrokenAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            raise RuntimeError("simulated crash")

    with patch("wisp.core.agent.WispAgentCore", BrokenAgent):
        contract = SubagentContract(name="broken", task="crash")
        result = await orch.run(contract)

    assert result.success is False
    assert "simulated crash" in result.error


# ── run_parallel() tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_parallel_success(orch):
    with patch("wisp.core.agent.WispAgentCore", FakeWispAgentCore):
        contracts = [
            SubagentContract(name=f"task-{i}", task=f"job {i}")
            for i in range(3)
        ]
        results = await orch.run_parallel(contracts, max_concurrent=2)

    assert len(results) == 3
    assert all(r.success for r in results)
    assert [r.task_id for r in results] == ["task-0", "task-1", "task-2"]


@pytest.mark.asyncio
async def test_run_parallel_mixed_results(orch):
    class FlakyAgent(FakeWispAgentCore):
        counter = 0

        async def run_task(self, **kwargs):
            FlakyAgent.counter += 1
            if FlakyAgent.counter == 2:
                raise RuntimeError("fail")
            return {"success": True, "output": "ok"}

    with patch("wisp.core.agent.WispAgentCore", FlakyAgent):
        contracts = [
            SubagentContract(name=f"task-{i}", task=f"job {i}")
            for i in range(3)
        ]
        results = await orch.run_parallel(contracts, max_concurrent=3)

    assert len(results) == 3
    assert results[0].success is True
    assert results[1].success is False
    assert results[2].success is True


# ── Schema validation tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_validates_json_schema(orch):
    class JSONAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            return {
                "success": True,
                "output": json.dumps({"findings": [{"severity": "HIGH"}], "summary": "ok"}),
            }

    schema = {
        "type": "object",
        "properties": {
            "findings": {"type": "array"},
            "summary": {"type": "string"},
        },
        "required": ["findings", "summary"],
    }

    with patch("wisp.core.agent.WispAgentCore", JSONAgent):
        contract = SubagentContract(
            name="schema-test",
            task="return json",
            output_format="json",
            output_schema=schema,
        )
        result = await orch.run(contract)

    assert result.success is True
    assert result.validated_output is not None
    assert result.validated_output["summary"] == "ok"


@pytest.mark.asyncio
async def test_run_schema_validation_failure_no_retry(orch):
    class BadJSONAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            return {"success": True, "output": "not json"}

    schema = {"type": "object", "properties": {"value": {"type": "integer"}}}

    with patch("wisp.core.agent.WispAgentCore", BadJSONAgent):
        contract = SubagentContract(
            name="bad-schema",
            task="return bad json",
            output_schema=schema,
            auto_retry_parse=False,
        )
        result = await orch.run(contract)

    assert result.success is True  # agent succeeded, but validation failed
    assert result.validated_output is None
    assert "not valid JSON" in result.error


# ── Worktree tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_without_worktree(orch):
    with patch("wisp.core.agent.WispAgentCore", FakeWispAgentCore):
        contract = SubagentContract(
            name="shared", task="hello", worktree_isolated=False
        )
        result = await orch.run(contract)
    assert result.success is True


# ── Result formatting tests ──────────────────────────────────────────


def test_result_backward_compat():
    r = SubagentResult(
        task_id="t1",
        success=True,
        output="ok",
        duration_seconds=5.0,
    )
    assert r.elapsed_seconds == 5.0


def test_contract_prompt_alias():
    c = SubagentContract(prompt="do this")
    assert c.task == "do this"


# ── Composable pattern tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_map_reduce(orch):
    with patch("wisp.core.agent.WispAgentCore", FakeWispAgentCore):
        result = await orch.run_map_reduce(
            task="Review codebase for bugs",
            items=["src/auth.py", "src/api.py"],
            mapper=lambda item: SubagentContract(
                name=f"review-{item}", task=f"Review {item} for bugs"
            ),
            reducer="Synthesize all reviews into a prioritized bug list.",
            max_concurrent=2,
        )

    assert isinstance(result, SubagentResult)
    assert result.task_id == "reducer"
    assert result.success is True


@pytest.mark.asyncio
async def test_run_vote_consensus(orch):
    """All agents agree — consensus reached."""
    class AgreeingAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            return {"success": True, "output": "YES, this is vulnerable."}

    with patch("wisp.core.agent.WispAgentCore", AgreeingAgent):
        result = await orch.run_vote(
            task="Is this function vulnerable?",
            agents=[
                SubagentContract(name="sec-1", role="security-auditor"),
                SubagentContract(name="sec-2", role="security-auditor"),
                SubagentContract(name="sec-3", role="security-auditor"),
            ],
            consensus_threshold=0.6,
        )

    assert result.success is True  # consensus reached
    assert "REACHED" in result.output
    assert "3/3" in result.output


@pytest.mark.asyncio
async def test_run_vote_no_consensus(orch):
    """Agents disagree — consensus not reached."""
    class DisagreeingAgent(FakeWispAgentCore):
        counter = 0

        async def run_task(self, **kwargs):
            DisagreeingAgent.counter += 1
            if DisagreeingAgent.counter == 1:
                return {"success": True, "output": "YES"}
            return {"success": True, "output": "NO"}

    with patch("wisp.core.agent.WispAgentCore", DisagreeingAgent):
        result = await orch.run_vote(
            task="Is this vulnerable?",
            agents=[
                SubagentContract(name="sec-1", role="security-auditor"),
                SubagentContract(name="sec-2", role="security-auditor"),
            ],
            consensus_threshold=0.6,
        )

    assert result.success is False  # no consensus
    assert "NOT REACHED" in result.output


@pytest.mark.asyncio
async def test_run_chain_success(orch):
    with patch("wisp.core.agent.WispAgentCore", FakeWispAgentCore):
        result = await orch.run_chain([
            SubagentContract(name="writer", task="Write code"),
            SubagentContract(name="reviewer", task="Review code"),
        ], pass_context=True)

    assert result.success is True
    assert "Chain Complete" in result.output
    assert "2 steps" in result.output


@pytest.mark.asyncio
async def test_run_chain_failure_midway(orch):
    class FailingAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            raise RuntimeError("step failed")

    with patch("wisp.core.agent.WispAgentCore", FailingAgent):
        result = await orch.run_chain([
            SubagentContract(name="step1", task="Do step 1"),
            SubagentContract(name="step2", task="Do step 2"),
        ], pass_context=True)

    assert result.success is False
    assert "Chain Failed" in result.output
    assert "step1" in result.output  # failed step name


# ── Token budget tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_global_token_budget_enforced(orch):
    """When global budget is exhausted, subsequent runs fail fast."""
    orch.set_global_token_budget(10)

    # First run should succeed but consume tokens
    with patch("wisp.core.agent.WispAgentCore", FakeWispAgentCore):
        result1 = await orch.run(SubagentContract(name="first", task="hello"))

    assert result1.success is True
    assert orch.get_tokens_consumed() > 0

    # Set budget to already-consumed amount to force exhaustion
    orch.set_global_token_budget(orch.get_tokens_consumed())

    with patch("wisp.core.agent.WispAgentCore", FakeWispAgentCore):
        result2 = await orch.run(SubagentContract(name="second", task="world"))

    assert result2.success is False
    assert "TOKEN BUDGET EXCEEDED" in result2.output
    assert "Global token budget exhausted" in result2.error


@pytest.mark.asyncio
async def test_token_estimation_tracked(orch):
    """Token counts are estimated and tracked on the result."""
    class MessageAgent(FakeWispAgentCore):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello there!"},
                {"role": "assistant", "content": "Hi! How can I help?"},
            ]

    with patch("wisp.core.agent.WispAgentCore", MessageAgent):
        result = await orch.run(SubagentContract(name="msg", task="hello"))

    assert result.success is True
    assert result.tokens_used > 0
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.tokens_used == result.input_tokens + result.output_tokens


@pytest.mark.asyncio
async def test_token_aggregation_in_chain(orch):
    """Chain aggregates token usage across all steps."""
    with patch("wisp.core.agent.WispAgentCore", FakeWispAgentCore):
        result = await orch.run_chain([
            SubagentContract(name="step1", task="Step 1"),
            SubagentContract(name="step2", task="Step 2"),
        ], pass_context=True)

    assert result.success is True
    assert result.tokens_used >= 0
    assert "tokens:" in result.output


@pytest.mark.asyncio
async def test_token_budget_remaining(orch):
    """get_token_budget_remaining reflects consumed tokens."""
    assert orch.get_token_budget_remaining() is None  # no budget set

    orch.set_global_token_budget(1000)
    assert orch.get_token_budget_remaining() == 1000

    with patch("wisp.core.agent.WispAgentCore", FakeWispAgentCore):
        await orch.run(SubagentContract(name="t1", task="hello"))

    remaining = orch.get_token_budget_remaining()
    assert remaining is not None
    assert remaining < 1000
    assert remaining >= 0


# ── AgentRegistry persistence tests ──────────────────────────────────────

def test_agent_registry_save_load(tmp_path):
    """Registry state survives save/load round-trip."""
    from wisp.multi_agent.registry import AgentRegistry, AgentRecord, AgentStatus

    reg = AgentRegistry()
    reg.register(AgentRecord(
        agent_id="agent-1",
        role="coder",
        status=AgentStatus.WORKING,
        current_task="fix bug",
        files_locked=["/tmp/test.py"],
        total_tasks_completed=5,
        total_tasks_failed=1,
    ))
    reg.register(AgentRecord(
        agent_id="agent-2",
        role="reviewer",
        status=AgentStatus.IDLE,
    ))

    path = tmp_path / "registry.json"
    reg.save(path)

    # Verify file exists and has content
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["version"] == 1
    assert len(data["agents"]) == 2

    # Load into fresh registry
    reg2 = AgentRegistry()
    reg2.load(path)

    assert reg2.count_active() == 2
    agents = {a.agent_id: a for a in reg2.list_agents()}
    assert "agent-1" in agents
    assert "agent-2" in agents

    a1 = agents["agent-1"]
    assert a1.role == "coder"
    assert a1.status == AgentStatus.WORKING
    assert a1.current_task == "fix bug"
    assert a1.files_locked == ["/tmp/test.py"]
    assert a1.total_tasks_completed == 5
    assert a1.total_tasks_failed == 1

    a2 = agents["agent-2"]
    assert a2.role == "reviewer"
    assert a2.status == AgentStatus.IDLE


def test_agent_registry_load_missing_file(tmp_path):
    """Loading a non-existent file is a no-op."""
    from wisp.multi_agent.registry import AgentRegistry

    reg = AgentRegistry()
    reg.load(tmp_path / "does_not_exist.json")
    assert reg.count_active() == 0


def test_agent_registry_from_dict():
    """Registry can be reconstructed from a dictionary."""
    from wisp.multi_agent.registry import AgentRegistry, AgentStatus

    data = {
        "agents": [
            {
                "agent_id": "a-1",
                "role": "tester",
                "status": "WORKING",
                "current_task": None,
                "files_locked": [],
                "spawned_at": "2024-01-01T00:00:00+00:00",
                "last_heartbeat": None,
                "total_tasks_completed": 3,
                "total_tasks_failed": 0,
            }
        ]
    }
    reg = AgentRegistry.from_dict(data)
    assert reg.count_active() == 1
    a = reg.get("a-1")
    assert a is not None
    assert a.role == "tester"
    assert a.status == AgentStatus.WORKING
    assert a.total_tasks_completed == 3
