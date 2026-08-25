"""TDD tests for renderer additions — phase_bar, turn_stats, file_ticker."""

import pytest
from wisp.colors import strip_ansi
from wisp.transport.renderer import (
    _box,
    render_phase_bar,
    render_turn_stats,
    render_file_ticker,
    render_provider_status,
)
from wisp.terminal_width import OutputMode, set_output_mode, get_output_mode, display_width


@pytest.fixture(autouse=True)
def _reset_mode():
    """Ensure tests run in unicode mode by default."""
    old = get_output_mode()
    set_output_mode(OutputMode.UNICODE)
    yield
    set_output_mode(old)


class TestPhaseBar:
    """render_phase_bar produces a phase indicator line."""

    def test_all_phases_present(self):
        bar = render_phase_bar("understand", {"tools_run": 0}, 80)
        assert "understand" in bar.lower()

    def test_current_phase_highlighted(self):
        bar = render_phase_bar("execute", {"tools_run": 3}, 80)
        assert "execute" in bar.lower()

    def test_shows_four_phases(self):
        bar = render_phase_bar("execute", {"tools_run": 5}, 80)
        assert "understand" in bar.lower()
        assert "plan" in bar.lower()
        assert "execute" in bar.lower()
        assert "verify" in bar.lower()

    def test_ascii_mode(self):
        set_output_mode(OutputMode.ASCII)
        bar = render_phase_bar("plan", {"tools_run": 1}, 80)
        assert "plan" in bar.lower()

    def test_accessible_mode(self):
        set_output_mode(OutputMode.ACCESSIBLE)
        bar = render_phase_bar("verify", {"tools_run": 2}, 80)
        assert "verify" in bar.lower()

    def test_minimal_mode_returns_empty(self):
        set_output_mode(OutputMode.MINIMAL)
        bar = render_phase_bar("understand", {"tools_run": 0}, 80)
        assert bar == ""


class TestTurnStats:
    """render_turn_stats produces a one-line turn summary."""

    def test_includes_turn_number(self):
        stats = {"turn_number": 3, "tools_run": 2, "tools_succeeded": 2,
                 "tools_failed": 0, "files_changed": [], "elapsed": 1.5,
                 "phase": "execute"}
        line = render_turn_stats(stats, 80)
        assert "3" in line

    def test_includes_tool_counts(self):
        stats = {"turn_number": 1, "tools_run": 4, "tools_succeeded": 3,
                 "tools_failed": 1, "files_changed": [], "elapsed": 2.0,
                 "phase": "execute"}
        line = render_turn_stats(stats, 80)
        assert "4" in line  # tools_run

    def test_includes_files(self):
        stats = {"turn_number": 1, "tools_run": 2, "tools_succeeded": 2,
                 "tools_failed": 0, "files_changed": ["a.py", "b.py"],
                 "elapsed": 1.0, "phase": "execute"}
        line = render_turn_stats(stats, 80)
        assert "2 files" in line

    def test_no_files_when_empty(self):
        stats = {"turn_number": 1, "tools_run": 1, "tools_succeeded": 1,
                 "tools_failed": 0, "files_changed": [], "elapsed": 0.5,
                 "phase": "understand"}
        line = render_turn_stats(stats, 80)
        assert "files" in line
        assert "0 files" in line

    def test_minimal_mode(self):
        set_output_mode(OutputMode.MINIMAL)
        stats = {"turn_number": 1, "tools_run": 2, "tools_succeeded": 2,
                 "tools_failed": 0, "files_changed": ["x.py"],
                 "elapsed": 1.0, "phase": "execute"}
        line = render_turn_stats(stats, 80)
        # Should still produce something useful even in minimal mode
        assert len(line) > 0


class TestFileTicker:
    """render_file_ticker shows changed files."""

    def test_empty_returns_empty(self):
        assert render_file_ticker([], 80) == ""

    def test_single_file(self):
        line = render_file_ticker(["src/auth.py"], 80)
        assert "src/auth.py" in line

    def test_multiple_files(self):
        line = render_file_ticker(["a.py", "b.py", "c.py"], 80)
        assert "a.py" in line
        assert "b.py" in line
        assert "c.py" in line

    def test_shows_file_list(self):
        line = render_file_ticker(["a.py", "b.py"], 80)
        assert "a.py" in line
        assert "b.py" in line

    def test_accessible_mode(self):
        set_output_mode(OutputMode.ACCESSIBLE)
        line = render_file_ticker(["src/app.py"], 80)
        assert "src/app.py" in line

    def test_minimal_mode(self):
        set_output_mode(OutputMode.MINIMAL)
        line = render_file_ticker(["x.py"], 80)
        assert "x.py" in line


