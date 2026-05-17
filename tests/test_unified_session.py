"""TDD tests for unified session management."""
from __future__ import annotations
import json
import pytest
import tempfile
from pathlib import Path

@pytest.fixture
def tmp_config_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        import wisp.config
        monkeypatch.setattr(wisp.config, 'WISP_CONFIG_DIR', tmp_path)
        import wisp.session as session_mod
        monkeypatch.setattr(session_mod, 'SESSIONS_DIR', tmp_path / 'sessions')
        yield tmp_path

class TestUnifiedSessionManager:
    def test_create_session(self, tmp_config_dir):
        from wisp.session import UnifiedSessionManager
        mgr = UnifiedSessionManager()
        session = mgr.create_session(model='llama3.2', workspace='/tmp/ws', first_prompt='Hello')
        assert session.id
        assert session.model == 'llama3.2'

    def test_save_and_load_session(self, tmp_config_dir):
        from wisp.session import UnifiedSessionManager
        mgr = UnifiedSessionManager()
        session = mgr.create_session(model='qwen2.5', workspace='.', first_prompt='Test')
        session.messages.append({'role': 'user', 'content': 'hi'})
        mgr.save_session(session)
        loaded = mgr.load_session(session.id)
        assert loaded is not None
        assert loaded.messages == [{'role': 'user', 'content': 'hi'}]

    def test_list_sessions(self, tmp_config_dir):
        from wisp.session import UnifiedSessionManager
        mgr = UnifiedSessionManager()
        s1 = mgr.create_session(model='a', workspace='.', first_prompt='A')
        s2 = mgr.create_session(model='b', workspace='.', first_prompt='B')
        mgr.save_session(s1)
        mgr.save_session(s2)
        assert len(mgr.list_sessions()) == 2

    def test_delete_session(self, tmp_config_dir):
        from wisp.session import UnifiedSessionManager
        mgr = UnifiedSessionManager()
        session = mgr.create_session(model='m', workspace='.', first_prompt='D')
        mgr.save_session(session)
        assert mgr.delete_session(session.id) is True
        assert mgr.load_session(session.id) is None

class TestUnifiedThreadRun:
    def test_create_thread(self, tmp_config_dir):
        from wisp.session import UnifiedSessionManager
        mgr = UnifiedSessionManager()
        thread = mgr.create_thread(workspace='/tmp/proj', title='Auth refactor')
        assert thread.id.startswith('thread-')
        assert thread.status == 'idle'

    def test_create_run(self, tmp_config_dir):
        from wisp.session import UnifiedSessionManager
        mgr = UnifiedSessionManager()
        thread = mgr.create_thread(workspace='.', title='T')
        run = mgr.create_run(thread_id=thread.id, prompt='fix auth')
        assert run.id.startswith('run-')
        assert run.status == 'queued'

    def test_update_run_status(self, tmp_config_dir):
        from wisp.session import UnifiedSessionManager
        mgr = UnifiedSessionManager()
        thread = mgr.create_thread(workspace='.', title='T')
        run = mgr.create_run(thread_id=thread.id, prompt='go')
        mgr.update_run_status(run.id, 'running')
        assert mgr.get_run(run.id).status == 'running'

class TestSupervisorIntegration:
    def test_supervisor_uses_unified_manager(self, tmp_config_dir):
        from wisp.session import UnifiedSessionManager
        from wisp.supervisor import WispSupervisor
        mgr = UnifiedSessionManager()
        supervisor = WispSupervisor(session_manager=mgr)
        thread = supervisor.create_thread(workspace='/tmp', title='Test')
        assert thread.workspace == '/tmp'