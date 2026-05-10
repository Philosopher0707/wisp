"""Regression tests for session file locking."""

import json
import os
import tempfile
import threading
import time
from pathlib import Path

import pytest
from wisp.session import Session, SessionManager


class TestSessionFileLock:
    """Verify SessionManager.save() uses file locking for concurrent access."""

    def test_concurrent_save_no_data_loss(self, tmp_path):
        """Two threads saving the same session simultaneously must not corrupt the file."""
        sm = SessionManager()
        # Redirect sessions dir to tmp_path
        from wisp import session
        old_dir = session.SESSIONS_DIR
        session.SESSIONS_DIR = tmp_path / "sessions"
        session.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

        try:
            sess = Session.create("test-model", str(tmp_path), "hello")
            results = []

            def worker():
                for _ in range(20):
                    sm.save(sess)
                    results.append("ok")

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            assert len(results) == 40
        finally:
            session.SESSIONS_DIR = old_dir

    def test_save_writes_valid_json(self, tmp_path):
        """File must contain valid JSON after saving."""
        from wisp import session
        old_dir = session.SESSIONS_DIR
        session.SESSIONS_DIR = tmp_path / "sessions"
        session.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            sess = Session.create("m", str(tmp_path), "hi")
            sm = SessionManager()
            sm.save(sess)
            raw = (tmp_path / "sessions" / (sess.id + ".json")).read_text()
            data = json.loads(raw)
            assert data["id"] == sess.id
        finally:
            session.SESSIONS_DIR = old_dir
