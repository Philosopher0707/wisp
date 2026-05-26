"""Tests for skills.py — SKILL.md parsing and discovery."""

from pathlib import Path
from wisp.skills import parse_skill, discover_skills, find_skill


class TestParseSkill:

    def test_valid_skill(self, tmp_path):
        skill_dir = tmp_path / ".agents" / "skills" / "coder"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            "---\n"
            "name: coder\n"
            "description: A coding skill\n"
            "---\n"
            "# Coder Skill\n"
            "Write code.\n"
        )
        skill = parse_skill(skill_file)
        assert skill is not None
        assert skill.name == "coder"
        assert skill.description == "A coding skill"
        assert "Write code" in skill.instructions

    def test_missing_frontmatter(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text("# No frontmatter\n")
        assert parse_skill(f) is None

    def test_invalid_yaml_frontmatter(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text("---\nname: [unclosed\n---\nbody\n")
        assert parse_skill(f) is None

    def test_missing_name(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text("---\ndescription: no name\n---\nbody\n")
        assert parse_skill(f) is None

    def test_nonexistent_file(self, tmp_path):
        assert parse_skill(tmp_path / "nope.md") is None


class TestDiscoverAndFind:

    def test_discover_project_skills(self, tmp_path, monkeypatch):
        """Discover skills from project-local .agents/skills/."""
        skill_dir = tmp_path / ".agents" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: My test skill\n---\nInstructions"
        )
        # Also create a global dir that should not shadow
        global_dir = Path(tmp_path / "global-agents" / "skills" / "my-skill")
        global_dir.mkdir(parents=True)
        (global_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: Global version\n---\nGlobal"
        )

        from wisp import skills as skills_mod
        original_global = skills_mod.GLOBAL_SKILL_DIRS
        skills_mod.GLOBAL_SKILL_DIRS = [tmp_path / "global-agents" / "skills"]
        try:
            skills = discover_skills(str(tmp_path))
        finally:
            skills_mod.GLOBAL_SKILL_DIRS = original_global

        names = [s.name for s in skills]
        assert "my-skill" in names

    def test_find_skill_by_name(self, tmp_path):
        skill_dir = tmp_path / ".agents" / "skills" / "finder-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: finder-skill\ndescription: Findable\n---\nDo stuff."
        )

        from wisp import skills as skills_mod
        original_global = skills_mod.GLOBAL_SKILL_DIRS
        skills_mod.GLOBAL_SKILL_DIRS = []
        try:
            skill = find_skill("finder-skill", str(tmp_path))
            assert skill is not None
            assert skill.name == "finder-skill"
        finally:
            skills_mod.GLOBAL_SKILL_DIRS = original_global

    def test_find_nonexistent_skill(self, tmp_path):
        assert find_skill("nope", str(tmp_path)) is None
