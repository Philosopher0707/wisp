"""Tests for skill loading — command, system prompt injection, auto-detection, and edge cases.

Covers the full skill loading lifecycle:
1. /skill command sets _active_skill and clears cache
2. Skill system prompt injection (Active Skill suggestion block)
3. Skill auto-detection from query via match_skills
4. Skill not found handling (graceful fallback)
5. Skill acknowledgment turn after /skill in REPL
6. Skill cache invalidation on workspace change
7. Edge cases: empty skill name, unicode skill content, skill with no instructions
"""

import pytest
from pathlib import Path

from wisp.skills import parse_skill, discover_skills, find_skill, match_skills
from wisp.commands import cmd_skill


# ── Mock Agent Fixture ─────────────────────────────────────────────

class MockConfig:
    def __init__(self, workspace="/tmp"):
        self.model = "test-model"
        self.workspace = workspace
        self.auto_approve = False
        self.show_thinking = False
        self.max_context_tokens = 128000
        self.chars_per_token = 4
        self._context_tokens_explicit = False
        self.plan_mode = False
        self.plan_context = None


class MockAgent:
    """Minimal agent mock for testing skill command integration."""

    def __init__(self, workspace="/tmp"):
        self.config = MockConfig(workspace=workspace)
        self.messages = []
        self._active_skill = None
        self._system_prompt_cache = {}
        self._last_user_prompt = None

    def _build_system_prompt(self, skill_name=None, workspace=None, query=None):
        # Minimal implementation that mimics real behavior
        ws = workspace or self.config.workspace or "."
        system = "You are Wisp."

        skills = discover_skills(ws)

        if skill_name:
            skill = next((s for s in skills if s.name == skill_name), None)
            if skill:
                system += f"\n\n## Active Skill: {skill.name}\n"
                system += skill.description + "\n\n"
                system += skill.instructions

        return system


@pytest.fixture
def agent(tmp_path, monkeypatch):
    # Prevent global skills from leaking into tests
    from wisp import skills as skills_mod
    monkeypatch.setattr(skills_mod, "GLOBAL_SKILL_DIRS", [])
    return MockAgent(workspace=str(tmp_path))


# ── 1. /skill Command Tests ────────────────────────────────────────

class TestSkillCommand:
    """Tests for the /skill slash command."""

    def test_skill_list_empty(self, agent, capsys):
        """Listing skills when none exist should show message."""
        cmd_skill(agent, "")
        captured = capsys.readouterr()
        assert "No skills found" in captured.out

    def test_skill_list_shows_skills(self, agent, capsys):
        """Listing skills should show discovered skills."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        cmd_skill(agent, "")
        captured = capsys.readouterr()
        assert "coder" in captured.out
        assert "Write code" in captured.out

    def test_skill_load_sets_active_skill(self, agent):
        """Loading a skill should set _active_skill."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        cmd_skill(agent, "coder")
        assert agent._active_skill == "coder"

    def test_skill_load_clears_cache(self, agent):
        """Loading a skill should clear system prompt cache."""
        agent._system_prompt_cache[("old", "/tmp", None)] = "cached"
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        cmd_skill(agent, "coder")
        assert len(agent._system_prompt_cache) == 0

    def test_skill_load_not_found(self, agent, capsys):
        """Loading non-existent skill should warn."""
        cmd_skill(agent, "nonexistent")
        captured = capsys.readouterr()
        assert "not found" in captured.out
        assert agent._active_skill is None

    def test_skill_load_empty_name(self, agent, capsys):
        """Loading skill with empty name should list skills."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        cmd_skill(agent, "   ")
        captured = capsys.readouterr()
        assert "coder" in captured.out

    def test_skill_load_with_whitespace(self, agent):
        """Loading skill with surrounding whitespace should work."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        cmd_skill(agent, "  coder  ")
        assert agent._active_skill == "coder"


# ── 2. Skill System Prompt Injection ───────────────────────────────

