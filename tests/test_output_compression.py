"""Tests for subagent output compression."""

from __future__ import annotations

import pytest

from wisp.multi_agent._runner import SubagentRunner


class TestCompressOutput:
    def test_short_output_unchanged(self):
        text = "This is a short output."
        result = SubagentRunner._compress_output(text, 1000, "test")
        assert result == text

    def test_long_output_gets_compressed(self):
        text = "x" * 2000
        result = SubagentRunner._compress_output(text, 500, "test")
        assert len(result) <= 500
        assert "COMPRESSED" in result
        assert "2000 chars" in result

    def test_preserves_first_section(self):
        text = (
            "## Summary\n"
            "I found a bug in the auth module.\n\n"
            "## Details\n" + "x" * 800 + "\n\n"
            "## Conclusion\n"
            "Fix the bug in line 42."
        )
        result = SubagentRunner._compress_output(text, 400, "test")
        assert "I found a bug" in result

    def test_preserves_last_section(self):
        text = (
            "## Summary\n"
            "Investigating the issue.\n\n"
            "## Details\n" + "x" * 800 + "\n\n"
            "## Conclusion\n"
            "Files changed: auth.py, utils.py"
        )
        result = SubagentRunner._compress_output(text, 400, "test")
        assert "Files changed" in result

    def test_includes_truncation_notice(self):
        text = "x" * 2000
        result = SubagentRunner._compress_output(text, 500, "exceeded 8000 chars")
        assert "COMPRESSED" in result
        assert "exceeded 8000 chars" in result

    def test_fallback_for_no_sections(self):
        text = "A" * 2000
        result = SubagentRunner._compress_output(text, 500, "test")
        assert len(result) <= 500
        assert "..." in result

    def test_preserves_code_blocks_priority(self):
        text = (
            "## Analysis\n"
            "Some analysis text.\n\n"
            "## Code\n"
            "```python\n"
            "def foo():\n"
            "    return 42\n"
            "```\n\n"
            "## More\n" + "y" * 600
        )
        result = SubagentRunner._compress_output(text, 400, "test")
        # Code should be prioritized over long prose
        assert "def foo" in result or "python" in result

    def test_very_small_budget(self):
        text = "x" * 100
        result = SubagentRunner._compress_output(text, 50, "test")
        assert len(result) <= 50

    def test_empty_text(self):
        result = SubagentRunner._compress_output("", 1000, "test")
        assert result == ""
