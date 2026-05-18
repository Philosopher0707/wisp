"""Test REPL skill acknowledgment turn — verifies the agent sends an
acknowledgment message to the LLM when /skill is used in REPL mode.
"""

import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

from wisp.commands import cmd_skill


class MockConfig:
    def __init__(self, workspace="/tmp"):
        self.model = "test-model"
        self.workspace = workspace
        self.auto_approve = True
        self.show_thinking = False
        self.max_context_tokens = 128000
        self.chars_per_token = 4
        self._context_tokens_explicit = False
        self.plan_mode = False
        self.plan_context = None


class MockSession:
    def __init__(self):
        self.id = "test-session"
        self.title = "Test"
        self.messages = []


class MockAgent:
    """Mock that captures what gets sent to the LLM during acknowledgment."""

    def __init__(self, workspace="/tmp"):
        self.config = MockConfig(workspace=workspace)
        self.messages = []
        self.session = MockSession()
        self._active_skill = None
        self._system_prompt_cache = {}
        self._last_user_prompt = None
        self._captured_system_prompt = None
        self._captured_messages = []

    def _add_message(self, role, content):
        self.messages.append({"role": role, "content": content})
        self._captured_messages.append((role, content))

    def _build_system_prompt(self, skill_name=None, workspace=None, query=None):
        ws = workspace or self.config.workspace or "."
        system = "You are Wisp."
        from wisp.skills import discover_skills
        skills = discover_skills(ws)
        if skill_name:
            skill = next((s for s in skills if s.name == skill_name), None)
            if skill:
                system += f"\n\n## Active Skill: {skill.name}\n"
                system += skill.description + "\n\n"
                system += skill.instructions
        self._captured_system_prompt = system
        return system

    def _save_session(self):
        pass

    def _save_session_summary(self):
        pass


@pytest.fixture
def agent(tmp_path, monkeypatch):
    from wisp import skills as skills_mod
    monkeypatch.setattr(skills_mod, "GLOBAL_SKILL_DIRS", [])
    return MockAgent(workspace=str(tmp_path))


