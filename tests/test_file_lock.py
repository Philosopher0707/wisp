"""Tests for wisp.file_lock — advisory file locking."""

import tempfile
import time
from pathlib import Path


from wisp.file_lock import FileLock, _generate_agent_id


class TestFileLock:
    """Unit tests for FileLock."""

    def setup_method(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fl = FileLock(self.tmp.name, agent_id="agent-a")

    def teardown_method(self):
        self.tmp.cleanup()

    def test_acquire_new_file(self):
        assert self.fl.acquire("test.py")
        assert self.fl.is_locked("test.py")

    def test_acquire_already_locked(self):
        self.fl.acquire("test.py")
        fl2 = FileLock(self.tmp.name, agent_id="agent-b")
        assert not fl2.acquire("test.py")

    def test_reacquire_same_agent(self):
        self.fl.acquire("test.py")
        assert self.fl.acquire("test.py")  # renew

    def test_release(self):
        self.fl.acquire("test.py")
        self.fl.release("test.py")
        assert not self.fl.is_locked("test.py")

    def test_release_wrong_agent(self):
        self.fl.acquire("test.py")
        fl2 = FileLock(self.tmp.name, agent_id="agent-b")
        fl2.release("test.py")  # should not release
        assert self.fl.is_locked("test.py")

    def test_lock_expires(self):
        self.fl.acquire("test.py", timeout_sec=1)
        assert self.fl.is_locked("test.py")
        time.sleep(1.1)
        assert not self.fl.is_locked("test.py")

    def test_lock_info(self):
        self.fl.acquire("test.py")
        info = self.fl.lock_info("test.py")
        assert info is not None
        assert info["agent"] == "agent-a"

    def test_lock_info_not_locked(self):
        assert self.fl.lock_info("test.py") is None

    def test_list_active_locks(self):
        self.fl.acquire("a.py")
        self.fl.acquire("b.py")
        locks = self.fl.list_active_locks()
        assert len(locks) == 2

    def test_release_all(self):
        self.fl.acquire("a.py")
        self.fl.acquire("b.py")
        self.fl.release_all()
        assert not self.fl.is_locked("a.py")
        assert not self.fl.is_locked("b.py")

    def test_generate_agent_id(self):
        aid = _generate_agent_id()
        assert aid.startswith("wisp-")
        assert len(aid) > 5

    def test_nested_path_lock(self):
        # Create nested directory
        nested = Path(self.tmp.name) / "wisp" / "deep"
        nested.mkdir(parents=True)
        file_path = str(nested / "file.py")
        Path(file_path).write_text("x")
        assert self.fl.acquire(file_path)
        assert self.fl.is_locked(file_path)


class TestFileLockConcurrency:
    """Tests for concurrent locking behavior."""

    def test_steal_expired_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            fl1 = FileLock(tmp, agent_id="agent-1")
            fl1.acquire("file.py", timeout_sec=1)
            time.sleep(1.1)
            fl2 = FileLock(tmp, agent_id="agent-2")
            assert fl2.acquire("file.py")
            assert fl2.is_locked("file.py")
