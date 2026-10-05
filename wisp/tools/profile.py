"""Tool profiles: which built-in tools the model is OFFERED by default.

A profile narrows the offer, never the capability. Every tool stays in the registry and is authorised exactly as before;
only the schemas sent to the provider and the prompt's tool menu change. Extension tools (MCP servers, skills) are the
operator's own configuration and are outside the profile. A subagent with an explicit tool list gets that list, whatever
the profile.

``core`` is the set wisp's own audit logs show it cannot do without: seven tools were 95% of 18,132 calls, plus
``run_tests`` (the completion gate's evidence), ``grep`` and ``glob`` (purpose-built search) and ``rewind`` (the undo
for file edits). ``full`` is the previous behaviour, one environment variable away: ``WISP_TOOL_PROFILE=full``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

PROFILES = ("core", "full")
DEFAULT_PROFILE = "core"

CORE_TOOLS: tuple[str, ...] = (
    "read_file", "write_file", "edit_file", "edit_file_multi",
    "run_bash", "run_tests",
    "list_files", "grep", "glob", "search_symbols",
    "rewind",
)


def parse_profile(raw: object) -> str:
    """A profile name, failing to the SMALLER surface: an unrecognised value means ``core``, never ``full``."""
    if isinstance(raw, str) and raw.strip().lower() in PROFILES:
        return raw.strip().lower()
    if raw not in (None, ""):
        logger.warning("Unknown tool profile %r; using %r", raw, DEFAULT_PROFILE)
    return DEFAULT_PROFILE


def builtin_names_for(profile: str) -> frozenset[str] | None:
    """The built-in tool names a profile offers, or None when it does not narrow (``full``)."""
    return frozenset(CORE_TOOLS) if parse_profile(profile) == "core" else None
