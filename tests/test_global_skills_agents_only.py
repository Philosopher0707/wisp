"""Global skills load from ~/.agents/skills only.

`~/.claude/skills` and `~/.warp/skills` belong to other tools. Loading them made one user's wisp see 56 skills whose
menu alone was 10,398 tokens, more than the whole 6,000-token prompt budget, plus 53 `skill__*` tool schemas
(10,197 tokens) sent on every request. Project-level directories are unchanged.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from wisp import skills as skills_mod

REPO = Path(__file__).resolve().parent.parent


def _skill(root: Path, name: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} skill\n---\n# {name}\nbody\n", encoding="utf-8")


def test_the_default_global_directory_is_dot_agents_alone():
    assert [p.relative_to(Path.home()).as_posix() for p in skills_mod.GLOBAL_SKILL_DIRS] == [".agents/skills"]


def test_with_a_real_home_only_the_dot_agents_skill_is_discovered(tmp_path):
    home, ws = tmp_path / "home", tmp_path / "ws"
    ws.mkdir()
    _skill(home / ".agents" / "skills", "from-agents")
    _skill(home / ".claude" / "skills", "from-claude")
    _skill(home / ".warp" / "skills", "from-warp")
    code = textwrap.dedent(f"""
        from wisp.skills import discover_skills
        print(sorted(s.name for s in discover_skills({str(ws)!r})))
    """)
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO)},
    ).stdout.strip()
    assert out == "['from-agents']", out


def test_project_level_directories_still_load(tmp_path):
    ws = tmp_path / "ws"
    _skill(ws / ".claude" / "skills", "project-claude")
    _skill(ws / ".agents" / "skills", "project-agents")
    original = skills_mod.GLOBAL_SKILL_DIRS
    skills_mod.GLOBAL_SKILL_DIRS = []
    try:
        names = sorted(s.name for s in skills_mod.discover_skills(str(ws)))
    finally:
        skills_mod.GLOBAL_SKILL_DIRS = original
    assert names == ["project-agents", "project-claude"]
