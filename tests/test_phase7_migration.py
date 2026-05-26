"""Phase 7.1 migration guard tests — verify new module locations work BEFORE deleting adapters.py.

Run these FIRST. Then after Phase 7.1 completes, these same tests confirm
consumers pointing at new locations still work.
"""



class TestGetStoreMigration:
    """get_store() moves from wisp.adapters → wisp.infra.store."""

    def test_get_store_returns_unified_store(self, tmp_path):
        from wisp.infra.store import UnifiedStore
        store = UnifiedStore(tmp_path / "test.db")
        assert store is not None
        assert hasattr(store, "load_session")
        assert hasattr(store, "save_session")

    def test_format_session_preview_dict(self):
        from wisp.infra.store import format_session_preview
        session = {
            "id": "test-sess",
            "title": "Test Session",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "model": "qwen",
            "messages": [{"role": "user", "content": "hello"}],
        }
        preview = format_session_preview(session)
        assert "test-sess" in preview
        assert "Test Session" in preview


class TestSessionDTOMigration:
    """Session dataclass moves from wisp.adapters → wisp.infra.session_dto (as SessionDTO)."""

    def test_session_dto_create(self):
        from wisp.infra.session_dto import SessionDTO
        s = SessionDTO.create(model="qwen", workspace="/tmp", first_prompt="hello world")
        assert s.id is not None
        assert s.model == "qwen"
        assert s.workspace == "/tmp"
        assert s.title == "hello world"

    def test_session_dto_to_dict_roundtrip(self):
        from wisp.infra.session_dto import SessionDTO
        s = SessionDTO.create(model="qwen", workspace="/tmp", first_prompt="test")
        data = s.to_dict()
        restored = SessionDTO.from_dict(data)
        assert restored.id == s.id
        assert restored.model == s.model
        assert restored.workspace == s.workspace


class TestHookTypesMigration:
    """Hook types move from wisp.adapters → wisp.infra.hook_types."""

    def test_hook_event_available(self):
        from wisp.infra.hook_types import HookEvent
        assert HookEvent.TOOL_CALL == "tool_call"
        assert HookEvent.BASH_COMMAND == "bash_command"
        assert HookEvent.FILE_WRITE == "file_write"

    def test_hook_manager_available(self):
        from wisp.infra.hook_types import HookManager
        mgr = HookManager()
        assert mgr.hooks == []

    def test_build_hook_context_available(self):
        from wisp.infra.hook_types import build_hook_context
        ctx = build_hook_context(foo="bar", baz=42)
        assert ctx["foo"] == "bar"
        assert ctx["baz"] == 42


class TestPluginToolsMigration:
    """Plugin tool stubs move from wisp.adapters → wisp.tools.registry."""

    def test_plugin_tools_in_registry(self):
        from wisp.tools.registry import has_plugin_tool, execute_plugin_tool
        assert callable(has_plugin_tool)
        assert callable(execute_plugin_tool)

    def test_register_and_has_plugin_tool(self):
        from wisp.tools.registry import register_tool, has_plugin_tool, unregister_tool
        register_tool("test_plugin", lambda **kw: "ok", description="test")
        assert has_plugin_tool("test_plugin") is True
        unregister_tool("test_plugin")
        assert has_plugin_tool("test_plugin") is False

    def test_execute_plugin_tool(self):
        from wisp.tools.registry import register_tool, execute_plugin_tool, unregister_tool
        register_tool("double", lambda x, **kw: x * 2, description="doubles")
        result = execute_plugin_tool("double", x=21)
        assert result == 42
        unregister_tool("double")
