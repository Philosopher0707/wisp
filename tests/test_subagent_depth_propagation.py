"""Regression tests for subagent depth propagation.

Issue: SubagentRunner creates a new WispAgentCore per subagent but never
sets _subagent_depth / _subagent_branch_count on the child instance. This
means nested subagents always reset depth=0, bypassing the depth guard.
"""

import pytest
from unittest.mock import patch, MagicMock

from wisp.multi_agent import SubagentOrchestrator, SubagentContract
from wisp.multi_agent.task import SubagentResult


class FakeWispAgentCore:
    """Captures depth/branch_count so tests can inspect propagation."""

    created_instances: list = []

    def __init__(self, config=None, session=None, role=""):
        self.config = config or MagicMock()
        self.config.workspace = "/tmp"
        self.session = session
        self.role = role
        self._subagent_depth = 0
        self._subagent_branch_count = 0
        self.messages = []
        self.closed = False
        FakeWispAgentCore.created_instances.append(self)

    async def run_task(self, **kwargs):
        return {
            "success": True,
            "output": f"depth={getattr(self, '_subagent_depth', 0)}",
        }

    def close(self):
        self.closed = True

    @classmethod
    def reset(cls):
        cls.created_instances = []


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


@pytest.mark.asyncio
async def test_subagent_depth_is_propagated_from_contract(orch):
    """When a subagent spawns, its WispAgentCore must inherit the depth from the contract."""
    FakeWispAgentCore.reset()

    contract = SubagentContract(
        name="child",
        task="hello",
        role="coder",
        _subagent_depth=1,
        _subagent_branch_count=3,
    )

    with patch("wisp.core.agent.WispAgentCore", FakeWispAgentCore):
        result = await orch.run(contract)

    assert result.success is True
    assert len(FakeWispAgentCore.created_instances) == 1
    child = FakeWispAgentCore.created_instances[0]
    assert child._subagent_depth == 1
    assert child._subagent_branch_count == 3


@pytest.mark.asyncio
async def test_subagent_depth_zero_when_not_specified(orch):
    """Default depth=0 and branch_count=0 when contract omits them."""
    FakeWispAgentCore.reset()

    contract = SubagentContract(name="default", task="hello", role="coder")

    with patch("wisp.core.agent.WispAgentCore", FakeWispAgentCore):
        result = await orch.run(contract)

    assert result.success is True
    assert len(FakeWispAgentCore.created_instances) == 1
    child = FakeWispAgentCore.created_instances[0]
    assert child._subagent_depth == 0
    assert child._subagent_branch_count == 0
