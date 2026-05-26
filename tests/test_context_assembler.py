"""Tests for ContextAssembler — system prompt construction.

Verifies that ContextAssembler correctly assembles system prompt
sections from various context sources.
"""



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
        # Context files are prepended before everything (priority -1)
        assert result.startswith("# Rules")
        assert "Be concise" in result
        assert "You are Wisp." in result

    def test_build_with_skill_instructions_not_override(self):
        """Skill instructions should be present but NOT claim to override
        earlier instructions. The word 'MANDATORY' must not appear."""
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            mandatory_skill=("test-skill", "Test skill", "Do testing"),
        )
        assert "Do testing" in result
        # The old dangerous language
        assert "MANDATORY Mode: test-skill" not in result
        assert "override ALL earlier instructions" not in result
        assert "Do NOT ask for confirmation" not in result

    def test_build_with_skill_mentions_workspace_first(self):
        """Skill instructions should appear BEFORE safety guidelines, not after.

        Safety footer is always appended *after* the skill content so that
        core safety guidelines remain effective.
        """
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="BASE",
            mandatory_skill=("coder", "Code well", "Write Python."),
        )
        base_idx = result.find("BASE")
        skill_idx = result.find("Write Python")
        # Safety footer added AFTER everything else
        safety_idx = result.find("## Safety Guardrails")
        assert base_idx < skill_idx < safety_idx

    def test_build_without_mandatory_skill_no_safety_footer(self):
        """No safety footer when no skill is active — don't waste context."""
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
        )
        assert "## Suggested Skill:" not in result
        assert "Safety Guardrails" not in result

    def test_build_with_mandatory_skill(self):
        from wisp.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        result = assembler.build(
            workspace="/tmp",
            default_system="You are Wisp.",
            mandatory_skill=("test-skill", "Test skill", "Do testing"),
        )
        assert "test-skill" in result
        assert "Do testing" in result
        assert "MANDATORY Mode: test-skill" not in result  # no dangerous language

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
        # Actual priority order: base(0) < workspace(0) < mandatory_skill(1) < skills_block(2) < memory(2) < project(3) < safety
        base_idx = result.find("BASE")
        ws_idx = result.find("Workspace")
        skill_idx = result.find("## Suggested Skill:")
        skills_idx = result.find("SKILLS")
        mem_idx = result.find("MEMORY")
        proj_idx = result.find("PROJECT")
        safety_idx = result.find("## Safety Guardrails")

        assert base_idx < ws_idx < skill_idx < skills_idx < mem_idx < proj_idx < safety_idx

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
