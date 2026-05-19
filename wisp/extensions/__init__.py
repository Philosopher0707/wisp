"""Wisp extensions package.

Unified extension system: Plugin, Hook, MCP, Skill.
"""

from wisp.extensions.plugins import PluginExtension
from wisp.extensions.hooks import HookExtension
from wisp.extensions.mcp import MCPExtension
from wisp.extensions.skills import SkillExtension

__all__ = [
    "PluginExtension",
    "HookExtension",
    "MCPExtension",
    "SkillExtension",
]
