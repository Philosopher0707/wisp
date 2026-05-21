"""Backward-compatibility shim — re-exports from new CLI transport.

The new CLI transport is wisp.transport.cli_v2.CLITransport (implements Transport ABC).
This module preserves imports for code still using the old driver-style CLITransport.
"""

from __future__ import annotations

import signal
from typing import Optional

from wisp.transport.cli_v2 import CLITransport as _CLITransport
from wisp.transport.cli_v2 import (
    _is_interactive,
    _input_line,
    _args_preview,
)
from wisp.transport.renderer import (
    render_content_block,
    render_thinking_block,
    render_tool_call,
    wrap_text as _wrap_text,
    _box,
    _rule,
)
from wisp.colors import dim, error
from wisp.core.events import AgentEvent, EventType

__all__ = [
    "CLITransport",
    "_is_interactive",
    "_input_line",
    "_args_preview",
    "_handle_sigint",
    "_render_event",
]

# Global registry for signal-handler backward compatibility
_transport_instances: list = []
_old_sigint_handler = None


class CLITransport(_CLITransport):
    """Backward-compatible CLITransport that registers in _transport_instances."""

    def __init__(self, runtime):
        super().__init__(runtime)
        self._interrupted = False
        _transport_instances.append(self)


def _handle_sigint(signum, frame):
    """Mark interruption on all live transport instances AND their cores."""
    for inst in _transport_instances:
        inst._interrupted = True
        if hasattr(inst, "runtime") and hasattr(inst.runtime, "_interrupted"):
            inst.runtime._interrupted = True
        # Also check old 'core' attribute
        if hasattr(inst, "core") and hasattr(inst.core, "_interrupted"):
            inst.core._interrupted = True
    print(error("\n\n⏹  Interrupted. Finishing current step... (Ctrl+C again to force quit)"))
    signal.signal(signal.SIGINT, signal.default_int_handler)


def _install_signal_handler():
    """Register interrupt handler and reset interrupt state on all instances."""
    global _old_sigint_handler
    for inst in _transport_instances:
        inst._interrupted = False
    _old_sigint_handler = signal.signal(signal.SIGINT, _handle_sigint)


def _restore_signal_handler():
    """Restore the previous SIGINT handler."""
    global _old_sigint_handler
    if _old_sigint_handler is not None:
        signal.signal(signal.SIGINT, _old_sigint_handler)
        _old_sigint_handler = None


def _render_event(
    event: AgentEvent,
    show_thinking: bool = False,
    show_tool_output: bool = True,
    box_mode: bool = True,
) -> Optional[str]:
    """Render an AgentEvent to a terminal string. Returns None for silent events."""
    etype = event.type

    if etype == EventType.CONTENT:
        raw = event.text
        if not box_mode:
            return raw
        return render_content_block(raw, box_mode=True, width=80)

    if etype == EventType.THINKING:
        text = event.text
        if not show_thinking:
            line_count = text.count("\n") + 1
            return dim(f"  🧠 Thinking... ({line_count} lines — /thinking to expand)")
        return render_thinking_block(text, box_mode=True, width=80)

    if etype == EventType.TOOL_CALL:
        name = event.data.get("name", "")
        args = event.data.get("arguments", {})
        return render_tool_call(name, args, box_mode=True)

    if etype == EventType.TOOL_RESULT:
        name = event.data.get("name", "")
        result = event.data.get("result", "")
        return f"  ✅ {name}: {result}"[:200]

    if etype == EventType.ERROR:
        return f"  ❌ Error: {event.text}"

    if etype == EventType.SYSTEM:
        return f"  ℹ {event.data.get('message', '')}"

    if etype == EventType.DONE:
        return None

    return None