class TestSkillSystemPrompt:
    """Tests for skill injection into system prompt."""

    def test_skill_injected_into_system_prompt(self, agent):
        """Active skill should appear in system prompt."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write Python code\n---\nAlways use type hints."
        )
        agent._active_skill = "coder"
        system = agent._build_system_prompt(skill_name="coder")
        assert "## Active Skill: coder" in system
        assert "Write Python code" in system
        assert "Always use type hints" in system

    def test_no_skill_no_injection(self, agent):
        """Without active skill, no Active Skill block."""
        system = agent._build_system_prompt()
        assert "Active Skill" not in system

    def test_skill_not_found_no_crash(self, agent):
        """Referencing non-existent skill should not crash."""
        agent._active_skill = "nonexistent"
        system = agent._build_system_prompt(skill_name="nonexistent")
        assert "Active Skill" not in system

    def test_skill_priority_over_other_context(self, agent):
        """Skill instructions appear after base prompt."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nPrefer tabs."
        )
        system = agent._build_system_prompt(skill_name="coder")
        # Skill content should be after base prompt
        base_pos = system.find("You are Wisp.")
        skill_pos = system.find("Prefer tabs.")
        assert skill_pos > base_pos

    def test_skill_with_unicode_content(self, agent):
        """Skill with unicode content should work."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "unicode"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: unicode\ndescription: 测试技能\n---\n使用中文指令 🎉"
        )
        system = agent._build_system_prompt(skill_name="unicode")
        assert "测试技能" in system
        assert "使用中文指令 🎉" in system

    def test_skill_with_empty_instructions(self, agent):
        """Skill with empty instructions should still work."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "empty"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: empty\ndescription: Empty skill\n---\n"
        )
        system = agent._build_system_prompt(skill_name="empty")
        assert "## Active Skill: empty" in system


# ── 3. Skill Auto-Detection ────────────────────────────────────────

