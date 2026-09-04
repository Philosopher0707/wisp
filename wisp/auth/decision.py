"""Layered authorization decision (M2 authority layer, pure).

Layer order (each narrows only):
  principal capabilities → workspace trust → tool risk class →
  action arguments/target → data sensitivity → approval requirement.

Broad PermissionMode is kept as the session-preference layer and mapped to
approval requirements, not authority.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from wisp.auth.principal import Principal
from wisp.auth.workspace_trust import WorkspaceTrust
from wisp.core.contracts import ToolRisk, risk_for_tool

# Tools that mutate without a specific path target.
_EXEC_TOOLS = frozenset({"run_bash", "git_push", "spawn", "fanout"})


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason: str = ""
    controlling_layer: str = ""  # principal|workspace|capability|arguments|sensitivity|approval|allow
    approval_required: bool = False
    obligations: tuple[str, ...] = ()
    version: int = 1


def _mcp_server_id(tool_name: str) -> str | None:
    """Extract the server id from namespaced MCP tool names."""
    if tool_name.startswith("mcp:") and "/" in tool_name:
        return tool_name[4:].split("/", 1)[0]
    if tool_name.startswith("mcp__") and tool_name.count("__") >= 2:
        return tool_name.split("__")[1]
    return None


def authorize(principal: Principal, tool_name: str, args: dict[str, Any],
              workspace_trust: WorkspaceTrust,
              permission_mode: str = "auto_edit",
              sensitivity: str = "confidential",
              effective_policy: Any = None) -> AuthorizationDecision:
    # L0 — organization policy bundle (narrow-only; absent = no opinion).
    if effective_policy is not None:
        matrix = getattr(effective_policy, "approval_matrix", {}) or {}
        provenance = getattr(effective_policy, "provenance", {}) or {}
        level = matrix.get(tool_name)
        if level == "deny":
            layer = provenance.get(f"approval:{tool_name}", "organization")
            return AuthorizationDecision(
                allowed=False, controlling_layer=layer,
                reason=f"Denied {tool_name}: approval level is 'deny', "
                       f"controlled by the {layer} policy layer")
        allowlist = tuple(getattr(effective_policy, "mcp_allowlist", ()) or ())
        server = _mcp_server_id(tool_name)
        if server is not None and allowlist and server not in allowlist:
            return AuthorizationDecision(
                allowed=False, controlling_layer="organization",
                reason=f"Denied {tool_name}: MCP server {server!r} is not "
                       f"on the organization allowlist")
        policy_approves = (level == "approve")
    else:
        policy_approves = False

    # L1 — principal capabilities.
    if not principal.allows_tool(tool_name):
        return AuthorizationDecision(
            allowed=False, controlling_layer="principal",
            reason=f"principal {principal.principal_id} lacks capability {tool_name}")

    # L2 — workspace trust.
    if workspace_trust == WorkspaceTrust.QUARANTINED:
        if risk_for_tool(tool_name) != ToolRisk.READ:
            return AuthorizationDecision(
                allowed=False, controlling_layer="workspace",
                reason="quarantined workspace: non-read tools denied")
    elif workspace_trust == WorkspaceTrust.READ_ONLY:
        if risk_for_tool(tool_name) != ToolRisk.READ:
            return AuthorizationDecision(
                allowed=False, controlling_layer="workspace",
                reason="read-only workspace: mutation denied")

    # L3 — tool risk class vs sensitivity.
    risk = risk_for_tool(tool_name)
    obligations: list[str] = []
    if risk in (ToolRisk.EXEC, ToolRisk.PRIVILEGED) and sensitivity == "restricted":
        return AuthorizationDecision(
            allowed=False, controlling_layer="sensitivity",
            reason=f"{tool_name} refused on restricted data")

    # L4 — arguments/target scan (hook-dir guard: audit §3 class).
    target = str((args or {}).get("path", "") or (args or {}).get("command", ""))
    if ".wisp/hooks" in target.replace("\\", "/") and risk != ToolRisk.READ:
        return AuthorizationDecision(
            allowed=False, controlling_layer="arguments",
            reason="hook-directory mutation refused (privilege-escalation guard)")

    # L5 — approval requirement from session preference layer.
    # A bundle-level "approve" forces approval even in full mode.
    if policy_approves:
        return AuthorizationDecision(
            allowed=True, controlling_layer="approval",
            reason=f"{tool_name} requires explicit approval (policy)",
            approval_required=True, obligations=("explicit-user-approval",))
    mode = (permission_mode or "auto_edit").lower()
    if mode == "full":
        approval_required = False
    elif mode == "read_only":
        if risk != ToolRisk.READ:
            return AuthorizationDecision(
                allowed=False, controlling_layer="approval",
                reason="read-only mode: mutation denied")
        approval_required = False
    elif mode == "ask_all":
        approval_required = risk != ToolRisk.READ
    else:  # auto_edit and unknown: writes auto, exec/network ask
        approval_required = risk in (ToolRisk.EXEC, ToolRisk.NETWORK, ToolRisk.PRIVILEGED)

    if approval_required:
        obligations.append("explicit-user-approval")
        return AuthorizationDecision(
            allowed=True, controlling_layer="approval",
            reason=f"{tool_name} requires explicit approval",
            approval_required=True, obligations=tuple(obligations))
    return AuthorizationDecision(
        allowed=True, controlling_layer="allow",
        reason=f"{tool_name} allowed by layered policy",
        obligations=tuple(obligations))
