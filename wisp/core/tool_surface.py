"""What an agent will actually be offered, and why a tool is absent.

The offer is the composition of four filters that live in four places: the role's tool list, the child permission-mode filter
(``filter_allowed_for_mode``), the tool profile, and the capability partition. A researcher "has no run_bash" because of the
second, and nothing could say so; a user setting ``WISP_TOOL_PROFILE`` could not see which tools it removed.

``explain_surface`` composes the REAL filters, in the order the real pipeline applies them, and reports for every missing tool
the FIRST stage that removed it and why. It re-implements none of them, so it cannot drift from them; a differential test
(``tests/test_core_tool_surface.py``) holds its ``offered`` set equal to what ``_effective_child_tools`` plus
``WispAgentCore._provider_tools`` really put in a provider request, across modes, profiles and the capability flag.

Read-only and pure: it never mutates its inputs and grants nothing. Visibility is not authorization (the executor's gates
remain the authority); this only answers "will the model be shown this tool, and if not, which switch hides it?".
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["Absence", "Surface", "explain_surface", "inherited_extension_tools"]

#: Stage names, in the order the real pipeline applies them.
STAGES = ("subagent", "role", "mode", "profile", "capability")


@dataclass(frozen=True)
class Absence:
    """A tool that is not offered: the first stage that removed it, and why, in words."""

    tool: str
    stage: str
    reason: str


@dataclass(frozen=True)
class Surface:
    offered: tuple[str, ...]
    absent: tuple[Absence, ...]


def inherited_extension_tools(permission_mode: Any, extension_tools: Iterable[str] = ()) -> frozenset[str]:
    """The extension (MCP) tools an unrestricted subagent child inherits under ``permission_mode``.

    The runner used to expand a child's "all" into the built-in registry only, so a generalist child never saw an extension
    tool. The rule is narrow, and it is ONE function, used by the runner (`_effective_child_tools`) and by `explain_surface`, so
    the two cannot drift:

    * every MCP tool whose operator declared ``tool_risk: read`` (``declared_read_names``), in every mode: it executes
      unprompted and ``read_only`` mode already permits it, so this adds no authority;
    * in ``full`` mode also the other MCP tools in ``extension_tools`` (the user has already authorised everything, and the child
      gets ``run_bash`` there too);
    * never a ``skill__*`` tool (the parent's skill menu, about 10k tokens per call), and never an undeclared MCP tool in
      ``auto_edit`` or ``ask_all``: it always needs an approver and a child has none, so offering it only buys a turn that ends in
      a guaranteed block.
    """
    from wisp.core.contracts import declared_read_names

    mode = str(getattr(permission_mode, "value", permission_mode) or "auto_edit").lower()
    inherited = set(declared_read_names())
    if mode == "full":
        inherited |= {t for t in extension_tools if t.startswith("mcp__")}
    return frozenset(inherited)


def _mode_reason(mode: str) -> str:
    if mode == "read_only":
        return "read_only mode offers a child only the safe read set"
    if mode == "ask_all":
        return "a child has no approver, so ask_all (which prompts before each write) blocks it for children"
    return ("it needs a human's approval in auto_edit and a child has no approver; "
            "set permission_mode=full (the user's decision) to offer it")


def explain_surface(
    all_tools: Iterable[str],
    *,
    role_tools: Sequence[str] | None = None,
    child: bool = False,
    subagent: bool = False,
    permission_mode: Any = "auto_edit",
    profile: str = "core",
    capability_filtering: bool = False,
    builtin_names: frozenset[str] | set[str] | None = None,
    extension_tools: Sequence[str] = (),
) -> Surface:
    """Offered tools and per-tool absence reasons for one agent.

    ``all_tools``: every tool the host could offer (built-ins plus extension tools). ``role_tools``: the agent's explicit tool
    list, or None for unrestricted ("all" also means unrestricted, except for a child: see stage 2). ``child``: a subagent child, whose inherited permission mode
    drops tools it has no approver for. ``subagent``: an unrestricted subagent (a subagent prompt and no explicit list), which
    also loses the parent's skill menu. ``builtin_names``: names in the built-in registry; any other name is an extension tool,
    which the profile never hides (defaults to the registry's own names).
    """
    from wisp.capability_filter import filter_schemas_for_mode
    from wisp.core.stateless import _SUBAGENT_EXCLUDED_TOOL_PREFIXES
    from wisp.infra.policy_engine import filter_allowed_for_mode
    from wisp.tools.profile import builtin_names_for

    names = list(dict.fromkeys(all_tools))
    mode = str(getattr(permission_mode, "value", permission_mode) or "auto_edit").lower()
    restricted = role_tools is not None and "all" not in {str(t).lower() for t in role_tools}
    if builtin_names is None:
        from wisp.tools.registry import TOOL_SCHEMAS

        builtin_names = {s.get("function", {}).get("name", "") for s in TOOL_SCHEMAS}

    absent: list[Absence] = []
    live = names

    def drop(keep: set[str], stage: str, reason: str) -> None:
        nonlocal live
        absent.extend(Absence(t, stage, reason) for t in live if t not in keep)
        live = [t for t in live if t in keep]

    # 1. An unrestricted subagent does not inherit the parent's skill menu.
    if subagent and not restricted:
        drop({t for t in live if not t.startswith(_SUBAGENT_EXCLUDED_TOOL_PREFIXES)}, "subagent",
             "an unrestricted subagent does not inherit the parent's skill menu (skill__* tools)")

    # 2. The role's explicit list. A child's list is ALWAYS explicit: the runner expands "all" (or no list) into the built-in
    #    registry plus the MCP tools it may inherit (`inherited_extension_tools`), so as the list is explicit the profile does
    #    not apply to it either, and skill tools are never inherited.
    if child and not restricted:
        restricted = True
        drop(set(builtin_names) | inherited_extension_tools(mode, extension_tools), "role",
             "a child's tool list is the built-in registry plus the MCP tools it may inherit (declared tool_risk: read in any "
             "mode; any MCP tool in full mode); skill tools are never inherited")
    elif restricted:
        allowed = {str(t) for t in role_tools or ()}
        drop(allowed, "role", "not in this role's tool list")

    # 3. A child under a permission mode with no approver (the runner applies this to the role's list).
    if child:
        drop(set(filter_allowed_for_mode(mode, live)), "mode", _mode_reason(mode))

    # 4. The tool profile narrows built-ins only, and only for an unrestricted agent (an explicit list is honoured).
    if not restricted:
        offered_builtins = builtin_names_for(profile)
        if offered_builtins is not None:
            drop({t for t in live if t not in builtin_names or t in offered_builtins}, "profile",
                 f"outside the {profile!r} tool profile (WISP_TOOL_PROFILE=full offers every built-in)")

    # 5. The capability partition (host-owned visibility), when enabled: read_only keeps only the safe read set.
    if capability_filtering:
        schemas = [{"function": {"name": t}} for t in live]
        kept = {s["function"]["name"] for s in filter_schemas_for_mode(schemas, mode)}
        drop(kept, "capability", "the read_only capability partition offers only the safe read set")

    return Surface(offered=tuple(live), absent=tuple(absent))