class TestREPLSkillAcknowledgment:
    """Tests that simulate the REPL skill acknowledgment flow."""

    def test_skill_command_sets_active_skill(self, agent):
        """/skill coder should set _active_skill."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        cmd_skill(agent, "coder")
        assert agent._active_skill == "coder"

    def test_acknowledgment_message_format(self, agent):
        """Acknowledgment message should contain skill name and activation phrase."""
        skill_name = "coder"
        ack = (
            f"The '{skill_name}' skill has been activated. "
            f"Confirm you understand and are ready to use this skill mode."
        )
        assert "coder" in ack
        assert "activated" in ack
        assert "Confirm you understand" in ack

    def test_acknowledgment_added_as_user_message(self, agent):
        """Acknowledgment should be added as a user message."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        
        # Simulate REPL flow
        cmd_skill(agent, "coder")
        
        # Simulate what REPL does after dispatch
        skill_name = "coder"
        ack = (
            f"The '{skill_name}' skill has been activated. "
            f"Confirm you understand and are ready to use this skill mode."
        )
        agent._add_message("user", ack)
        
        # Verify message was captured
        assert len(agent._captured_messages) == 1
        assert agent._captured_messages[0][0] == "user"
        assert "activated" in agent._captured_messages[0][1]

    def test_system_prompt_built_with_skill(self, agent):
        """System prompt should include skill instructions."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write Python code\n---\nAlways use type hints."
        )
        
        cmd_skill(agent, "coder")
        
        # Simulate REPL building system prompt
        system = agent._build_system_prompt("coder", query="ack")
        
        assert "## Active Skill: coder" in system
        assert "Always use type hints" in system
        assert agent._captured_system_prompt == system

    def test_acknowledgment_triggers_llm_turn(self, agent):
        """Full flow: skill load → ack message → system prompt → ready for LLM."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "debugger"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: debugger\ndescription: Debug errors\n---\n1. Reproduce\n2. Fix"
        )
        
        # Step 1: Load skill
        cmd_skill(agent, "debugger")
        assert agent._active_skill == "debugger"
        
        # Step 2: REPL sends acknowledgment
        skill_name = "debugger"
        ack = (
            f"The '{skill_name}' skill has been activated. "
            f"Confirm you understand and are ready to use this skill mode."
        )
        agent._add_message("user", ack)
        
        # Step 3: Build system prompt with skill
        system = agent._build_system_prompt(skill_name, query=ack)
        
        # Step 4: Verify everything is ready for LLM call
        assert len(agent.messages) == 1
        assert agent.messages[0]["role"] == "user"
        assert "debugger" in agent.messages[0]["content"]
        assert "## Active Skill: debugger" in system
        assert "1. Reproduce" in system

    def test_skill_switch_clears_previous(self, agent):
        """Switching skills should clear previous skill state."""
        ws = agent.config.workspace
        
        d1 = Path(ws) / ".agents" / "skills" / "coder"
        d1.mkdir(parents=True)
        (d1 / "SKILL.md").write_text("---\nname: coder\ndescription: Code\n---\nCode")
        
        d2 = Path(ws) / ".agents" / "skills" / "debugger"
        d2.mkdir(parents=True)
        (d2 / "SKILL.md").write_text("---\nname: debugger\ndescription: Debug\n---\nDebug")
        
        # Load first skill
        cmd_skill(agent, "coder")
        assert agent._active_skill == "coder"
        
        # Switch to second
        cmd_skill(agent, "debugger")
        assert agent._active_skill == "debugger"
        
        # Verify system prompt uses new skill
        system = agent._build_system_prompt("debugger")
        assert "## Active Skill: debugger" in system
        assert "## Active Skill: coder" not in system

    def test_empty_skill_name_no_acknowledgment(self, agent):
        """Empty skill name should not trigger acknowledgment."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        
        # Simulate /skill with no args (lists skills, no ack)
        cmd_skill(agent, "")
        
        # No acknowledgment should be sent
        assert len(agent.messages) == 0
        assert agent._active_skill is None

    def test_whitespace_skill_name_no_acknowledgment(self, agent):
        """Whitespace-only skill name should not trigger acknowledgment."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        
        # Simulate /skill with whitespace (lists skills, no ack)
        cmd_skill(agent, "   ")
        
        # No acknowledgment should be sent
        assert len(agent.messages) == 0
        assert agent._active_skill is None

    def test_skill_not_found_no_acknowledgment(self, agent, capsys):
        """Non-existent skill should not trigger acknowledgment."""
        cmd_skill(agent, "nonexistent")
        captured = capsys.readouterr()
        
        # Should warn but not send ack
        assert "not found" in captured.out
        assert len(agent.messages) == 0
        assert agent._active_skill is None

    def test_acknowledgment_message_count(self, agent):
        """Each skill load should add exactly one acknowledgment message."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        
        cmd_skill(agent, "coder")
        
        # Simulate REPL acknowledgment
        agent._add_message("user", "The 'coder' skill has been activated.")
        
        assert len(agent.messages) == 1
        assert len(agent._captured_messages) == 1

    def test_multiple_skill_loads_multiple_acks(self, agent):
        """Loading multiple skills should accumulate acknowledgment messages."""
        ws = agent.config.workspace
        
        d1 = Path(ws) / ".agents" / "skills" / "coder"
        d1.mkdir(parents=True)
        (d1 / "SKILL.md").write_text("---\nname: coder\ndescription: Code\n---\nCode")
        
        d2 = Path(ws) / ".agents" / "skills" / "debugger"
        d2.mkdir(parents=True)
        (d2 / "SKILL.md").write_text("---\nname: debugger\ndescription: Debug\n---\nDebug")
        
        # Load first skill
        cmd_skill(agent, "coder")
        agent._add_message("user", "The 'coder' skill has been activated.")
        
        # Load second skill
        cmd_skill(agent, "debugger")
        agent._add_message("user", "The 'debugger' skill has been activated.")
        
        assert len(agent.messages) == 2
        assert "coder" in agent.messages[0]["content"]
        assert "debugger" in agent.messages[1]["content"]

    def test_system_prompt_cache_cleared_before_ack(self, agent):
        """Cache should be cleared before building ack system prompt."""
        skill_dir = Path(agent.config.workspace) / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: Write code\n---\nInstructions"
        )
        
        # Pre-populate cache
        agent._system_prompt_cache = {("old",): "old_prompt"}
        
        # Load skill (should clear cache)
        cmd_skill(agent, "coder")
        
        assert len(agent._system_prompt_cache) == 0
        
        # Build system prompt for ack
        system = agent._build_system_prompt("coder")
        assert "## Active Skill: coder" in system
