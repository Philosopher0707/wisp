"""Regression tests for subagent depth propagation.

Issue: SubagentRunner creates a new WispAgentCore per subagent but never
sets _subagent_depth / _subagent_branch_count on the child instance. This
means nested subagents always reset depth=0, bypassing the depth guard.
"""

import pytest
from unittest.mock import patch, MagicMock

from wisp.multi_agent import SubagentOrchestrator, SubagentContract


class FakeStatelessCore:
    """Captures depth/branch_count so tests can inspect propagation."""

    created_instances: list = []

    def __init__(self, provider=None, security=None, extensions=None, config=None, **kwargs):
        self.config = config or MagicMock()
        # Extract depth from config if it's a real value, not a MagicMock
        depth = getattr(config, "_subagent_depth", 0) if config else 0
        branch = getattr(config, "_subagent_branch_count", 0) if config else 0
        self._subagent_depth = depth if not isinstance(depth, MagicMock) else 0
        self._subagent_branch_count = branch if not isinstance(branch, MagicMock) else 0
        FakeStatelessCore.created_instances.append(self)

    async def turn(self, session_dict, task):
        yield {"type": "content", "text": f"depth={self._subagent_depth}"}
        yield {"type": "done"}

    @classmethod
    def reset(cls):
        cls.created_instances = []


@pytest.fixture
def mock_parent_agent():
    agent = MagicMock()
    agent.config.model = "test-model"
    agent.config.provider = "ollama"
    agent.config.workspace = "/tmp"
    agent.config.show_thinking = False
    agent.config.chars_per_token = 4
    agent.config.ollama_url = "http://localhost:11434"
    agent.config.temperature = 0.2
    agent.config.max_context_tokens = 128000
    agent.config._context_tokens_explicit = True
    agent.config.permission_mode = "auto"
    agent.config.max_iterations = 30
    agent.config.subagent_pool_size = 4
    agent.config.chars_per_token = 4
    agent.config.max_subagent_depth = 2
    agent.config.max_subagent_branching = 3
    return agent


@pytest.fixture
def orch(mock_parent_agent):
    return SubagentOrchestrator(parent_agent=mock_parent_agent)


@pytest.mark.asyncio
async def test_subagent_depth_is_propagated_from_contract(orch):
    """When a subagent spawns, its core must inherit the depth from the contract."""
    FakeStatelessCore.reset()

    contract = SubagentContract(
        name="child",
        task="hello",
        role="coder",
        _subagent_depth=1,
        _subagent_branch_count=3,
    )

    with patch("wisp.core.engine.WispAgentCore", FakeStatelessCore):
        result = await orch.run(contract)

    assert result.success is True
    assert len(FakeStatelessCore.created_instances) == 1
    child = FakeStatelessCore.created_instances[0]
    # In the new architecture depth is carried on the config object if set
    assert child._subagent_depth == 1
    assert child._subagent_branch_count == 3


@pytest.mark.asyncio
async def test_subagent_depth_zero_when_not_specified(orch):
    """Default depth=0 and branch_count=0 when contract omits them."""
    FakeStatelessCore.reset()

    contract = SubagentContract(name="default", task="hello", role="coder")

    with patch("wisp.core.engine.WispAgentCore", FakeStatelessCore):
        result = await orch.run(contract)

    assert result.success is True
    assert len(FakeStatelessCore.created_instances) == 1
    child = FakeStatelessCore.created_instances[0]
    assert child._subagent_depth == 0
    assert child._subagent_branch_count == 0
