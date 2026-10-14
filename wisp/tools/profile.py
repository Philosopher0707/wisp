"""Tool profiles: which tools the model is offered, and so which it may call.

What a session is offered is what it may call: the engine refuses a call to any tool outside the list it sent the
provider (status NOT_OFFERED), so a profile narrows the capability, not only the offer. Owner decision 2026-10-14:
the default, ``basic``, is exactly ``run_bash``, ``read_file``, ``edit_file`` and ``write_file``, with no extension
(MCP, plugin, skill) tools. A subagent with an explicit tool list gets that list, whatever the profile.

``core`` (opt-in) is the set wisp's own audit logs show it uses most: seven tools were 95% of 18,132 calls, plus
``run_tests``, ``grep``, ``glob`` and ``rewind``, with extension tools. ``full`` offers every built-in:
``WISP_TOOL_PROFILE=full``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

PROFILES = ("basic", "core", "full")
DEFAULT_PROFILE = "basic"

BASIC_TOOLS: tuple[str, ...] = ("read_file", "write_file", "edit_file", "run_bash")

CORE_TOOLS: tuple[str, ...] = (
    "read_file", "write_file", "edit_file", "edit_file_multi",
    "run_bash", "run_tests",
    "list_files", "grep", "glob", "search_symbols",
    "rewind",
)


def parse_profile(raw: object) -> str:
    """A profile name, failing to the SMALLEST surface: an unrecognised value means ``basic``, never ``full``."""
    if isinstance(raw, str) and raw.strip().lower() in PROFILES:
        return raw.strip().lower()
    if raw not in (None, ""):
        logger.warning("Unknown tool profile %r; using %r", raw, DEFAULT_PROFILE)
    return DEFAULT_PROFILE


def builtin_names_for(profile: str) -> frozenset[str] | None:
    """The built-in tool names a profile offers, or None when it does not narrow (``full``)."""
    name = parse_profile(profile)
    if name == "basic":
        return frozenset(BASIC_TOOLS)
    return frozenset(CORE_TOOLS) if name == "core" else None


def offers_extensions(profile: str) -> bool:
    """Whether extension tools (MCP servers, plugins, skills) are offered too: not under ``basic``."""
    return parse_profile(profile) != "basic"
