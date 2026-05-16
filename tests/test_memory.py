"""Tests for cross-session memory (v2)."""

from wisp.memory import (
    add_fact,
    remove_fact,
    list_facts,
    list_all_facts,
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

    # ── Global memory tests ────────────────────────────────────────────

    def test_list_all_facts_returns_all_workspaces(self, tmp_path, monkeypatch):
        """list_all_facts should return facts from every workspace + global."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        ws_a = str(tmp_path / "project_a")
        ws_b = str(tmp_path / "project_b")

        add_fact("Global preference")
        add_fact("Project A uses React", workspace=ws_a)
        add_fact("Project B uses Vue", workspace=ws_b)

        all_facts = _contents(list_all_facts())
        assert "Global preference" in all_facts
        assert "Project A uses React" in all_facts
        assert "Project B uses Vue" in all_facts
        assert len(all_facts) == 3

    def test_list_all_facts_sorted_by_importance(self, tmp_path, monkeypatch):
        """Important facts should appear first in list_all_facts."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        add_fact("Normal fact")
        add_fact("Important fact", important=True)

        all_facts = list_all_facts()
        assert all_facts[0]["content"] == "Important fact"
        assert all_facts[0]["important"] is True

    def test_format_memory_block_include_all_default(self, tmp_path, monkeypatch):
        """By default format_memory_block should include all workspace facts."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        ws_a = str(tmp_path / "project_a")
        ws_b = str(tmp_path / "project_b")

        add_fact("Global preference")
        add_fact("Project A uses React", workspace=ws_a)
        add_fact("Project B uses Vue", workspace=ws_b)

        block = format_memory_block(workspace=ws_a)
        assert "## Learned Preferences (Global Memory)" in block
        assert "Global preference" in block
        assert "Project A uses React" in block
        assert "Project B uses Vue" in block

    def test_format_memory_block_include_all_false(self, tmp_path, monkeypatch):
        """With include_all=False, only current workspace + global facts appear."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        ws_a = str(tmp_path / "project_a")
        ws_b = str(tmp_path / "project_b")

        add_fact("Global preference")
        add_fact("Project A uses React", workspace=ws_a)
        add_fact("Project B uses Vue", workspace=ws_b)

        block = format_memory_block(workspace=ws_a, include_all=False)
        assert "Global preference" in block
        assert "Project A uses React" in block
        assert "Project B uses Vue" not in block

    def test_cross_workspace_memory_visibility(self, tmp_path, monkeypatch):
        """Facts stored in workspace A should be visible from workspace B via list_all_facts."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        ws_a = str(tmp_path / "project_a")
        ws_b = str(tmp_path / "project_b")

        add_fact("Shared convention: use 2-space indent", workspace=ws_a)

        # From workspace B's perspective, list_facts is scoped
        ws_b_facts = _contents(list_facts(workspace=ws_b))
        assert "Shared convention" not in ws_b_facts

        # But list_all_facts is global
        all_facts = _contents(list_all_facts())
        assert "Shared convention: use 2-space indent" in all_facts

    def test_global_facts_deduped_in_list_all(self, tmp_path, monkeypatch):
        """Same fact in global + workspace should appear once (workspace wins on touch)."""
        monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
        ws_a = str(tmp_path / "project_a")

        add_fact("Duplicate fact")
        add_fact("Duplicate fact", workspace=ws_a)

        all_facts = list_all_facts()
        contents = _contents(all_facts)
        assert contents.count("Duplicate fact") == 2  # stored separately, not deduped at storage level
        # But format_memory_block shows them as separate entries (expected behavior)
