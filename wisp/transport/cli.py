"""Backward-compatibility shim — lazily re-exports from _legacy_cli.py.

The new CLI transport is wisp.transport.cli_v2.CLITransport (implements Transport ABC).
This module preserves imports for code still using the old driver-style CLITransport.
"""

from __future__ import annotations

import importlib

# Module-level state forwarded from legacy implementation
_transport_instances = None


def __getattr__(name: str):
    """Lazy import from _legacy_cli — only loads when accessed."""
    global _transport_instances
    mod = importlib.import_module("wisp.transport._legacy_cli")
    if name == "_transport_instances":
        if _transport_instances is None:
            _transport_instances = mod._transport_instances
        return _transport_instances
    return getattr(mod, name)


__all__ = [
    "CLITransport",
    "_is_interactive",
    "_input_line",
    "_args_preview",
    "_handle_sigint",
    "_render_event",
]
