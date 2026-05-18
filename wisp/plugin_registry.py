"""Plugin system for registering custom tools at runtime.

Register tools without monkey-patching `TOOL_SCHEMAS` / `TOOL_IMPLS`.

Usage::
    from wisp.plugin_registry import register_tool, list_plugin_tools
    import os

    def my_webhook(data: str, url: str = "https://api.example.com") -> str:
        return f"Sent {data} to {url}"

    register_tool(
        name="notify_webhook",
        impl=my_webhook,
        schema=None,  # auto-generated from signature
        description="Send a notification to a webhook endpoint",
    )

    # Inside WispAgentCore, tools are available automatically.
"""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, get_type_hints

logger = logging.getLogger(__name__)


@dataclass
class PluginTool:
    name: str
    impl: Callable
    schema: dict[str, Any]


# Module-level storage (thread-safe in CPython due to GIL on dict ops)
_plugin_tools: dict[str, PluginTool] = {}
_plugin_schemas: list[dict] = []


# ── JSON Schema helpers ──────────────────────────────────────────────

_TYPE_MAP: dict[type, tuple[str, Any]] = {
    str: ("string", None),
    int: ("integer", None),
    float: ("number", None),
    bool: ("boolean", None),
    list: ("array", None),
    dict: ("object", None),
}


