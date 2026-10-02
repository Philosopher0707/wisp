"""Capability and principal introspection router.

This router **reports** authority; it never grants or widens it. The gates
in ``infra/security.py`` / ``infra/policy_engine.py`` / ``ToolExecutor``
remain the sole enforcement points, exactly as ``wisp/capability_filter.py``
states for its own visibility partitions. A client reading this endpoint
learns what the current principal *is*; whether a given call is *permitted*
is still decided at execution time against those gates.

Endpoints:

* ``GET /api/capabilities``       effective tool surface for a principal
* ``GET /api/capabilities/tools`` full registry inventory, unfiltered
* ``GET /api/capabilities/health`` liveness for this surface

Never exposed: ``Principal.credential_handle``. Introspection reports *what*
may act, never the material that proves it.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Query

from wisp.auth.principal import Principal, local_principal
from wisp.server.deps import verify_api_key
from wisp.tools.registry import TOOL_SCHEMAS

router = APIRouter()

_DEFAULT_PROFILE = os.environ.get("WISP_PROFILE", "default")

_AUTHORITY_NOTE = (
    "Visibility only. Execution gates remain authoritative; a listed tool "
    "may still be denied by policy, approval, or principal."
)


def _schema_name(schema: dict[str, Any]) -> str:
    """Extract a tool name from either registry schema shape.

    Registry entries are OpenAI-style ``{"type": "function", "function":
    {"name": ...}}``, but plugin-contributed schemas have historically
    arrived in the flatter ``{"name": ...}`` form. Accept both rather than
    assuming one, so inventory never silently drops tools.
    """
    fn = schema.get("function")
    if isinstance(fn, dict) and fn.get("name"):
        return str(fn["name"])
    return str(schema.get("name") or "")


def _schema_description(schema: dict[str, Any]) -> str:
    fn = schema.get("function")
    if isinstance(fn, dict) and fn.get("description"):
        return str(fn["description"])
    return str(schema.get("description") or "")


def _tool_inventory() -> list[dict[str, str]]:
    """Deduplicated, sorted registry inventory."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for schema in TOOL_SCHEMAS:
        name = _schema_name(schema)
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "description": _schema_description(schema)})
    return sorted(out, key=lambda t: t["name"])


def _principal_view(principal: Principal) -> dict[str, Any]:
    """Serialise a principal for display. Omits credential_handle by design."""
    unbounded = principal.capabilities is None
    return {
        "id": principal.principal_id,
        "kind": str(principal.kind),
        "os_user": principal.os_user,
        "workspace": principal.workspace,
        "profile": principal.profile,
        "org_id": principal.org_id,
        "parent_principal_id": principal.parent_principal_id,
        "unbounded": unbounded,
    }


@router.get("/api/capabilities", dependencies=[Depends(verify_api_key)])
def get_capabilities(
    workspace: str = Query(default=""),
    profile: str = Query(default=_DEFAULT_PROFILE),
) -> dict[str, Any]:
    """Report the effective tool surface for a principal.

    ``unbounded`` is an explicit boolean rather than a folded-in count:
    ``capabilities=None`` means "governed by approval/policy", which is
    materially different from "every tool in the registry is permitted".
    A client must not render the two identically.

    Bounded sets are filtered through ``Principal.allows_tool`` — the same
    predicate the executor consults — so this list cannot drift from what
    the executor would actually admit.
    """
    principal = local_principal(workspace=workspace, profile=profile)
    unbounded = principal.capabilities is None

    inventory = _tool_inventory()
    if unbounded:
        tools = list(inventory)
    else:
        tools = [t for t in inventory if principal.allows_tool(t["name"])]

    return {
        "principal": _principal_view(principal),
        "tools": tools,
        "tool_count": len(tools),
        "registry_count": len(inventory),
        "authority_note": _AUTHORITY_NOTE,
    }


@router.get("/api/capabilities/tools", dependencies=[Depends(verify_api_key)])
def list_registry_tools() -> dict[str, Any]:
    """Full registry inventory, unfiltered. Reference display only."""
    tools = _tool_inventory()
    return {
        "tools": tools,
        "tool_count": len(tools),
        "authority_note": (
            "Inventory only. Presence here means the tool exists, not that "
            "the current principal may call it."
        ),
    }


@router.get("/api/capabilities/health", dependencies=[Depends(verify_api_key)])
def capabilities_health() -> dict[str, Any]:
    """Liveness for the capability surface."""
    return {"status": "ok", "registry_count": len(_tool_inventory())}