class TestProviderStatus:
    """render_provider_status surfaces circuit breaker lifecycle honestly."""

    def _event(self, status, retry_after=None):
        from wisp.core.events import provider_status

        return provider_status(status, detail="Provider failing repeatedly.", retry_after=retry_after)

    def test_open_shows_retry_horizon(self):
        out = render_provider_status(self._event("circuit_open", retry_after=12.3), 80)
        assert "Provider paused" in out
        assert "12s" in out

    def test_closed_confirms_recovery(self):
        out = render_provider_status(self._event("circuit_closed"), 80)
        assert "recovered" in out.lower()

    def test_ascii_mode_uses_ascii_marker(self):
        set_output_mode(OutputMode.ASCII)
        out = render_provider_status(self._event("circuit_open", retry_after=5), 80)
        assert "-" in out and "Provider paused" in out
        assert "◌" not in out

    def test_accessible_mode_labels_change(self):
        set_output_mode(OutputMode.ACCESSIBLE)
        out = render_provider_status(self._event("circuit_open", retry_after=7), 80)
        assert "[PROVIDER]" in out and "Circuit open" in out

    def test_minimal_mode_silent(self):
        set_output_mode(OutputMode.MINIMAL)
        assert render_provider_status(self._event("circuit_open", retry_after=3), 80) == ""

    def test_unknown_status_renders_nothing(self):
        assert render_provider_status(self._event("warp_field_fluctuation"), 80) is None

    def test_open_without_retry_omits_horizon(self):
        out = render_provider_status(self._event("circuit_open"), 80)
        assert "retry" not in out


class TestBoxPadding:
    """_box pads by display width so right borders align for wide chars."""

    def test_wide_chars_align_right_border(self):
        plain = _box("hello world", width=24)
        wide = _box("こんにちは世界", width=24)
        for rendered in (plain, wide):
            body = strip_ansi(rendered.splitlines()[1])
            assert body.endswith("│")
            assert display_width(body) == 24

    def test_ascii_content_unchanged_by_display_width_padding(self):
        body = strip_ansi(_box("hello world", width=24).splitlines()[1])
        assert body == "│ hello world" + " " * 9 + " │"


# ═══════════════════════════════════════════════════════════════════
# Context meter in turn stats (estimate, clearly bounded)
# ═══════════════════════════════════════════════════════════════════


class TestContextMeter:
    def test_stats_include_context_when_provided(self):
        from wisp.transport.renderer import render_turn_stats

        stats = {
            "turn_number": 1, "tools_run": 2,
            "files_changed": [], "elapsed": 3.0,
            "ctx_tokens": 18432, "ctx_limit": 131072,
        }
        line = render_turn_stats(stats)
        assert "ctx 18k (14%)" in line, line

    def test_context_hidden_in_minimal_mode(self):
        import wisp.terminal_width as TW
        from wisp.transport.renderer import render_turn_stats

        TW.set_output_mode(TW.OutputMode.MINIMAL)
        try:
            stats = {
                "turn_number": 1, "tools_run": 0,
                "files_changed": [], "elapsed": 0.0,
                "ctx_tokens": 5000, "ctx_limit": 100000,
            }
            assert "ctx" not in render_turn_stats(stats)
        finally:
            TW.set_output_mode(TW.OutputMode.UNICODE)

    def test_no_limit_no_meter(self):
        from wisp.transport.renderer import render_turn_stats

        stats = {"turn_number": 1, "tools_run": 0, "files_changed": [],
                 "elapsed": 0.0, "ctx_tokens": 900}
        assert "ctx" not in render_turn_stats(stats)

    def test_over_limit_shows_warning_pct(self):
        from wisp.transport.renderer import render_turn_stats

        stats = {"turn_number": 9, "tools_run": 0, "files_changed": [],
                 "elapsed": 1.0, "ctx_tokens": 140000, "ctx_limit": 131072}
        line = render_turn_stats(stats)
        assert "(107%)" in line


# ── Background agents ─────────────────────────────────────────────────

from wisp.transport.renderer import render_agent_detail, render_background_agents


