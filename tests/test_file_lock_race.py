"""Regression: FileLock must not TOCTOU-race on concurrent acquire."""

import json
import tempfile
import threading
import time

import pytest

from wisp.file_lock import FileLock


class TestFileLockConcurrencyRegressions:
    """Race-condition tests for FileLock."""

    def test_concurrent_acquires_only_one_wins(self):
        """Two agents racing on the same file — only one must acquire."""
        with tempfile.TemporaryDirectory() as tmp:
            fl1 = FileLock(tmp, agent_id="agent-1")
            fl2 = FileLock(tmp, agent_id="agent-2")

            results = {"agent-1": None, "agent-2": None}
            barrier = threading.Barrier(2)

            def worker(agent, fl):
                barrier.wait()
                results[agent] = fl.acquire("main.py")

            t1 = threading.Thread(target=worker, args=("agent-1", fl1))
            t2 = threading.Thread(target=worker, args=("agent-2", fl2))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            winners = [a for a, ok in results.items() if ok]
            assert len(winners) == 1, f"Expected exactly one winner, got {winners}"
            assert results["agent-1"] ^ results["agent-2"]  # XOR

    def test_renew_same_agent_concurrent(self):
        """An agent can renew its own lock without a race."""
        with tempfile.TemporaryDirectory() as tmp:
            fl = FileLock(tmp, agent_id="agent-1")
            fl.acquire("main.py", timeout_sec=10)

            barrier = threading.Barrier(2)
            results = []

            def worker():
                barrier.wait()
                results.append(fl.acquire("main.py", timeout_sec=5))

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            assert all(results), f"Both renews should succeed: {results}"
            assert fl.is_locked("main.py")

    def test_cross_process_is_locked(self):
        """is_locked correctly reports state held by another FileLock instance."""
        with tempfile.TemporaryDirectory() as tmp:
            fl1 = FileLock(tmp, agent_id="agent-1")
            fl2 = FileLock(tmp, agent_id="agent-2")

            fl1.acquire("shared.py")

            assert fl2.is_locked("shared.py"), "is_locked should see lock held by another instance"

            fl1.release("shared.py")
            assert not fl2.is_locked("shared.py")

    def test_release_wrong_agent_does_not_unlock(self):
        """A wrong agent calling release must not drop the OS-level advisory lock."""
        with tempfile.TemporaryDirectory() as tmp:
            fl1 = FileLock(tmp, agent_id="agent-a")
            fl2 = FileLock(tmp, agent_id="agent-b")

            fl1.acquire("test.py")
            fl2.release("test.py")  # Wrong agent — should be a no-op

            assert fl1.is_locked("test.py")
            assert fl2.is_locked("test.py")

    def test_stale_lock_file_detected(self):
        """If metadata file exists but is EXPIRED and no OS lock is held, acquire should succeed."""
        with tempfile.TemporaryDirectory() as tmp:
            fl = FileLock(tmp, agent_id="agent-x")
            fl.acquire("orphan.py")
            fl.release("orphan.py")

            # Manually write EXPIRED metadata (simulating a crashed agent whose
            # OS-level lock has already been released by the kernel).
            from wisp.file_lock import _write_meta
            from datetime import datetime, timezone, timedelta
            lock_path = fl._lock_path("orphan.py")
            _write_meta(lock_path, "ghost-agent", 300)
            # Backdate the metadata so it is definitely before now()
            data = json.loads(lock_path.read_text(encoding="utf-8"))
            data["expires"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            lock_path.write_text(json.dumps(data), encoding="utf-8")

            fl2 = FileLock(tmp, agent_id="agent-y")
            assert fl2.acquire("orphan.py"), "Should acquire expired stale lock"

    def test_ten_agents_race(self):
        """Ten agents racing on the same file — one winner."""
        with tempfile.TemporaryDirectory() as tmp:
            agents = [FileLock(tmp, agent_id=f"agent-{i}") for i in range(10)]
            results: list[bool] = []
            barrier = threading.Barrier(10)
            lock = threading.Lock()

            def worker(fl: FileLock):
                barrier.wait()
                ok = fl.acquire("race.py")
                with lock:
                    results.append(ok)

            threads = [threading.Thread(target=worker, args=(fl,)) for fl in agents]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            winners = sum(1 for ok in results if ok)
            assert winners == 1, f"Expected exactly 1 winner, got {winners} / {len(results)}"
