"""Skills — Warp-compatible skill discovery and parsing.

Supports the same SKILL.md format Warp uses:
  .agents/skills/<skill-name>/SKILL.md
  .warp/skills/<skill-name>/SKILL.md
  (and others: .claude, .codex, .cursor, .gemini, etc.)
"""

import yaml
from pathlib import Path
from typing import Optional


SKILL_DIR_NAMES = [
    ".agents/skills",
    ".warp/skills",
    ".claude/skills",
    ".codex/skills",
    ".cursor/skills",
    ".gemini/skills",
    ".opencode/skills",
    ".github/skills",
    ".copilot/skills",
    ".factory/skills",
]

GLOBAL_SKILL_DIRS = [
    Path.home() / ".agents/skills",
    Path.home() / ".warp/skills",
    Path.home() / ".claude/skills",
]


class Skill:
    """A parsed Warp-compatible skill from a SKILL.md file."""

    def __init__(self, name: str, description: str, instructions: str, file_path: Path):
        self.name = name
        self.description = description
        self.instructions = instructions
        self.file_path = file_path

    def __repr__(self):
        return f"Skill(name='{self.name}', desc='{self.description[:50]}')"


def parse_skill(file_path: Path) -> Optional[Skill]:
    """Parse a SKILL.md file into a Skill object.

    Expected format:
      ---
      name: skill-name
      description: What this skill does
      ---
      # Skill Title
      ... instructions ...
    """
    if not file_path.exists():
        return None

    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    # Extract YAML frontmatter between --- delimiters
    if not content.startswith("---"):
        return None

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter_str = parts[1].strip()
    instructions = parts[2].strip()

    try:
        meta = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError:
        return None

    if not isinstance(meta, dict):
        return None

    name = meta.get("name")
    description = meta.get("description", "")

    if not name:
        return None

    return Skill(
        name=name,
        description=description,
        instructions=instructions,
        file_path=file_path,
    )


def discover_skills(workspace: str) -> list[Skill]:
    """Discover all skills in the workspace and home directory.

    Scans:
      1. Global dirs: ~/.agents/skills/, ~/.warp/skills/, etc.
      2. Project dirs: <workspace>/.agents/skills/, etc.

    Returns skills sorted by: global first (lower priority), then project.
    """
    discovered: list[Skill] = []
    seen_names: set[str] = set()
    ws_path = Path(workspace).resolve()

    # Scan global dirs (lower priority)
    for skill_dir in GLOBAL_SKILL_DIRS:
        if skill_dir.exists():
            _scan_skill_dir(skill_dir, discovered, seen_names)

    # Scan project dirs
    for dir_name in SKILL_DIR_NAMES:
        project_dir = ws_path / dir_name
        if project_dir.exists():
            _scan_skill_dir(project_dir, discovered, seen_names)

    return discovered


def _scan_skill_dir(skill_dir: Path, result: list[Skill], seen: set[str]):
    """Scan a skill directory for SKILL.md files."""
    for entry in sorted(skill_dir.iterdir()):
        if entry.is_dir():
            skill_file = entry / "SKILL.md"
            if skill_file.exists():
                skill = parse_skill(skill_file)
                if skill and skill.name not in seen:
                    seen.add(skill.name)
                    result.append(skill)


def find_skill(name: str, workspace: str) -> Optional[Skill]:
    """Find a skill by name across all discovered locations."""
    skills = discover_skills(workspace)
    for skill in skills:
        if skill.name == name:
            return skill
    return None
