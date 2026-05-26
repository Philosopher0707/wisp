"""Tests for capability mismatch detection."""
from wisp.multi_agent.capability_matcher import (
    CapabilityMatcher,
    CapabilityMismatch,
    detect_mismatch,
    suggest_role,
)
from wisp.multi_agent.roles import AgentRole


class TestCapabilityMatcher:
    def test_no_mismatch_when_role_matches(self):
        matcher = CapabilityMatcher()
        mismatch = matcher.detect_mismatch(
            current_role=AgentRole.CODER,
            task="Implement a new authentication module",
            available_tools=["write_file", "edit_file", "read_file"],
        )
        assert mismatch is None

    def test_mismatch_reviewer_trying_to_code(self):
        matcher = CapabilityMatcher()
        mismatch = matcher.detect_mismatch(
            current_role=AgentRole.REVIEWER,
            task="Write unit tests for auth.py",
            available_tools=["read_file", "git_diff"],
        )
        assert mismatch is not None
        assert mismatch.required_role == AgentRole.TESTER
        assert mismatch.confidence > 0.2
        assert "write_file" in mismatch.required_tools

    def test_mismatch_tester_missing_run_bash(self):
        matcher = CapabilityMatcher()
        mismatch = matcher.detect_mismatch(
            current_role=AgentRole.REVIEWER,
            task="Run pytest and check coverage",
            available_tools=["read_file", "write_file"],
        )
        assert mismatch is not None
        assert mismatch.required_role == AgentRole.TESTER
        assert "run_bash" in mismatch.required_tools

    def test_researcher_needs_web_tools(self):
        matcher = CapabilityMatcher()
        mismatch = matcher.detect_mismatch(
            current_role=AgentRole.CODER,
            task="Research the latest React patterns online",
            available_tools=["read_file", "edit_file"],
        )
        assert mismatch is not None
        assert mismatch.required_role == AgentRole.RESEARCHER
        assert "web_fetch" in mismatch.required_tools

    def test_debugger_role_detected(self):
        matcher = CapabilityMatcher()
        mismatch = matcher.detect_mismatch(
            current_role=AgentRole.RESEARCHER,
            task="Debug this segmentation fault in the C extension",
            available_tools=["web_fetch", "web_search"],
        )
        assert mismatch is not None
        assert mismatch.required_role == AgentRole.DEBUGGER

    def test_planner_role_detected(self):
        matcher = CapabilityMatcher()
        mismatch = matcher.detect_mismatch(
            current_role=AgentRole.CODER,
            task="Design the architecture for a microservices system",
            available_tools=["write_file", "edit_file"],
        )
        assert mismatch is not None
        assert mismatch.required_role == AgentRole.PLANNER

    def test_low_confidence_no_mismatch(self):
        matcher = CapabilityMatcher(delegation_threshold=0.8)
        mismatch = matcher.detect_mismatch(
            current_role=AgentRole.CODER,
            task="hello",
            available_tools=["read_file"],
        )
        assert mismatch is None

    def test_suggest_role_for_testing(self):
        matcher = CapabilityMatcher()
        role = matcher.suggest_role("Run pytest and verify test coverage")
        assert role == AgentRole.TESTER

    def test_suggest_role_for_research(self):
        matcher = CapabilityMatcher()
        role = matcher.suggest_role("Research database sharding strategies")
        assert role == AgentRole.RESEARCHER

    def test_suggest_role_for_debugging(self):
        matcher = CapabilityMatcher()
        role = matcher.suggest_role("Fix the null pointer exception")
        assert role == AgentRole.DEBUGGER

    def test_build_delegation_contract(self):
        matcher = CapabilityMatcher()
        mismatch = CapabilityMismatch(
            reason="Needs coder role",
            required_role=AgentRole.CODER,
            required_tools=["write_file"],
            confidence=0.8,
        )
        contract = matcher.build_delegation_contract(mismatch, "Implement auth")
        assert contract.role == AgentRole.CODER
        assert contract.name == "delegate-coder"
        assert "write_file" in contract.tools or contract.tools == ["all"]

    def test_build_delegation_contract_with_context(self):
        matcher = CapabilityMatcher()
        mismatch = CapabilityMismatch(
            reason="Needs researcher role",
            required_role=AgentRole.RESEARCHER,
            required_tools=["web_fetch"],
            confidence=0.8,
        )
        contract = matcher.build_delegation_contract(
            mismatch, "Research LLMs", parent_context="Current project: Wisp"
        )
        assert "Current project: Wisp" in (contract.system_prompt_extra or "")

    def test_missing_tools_for_task(self):
        matcher = CapabilityMatcher()
        missing = matcher._missing_tools_for_task(
            "run pytest and check coverage", ["read_file", "write_file"]
        )
        assert "run_bash" in missing

    def test_no_missing_tools_when_available(self):
        matcher = CapabilityMatcher()
        missing = matcher._missing_tools_for_task(
            "run pytest and check coverage", ["read_file", "run_bash"]
        )
        assert "run_bash" not in missing

    def test_convenience_detect_mismatch(self):
        mismatch = detect_mismatch(
            current_role=AgentRole.REVIEWER,
            task="Implement a new feature",
            available_tools=["read_file"],
        )
        assert mismatch is not None
        assert mismatch.required_role == AgentRole.CODER

    def test_convenience_suggest_role(self):
        role = suggest_role("Debug the crash in production")
        assert role == AgentRole.DEBUGGER


class TestCapabilityMismatchDataclass:
    def test_should_delegate_with_threshold(self):
        mismatch = CapabilityMismatch(
            reason="test", required_role="coder", required_tools=[], confidence=0.7
        )
        assert mismatch.should_delegate(threshold=0.6)
        assert not mismatch.should_delegate(threshold=0.8)

    def test_default_confidence(self):
        mismatch = CapabilityMismatch(
            reason="test", required_role="coder", required_tools=[]
        )
        assert mismatch.confidence == 0.5
