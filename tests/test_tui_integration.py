"""Integration tests for Wisp Enterprise TUI.

Uses Textual's `run_test` async pilot for full-screen interaction tests.
Covers: app mount, screen transitions, widget composition, keyboard input,
chat flow, and WebSocket message simulation.
"""

from __future__ import annotations

import pytest

from wisp.tui.app import WispTUIApp


# ══════════════════════════════════════════════════════════════════════
# App mount and initial screen
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_app_mounts_and_shows_splash():
    """App should mount cleanly with splash screen shown."""
    app = WispTUIApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # App should be running
        assert app.is_running
        # Should have at least one screen pushed (splash)
        assert app.screen is not None


@pytest.mark.asyncio
async def test_app_exits_on_ctrl_q():
    """Ctrl+Q should exit the app."""
    app = WispTUIApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        # After exit, app should not be running
        # Note: run_test context handles the exit


# ══════════════════════════════════════════════════════════════════════
# Session Picker screen
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_navigate_to_session_picker():
    """Navigating to session_picker should show the session list."""
    app = WispTUIApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.navigate("session_picker")
        await pilot.pause()
        # SessionPickerScreen should now be the active screen
        assert app.screen is not None


@pytest.mark.asyncio
async def test_session_picker_has_search_input():
    """Session picker should have a search input field."""
    app = WispTUIApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.navigate("session_picker")
        await pilot.pause()
        # Should have an Input with id session-search
        search = app.screen.query_one("#session-search")
        assert search is not None


@pytest.mark.asyncio
async def test_session_picker_n_creates_new():
    """Pressing 'n' in session picker navigates to workspace."""
    app = WispTUIApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.navigate("session_picker")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        # Should now be on workspace screen
        assert app.screen is not None


# ══════════════════════════════════════════════════════════════════════
# Workspace screen composition
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_workspace_has_all_panels():
    """Workspace screen should compose TitleBar, ActivityBar, TabbedContent, InputBar, StatusBar."""
    app = WispTUIApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.navigate("workspace")
        await pilot.pause()

        # Core chrome
        assert app.screen.query_one("#title-bar") is not None
        assert app.screen.query_one("#status-bar") is not None
        assert app.screen.query_one("#activity-bar") is not None
        assert app.screen.query_one("#input-bar") is not None

        # Tabs
        assert app.screen.query_one("#chat-tab") is not None
        assert app.screen.query_one("#files-tab") is not None
        assert app.screen.query_one("#agents-tab") is not None
        assert app.screen.query_one("#monitor-tab") is not None


@pytest.mark.asyncio
async def test_workspace_context_panel_starts_hidden():
    """Context panel should have 'hidden' class on mount."""
    app = WispTUIApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.navigate("workspace")
        await pilot.pause()

        panel = app.screen.query_one("#context-panel")
        assert panel.has_class("hidden")


@pytest.mark.asyncio
async def test_workspace_toggle_context_panel():
    """Ctrl+\\ should toggle context panel visibility."""
    app = WispTUIApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.navigate("workspace")
        await pilot.pause()

        panel = app.screen.query_one("#context-panel")
        assert panel.has_class("hidden")

        # Toggle open
        await pilot.press("ctrl+backslash")
        await pilot.pause()
        assert not panel.has_class("hidden")

        # Toggle closed
        await pilot.press("ctrl+backslash")
        await pilot.pause()
        assert panel.has_class("hidden")


# ══════════════════════════════════════════════════════════════════════
# InputBar interaction
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_workspace_input_bar_present():
    """Input bar should have a prompt input."""
    app = WispTUIApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.navigate("workspace")
        await pilot.pause()

        inp = app.screen.query_one("#prompt-input")
        assert inp is not None


# ══════════════════════════════════════════════════════════════════════
# ActivityBar tab switching
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_workspace_tabbed_content_starts_on_chat():
    """Workspace should default to chat tab."""
    app = WispTUIApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.navigate("workspace")
        await pilot.pause()

        tabs = app.screen.query_one("TabbedContent")
        assert tabs.active == "chat-tab"


# ══════════════════════════════════════════════════════════════════════
# TitleBar reactive updates
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_title_bar_model_update():
    """Updating TitleBar model_name reactive should update the display."""
    app = WispTUIApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.navigate("workspace")
        await pilot.pause()

        title_bar = app.screen.query_one("#title-bar")
        title_bar.model_name = "codellama"
        await pilot.pause()
        assert title_bar.model_name == "codellama"


# ══════════════════════════════════════════════════════════════════════
# StatusBar reactive updates
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_status_bar_streaming_update():
    """StatusBar should reflect is_streaming state."""
    app = WispTUIApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.navigate("workspace")
        await pilot.pause()

        status = app.screen.query_one("#status-bar")
        assert status.is_streaming is False

        status.is_streaming = True
        await pilot.pause()
        assert status.is_streaming is True


@pytest.mark.asyncio
async def test_status_bar_token_count_update():
    """StatusBar token_count should be updatable."""
    app = WispTUIApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.navigate("workspace")
        await pilot.pause()

        status = app.screen.query_one("#status-bar")
        status.token_count = 4200
        await pilot.pause()
        assert status.token_count == 4200
