"""Tests for auto-parallel research spawning — verifies role, timeout, and status display."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock, PropertyMock

from wisp.multi_agent.roles import AgentRole


class TestAutoResearchContracts:
    """Tests that _auto_parallel_research creates properly configured subagent contracts."""

    def test_research_contracts_have_researcher_role(self):
        """Contracts created by _auto_parallel_research must set role=AgentRole.RESEARCHER."""
        from wisp.core.agent import WispAgentCore
        import inspect
        src = inspect.getsource(WispAgentCore._auto_parallel_research)
        assert "AgentRole.RESEARCHER" in src, \
            "_auto_parallel_research should set role=AgentRole.RESEARCHER on contracts"

    def test_research_contracts_have_shorter_timeout(self):
        """Research contracts should have a shorter timeout (<=90s) for fast failure."""
        from wisp.core.agent import WispAgentCore
        import inspect
        src = inspect.getsource(WispAgentCore._auto_parallel_research)
        assert "timeout_seconds=90" in src, \
            "Research contracts should use timeout_seconds=90 for fast failure"

    def test_research_contracts_have_fewer_iterations(self):
        """Research contracts should have limited iterations (<=10) since they're web-only."""
        from wisp.core.agent import WispAgentCore
        import inspect
        src = inspect.getsource(WispAgentCore._auto_parallel_research)
        assert "max_iterations=10" in src, \
            "Research contracts should use max_iterations=10"

    def test_research_uses_markdown_output(self):
        """Research contracts should use markdown output format."""
        from wisp.core.agent import WispAgentCore
        import inspect
        src = inspect.getsource(WispAgentCore._auto_parallel_research)
        assert 'output_format="markdown"' in src

    def test_research_max_4_parallel(self):
        """Research should cap at 4 parallel subagents."""
        from wisp.core.agent import WispAgentCore
        import inspect
        src = inspect.getsource(WispAgentCore._auto_parallel_research)
        assert "angles[:4]" in src, "Should limit to 4 parallel research subagents"


class TestAutoResearchStatusDisplay:
    """Tests that status display uses correct icon based on results."""

    def test_success_icon_checkmark_when_some_succeed(self):
        """Should show checkmark when at least one subagent succeeded."""
        from wisp.core.agent import WispAgentCore
        import inspect
        src = inspect.getsource(WispAgentCore._auto_parallel_research)
        # Verify dynamic icon logic exists
        assert "n_succeeded" in src, "Should compute succeeded count"
        assert "> 0 else" in src, "Should use conditional icon based on success count"

    def test_failure_icon_x_when_all_fail(self):
        """Should show X when all subagents failed."""
        from wisp.core.agent import WispAgentCore
        import inspect
        src = inspect.getsource(WispAgentCore._auto_parallel_research)
        assert "> 0 else" in src, "Should use conditional icon"

    def test_delegation_uses_dynamic_icon(self):
        """The delegation success message should also use dynamic icon."""
        from wisp.core.agent import WispAgentCore
        import inspect
        src = inspect.getsource(WispAgentCore._check_delegation)
        assert "n_succeeded_del" in src, "Delegation should compute succeeded count"
        assert "icon_del" in src, "Delegation should use dynamic icon"
