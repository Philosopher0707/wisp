"""Backward-compatibility shim — re-exports from _legacy_cli.py.

The new CLI transport is wisp.transport.cli_v2.CLITransport (implements Transport ABC).
This module preserves imports for code still using the old driver-style CLITransport.
"""

from wisp.transport._legacy_cli import (
    CLITransport,
    _is_interactive,
    _input_line,
    _args_preview,
    _handle_sigint,
    _render_event,
    _get_git_branch,
    _shorten_path,
    _get_context_info,
    _render_status_bar,
    _input_box_top,
    _input_box_bottom,
    _paste_indicator,
    _term_width,
    _use_box_mode,
    _spinner_gen,
    _setup_readline_history,
    _detect_language,
    _has_unclosed_brackets,
    _continuation_prompt,
    _prompt_approve,
    _is_benign_bash_command,
    _prompt_with_session_options,
    _prompt_dangerous,
    _prompt_edit_approval,
    _render_tool_result,
    _print_separator,
)

# Module-level state forwarded from legacy implementation
import wisp.transport._legacy_cli as _legacy
_transport_instances = _legacy._transport_instances

__all__ = [
    "CLITransport",
    "_is_interactive",
    "_input_line",
    "_args_preview",
    "_handle_sigint",
    "_render_event",
]
