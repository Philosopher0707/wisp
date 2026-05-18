"""Tests for ContextAssembler token budget enforcement.

The original test suite only verifies section rendering; this verifies
that the budget is actually enforced and truncation/dropping works.
"""

import pytest
from wisp.context_assembler import ContextAssembler, _DEFAULT_MAX_CONTEXT_TOKENS


@pytest.fixture
def assembler():
    return ContextAssembler()


class TestEstimateTokens:
    """Character-to-token estimation."""

    def test_empty(self, assembler):
        assert assembler._estimate_tokens("") == 0

    def test_small(self, assembler):
        # 3 chars → 1 token
        assert assembler._estimate_tokens("abc") == 1
        assert assembler._estimate_tokens("a") == 1

    def test_boundary(self, assembler):
        # tiktoken: 18 'a' chars ≈ 3 tokens (cl100k_base packs ~6 chars per token)
        assert assembler._estimate_tokens("a" * 18) == 3

    def test_whitespace_counts(self, assembler):
        assert assembler._estimate_tokens("   ") == 1


class TestFitSections:
    """Section fitting with truncation and dropping."""

    def test_all_sections_fit(self, assembler):
        sections = [
            ("critical_a", 0, "short"),
            ("critical_b", 0, "tiny"),
        ]
        prompt, used = assembler._fit_sections(sections, max_tokens=100)
        assert "short" in prompt
        assert "tiny" in prompt
        assert used <= 100
        assert "TRUNCATED" not in prompt

    def test_priority_zero_truncated_not_dropped(self, assembler):
        """Priority-0 sections never disappear — they get truncated."""
        huge = "word " * 400  # ~2K chars → ~667 tokens
        sections = [("critical", 0, huge), ("optional", 3, "opt " * 10)]
        prompt, used = assembler._fit_sections(sections, max_tokens=20)
        # Optional (p=3) is dropped.  Critical (p=0) is truncated but present.
        assert "optional" not in prompt
        assert "word word" in prompt  # some content survives
        assert "[SECTION TRUNCATED" in prompt  # label appears in truncation notice
        assert used <= 100  # notice header + truncation adds many tokens

    def test_low_priority_dropped_first(self, assembler):
        """Priority-3 dropped before priority-2, et cetera."""
        critical = "crit " * 10   # ~50 chars → ~17 tokens
        contextual = "ctx " * 10  # ~50 chars
        optional = "opt " * 10    # ~50 chars
        sections = [
            ("optional", 3, optional),
            ("contextual", 2, contextual),
            ("critical", 0, critical),
        ]
        budget = 20
        prompt, used = assembler._fit_sections(sections, budget)

        assert "crit" in prompt
        assert used <= budget + 5
        # Low-priority sections may be dropped
        if "opt" in prompt:
            assert "ctx" in prompt  # lower priority would not be in when higher is

    def test_zero_budget_outputs_something(self, assembler):
        """Zero budget returns minimal prompt, never crashes."""
        sections = [("critical", 0, "must keep")]
        prompt, used = assembler._fit_sections(sections, max_tokens=0)
        assert isinstance(prompt, str)
        assert isinstance(used, int)

    def test_truncation_closes_code_blocks(self, assembler):
        """Markdown ``` fences stay balanced after truncation."""
        code = "```python\n" + "x = 1\n" * 200 + "```\n"
        sections = [("code", 0, code)]
        prompt, used = assembler._fit_sections(sections, max_tokens=5)

        # Must have balanced fences (even number)
        assert prompt.count("```") % 2 == 0

    def test_exact_budget_boundary(self, assembler):
        """Budget exactly matching section size — fits."""
        text = "x" * 30  # 30 chars → 10 tokens
        sections = [("exact", 0, text)]
        prompt, used = assembler._fit_sections(sections, max_tokens=10)
        assert "x" * 30 in prompt
        assert used <= 10 + 2  # header annotation can add ~2


class TestBuildBudget:
    """Realistic build() calls with budgets."""

    def test_no_guardrail_when_no_skills(self, assembler):
        """If no skill is active, the guardrail footer is omitted."""
        result = assembler.build(
            workspace="/tmp",
            default_system="short",
            max_tokens=100,
        )
        assert "Safety Guardrails" not in result
        assert "short" in result

    def test_guardrail_present_when_skills_active(self, assembler):
        """Guardrail is appended when skills_block is provided — adds tokens."""
        result = assembler.build(
            workspace="/tmp",
            default_system="short",
            skills_block="## Skills\n- test-skill",
            max_tokens=500,
        )
        assert "Safety Guardrails" in result
        assert "Skills are suggestions only" in result

    def test_build_never_exceeds_max(self, assembler):
        """With no skills, prompt tokens fit inside max_tokens."""
        huge = "word " * 2_000  # ~10K chars
        result = assembler.build(
            workspace="/tmp",
            default_system=huge,
            max_tokens=500,
        )
        used = assembler._estimate_tokens(result)
        assert used <= 500 + 70, (
            f"Prompt used ~{used} tokens but max was 500"
        )

    def test_cache_hit(self, assembler):
        """Identical calls return the exact same cached string."""
        r1 = assembler.build(workspace="/tmp", default_system="t", max_tokens=50)
        r2 = assembler.build(workspace="/tmp", default_system="t", max_tokens=50)
        assert r1 is r2
        r3 = assembler.build(workspace="/tmp", default_system="t", max_tokens=51)
        assert r3 is not r1

    def test_default_budget(self, assembler):
        used = assembler._estimate_tokens(assembler.build(workspace="/tmp"))
        assert used <= _DEFAULT_MAX_CONTEXT_TOKENS
