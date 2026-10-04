"""Skills — Warp-compatible skill discovery and parsing.

Supports the same SKILL.md format Warp uses:
  .agents/skills/<skill-name>/SKILL.md
  .warp/skills/<skill-name>/SKILL.md
  (and others: .claude, .codex, .cursor, .gemini, etc.)
"""

import logging
import re
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
    # Where `capture_resolved_skill` writes. It was missing here, so auto-captured skills were written and never
    # loaded back.
    ".wisp/skills/auto",
]

GLOBAL_SKILL_DIRS = [
    Path.home() / ".agents/skills",
    Path.home() / ".warp/skills",
    Path.home() / ".claude/skills",
]


class Skill:
    """A parsed Warp-compatible skill from a SKILL.md file."""

    def __init__(self, name: str, description: str, instructions: str, triggers: list[str], file_path: Path,
                 model_invocable: bool = True, inline_instructions: bool = False):
        self.name = name
        self.description = description
        self.instructions = instructions
        self.triggers = triggers  # trigger phrases for auto-detection
        self.file_path = file_path
        # Advertisement eligibility (13-I0): False hides the skill from
        # model-visible tool advertisement. Authorization is untouched —
        # this never grants anything, only withholds advertisement.
        self.model_invocable = model_invocable
        # `inline-instructions: true` puts the whole body in the system prompt instead of the first 200
        # characters. For skills a session cannot load through `skill__*` (read_only refuses it).
        self.inline_instructions = inline_instructions

    def __repr__(self):
        return f"Skill(name='{self.name}', desc='{self.description[:50]}')"


# ── disable-model-invocation contract (13-I0) ─────────────────────
# Exactly four states; never truthiness. The contract applies to the
# PARSED Python value (PyYAML coerces yes/no/on/off to bool — that is
# the parser's business; we only classify what we receive).
VISIBILITY_ABSENT = "ABSENT"      # field absent → existing default
VISIBILITY_ENABLED = "ENABLED"    # boolean False → model-invocable
VISIBILITY_DISABLED = "DISABLED"  # boolean True → hidden
VISIBILITY_INVALID = "INVALID"    # anything else → hidden, fail closed

_DISABLE_FIELD = "disable-model-invocation"
_ABSENT = object()


def resolve_invocation_visibility(raw: object, *, skill_name: str = "",
                                  file_path: object = None) -> str:
    """Classify a parsed `disable-model-invocation` value.

    Actual booleans only: `type(v) is bool` (isinstance would admit
    ints, since bool subclasses int). Every non-bool — including the
    strings "true"/"false", ints, null, collections — is INVALID and
    therefore hidden. Logs a diagnostic for INVALID (existing logger
    channel; name/path/field only, never skill contents).
    """
    if raw is _ABSENT:
        return VISIBILITY_ABSENT
    if type(raw) is bool:
        return VISIBILITY_DISABLED if raw else VISIBILITY_ENABLED
    logger.warning(
        "Skill %r (%s): malformed %r value %r — model invocation "
        "disabled (fail-closed). Expected boolean true/false or absence.",
        skill_name or "?", str(file_path or "?"), _DISABLE_FIELD, raw,
    )
    return VISIBILITY_INVALID


def is_model_advertisable(state: str) -> bool:
    """ABSENT keeps the existing default (advertised); only an explicit
    boolean true — or any malformed value — hides the skill."""
    return state in (VISIBILITY_ABSENT, VISIBILITY_ENABLED)


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
    triggers = meta.get("triggers", []) or []
    if isinstance(triggers, str):
        triggers = [t.strip() for t in triggers.split(",") if t.strip()]

    if not name:
        return None

    # 13-I0: resolve advertisement eligibility at the parsing boundary.
    visibility = resolve_invocation_visibility(
        meta.get(_DISABLE_FIELD, _ABSENT),
        skill_name=str(name), file_path=file_path)

    # NOTE: Skills are parsed without a regex blacklist. The prompt assembler
    # treats them as *suggestions*, not mandates, and appends a safety footer.
    # Tool-level guards (dangerous-command blocking, permission mode, hooks)
    # provide the real defense, not pattern matching on user-supplied markdown.

    return Skill(
        name=name,
        description=description,
        instructions=instructions,
        triggers=triggers,
        file_path=file_path,
        model_invocable=is_model_advertisable(visibility),
        # A real boolean only, like `disable-model-invocation`: "true", 1 and the rest keep the cut.
        inline_instructions=meta.get("inline-instructions") is True,
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


def match_skills(query: str, workspace: str, min_score: float = 0.0) -> list[tuple[Skill, float]]:
    """Match skills against a user query using trigger-based scoring.

    Returns a list of (skill, score) tuples sorted by score (descending).
    
    Matching rules:
    - Name exact or partial match: +3.0
    - Trigger phrase found in query: +2.0 per phrase
    - Description keyword overlap: +0.5 per keyword
    
    Only skills with score > min_score are returned.
    """
    skills = discover_skills(workspace)
    query_lower = query.lower()
    query_words = set(re.findall(r"[a-zA-Z0-9]{2,}", query_lower))
    
    results: list[tuple[Skill, float]] = []
    for skill in skills:
        score = 0.0
        
        # Name match
        name_lower = skill.name.lower()
        if name_lower == query_lower:
            score += 3.0
        elif name_lower in query_lower:
            score += 2.0
        
        # Trigger matches
        for trigger in skill.triggers:
            trigger_lower = trigger.lower()
            if trigger_lower in query_lower:
                score += 2.0
        
        # Description keyword overlap
        desc_words = set(re.findall(r"[a-zA-Z0-9]{2,}", skill.description.lower()))
        overlap = query_words & desc_words
        score += len(overlap) * 0.5
        
        if score >= min_score:
            results.append((skill, score))
    
    results.sort(key=lambda x: x[1], reverse=True)
    return results
