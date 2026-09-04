"""Golden transcripts: the REPL interface design as executable spec.

Drives CLITransport._render_event through scripted event sequences and
compares the exact rendered bytes against checked-in fixtures, one per
output mode. Any visual regression — a glyph change, an indent drift, a
mode leak — fails here with a byte diff instead of slipping into the
terminal.

Regenerate deliberately after an intentional design change:

    UPDATE_GOLDENS=1 python -m pytest tests/test_golden_transcripts.py
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch as mpatch

import pytest

from wisp.transport.cli import CLITransport
from wisp.transport.progress import ProgressTracker

GOLDEN_DIR = Path(__file__).parent / "goldens" / "transcripts"


# ── Deterministic transport ──────────────────────────────────────────


class _Buffer(io.StringIO):
    def isatty(self):
        return True


def _make_transport() -> CLITransport:
    """A bare CLITransport wired for deterministic rendering.

    Spinner is None so tool events render their result line directly —
    animation frames are timing-dependent and would poison goldens.
    """
    t = CLITransport.__new__(CLITransport)
    t.config = None
    t._stdout = None
    t._spinner = None
    t._progress = ProgressTracker()
    t._thinking_buffer = []
    t._content_buffer = []
    t._in_thinking = False
    t._in_content = False
    t.show_tool_output = True
    t._turn_number = 1
    t._last_block_was_tool = False
    t._phase = "understand"
    return t


def _reset_style_ansi_cache() -> None:
    """Rich memoizes ``Style._ansi`` on first render (style.py: _make_ansi_codes).

    Any Style instance rendered under one palette (e.g. the ambient terminal's
    256/truecolor codes) freezes those SGR bytes; a later golden comparison on
    a different Console color-system then drifts even though the text/glyphs
    are identical. Sweep every live Style instance so rendering re-derives
    codes under the pinned standard palette.
    """
    import gc

    from rich.style import Style

    for obj in gc.get_objects():
        if type(obj) is Style and obj._ansi is not None:
            obj._ansi = None


def _render_sequence(events: list[dict], mode: Any) -> str:
    from wisp.terminal_width import set_output_mode

    out = _Buffer()
    t = _make_transport()
    set_output_mode(mode)
    _reset_style_ansi_cache()
    # Pin the ANSI palette: Rich sniffs COLORTERM/TERM at Console creation,
    # so a truecolor terminal would re-encode every SGR sequence and poison
    # the byte comparison. 'standard' (8-color) is the canonical golden
    # palette — layout + glyphs are the contract, not the host terminal.
    env_keys = ("COLORTERM", "FORCE_COLOR", "NO_COLOR")
    saved_env = {k: os.environ.pop(k, None) for k in env_keys}
    saved_term = os.environ.get("TERM")
    os.environ["TERM"] = "xterm"
    try:
        with mpatch("wisp.transport.cli._term_width", lambda: 60):
            for ev in events:
                t._render_event(out, ev)
            t._flush_thinking(out)
            t._flush_content(out)
    finally:
        from wisp.terminal_width import OutputMode

        set_output_mode(OutputMode.UNICODE)
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v
        if saved_term is None:
            os.environ.pop("TERM", None)
        else:
            os.environ["TERM"] = saved_term
    # Strip volatile spinner-frame remnants if any slipped in; the
    # contract under test is layout + glyphs, not frame counts.
    return out.getvalue()


# ── Scripted scenarios (design §2 turn anatomy) ──────────────────────


def _scenario_full_turn() -> list[dict]:
    return [
        {"type": "thinking", "text": "The user wants caching strategies."},
        {"type": "content", "text": "Here's what I found about caching."},
        {"type": "phase_change", "phase": "execute"},
    ]


def _scenario_tools_and_diff() -> list[dict]:
    return [
        {"type": "tool_call", "name": "edit_file",
         "args": {"path": "app.py"}},
        {"type": "tool_result", "name": "edit_file", "success": True,
         "duration_ms": 12.0,
         "result": {"status": "ok", "data": "Edited app.py",
                    "metadata": {"diff":
                        "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n-old\n+new",
                        "path": "app.py"}}},
        {"type": "system", "message": "Applied 1 edit.",
         "level": "info"},
    ]


def _scenario_delegation() -> list[dict]:
    # Shape mirrors orchestrator_event_to_agent_event: kind/role/detail
    # inside data, exactly what render_subagent_status consumes.
    return [
        {"type": "subagent", "kind": "task_started", "role": "researcher",
         "detail": "Research caching strategies"},
        {"type": "subagent", "kind": "task_completed", "role": "researcher",
         "detail": "47.8s · 2 files"},
    ]


def _scenario_failure() -> list[dict]:
    return [
        {"type": "warning", "message": "Provider stalled; retrying once."},
        {"type": "error", "message": "Provider returned no usable response after a retry."},
    ]


SCENARIOS = {
    "full_turn": _scenario_full_turn,
    "tools_diff": _scenario_tools_and_diff,
    "delegation": _scenario_delegation,
    "failure": _scenario_failure,
}

MODES = ["unicode", "ascii", "accessible", "minimal"]


# ── The gate ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_golden_transcript(scenario: str, mode: str) -> None:
    from wisp.terminal_width import OutputMode

    rendered = _render_sequence(SCENARIOS[scenario](), OutputMode(mode))
    golden = GOLDEN_DIR / f"{scenario}.{mode}.txt"
    if os.environ.get("UPDATE_GOLDENS") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(rendered)
        pytest.skip(f"regenerated {golden.name}")
    assert golden.exists(), (
        f"missing golden {golden.name}; run UPDATE_GOLDENS=1 to seed"
    )
    expected = golden.read_text()
    assert rendered == expected, (
        f"{golden.name} drifted:\n--- golden\n{expected}\n--- rendered\n{rendered}"
    )
