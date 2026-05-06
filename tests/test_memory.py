"""Tests for cross-session memory (v2)."""

from wisp.memory import (
    add_fact,
    remove_fact,
    list_facts,
    set_importance,
    format_memory_block,
    clear_memory,
    load_memory,
)


def _contents(facts: list[dict]) -> list[str]:
    """Extract content strings from fact dicts."""
    return [f["content"] for f in facts]


class TestMemory:
    def test_add_global_fact(self, tmp_path, monkeypatch):
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        assert add_fact("User likes Python")
        assert "User likes Python" in _contents(list_facts())

    def test_add_workspace_fact(self, tmp_path, monkeypatch):
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        assert add_fact("Uses pytest", workspace=str(tmp_path))
        assert "Uses pytest" in _contents(list_facts(workspace=str(tmp_path)))

    def test_global_and_workspace_separate(self, tmp_path, monkeypatch):
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        add_fact("Global fact")
        add_fact("Workspace fact", workspace=str(tmp_path))

        global_facts = _contents(list_facts())
        ws_facts = _contents(list_facts(workspace=str(tmp_path)))

        assert "Global fact" in global_facts
        assert "Workspace fact" in ws_facts

    def test_no_duplicates(self, tmp_path, monkeypatch):
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        assert add_fact("Unique fact")
        assert not add_fact("Unique fact")

    def test_dedup_case_insensitive(self, tmp_path, monkeypatch):
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        assert add_fact("Hello World")
        assert not add_fact("  hello   world  ")
        assert len(list_facts()) == 1

    def test_remove_fact(self, tmp_path, monkeypatch):
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        add_fact("Remove me")
        assert remove_fact("Remove me")
        assert "Remove me" not in _contents(list_facts())

    def test_remove_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        assert not remove_fact("Does not exist")

    def test_clear_memory(self, tmp_path, monkeypatch):
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        add_fact("Fact 1")
        add_fact("Fact 2", workspace=str(tmp_path))
        clear_memory()
        assert list_facts() == []
        assert list_facts(workspace=str(tmp_path)) == []

    def test_format_memory_block(self, tmp_path, monkeypatch):
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        add_fact("Test fact")
        block = format_memory_block()
        assert "## Learned Preferences" in block
        assert "Test fact" in block
        assert "remember" in block

    def test_format_memory_block_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        assert format_memory_block() == ""

    def test_persistence(self, tmp_path, monkeypatch):
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        add_fact("Persistent fact")
        memory = load_memory()
        assert "Persistent fact" in [f["content"] for f in memory["global_facts"]]

    def test_lru_eviction(self, tmp_path, monkeypatch):
        """Oldest non-important fact is evicted, not rejected."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        monkeypatch.setattr("wisp.memory._MAX_FACTS", 3)
        assert add_fact("Fact 1")
        assert add_fact("Fact 2")
        assert add_fact("Fact 3")
        # At capacity, adding new fact evicts oldest (Fact 1)
        assert add_fact("Fact 4")
        facts = _contents(list_facts())
        assert len(facts) == 3
        assert "Fact 1" not in facts
        assert "Fact 4" in facts

    def test_important_facts_resist_eviction(self, tmp_path, monkeypatch):
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        monkeypatch.setattr("wisp.memory._MAX_FACTS", 3)
        add_fact("Important fact", important=True)
        add_fact("Filler 1")
        add_fact("Filler 2")
        # Adding new fact should evict Filler 1, not Important fact
        add_fact("Filler 3")
        facts = _contents(list_facts())
        assert "Important fact" in facts
        assert len(facts) == 3

    def test_set_importance(self, tmp_path, monkeypatch):
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        add_fact("Mark me")
        assert set_importance("Mark me", True)
        facts = list_facts()
        assert facts[0]["important"] is True
        set_importance("Mark me", False)
        facts = list_facts()
        assert facts[0]["important"] is False

    def test_touch_on_access(self, tmp_path, monkeypatch):
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        add_fact("Touched fact")
        facts = list_facts()
        assert facts[0]["access_count"] == 1
        list_facts()
        facts = list_facts()
        assert facts[0]["access_count"] == 3
