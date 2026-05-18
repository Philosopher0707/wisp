"""Tests for PromptContext / PlanState dataclass refactor.

Verifies that the new structured API works and that the old
positional API remains backward-compatible.
"""

import pytest
from wisp.context_assembler import ContextAssembler, PromptContext, PlanState


class TestPromptContextCreation:

    def test_create_minimal(self):
        ctx = PromptContext(workspace="/tmp")
        assert ctx.workspace == "/tmp"
        assert ctx.max_tokens == 6_000
        assert ctx.plan is None

    def test_plan_state_defaults(self):
        plan = PlanState()
        assert plan.is_active is False
        assert plan.context == ""
        assert plan.active_plan == ""

    def test_plan_state_populated(self):
        plan = PlanState(is_active=True, context="1. Foo", active_plan="Do X")
        assert plan.is_active is True


class TestBuildWithPromptContext:

    def test_basic(self):
        assembler = ContextAssembler()
        ctx = PromptContext(workspace="/tmp", default_system="SYS")
        result = assembler.build(ctx)
        assert "SYS" in result
        assert "/tmp" in result

    def test_with_plan(self):
        assembler = ContextAssembler()
        ctx = PromptContext(
            workspace="/tmp",
            default_system="SYS",
            plan=PlanState(is_active=True, context="1. Foo"),
        )
        result = assembler.build(ctx)
        assert "PLAN MODE ACTIVE" in result
        assert "Foo" in result

    def test_cache_hit_with_context(self):
        assembler = ContextAssembler()
        ctx = PromptContext(workspace="/tmp", default_system="SYS")
        r1 = assembler.build(ctx)
        r2 = assembler.build(ctx)
        assert r1 is r2


class TestBackwardCompatibility:
    """Old positional API must still work."""

    def test_positional_params(self):
        """Legacy API: first positional = workspace, rest are keywords."""
        assembler = ContextAssembler()
        result = assembler.build(
            "/tmp",
            default_system="SYS",
            plan_mode=True,
            plan_context="1. Foo",
        )
        assert "SYS" in result
        assert "PLAN MODE ACTIVE" in result