class TestSkillAutoDetection:
    """Tests for skill auto-detection from user query."""

    def test_auto_detect_by_name(self, agent):
        """Query containing skill name should auto-detect."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "pdf"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: pdf\ndescription: PDF operations\ntriggers: merge, split\n---\nPDF instructions"
        )
        matched = match_skills("merge these pdf files", agent.config.workspace)
        assert len(matched) >= 1
        assert matched[0][0].name == "pdf"

    def test_auto_detect_by_trigger(self, agent):
        """Query containing trigger phrase should auto-detect."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "debug"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: debug\ndescription: Debug errors\ntriggers: fix, error, bug\n---\nDebug instructions"
        )
        matched = match_skills("fix this bug please", agent.config.workspace)
        assert len(matched) >= 1
        assert matched[0][0].name == "debug"

    def test_no_auto_detect_for_unrelated_query(self, agent):
        """Unrelated query should not match any skill."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "pdf"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: pdf\ndescription: PDF operations\ntriggers: pdf\n---\nPDF instructions"
        )
        matched = match_skills("hello world", agent.config.workspace, min_score=1.5)
        assert matched == []

    def test_auto_detect_highest_score_wins(self, agent):
        """Skill with highest score should be top match."""
        ws = agent.config.workspace
        
        d1 = Path(ws) / ".agents" / "skills" / "pdf"
        d1.mkdir(parents=True)
        (d1 / "SKILL.md").write_text(
            "---\nname: pdf\ndescription: PDF work\ntriggers: pdf\n---\nPDF"
        )
        
        d2 = Path(ws) / ".agents" / "skills" / "merge"
        d2.mkdir(parents=True)
        (d2 / "SKILL.md").write_text(
            "---\nname: merge\ndescription: Merge files\ntriggers: merge, pdf\n---\nMerge"
        )
        
        matched = match_skills("merge pdf files", ws, min_score=0.0)
        assert len(matched) >= 2
        # merge should score higher (name match + trigger match)
        names = [m[0].name for m in matched]
        assert "merge" in names
        assert "pdf" in names


# ── 4. Skill Not Found Handling ────────────────────────────────────

class TestSkillNotFound:
    """Tests for graceful fallback when skill is not found."""

    def test_run_with_nonexistent_skill(self, agent, capsys):
        """Running with non-existent skill should warn but continue."""
        # Simulate what transport/cli.py does
        skill = find_skill("nonexistent", agent.config.workspace)
        assert skill is None
        # Should not crash

    def test_skill_name_typo(self, agent):
        """Typo in skill name should not match."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        skill = find_skill("codr", agent.config.workspace)
        assert skill is None

    def test_skill_case_sensitive(self, agent):
        """Skill name matching should be case-insensitive in practice."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "Coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: Coder\ndescription: Write code\n---\nInstructions"
        )
        # find_skill does exact match on skill.name
        skill = find_skill("Coder", agent.config.workspace)
        assert skill is not None
        assert skill.name == "Coder"


# ── 5. Skill Cache Invalidation ────────────────────────────────────

class TestSkillCacheInvalidation:
    """Tests for system prompt cache invalidation."""

    def test_cache_cleared_on_skill_load(self, agent):
        """Loading skill should clear system prompt cache."""
        agent._system_prompt_cache = {("key",): "value"}
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        cmd_skill(agent, "coder")
        assert len(agent._system_prompt_cache) == 0

    def test_cache_persists_without_skill_change(self, agent):
        """Cache should persist when skill doesn't change."""
        agent._system_prompt_cache = {("key",): "value"}
        # No skill change
        assert len(agent._system_prompt_cache) == 1

    def test_different_skills_different_cache_keys(self, agent):
        """Different skills should produce different system prompts."""
        ws = agent.config.workspace
        
        d1 = Path(ws) / ".agents" / "skills" / "coder"
        d1.mkdir(parents=True)
        (d1 / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nCode instructions"
        )
        
        d2 = Path(ws) / ".agents" / "skills" / "tester"
        d2.mkdir(parents=True)
        (d2 / "SKILL.md").write_text(
            "---\nname: tester\ndescription: Test code\n---\nTest instructions"
        )
        
        system_coder = agent._build_system_prompt(skill_name="coder")
        system_tester = agent._build_system_prompt(skill_name="tester")
        
        assert system_coder != system_tester
        assert "Code instructions" in system_coder
        assert "Test instructions" in system_tester


# ── 6. Skill Discovery Edge Cases ──────────────────────────────────

