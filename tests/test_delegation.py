"""Tests for auto-delegation triggers."""
from wisp.multi_agent.delegation import DelegationAnalyzer


class _MockCore:
    """Tripwire: delegation-refusal tests must never construct a core."""

    def __init__(self):
        raise AssertionError(
            "core_factory was invoked — the gate should have refused before this"
        )


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


# ═══════════════════════════════════════════════════════════════════
# Grill Q3: explicit fast-path bypasses scoring; auto path is stricter
# ═══════════════════════════════════════════════════════════════════


class TestExplicitFastPath:
    """Naming subagents in the prompt IS the decision — confidence 1.0."""

    def test_user_says_subagents_gets_confidence_one(self):
        analyzer = DelegationAnalyzer()
        signal = analyzer.analyze(
            "use subagents to research and findout the details of the "
            "last 12 years policies launched by Modi government"
        )
        assert signal.should_delegate
        assert signal.confidence == 1.0
        assert "explicit_request" in signal.reason

    def test_fast_path_skips_llm_classifier(self):
        import asyncio

        from wisp.multi_agent.delegation import DelegationAnalyzer

        analyzer = DelegationAnalyzer()

        async def boom(_prompt):
            raise AssertionError("LLM must not be called for explicit requests")

        signal = asyncio.run(analyzer.analyze_with_llm("spawn agents for this", boom))
        assert signal.should_delegate and signal.confidence == 1.0

    def test_all_explicit_phrasings_match(self):
        analyzer = DelegationAnalyzer()
        for phrase in (
            "use subagents to look into X",
            "delegate this research",
            "fanout workers on it",
            "run parallel agents please",
        ):
            signal = analyzer.analyze(phrase)
            assert signal.should_delegate, phrase
            assert signal.confidence == 1.0, phrase


class TestStricterAutoThreshold:
    """Implicit auto-delegation now needs 0.45, not 0.18."""

    def test_config_default_raised(self):
        from wisp.config import WispConfig

        cfg = WispConfig()
        assert cfg.delegation_threshold == 0.45

    def test_moderate_research_prompt_no_longer_auto_delegates(self, tmp_path):
        import asyncio

        from wisp.core.runtime import AgentRuntime
        from wisp.infra.store import UnifiedStore
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.security import PermissionMode, SecurityPolicy
        from wisp.infra.telemetry import Telemetry
        from unittest.mock import AsyncMock, MagicMock, patch

        runtime = AgentRuntime(
            store=UnifiedStore(tmp_path / "t.db"),
            security=SecurityPolicy(permission_mode=PermissionMode.FULL),
            extensions=ExtensionHost(),
            telemetry=Telemetry(),
            core_factory=lambda: _MockCore(),
        )
        signal = MagicMock()
        signal.should_delegate = True
        signal.confidence = 0.30  # passes old 0.18 gate, fails 0.45
        signal.reason = "research keywords"
        signal.suggested_contracts = [{"name": "r", "task": "x"}]
        analyzer = MagicMock()
        analyzer.analyze_with_llm = AsyncMock(return_value=signal)
        with patch(
            "wisp.multi_agent.delegation.get_delegation_analyzer",
            return_value=analyzer,
        ):
            result = asyncio.run(runtime._maybe_delegate(
                "research database sharding strategies for our stack",
                {"id": "s", "messages": []}, MagicMock(delegation_threshold=0.45),
            ))
        assert result is None, "implicit 0.30 must not trigger delegation"
