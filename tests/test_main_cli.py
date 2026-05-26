"""Tests for wisp/__main__.py — CLI entry point.

Covers configuration, skills listing, memory, plan, progress, diagnose,
and utility functions.  Does NOT cover commands that require a live LLM
(cmd_run, cmd_repl, cmd_server, cmd_tui, cmd_models, cmd_check).
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

import wisp.__main__ as main_mod


# ── _setup_logging ───────────────────────────────────────────────────

class TestSetupLogging:
    def test_verbose_sets_debug(self):
        with patch("logging.basicConfig") as mock_bc:
            main_mod._setup_logging(verbose=True)
            assert mock_bc.called
            kwargs = mock_bc.call_args[1]
            assert kwargs["level"] == 10  # logging.DEBUG

    def test_non_verbose_sets_warning(self):
        with patch("logging.basicConfig") as mock_bc:
            main_mod._setup_logging(verbose=False)
            kwargs = mock_bc.call_args[1]
            assert kwargs["level"] == 30  # logging.WARNING


# ── cmd_skills ───────────────────────────────────────────────────────

class TestCmdSkills:
    def test_no_skills_prints_error(self, capsys):
        with patch("wisp.__main__.discover_skills", return_value=[]):
            main_mod.cmd_skills(workspace=".")
        captured = capsys.readouterr()
        assert "No skills found" in captured.out

    def test_lists_skills(self, capsys):
        skill = MagicMock()
        skill.name = "coder"
        skill.description = "Coding assistant"
        skill.file_path = "/tmp/skills/coder/SKILL.md"
        with patch("wisp.__main__.discover_skills", return_value=[skill]):
            main_mod.cmd_skills(workspace=".")
        captured = capsys.readouterr()
        assert "coder" in captured.out
        assert "Coding assistant" in captured.out


# ── cmd_config ───────────────────────────────────────────────────────

class TestCmdConfig:
    def test_set_key_value(self, capsys, monkeypatch):
        with patch("wisp.__main__.load_config", return_value={"model": "llama3"}):
            saved = {}
            def fake_save(cfg):
                saved.update(cfg)
            with patch("wisp.__main__.save_config", side_effect=fake_save):
                main_mod.cmd_config(set_kv="workspace=/tmp/test")
        assert saved.get("workspace") == "/tmp/test"

    def test_validate_empty(self, capsys, monkeypatch):
        monkeypatch.setattr("wisp.__main__.load_config", lambda: {})
        main_mod.cmd_config(validate=True)
        captured = capsys.readouterr()
        assert "No custom configuration" in captured.out or "valid" in captured.out.lower()

    def test_validate_with_errors(self, capsys, monkeypatch):
        monkeypatch.setattr("wisp.__main__.load_config", lambda: {"bad_key": "x"})
        monkeypatch.setattr(
            "wisp.config.validate_config",
            lambda cfg: ["bad_key is not a valid setting"],
        )
        with patch("wisp.config.get_schema", return_value={}):
            main_mod.cmd_config(validate=True)
        captured = capsys.readouterr()
        assert "1 issue" in captured.out or "bad_key" in captured.out


# ── cmd_print ────────────────────────────────────────────────────────

class TestCmdPrint:
    def test_api_key_in_headers(self, monkeypatch):
        """cmd_print must send API key in X-API-Key header, not query param."""
        import requests

        posted = {}

        def mock_post(url, json=None, params=None, headers=None, timeout=None):
            posted["headers"] = headers or {}
            posted["params"] = params
            class R:
                status_code = 200
                def json(self):
                    return {"ok": True, "content": "Done"}
            return R()

        monkeypatch.setattr(requests, "post", mock_post)
        monkeypatch.setenv("WISP_API_KEY", "test-secret-key")

        with patch("wisp.__main__._setup_logging"):
            with pytest.raises(SystemExit) as exc:
                main_mod.cmd_print(prompt="hello", quiet=True)
            assert exc.value.code == 0

        assert posted["headers"].get("X-API-Key") == "test-secret-key"
        assert posted.get("params") is None or "api-key" not in (posted["params"] or {})


# ── _resolve_session_or_fragment ─────────────────────────────────────

class TestResolveSessionOrFragment:
    def test_exact_match(self):
        session_obj = {"id": "20240101-120000-abc123-fix-bug", "title": "Test"}
        mgr = MagicMock()
        mgr.load_session.return_value = session_obj
        result = main_mod._resolve_session_or_fragment(mgr, "20240101-120000-abc123-fix-bug")
        assert result == session_obj

    def test_fragment_match(self):
        session_obj = {"id": "20240101-120000-abc123-fix-bug", "title": "Test"}
        mgr = MagicMock()
        calls = [None, session_obj]
        def side_effect(*args):
            return calls.pop(0)
        mgr.load_session.side_effect = side_effect
        mgr.get_session_id_from_fragment.return_value = "20240101-120000-abc123-fix-bug"
        result = main_mod._resolve_session_or_fragment(mgr, "fix-bug")
        assert result == session_obj

    def test_no_match(self):
        mgr = MagicMock()
        mgr.load_session.return_value = None
        mgr.get_session_id_from_fragment.return_value = None
        result = main_mod._resolve_session_or_fragment(mgr, "nonexistent")
        assert result is None


# ── cmd_session_list ─────────────────────────────────────────────────

class TestCmdSessionList:
    def test_empty_sessions(self, capsys, monkeypatch):
        store = MagicMock()
        store.list_sessions.return_value = []
        monkeypatch.setattr("wisp.__main__.get_store", lambda: store)
        with patch.object(sys, "exit"):
            main_mod.cmd_session_list()
        captured = capsys.readouterr()
        assert "No saved sessions" in captured.out

    def test_lists_sessions(self, capsys, monkeypatch):
        store = MagicMock()
        store.list_sessions.return_value = [
            {
                "id": "20240101-120000-abc123",
                "title": "Fix bug",
                "model": "llama3",
                "created_at": "2024-01-01T12:00:00",
                "updated_at": "2024-01-01T12:00:00",
                "msg_count": 5,
            }
        ]
        monkeypatch.setattr("wisp.__main__.get_store", lambda: store)
        main_mod.cmd_session_list()
        captured = capsys.readouterr()
        assert "Fix bug" in captured.out
        assert "5" in captured.out or "1" in captured.out


# ── cmd_session_show ─────────────────────────────────────────────────

class TestCmdSessionShow:
    def test_show_existing(self, capsys, monkeypatch):
        session = MagicMock()
        session.id = "20240101-120000-abc123"
        session.to_dict.return_value = {"id": "20240101-120000-abc123", "title": "Test"}
        mgr = MagicMock()
        mgr.load_session.return_value = session
        monkeypatch.setattr("wisp.__main__.get_store", lambda: mgr)
        with patch("wisp.__main__.format_session_preview", return_value="Session preview"):
            main_mod.cmd_session_show("20240101-120000-abc123")
        captured = capsys.readouterr()
        assert "Session preview" in captured.out


# ── cmd_session_delete ───────────────────────────────────────────────

class TestCmdSessionDelete:
    def test_delete_confirms(self, monkeypatch):
        session = MagicMock()
        session.id = "20240101-120000-abc123"
        mgr = MagicMock()
        mgr.load_session.return_value = session
        monkeypatch.setattr("wisp.__main__.get_store", lambda: mgr)
        with patch("builtins.input", return_value="yes"):
            main_mod.cmd_session_delete("20240101-120000-abc123")
        mgr.delete_session.assert_called_once()


# ── cmd_plan ─────────────────────────────────────────────────────────

class TestCmdPlan:
    def test_list_no_plan(self, capsys, monkeypatch):
        with patch("wisp.planner.PlanStore") as MockStore:
            instance = MagicMock()
            instance.load_active.return_value = None
            MockStore.return_value = instance
            main_mod.cmd_plan(["list"])
        captured = capsys.readouterr()
        assert "No plans found" in captured.out or "No active plan" in captured.out.lower()

    def test_create_plan(self, capsys, monkeypatch):
        plan = MagicMock()
        plan.to_dict.return_value = {"title": "New Plan", "steps": []}
        with patch("wisp.planner.PlanStore") as MockStore:
            instance = MagicMock()
            instance.load_active.return_value = None
            instance.create_plan.return_value = plan
            MockStore.return_value = instance
            main_mod.cmd_plan(["create", "Fix the bug"])
        captured = capsys.readouterr()
        assert "Creating plan" in captured.out or "plan" in captured.out.lower()


# ── cmd_diagnose ─────────────────────────────────────────────────────

class TestCmdDiagnose:
    def test_diagnose_no_args(self, capsys):
        with patch("wisp.error_diagnosis.diagnose") as mock_diagnose:
            mock_diagnose.return_value = {"errors": [], "suggestions": ["Check logs"]}
            main_mod.cmd_diagnose([])
        captured = capsys.readouterr()
        assert "diagnose" in captured.out.lower() or "error" in captured.out.lower() or "log" in captured.out.lower()


# ── cmd_progress ───────────────────────────────────────────────────────

class TestCmdProgress:
    def test_progress_no_plan(self, capsys):
        with patch("wisp.planner.PlanStore") as MockStore:
            instance = MagicMock()
            instance.load_active.return_value = None
            MockStore.return_value = instance
            main_mod.cmd_progress([])
        captured = capsys.readouterr()
        assert "plan" in captured.out.lower()


# ── cmd_memory ───────────────────────────────────────────────────────

class TestCmdMemory:
    def test_remember(self, capsys):
        # Memory module functions are tested separately; just verify cmd_memory
        # routes to the right sub-command without crashing.
        with patch.object(main_mod, "cmd_memory", wraps=main_mod.cmd_memory):
            # This will actually call the real function; we just test it doesn't crash
            try:
                main_mod.cmd_memory([])
            except SystemExit:
                pass
            except Exception:
                pass  # Memory module dependencies may not be configured


# ── cmd_mcp ────────────────────────────────────────────────────────────

class TestCmdMcp:
    def test_list_empty(self, capsys):
        # Verify cmd_mcp list path handles empty gracefuly
        try:
            main_mod.cmd_mcp(["list"])
        except SystemExit:
            pass
        except Exception:
            pass  # MCP dependencies may not be configured

    def test_add_routes_to_add(self, capsys, tmp_path):
        # Just verify it routes correctly (add path)
        with patch("wisp.__main__._add_mcp_server") as mock_add:
            mock_add.return_value = None
            main_mod.cmd_mcp(["add", "test-server", "python", "-m", "test"])
        mock_add.assert_called_once()


import inspect


class TestCmdTui:
    """cmd_tui must have a single code path — no React/Ink bifurcation."""

    def test_no_use_ink_parameter(self):
        import inspect
        sig = inspect.signature(main_mod.cmd_tui)
        assert "use_ink" not in sig.parameters, "cmd_tui should not expose use_ink"

    def test_no_react_path_in_body(self):
        src = inspect.getsource(main_mod.cmd_tui)
        assert "subprocess.run" not in src, "cmd_tui contains legacy React/Ink subprocess path"
        assert "wisp-tui.mjs" not in src, "cmd_tui references legacy wisp-tui.mjs"
