"""Tests for researcher role prompt — verifies fail-fast guidance for web_fetch."""


from wisp.multi_agent.roles import ROLE_CONFIGS, AgentRole


class TestResearcherRolePrompt:
    """Tests for the researcher role's system prompt containing fail-fast guidance."""

    def test_researcher_role_exists(self):
        """The researcher role should be configured."""
        assert AgentRole.RESEARCHER in ROLE_CONFIGS

    def test_researcher_prompt_contains_fail_fast_guidance(self):
        """The researcher prompt should instruct failing fast on web_fetch errors."""
        prompt = ROLE_CONFIGS[AgentRole.RESEARCHER].system_prompt
        assert "FAIL FAST" in prompt, \
            "Researcher should have FAIL FAST guidance"

    def test_researcher_prompt_mentions_404(self):
        """The researcher prompt should mention 404 errors."""
        prompt = ROLE_CONFIGS[AgentRole.RESEARCHER].system_prompt
        assert "404" in prompt or "HTTP error" in prompt, \
            "Researcher should be told about HTTP errors"

    def test_researcher_prompt_mentions_dns_failure(self):
        """The researcher prompt should mention DNS resolution failure."""
        prompt = ROLE_CONFIGS[AgentRole.RESEARCHER].system_prompt
        assert "DNS" in prompt or "connection error" in prompt, \
            "Researcher should be told about connection errors"

    def test_researcher_prompt_tells_to_stop_on_failures(self):
        """The researcher prompt should tell the agent to STOP after consecutive failures."""
        prompt = ROLE_CONFIGS[AgentRole.RESEARCHER].system_prompt
        assert "STOP" in prompt, \
            "Researcher should be told to STOP trying new URLs"
        assert "2 consecutive" in prompt or "consecutive" in prompt, \
            "Should mention a threshold of consecutive failures"

    def test_researcher_prompt_tells_to_report_even_if_nothing(self):
        """The researcher should be told to produce a report even if nothing found."""
        prompt = ROLE_CONFIGS[AgentRole.RESEARCHER].system_prompt
        assert "nothing found" in prompt or "even if that's nothing" in prompt or "even if nothing" in prompt, \
            "Should instruct reporting even if nothing was found"

    def test_researcher_has_web_tools(self):
        """Researcher should have web_fetch and web_search in allowed tools."""
        config = ROLE_CONFIGS[AgentRole.RESEARCHER]
        assert "web_fetch" in config.allowed_tools, \
            "Researcher needs web_fetch"
        assert "search_symbols" in config.allowed_tools, \
            "Researcher needs search_symbols"