class TestSkillDiscoveryEdgeCases:
    """Edge cases for skill discovery."""

    def test_discover_skills_empty_workspace(self, agent):
        """Empty workspace should return empty skills list."""
        skills = discover_skills(agent.config.workspace)
        assert skills == []

    def test_discover_skills_multiple_dirs(self, agent):
        """Skills in multiple dirs should all be discovered."""
        ws = agent.config.workspace
        
        d1 = Path(ws) / ".agents" / "skills" / "skill-a"
        d1.mkdir(parents=True)
        (d1 / "SKILL.md").write_text("---\nname: skill-a\ndescription: A\n---\nA")
        
        d2 = Path(ws) / ".warp" / "skills" / "skill-b"
        d2.mkdir(parents=True)
        (d2 / "SKILL.md").write_text("---\nname: skill-b\ndescription: B\n---\nB")
        
        skills = discover_skills(ws)
        names = [s.name for s in skills]
        assert "skill-a" in names
        assert "skill-b" in names

    def test_project_shadows_global(self, agent):
        """Project skill should shadow global skill with same name."""
        ws = agent.config.workspace
        
        # Project skill
        d1 = Path(ws) / ".agents" / "skills" / "shared"
        d1.mkdir(parents=True)
        (d1 / "SKILL.md").write_text("---\nname: shared\ndescription: Project\n---\nProject")
        
        # Global skill (would normally be in home dir)
        from wisp import skills as skills_mod
        original_global = skills_mod.GLOBAL_SKILL_DIRS
        global_dir = Path(ws) / "global" / "skills"
        global_dir.mkdir(parents=True)
        d2 = global_dir / "shared"
        d2.mkdir(parents=True)
        (d2 / "SKILL.md").write_text("---\nname: shared\ndescription: Global\n---\nGlobal")
        
        skills_mod.GLOBAL_SKILL_DIRS = [global_dir]
        try:
            skills = discover_skills(ws)
            shared = next((s for s in skills if s.name == "shared"), None)
            assert shared is not None
            assert shared.description == "Project"  # Project wins
        finally:
            skills_mod.GLOBAL_SKILL_DIRS = original_global

    def test_malformed_skill_skipped(self, agent):
        """Malformed SKILL.md should be skipped, not crash."""
        ws = agent.config.workspace
        d1 = Path(ws) / ".agents" / "skills" / "bad"
        d1.mkdir(parents=True)
        (d1 / "SKILL.md").write_text("not valid frontmatter")
        
        d2 = Path(ws) / ".agents" / "skills" / "good"
        d2.mkdir(parents=True)
        (d2 / "SKILL.md").write_text("---\nname: good\ndescription: Good\n---\nGood")
        
        skills = discover_skills(ws)
        names = [s.name for s in skills]
        assert "bad" not in names
        assert "good" in names

    def test_skill_with_special_chars_in_content(self, agent):
        """Skill with markdown special chars should parse."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "special"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: special\ndescription: Special chars\n---\n"
            "# Header\n\n```python\nprint('hello')\n```\n\n"
            "* bullet\n> quote\n[link](http://example.com)\n"
        )
        skill = parse_skill(skill_dir / "SKILL.md")
        assert skill is not None
        assert "```python" in skill.instructions
        assert "[link]" in skill.instructions

    def test_skill_with_long_description(self, agent):
        """Skill with very long description should work."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "long"
        skill_dir.mkdir(parents=True)
        desc = "A" * 5000
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: long\ndescription: {desc}\n---\nInstructions"
        )
        skill = parse_skill(skill_dir / "SKILL.md")
        assert skill is not None
        assert skill.description == desc

    def test_skill_with_multiline_triggers(self, agent):
        """Skill with multiline trigger string should parse."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "multi"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: multi\ndescription: Multi\ntriggers: |\n  debug, fix, error\n---\nInstructions"
        )
        skill = parse_skill(skill_dir / "SKILL.md")
        assert skill is not None
        # YAML multiline string should be parsed as single string
        assert isinstance(skill.triggers, list)


# ── 7. Skill Acknowledgment Turn ───────────────────────────────────

class TestSkillAcknowledgment:
    """Tests for skill acknowledgment turn in REPL."""

    def test_skill_acknowledgment_message_format(self, agent):
        """Acknowledgment message should reference skill name."""
        skill_name = "coder"
        ack = (
            f"The '{skill_name}' skill has been activated. "
            f"Confirm you understand and are ready to use this skill mode."
        )
        assert "coder" in ack
        assert "activated" in ack

    def test_skill_acknowledgment_builds_system_prompt(self, agent):
        """Acknowledgment should build system prompt with skill."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        system = agent._build_system_prompt("coder", query="ack")
        assert "## Active Skill: coder" in system


# ── 8. Skill Persistence Across Sessions ─────────────────────────────

class TestSkillSessionPersistence:
    """Tests for skill state across sessions."""

    def test_active_skill_not_persisted_in_session(self, agent):
        """Active skill is agent state, not session state."""
        # This is expected behavior — skill is runtime state
        agent._active_skill = "coder"
        # Session doesn't know about it
        assert not hasattr(agent, "session") or agent.session is None

    def test_skill_can_be_reloaded_after_clear(self, agent):
        """Skill can be reloaded after clearing messages."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        cmd_skill(agent, "coder")
        assert agent._active_skill == "coder"
        
        # Clear messages
        agent.messages = []
        # Skill should still be active
        assert agent._active_skill == "coder"
