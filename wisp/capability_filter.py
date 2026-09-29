"""Host-owned capability partitions (13-I2).

Visibility only — NEVER authorization. The gates in
``infra/security.py`` / ``infra/policy_engine.py`` / ``ToolExecutor``
remain the final authority; a hidden tool is still denied if invoked,
and a visible tool is still gated before execution.

READ_ONLY_TOOLS mirrors ``infra.security._SAFE_READ_TOOLS`` (the
enforcement set) so visibility can never exceed what execution
permits. A test pins the two equal — drift in either file fails loudly.
NETWORK DECISION (§5): ``web_fetch``/``web_search`` stay visible in
READ_ONLY because execution already permits them there; removing them
would silently change intended read-only network behavior.
"""
from __future__ import annotations

from typing import Any

from wisp.core.contracts import declared_read_names, is_declared_read

# ponytail: literal 14-name allowlist (not derived at runtime) so the
# partition is auditable in one glance; equality with the enforcement
# set is pinned by test. If a 15th safe tool lands, update this set
# AND the pin together.
READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "read_file", "list_files", "search_codebase", "search_symbols",
    "git_status", "git_diff", "lsp_diagnostics", "lsp_definition",
    "lsp_references", "lsp_hover", "lsp_symbols", "web_fetch",
    "web_search", "recall",
})


def _mode_name(mode: Any) -> str:
    return str(getattr(mode, "value", mode) or "auto_edit").lower()


def _schema_name(schema: Any) -> str:
    if isinstance(schema, dict):
        fn = schema.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            return str(fn["name"])
    return ""


def filter_schemas_for_mode(schemas: list[dict[str, Any]],
                            permission_mode: Any) -> list[dict[str, Any]]:
    """Return a NEW provider-bound schema list for the mode.

    READ_ONLY keeps only allowlisted names (unknown/runtime-added names
    fail closed: hidden). Every other mode returns the input unchanged
    in content and order. Never mutates the input list or its dicts.
    """
    if _mode_name(permission_mode) != "read_only":
        return list(schemas)
    return [s for s in schemas
            if _schema_name(s) in READ_ONLY_TOOLS or is_declared_read(_schema_name(s))]


def visible_tool_names(allowed_set: set[str] | None,
                       permission_mode: Any,
                       capability_filtering: bool) -> set[str] | None:
    """Combine role allowlist with the mode partition for menus.

    Returns None when nothing filters (legacy "all" semantics preserved
    downstream); otherwise the intersected set. Child-role sets compose
    by intersection — narrowing only, never broadening.
    """
    if not capability_filtering:
        return allowed_set
    if _mode_name(permission_mode) != "read_only":
        return allowed_set
    permitted = set(READ_ONLY_TOOLS) | set(declared_read_names())
    if allowed_set is None:
        return permitted
    return set(allowed_set) & permitted
