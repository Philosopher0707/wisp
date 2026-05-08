"""Skills — Warp-compatible skill discovery and parsing.

Supports the same SKILL.md format Warp uses:
  .agents/skills/<skill-name>/SKILL.md
  .warp/skills/<skill-name>/SKILL.md
  (and others: .claude, .codex, .cursor, .gemini, etc.)

Also supports OntoSkills ontology-backed skill resolution:
  - Deterministic skill matching via SPARQL queries
  - 150x fewer tokens than loading raw markdown
  - Works on small models (4B+)
  - Set WISP_ONTOLOGY_PATH to enable (e.g. ~/.ontoskills/wisp/)
"""

import logging
import yaml
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


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
      1. Project dirs: <workspace>/.agents/skills/, etc. (higher priority)
      2. Global dirs: ~/.agents/skills/, ~/.warp/skills/, etc. (lower priority)

    Returns skills sorted by: project first (higher priority), then global.
    Project skills shadow global skills with the same name.
    """
    discovered: list[Skill] = []
    seen_names: set[str] = set()
    ws_path = Path(workspace).resolve()

    # Scan project dirs FIRST (higher priority)
    for dir_name in SKILL_DIR_NAMES:
        project_dir = ws_path / dir_name
        if project_dir.exists():
            _scan_skill_dir(project_dir, discovered, seen_names)

    # Scan global dirs (lower priority - only if not already seen)
    for skill_dir in GLOBAL_SKILL_DIRS:
        if skill_dir.exists():
            _scan_skill_dir(skill_dir, discovered, seen_names)

    return discovered


def _scan_skill_dir(skill_dir: Path, result: list[Skill], seen: set[str]):
    """Scan a skill directory for SKILL.md files."""
    try:
        entries = sorted(skill_dir.iterdir())
    except (PermissionError, OSError) as e:
        logger.warning("Cannot read skill directory %s: %s", skill_dir, e)
        return
    for entry in entries:
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


# ── OntoSkills integration ──────────────────────────────────────────

import os as _os
import sys as _sys

_ONTOLOGY_PATH = _os.environ.get("WISP_ONTOLOGY_PATH", "")
_ontology_cache: dict = {}


def has_ontology() -> bool:
    """Check if ontology-backed skills are available.
    
    Re-checks WISP_ONTOLOGY_PATH env var on each call (not cached at import).
    """
    path = _os.environ.get("WISP_ONTOLOGY_PATH", "")
    return bool(path and _os.path.isdir(path))


def _get_ontology_path() -> str:
    """Get current ontology path from environment."""
    return _os.environ.get("WISP_ONTOLOGY_PATH", _ONTOLOGY_PATH)


def _get_ontology_client():
    """Lazy-load the OntoSkills client (subprocess-based, heavy)."""
    path = _get_ontology_path()
    if "client" not in _ontology_cache or _ontology_cache.get("_path") != path:
        from ontoskills import OntoSkillsClient
        client = OntoSkillsClient(ontology_root=path)
        _ontology_cache["client"] = client
        _ontology_cache["started"] = False
        _ontology_cache["_path"] = path
    return _ontology_cache["client"]


def match_skill_via_ontology(query: str) -> Optional[dict]:
    """Query the ontology for a matching skill.

    Returns dict with 'name', 'intent', 'context' keys, or None if no match.
    This is DETERMINISTIC — same query always returns same result.
    """
    if not has_ontology():
        return None

    # Quick cache check
    cache_key = query.lower().strip()[:100]
    if cache_key in _ontology_cache:
        return _ontology_cache[cache_key]

    import asyncio

    async def _query():
        client = _get_ontology_client()
        if not _ontology_cache["started"]:
            await client.start()
            _ontology_cache["started"] = True

        # Use search() which handles both exact ID match and BM25 search
        results = await client.search(query, top_k=1)
        if not results:
            return None

        r = results[0]
        ctx = await client.get_context(r.skill_id)

        from ontoskills.formatter import ContextFormatter
        return {
            "name": r.skill_id,
            "intent": r.intent or ctx.intent or "",
            "context": ContextFormatter.format_context(ctx),
        }

    try:
        result = asyncio.run(_query())
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Ontology query failed: %s", e)
        result = None

    if result:
        _ontology_cache[cache_key] = result
    return result
