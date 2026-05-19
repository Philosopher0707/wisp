"""SkillExtension — wraps Skill discovery for ExtensionHost.

Provides skill-based tools and lifecycle management.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SkillExtension:
    """Extension adapter for the skill system."""

    name = "skills"

    def __init__(self, workspace: str = "."):
        self._workspace = Path(workspace)
        self._skills: list[Any] = []

    def start(self) -> None:
        """Start the skill extension — discover skills."""
        from wisp.skills import discover_skills
        try:
            self._skills = discover_skills(str(self._workspace))
            logger.debug("SkillExtension started with %d skills", len(self._skills))
        except Exception as exc:
            logger.warning("SkillExtension start() failed: %s", exc)
            self._skills = []

    def stop(self) -> None:
        """Stop the skill extension."""
        self._skills = []
        logger.debug("SkillExtension stopped")

    def tools(self) -> list[dict]:
        """Return skill-based tools.

        Each skill can be invoked as a tool with its instructions.
        """
        tools = []
        for skill in self._skills:
            try:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": f"skill__{skill.name}",
                        "description": skill.description,
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "prompt": {
                                    "type": "string",
                                    "description": "The task to perform using this skill",
                                },
                            },
                            "required": ["prompt"],
                        },
                    },
                })
            except Exception as exc:
                logger.warning("SkillExtension tools() failed for %s: %s", getattr(skill, "name", "?"), exc)
        return tools

    def intercept(self, event: dict) -> dict:
        """Skills don't block events by default."""
        return {"action": "allow"}
