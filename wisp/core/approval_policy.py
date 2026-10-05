"""Tool approval policy: which tools need an approver, and what waives or answers that need.

One small, pure module (no I/O, no executor, no presentation) holding the rule that every surface consults:

* ``get_write_tools``: the set of tools gated as writes (the default set, plus thin-harness names, plus every non-READ row of
  the risk table, so a new EXEC/WRITE tool can never silently skip approval, plan mode or read-only).
* ``forced_by_mode`` / ``approval_needed``: would the executor stop for an approver here? The one rule behind "no approver
  means no" (ADR-0061 R4), shared by the agent path and the REST gate so the two cannot drift.
* ``standing_grant_applies``: the USER's narrow, standing yes (``unattended_auto_approve_tools``) for callers with nobody to
  ask; READ- and NETWORK-class tools only, never over a forced approval.

It lived inside ``tool_executor.py`` (2,400 lines) and ``server/deps.py`` imported the whole executor to ask the predicate.
Moved here unchanged; ``tool_executor`` re-exports the names it used to define, so no caller and no test changed.
"""

from __future__ import annotations

import logging
from typing import Any

from wisp.core.contracts import TOOL_RISK_TABLE, ToolRisk, risk_for_tool
from wisp.infra.security import PermissionMode

logger = logging.getLogger(__name__)

__all__ = [
    "ALWAYS_GATED_TOOLS", "AUTO_EDIT_FORCED", "DEFAULT_WRITE_TOOLS", "THIN_WRITE_TOOLS",
    "approval_needed", "forced_by_mode", "get_write_tools", "non_read_table_tools", "standing_grant_applies",
]

# Tools that modify workspace state and require approval when auto_approve=False
DEFAULT_WRITE_TOOLS: set[str] = {
    "write_file",
    "edit_file",
    "edit_file_multi",
    "run_bash",
    # Thin-harness equivalents of the above (GH#25): same approval /
    # plan-mode / read-only treatment as the 42-tool names they replace.
    "exec_sandbox",
    "fs_mutate",
    "git_branch",
    "git_commit",
    "git_push",
    "gh_pr_create",
    "gh_pr_comment",
    "gh_pr_close",
    "gh_pr_merge",
    "git_sync_base",
    "plan_task",
    "mark_step_done",
    "update_plan",
    "spawn",
    "fanout",
    "spawn_background",
    "subagent_send",
    "orchestrate_vote",
    "orchestrate_map_reduce",
    "orchestrate_chain",
    "orchestrate_dag",
    "capture_skill",
}

# Thin-harness names that are ALWAYS write-classified (GH#25) — unioned
# into every resolution in get_write_tools so no config default or user
# override can leave the run_bash / write_file equivalents ungated.
THIN_WRITE_TOOLS: set[str] = {"exec_sandbox", "fs_mutate"}

# Gated although the risk table names no row for them (fail-closed EXEC).
ALWAYS_GATED_TOOLS: frozenset[str] = frozenset({"rewind"})


#: In `auto_edit`, the tools that go through the approver even when `auto_approve` is on.
AUTO_EDIT_FORCED = frozenset({
    "run_bash", "git_branch", "git_commit", "git_push", "gh_pr_create",
    "gh_pr_comment", "gh_pr_close", "gh_pr_merge", "git_sync_base",
})


def forced_by_mode(config: Any, func_name: str) -> bool:
    """True when the permission mode forces this built-in tool through the approver (auto_approve does not waive it)."""
    mode = getattr(config, "permission_mode", PermissionMode.AUTO_EDIT)
    if mode == PermissionMode.ASK_ALL:
        return func_name in get_write_tools(config)
    if mode == PermissionMode.AUTO_EDIT:
        return func_name in AUTO_EDIT_FORCED
    return False


def approval_needed(config: Any, func_name: str) -> bool:
    """Would the executor stop for an approver before running this built-in tool under `config`?

    The one rule behind "no approver ⇒ deny" (ADR-0061 R4), shared by the agent path and the REST gate so
    the two cannot drift: the tool is gated as a write, and either the mode forces it through the approver
    or nobody authorised it (`full` mode, or `auto_approve`, is the caller's explicit decision). In
    `read_only` writes are hard-blocked earlier, so no approver is ever asked and this is False. MCP tools
    are not covered here; they are always asked (`_is_external_call`).
    """
    mode = getattr(config, "permission_mode", PermissionMode.AUTO_EDIT)
    if mode == PermissionMode.READ_ONLY or func_name not in get_write_tools(config):
        return False
    return forced_by_mode(config, func_name) or (
        mode != PermissionMode.FULL and not getattr(config, "auto_approve", False))


_UNGRANTABLE_WARNED: set[str] = set()


def standing_grant_applies(config: Any, func_name: str) -> bool:
    """Has the USER pre-approved ``func_name`` for callers with nobody to ask (`unattended_auto_approve_tools`)?

    Narrow by construction: only READ- and NETWORK-class tools can be named. A mutating, shell or MCP tool in the
    list is ignored (once, with a warning), because the model must never be able to authorise a side effect and a
    config typo must not widen the surface; those still need a real approver or an explicit `auto_approve`.
    The caller decides when this may be consulted (only when there is no approver, never over a forced approval).
    """
    if func_name not in (getattr(config, "unattended_auto_approve_tools", ()) or ()):
        return False
    if risk_for_tool(func_name) in (ToolRisk.READ, ToolRisk.NETWORK):
        return True
    if func_name not in _UNGRANTABLE_WARNED:
        _UNGRANTABLE_WARNED.add(func_name)
        logger.warning("unattended_auto_approve_tools names %s, which is not a read/network tool: ignored "
                       "(it still needs an approver or auto_approve)", func_name)
    return False


def get_write_tools(config: Any = None) -> set[str]:
    """Resolve write-classification tools from config (env: WISP_WRITE_TOOLS).

    Union rule (no drift by construction): every non-READ row of the risk
    table is gated, so a new EXEC/WRITE tool can never silently skip
    approval / plan-mode / read-only. Thin-harness names are always unioned
    in (GH#25), and "rewind" is always gated (EXEC by fail-closed default;
    it restores files). git_checkpoint stays read-classified by intent.
    """
    if config is not None and hasattr(config, "write_tools") and config.write_tools:
        base = set(config.write_tools)
    else:
        base = set(DEFAULT_WRITE_TOOLS)
    base |= THIN_WRITE_TOOLS | ALWAYS_GATED_TOOLS | non_read_table_tools()
    return base


def non_read_table_tools() -> set[str]:
    """Names the risk table classifies above READ."""
    return {name for name, risk in TOOL_RISK_TABLE.items() if risk is not ToolRisk.READ}
