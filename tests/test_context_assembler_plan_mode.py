"""Tests for plan_mode + budget interaction in ContextAssembler.

When plan_mode=True, a large static block is injected at priority 1.
This must not starve critical sections (priority 0).
"""

import pytest
from wisp.context_assembler import ContextAssembler


class TestPlanModeBudget:
    """When plan_mode=True, the LLM must still see priority-0 sections."""

    PLAN_MODE_MARKER = "PLAN MODE ACTIVE"

    def test_plan_mode_present(self):
        assembler = ContextAssembler()
        result = assembler.build(
            "/tmp",
            default_system="SYS",
            plan_mode=True,
        )
        assert self.PLAN_MODE_MARKER in result

    def test_plan_mode_absent_when_false(self):
        assembler = ContextAssembler()
        result = assembler.build(
            "/tmp",
            default_system="SYS",
            plan_mode=False,
        )
        assert self.PLAN_MODE_MARKER not in result

    def test_plan_mode_truncated_before_critical_sections(self):
        assembler = ContextAssembler()
        result = assembler.build(
            "/tmp",
            default_system="SYS",
            plan_mode=True,
            max_tokens=10,
        )
        assert "SYS" in result
        assert self.PLAN_MODE_MARKER not in result

    def test_plan_mode_with_plan_context_both_present(self):
        assembler = ContextAssembler()
        result = assembler.build(
            "/tmp",
            default_system="SYS",
            plan_mode=True,
            plan_context="1. Foo\n2. Bar",
        )
        assert self.PLAN_MODE_MARKER in result
        assert "Approved Plan" in result
        assert "Foo" in result

    def test_plan_context_does_not_inject_plan_mode(self):
        assembler = ContextAssembler()
        result = assembler.build(
            "/tmp",
            default_system="SYS",
            plan_context="1. Foo",
        )
        assert self.PLAN_MODE_MARKER not in result
        assert "Approved Plan" in result
