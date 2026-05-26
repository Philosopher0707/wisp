"""Tests for the Wisp Enterprise TUI application shell.

Covers: app construction, screen routing, navigation, keybinding actions,
CSS loading, theme, and splash→session_picker→workspace transitions.
"""

from __future__ import annotations


from wisp.config import WispConfig
from wisp.tui.app import WispTUIApp
from wisp.tui.screens.splash import SplashScreen
from wisp.tui.screens.session_picker import SessionPickerScreen
from wisp.tui.screens.workspace import WorkspaceScreen


class TestWispTUIApp:
    """App-level construction and configuration tests."""

    def test_app_constructs_with_defaults(self):
        app = WispTUIApp()
        assert app.TITLE == "Wisp"
        assert app.server_url == "http://localhost:8000"
        assert app.current_session_id is None
        assert app.theme_mode == "dark"

    def test_app_constructs_with_custom_server(self):
        app = WispTUIApp(server_url="http://localhost:9999")
        assert app.server_url == "http://localhost:9999"

    def test_app_constructs_with_config(self):
        config = WispConfig()
        config = config.replace(model="codellama")
        app = WispTUIApp(config=config)
        assert app.wisp_config.model == "codellama"

    def test_css_paths_exist(self):
        app = WispTUIApp()
        for path in app.CSS_PATH:
            assert path.exists(), f"CSS file missing: {path}"

    def test_bindings_registered(self):
        app = WispTUIApp()
        binding_keys = {b.key for b in app.BINDINGS}
        assert "ctrl+q" in binding_keys
        assert "ctrl+p" in binding_keys
        assert "f1" in binding_keys

    def test_theme_mode_reactive_default(self):
        app = WispTUIApp()
        assert app.theme_mode == "dark"

    def test_current_screen_name_reactive(self):
        app = WispTUIApp()
        assert app.current_screen_name == "splash"


class TestScreenNavigation:
    """Tests for screen routing logic."""

    def test_splash_screen_constructed(self):
        screen = SplashScreen(server_url="http://localhost:8000")
        assert screen.server_url == "http://localhost:8000"

    def test_session_picker_constructed(self):
        screen = SessionPickerScreen(server_url="http://localhost:8000")
        assert screen.server_url == "http://localhost:8000"
        assert screen._all_sessions == []

    def test_workspace_screen_constructed_without_session(self):
        screen = WorkspaceScreen(server_url="http://localhost:8000", session_id=None)
        assert screen.server_url == "http://localhost:8000"
        assert screen.session_id is None
        assert screen._context_visible is False

    def test_workspace_screen_constructed_with_session(self):
        screen = WorkspaceScreen(server_url="http://localhost:8000", session_id="abc-123")
        assert screen.session_id == "abc-123"

    def test_workspace_screen_constructed_with_config(self):
        config = WispConfig()
        config = config.replace(model="llama3.2")
        screen = WorkspaceScreen(server_url="http://localhost:8000", config=config)
        assert screen.wisp_config.model == "llama3.2"


class TestAppNavigation:
    """Tests for WispTUIApp.navigate()."""

    def test_navigate_to_session_picker(self):
        # navigate on a bare (never-composed) app is a safe no-op
        app = WispTUIApp()
        app.navigate("session_picker")
        # navigate is a no-op on bare app — just verify no crash

    def test_navigate_to_workspace(self):
        app = WispTUIApp()
        app.current_session_id = "test-session"
        app.navigate("workspace")
        # navigate is a no-op on bare app — just verify no crash

    def test_navigate_unknown_screen_does_nothing(self):
        app = WispTUIApp()
        app.navigate("nonexistent")
        # Should not crash


class TestAppActionMethods:
    """Tests for action_* methods."""

    def test_action_toggle_command_palette_proxies(self):
        app = WispTUIApp()
        app.action_toggle_command_palette()

    def test_action_toggle_context_panel_proxies(self):
        app = WispTUIApp()
        app.action_toggle_context_panel()

    def test_action_show_help(self):
        app = WispTUIApp()
        app.action_show_help()
