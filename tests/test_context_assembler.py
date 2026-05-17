"""Tests for ContextAssembler — system prompt construction.

Verifies that ContextAssembler correctly assembles system prompt
sections from various context sources.
"""

import pytest
from pathlib import Path


class TestContextAssemblerConstruction:

    def test_can_be_constructed(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        assert assembler is not None

    def test_default_system_prompt(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        assert "Wisp" in assembler.default_system


class TestContextAssemblerBuild:

    def test_build_basic_prompt(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
        )
        assert "You are Wisp." in result
        assert "/tmp" in result

    def test_build_with_skills(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            skills_block="## Available Skills\n- test-skill",
        )
        assert "Available Skills" in result
        assert "test-skill" in result

    def test_build_with_project_context(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            project_context="## Project\nPython project",
        )
        assert "Python project" in result

    def test_build_with_code_index(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            code_index_summary="## Code Index\n- main.py",
        )
        assert "Code Index" in result

    def test_build_with_memory(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            memory_block="## Memory\nUser prefers tabs",
        )
        assert "User prefers tabs" in result

    def test_build_with_git_context(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            git_context="## Git\nOn branch main",
        )
        assert "On branch main" in result

    def test_build_with_active_plan(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            active_plan="## Active Plan\n1. Fix bug",
        )
        assert "Active Plan" in result

    def test_build_with_repo_map(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            repo_map="## Codebase Map\n- src/",
        )
        assert "Codebase Map" in result

    def test_build_with_context_files(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            context_files="# Rules\nBe concise",
        )
        # Context files are prepended
        assert result.startswith("# Rules")

    def test_build_with_mandatory_skill(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            mandatory_skill=("test-skill", "Test skill", "Do testing"),
        )
        assert "MANDATORY Mode: test-skill" in result
        assert "Do testing" in result

    def test_build_with_plan_mode(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            plan_mode=True,
        )
        assert "PLAN MODE ACTIVE" in result

    def test_build_with_plan_context(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            plan_context="1. Refactor auth\n2. Add tests",
        )
        assert "Approved Plan" in result

    def test_build_with_role_extra(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            role_extra="You are a security auditor.",
        )
        assert "security auditor" in result

    def test_build_with_recent_summaries(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            recent_summaries="## Previous Session\nWe refactored auth.py",
        )
        assert "Previous Session" in result

    def test_build_omits_empty_sections(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
        )
        # Should not contain sections that weren't provided
        assert "Available Skills" not in result
        assert "Memory" not in result
        assert "Git" not in result

    def test_build_ordering(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="BASE",
            skills_block="SKILLS",
            project_context="PROJECT",
            memory_block="MEMORY",
            mandatory_skill=("skill", "name", "instructions"),
        )
        # Verify ordering: base -> workspace -> skills -> project -> memory -> mandatory
        base_idx = result.find("BASE")
        ws_idx = result.find("Workspace")
        skills_idx = result.find("SKILLS")
        proj_idx = result.find("PROJECT")
        mem_idx = result.find("MEMORY")
        mand_idx = result.find("MANDATORY")

        assert base_idx < ws_idx < skills_idx < proj_idx < mem_idx < mand_idx

    def test_build_caching(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result1 = assembler.build(workspace="/tmp", default_system="SYS")
        result2 = assembler.build(workspace="/tmp", default_system="SYS")
        assert result1 is result2  # Same cache key returns cached result

    def test_build_different_cache_keys(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result1 = assembler.build(workspace="/tmp", default_system="SYS")
        result2 = assembler.build(workspace="/other", default_system="SYS")
        assert result1 is not result2
