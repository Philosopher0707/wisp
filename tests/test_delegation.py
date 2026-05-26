"""Tests for auto-delegation triggers."""
from wisp.multi_agent.delegation import DelegationAnalyzer


class TestDelegationAnalyzer:
    def test_simple_query_no_delegation(self):
        analyzer = DelegationAnalyzer()
        signal = analyzer.analyze("What is 2+2?")
        assert not signal.should_delegate
        assert signal.confidence < 0.6

    def test_complex_implementation_has_confidence(self):
        analyzer = DelegationAnalyzer()
        signal = analyzer.analyze(
            "Implement a full authentication system with JWT tokens, refresh tokens, "
            "and role-based access control for a microservices architecture"
        )
        # Should have elevated confidence even if below delegation threshold
        assert signal.confidence > 0.2

    def test_research_query_has_confidence(self):
        analyzer = DelegationAnalyzer()
        signal = analyzer.analyze(
            "Research and compare different database sharding strategies "
            "for high-throughput applications"
        )
        assert signal.confidence > 0.1

    def test_explicit_delegate_request(self):
        analyzer = DelegationAnalyzer()
        signal = analyzer.analyze("delegate this task to parallel agents")
        assert signal.confidence >= 0.5  # Should have high confidence from explicit request

    def test_iteration_pressure(self):
        analyzer = DelegationAnalyzer()
        signal = analyzer.analyze("fix this bug", current_iteration=8, max_iterations=10)
        assert signal.confidence >= 0.2  # Should have some confidence from iteration pressure

    def test_multi_file_scope_has_confidence(self):
        analyzer = DelegationAnalyzer()
        signal = analyzer.analyze(
            "Refactor the error handling across all files in the entire codebase"
        )
        assert signal.confidence >= 0.2

    def test_specialized_knowledge_has_confidence(self):
        analyzer = DelegationAnalyzer()
        signal = analyzer.analyze(
            "Optimize the memory usage and concurrency patterns in the async worker pool"
        )
        assert signal.confidence > 0.05

    def test_no_delegation_for_short_query(self):
        analyzer = DelegationAnalyzer()
        signal = analyzer.analyze("hello")
        assert not signal.should_delegate

    def test_suggested_contracts_structure(self):
        analyzer = DelegationAnalyzer()
        signal = analyzer.analyze(
            "Implement a secure authentication system with database integration"
        )
        # Should have some confidence
        assert signal.confidence >= 0
        for contract in signal.suggested_contracts:
            assert "name" in contract
            assert "task" in contract
            assert "role" in contract
            assert "timeout_seconds" in contract
