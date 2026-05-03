"""Tests for cross-session memory."""

import json
from pathlib import Path

from wisp.memory import (
    add_fact,
    remove_fact,
    list_facts,
    format_memory_block,
    clear_memory,
    load_memory,
    save_memory,
)


class TestMemory:
    def test_add_global_fact(self, tmp_path, monkeypatch):
        """Add a global fact."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        assert add_fact("User likes Python")
        facts = list_facts()
        assert "User likes Python" in facts

    def test_add_workspace_fact(self, tmp_path, monkeypatch):
        """Add a workspace-specific fact."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        assert add_fact("Uses pytest", workspace=str(tmp_path))
        facts = list_facts(workspace=str(tmp_path))
        assert "Uses pytest" in facts

    def test_global_and_workspace_separate(self, tmp_path, monkeypatch):
        """Global and workspace facts are separate."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        add_fact("Global fact")
        add_fact("Workspace fact", workspace=str(tmp_path))

        global_facts = list_facts()
        ws_facts = list_facts(workspace=str(tmp_path))

        assert "Global fact" in global_facts
        assert "Workspace fact" in ws_facts

    def test_no_duplicates(self, tmp_path, monkeypatch):
        """Duplicate facts are not added."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        assert add_fact("Unique fact")
        assert not add_fact("Unique fact")  # duplicate

    def test_remove_fact(self, tmp_path, monkeypatch):
        """Remove a fact."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        add_fact("Remove me")
        assert remove_fact("Remove me")
        assert "Remove me" not in list_facts()

    def test_remove_nonexistent(self, tmp_path, monkeypatch):
        """Removing a nonexistent fact returns False."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        assert not remove_fact("Does not exist")

    def test_clear_memory(self, tmp_path, monkeypatch):
        """Clear all memory."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        add_fact("Fact 1")
        add_fact("Fact 2", workspace=str(tmp_path))
        clear_memory()
        assert list_facts() == []
        assert list_facts(workspace=str(tmp_path)) == []

    def test_format_memory_block(self, tmp_path, monkeypatch):
        """Format memory as system prompt block."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        add_fact("Test fact")
        block = format_memory_block()
        assert "## Learned Preferences" in block
        assert "Test fact" in block
        assert "remember" in block

    def test_format_memory_block_empty(self, tmp_path, monkeypatch):
        """Empty memory yields empty block."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        assert format_memory_block() == ""

    def test_persistence(self, tmp_path, monkeypatch):
        """Facts persist across load/save cycles."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        add_fact("Persistent fact")
        # Reload from disk
        memory = load_memory()
        assert "Persistent fact" in memory["global_facts"]

    def test_max_facts(self, tmp_path, monkeypatch):
        """Adding beyond max capacity is rejected."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        monkeypatch.setattr("wisp.memory._MAX_FACTS", 3)
        assert add_fact("Fact 1")
        assert add_fact("Fact 2")
        assert add_fact("Fact 3")
        assert not add_fact("Fact 4")  # at capacity