def _agent_entry(**overrides):
    entry = {
        "agent_id": "bg-abc123",
        "label": "researcher-1",
        "role": "researcher",
        "task": "Survey the auth module and report findings",
        "status": "running",
        "turns": 1,
        "elapsed_seconds": 12.4,
    }
    entry.update(overrides)
    return entry


class TestRenderBackgroundAgents:
    def test_empty_registry_unicode(self):
        out = render_background_agents([])
        assert "No background agents" in out

    def test_empty_registry_minimal(self):
        set_output_mode(OutputMode.MINIMAL)
        out = render_background_agents([])
        assert "spawn_background" in out

    def test_lists_agent_with_fields(self):
        out = strip_ansi(render_background_agents([_agent_entry()]))
        assert "bg-abc123" in out
        assert "researcher" in out
        assert "12s" in out
        assert "auth module" in out

    def test_running_sorts_first(self):
        entries = [
            _agent_entry(agent_id="bg-done", status="completed", elapsed_seconds=50.0),
            _agent_entry(agent_id="bg-run"),
        ]
        out = strip_ansi(render_background_agents(entries))
        assert out.index("bg-run") < out.index("bg-done")

    def test_status_marks_unicode(self):
        out = strip_ansi(render_background_agents([
            _agent_entry(status="completed"),
            _agent_entry(agent_id="bg-f", status="failed"),
            _agent_entry(agent_id="bg-c", status="cancelled"),
            _agent_entry(agent_id="bg-r"),
        ]))
        assert "✓" in out and "✗" in out and "⏹" in out and "●" in out

    def test_ascii_mode_marks(self):
        set_output_mode(OutputMode.ASCII)
        out = strip_ansi(render_background_agents([
            _agent_entry(status="completed"),
            _agent_entry(agent_id="bg-r"),
        ]))
        assert "+" in out and "o" in out

    def test_accessible_mode_spells_words(self):
        set_output_mode(OutputMode.ACCESSIBLE)
        out = strip_ansi(render_background_agents([_agent_entry()]))
        assert "[RUNNING]" in out
        assert "task:" in out

    def test_accessible_mode_shows_result_summary(self):
        set_output_mode(OutputMode.ACCESSIBLE)
        entry = _agent_entry(status="completed", result={
            "ok": True, "summary": "Found 3 issues", "files": [], "error": None,
        })
        out = strip_ansi(render_background_agents([entry]))
        assert "summary: Found 3 issues" in out

    def test_minimal_mode_is_terse_lines(self):
        set_output_mode(OutputMode.MINIMAL)
        out = strip_ansi(render_background_agents([_agent_entry(), _agent_entry(status="completed")]))
        assert "running bg-abc123 12s t1" in out
        assert "completed bg-abc123 12s t1" in out

    def test_long_task_truncated(self):
        entry = _agent_entry(task="x" * 500)
        out = strip_ansi(render_background_agents([entry], width=80))
        assert "..." in out
        for line in out.splitlines():
            assert display_width(line) <= 120

    def test_turn_count_shown_when_continued(self):
        out = strip_ansi(render_background_agents([_agent_entry(turns=2)]))
        assert "turn 2" in out

    def test_failed_line_present_for_error_result(self):
        set_output_mode(OutputMode.ACCESSIBLE)
        entry = _agent_entry(status="failed", result={"ok": False, "error": "boom"})
        out = strip_ansi(render_background_agents([entry]))
        assert "error: boom" in out


class TestRenderAgentDetail:
    def test_detail_box_contains_fields(self):
        snap = _agent_entry()
        out = strip_ansi(render_agent_detail(snap))
        assert "bg-abc123" in out and "researcher-1" in out and "running" in out

    def test_detail_shows_files_and_summary(self):
        snap = _agent_entry(status="completed", result={
            "ok": True, "summary": "All good", "files": ["a.py"], "error": None,
            "session_id": "sess-1",
        })
        out = strip_ansi(render_agent_detail(snap))
        assert "a.py" in out and "All good" in out

    def test_minimal_detail_no_box(self):
        set_output_mode(OutputMode.MINIMAL)
        out = strip_ansi(render_agent_detail(_agent_entry()))
        assert "╔" not in out and "id:" in out

    def test_empty_snapshot_message(self):
        out = strip_ansi(render_agent_detail({}))
        assert "No such agent" in out