def _python_type_to_json_schema(annotation: type) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON schema fragment."""
    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ())

    # Handle Optional[T] = Union[T, None]
    if origin is type(None) or annotation is type(None):
        return {}

    if origin is not None:
        if origin is list or origin is set or origin is tuple:
            items = _python_type_to_json_schema(args[0]) if args else {"type": "string"}
            return {"type": "array", "items": items}
        if origin is dict:
            return {"type": "object"}
        # Optional[T]
        if len(args) == 2 and type(None) in args:
            non_none = [a for a in args if a is not type(None)][0]
            return _python_type_to_json_schema(non_none)
        return {"type": "string"}

    # Direct type
    type_name, meta = _TYPE_MAP.get(annotation, ("string", None))
    result: dict[str, Any] = {"type": type_name}
    if meta is not None:
        result.update(meta)
    return result


def _build_schema_from_signature(func: Callable, name: str, description: str = "") -> dict:
    """Build an Ollama-compatible tool schema from a Python function."""
    sig = inspect.signature(func)
    hints = get_type_hints(func) if hasattr(func, "__annotations__") else {}

    properties: dict[str, dict] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("workspace", "file_lock", "lsp_manager"):
            # These are auto-injected by execute_tool — skip from LLM schema
            continue

        if param_name in ("args", "kwargs"):
            continue

        annotation = hints.get(param_name, str)
        schema_frag = _python_type_to_json_schema(annotation)

        if description:
            schema_frag["description"] = f"{param_name} of {name}"

        properties[param_name] = schema_frag

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or func.__doc__ or f"Tool: {name}",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


# ── Public API ───────────────────────────────────────────────────────

def register_tool(
    name: str,
    impl: Callable,
    schema: Optional[dict] = None,
    description: str = "",
    namespace: str = "",
) -> None:
    """Register a custom tool at runtime.

    SECURITY WARNING: Plugin tools run in the **same process** with full
    filesystem and network access. There is no sandbox. Only load plugins
    from trusted sources.

    Args:
        name: Tool function name.  Must be unique; conflicts silently
              override the previous registration with a logged warning.
        impl: The synchronous function that implements the tool.  Parameter
              names must match the schema.  Can auto-return dict/status.
        schema: Ollama-style ``{"type": "function", "function": {...}}`` dict.
                If *None*, auto-generated from the function's signature.
        description: Fallback description for auto-generated schema.
        namespace: Optional prefix to isolate the tool (e.g. ``"myplugin"``
                   produces ``"myplugin__read_file"``).  This prevents
                   shadowing built-in tools and collisions between plugins.
    """
    # Apply namespace isolation so plugins cannot shadow built-ins
    if namespace:
        name = f"{namespace}__{name}"

    # Protect against collision shadowing built-in tools
    try:
        from wisp.tools.registry import TOOL_IMPLS
        if name in TOOL_IMPLS:
            raise ValueError(f"Cannot register plugin tool '{name}' — name is reserved by built-in tools.")
    except ImportError:
        pass

    if name in _plugin_tools:
        # Allow overriding with a warning (plugins are expected to be user-controlled)
        logger.warning("Overriding previously registered plugin tool '%s'", name)
        _plugin_schemas[:] = [s for s in _plugin_schemas if s["function"]["name"] != name]

    if schema is None:
        schema = _build_schema_from_signature(impl, name, description)

    # Validate that schema shapes match
    if not _is_valid_tool_schema(schema):
        raise ValueError(f"Invalid tool schema for '{name}': missing 'function' or 'name'")

    # Ensure the schema name matches the namespaced name
    schema["function"]["name"] = name

    _plugin_tools[name] = PluginTool(name=name, impl=impl, schema=schema)
    _plugin_schemas.append(schema)
    logger.debug("Registered plugin tool '%s' (%d param)", name, len(schema.get("function", {}).get("parameters", {}).get("properties", {})))


def register_tools(tools: list[dict]) -> None:
    """Bulk-register tools from a list of descriptors.

    Each descriptor::
        {
            "name": "my_tool",
            "impl": my_func,
            "schema": schema_dict,  # optional
            "description": "...",      # optional
        }
    """
    for desc in tools:
        register_tool(
            name=desc["name"],
            impl=desc["impl"],
            schema=desc.get("schema"),
            description=desc.get("description", ""),
            namespace=desc.get("namespace", ""),
        )


def unregister_tool(name: str) -> bool:
    """Remove a registered tool. Returns True if it existed."""
    if name not in _plugin_tools:
        return False
    del _plugin_tools[name]
    _plugin_schemas[:] = [s for s in _plugin_schemas if s["function"]["name"] != name]
    logger.debug("Unregistered plugin tool '%s'", name)
    return True


def list_plugin_tools() -> list[str]:
    """Return the names of all registered plugin tools."""
    return list(_plugin_tools.keys())


def get_plugin_impl(name: str) -> Optional[Callable]:
    """Return the implementation function for a plugin tool, or None."""
    pt = _plugin_tools.get(name)
    return pt.impl if pt else None


def get_plugin_schemas() -> list[dict]:
    """Return all plugin tool schemas (Ollama format)."""
    return list(_plugin_schemas)


def get_all_plugin_tools() -> dict[str, PluginTool]:
    """Return a shallow copy of the plugin registry."""
    return dict(_plugin_tools)


def clear_plugin_tools() -> None:
    """Remove all registered plugin tools.  Primarily for tests."""
    _plugin_tools.clear()
    _plugin_schemas.clear()


def _is_valid_tool_schema(schema: dict) -> bool:
    """Minimal validation — just check essential shape."""
    func = schema.get("function", schema)  # accept both Ollama and bare shape
    if "name" not in func:
        return False
    params = func.get("parameters", {})
    if not isinstance(params.get("properties", {}), dict):
        return False
    return True


# ── Integration helpers for wisp/tools.py ─────────────────────────────

def has_plugin_tool(name: str) -> bool:
    """Check if a plugin tool exists.  Used by execute_tool()."""
    return name in _plugin_tools


def execute_plugin_tool(name: str, **kwargs) -> Any:
    """Execute a plugin tool by name.  Used by execute_tool() as fallback.

    Mirrors execute_tool's argument filtering: only passes kwargs the
    function actually accepts, auto-injecting workspace/file_lock/lsp_manager.
    """
    pt = _plugin_tools.get(name)
    if pt is None:
        raise RuntimeError(f"Plugin tool '{name}' not found")

    import inspect
    sig = inspect.signature(pt.impl)
    filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
    # Note: caller already passes workspace via kwargs, but we filter it
    # only if accepted by the signature.  Same for file_lock / lsp_manager.
    return pt.impl(**filtered)
