"""The fact store keeps every fact it accepted, and says so when it cannot.

Field evidence (docs/harness/field-observations-2026-10-07.md, O-12 and O-13): `remember`
failed 52 of 82 calls on the owner's machine (the stored results say `fact too long (n > 500
chars)`, n from 501 to 3,006, and the tool schema never mentioned the limit), and the live
`~/.config/wisp/memory.json` held 0 facts while a backup held 63. Each test below is one way
the store could lose a fact or claim a save it did not make.
"""

import json
import os
import stat
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

import wisp.memory as mem

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("wisp.memory.WISP_CONFIG_DIR", tmp_path)
    # Where the store falls back to when its directory is not writable: keep it in the test.
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    return tmp_path / "memory.json"


def _contents(facts):
    return [f["content"] for f in facts]


def _run_writer(home: Path, prefix: str, count: int) -> subprocess.Popen:
    code = textwrap.dedent(f"""
        from wisp.memory import add_fact
        for i in range({count}):
            add_fact("{prefix} fact number %d" % i)
    """)
    # TMPDIR too: the store falls back to the temp dir when its own is not writable, and a
    # test must not write into the machine's shared one.
    env = {"HOME": str(home), "TMPDIR": str(home), "PATH": os.environ.get("PATH", ""),
           "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.Popen([sys.executable, "-P", "-c", code], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class TestConcurrentWriters:
    def test_concurrent_processes_lose_no_fact(self, tmp_path):
        """Several Wisp processes (REPL, server, subagents) write one store at once."""
        writers, per_writer = 6, 15
        procs = [_run_writer(tmp_path, f"writer{w}", per_writer) for w in range(writers)]
        for p in procs:
            out, err = p.communicate(timeout=120)
            assert p.returncode == 0, err.decode(errors="replace")[-2000:]
        data = json.loads((tmp_path / ".config" / "wisp" / "memory.json").read_text())
        stored = {f["content"] for f in data["global_facts"]}
        expected = {f"writer{w} fact number {i}" for w in range(writers) for i in range(per_writer)}
        assert expected - stored == set(), f"{len(expected - stored)} accepted facts were lost"

    def test_prompt_builds_while_others_write_lose_no_fact(self, tmp_path):
        """Readers (every prompt build reads, and records access) next to writers."""
        reader_code = textwrap.dedent("""
            import time
            from wisp.memory import facts_grouped_by_scope
            end = time.monotonic() + 3
            while time.monotonic() < end:
                facts_grouped_by_scope("/some/project")
        """)
        env = {"HOME": str(tmp_path), "TMPDIR": str(tmp_path), "PATH": os.environ.get("PATH", ""),
               "PYTHONPATH": str(REPO_ROOT)}
        readers = [subprocess.Popen([sys.executable, "-P", "-c", reader_code], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
        writers = [_run_writer(tmp_path, f"writer{w}", 10) for w in range(4)]
        for p in writers + readers:
            out, err = p.communicate(timeout=120)
            assert p.returncode == 0, err.decode(errors="replace")[-2000:]
        data = json.loads((tmp_path / ".config" / "wisp" / "memory.json").read_text())
        stored = {f["content"] for f in data["global_facts"]}
        expected = {f"writer{w} fact number {i}" for w in range(4) for i in range(10)}
        assert expected - stored == set(), f"{len(expected - stored)} accepted facts were lost"

    def test_a_read_does_not_undo_a_later_write(self, store, monkeypatch):
        """Building a prompt reads the store; that read must never write back an older copy."""
        monkeypatch.setattr("wisp.memory._SAVE_DEBOUNCE_SECONDS", 0.2, raising=False)
        mem.add_fact("written before the read")
        mem.list_all_facts()
        mem.add_fact("written after the read")
        time.sleep(0.6)
        assert "written after the read" in _contents(mem.list_all_facts())

    def test_the_store_location_does_not_flip_under_concurrent_lookups(self, store):
        """A shared probe file made a racing caller fall back to a temp-dir store."""
        seen: set[Path] = set()
        lock = threading.Lock()

        def look():
            for _ in range(200):
                p = mem._get_memory_file()
                with lock:
                    seen.add(p)

        threads = [threading.Thread(target=look) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert seen == {store}

    def test_reading_leaves_no_thread_that_delays_exit(self, store):
        """A pending write-behind timer is a non-daemon thread: exit waited on it."""
        mem.add_fact("a fact")
        mem.list_all_facts()
        lingering = [t for t in threading.enumerate()
                     if t is not threading.current_thread() and not t.daemon
                     and isinstance(t, threading.Timer)]
        assert lingering == []


class TestUnreadableStore:
    @pytest.mark.parametrize("raw", [
        b"{ this is not json",
        b"[]",
        b'{"version": 2, "global_facts": "not a list", "workspace_facts": {}}',
        b'{"version": 2, "global_facts": [], "workspace_facts": {"/p": [42]}}',
        b'{"version": 3, "global_facts": [], "workspace_facts": {}}',
    ], ids=["not-json", "not-an-object", "wrong-shape", "fact-not-an-object", "newer-version"])
    def test_an_unreadable_store_is_never_overwritten(self, store, raw, monkeypatch):
        monkeypatch.setattr("wisp.memory._SAVE_DEBOUNCE_SECONDS", 0, raising=False)
        store.write_bytes(raw)
        # The prompt path reads it and gets nothing, without writing.
        assert mem.facts_grouped_by_scope("/somewhere") == {"here": [], "global": [], "elsewhere": []}
        mem.list_all_facts()
        mem.format_memory_block()
        # A write is refused rather than replacing what it could not read.
        with pytest.raises(mem.MemoryStoreError) as exc_info:
            mem.add_fact("new fact")
        assert store.read_bytes() == raw
        assert str(store) in str(exc_info.value)

    @pytest.mark.parametrize("raw", [None, b"", b"  \n"], ids=["missing", "zero-bytes", "whitespace"])
    def test_a_store_with_no_content_is_simply_empty(self, store, raw):
        if raw is not None:
            store.write_bytes(raw)
        assert mem.list_all_facts() == []
        assert mem.add_fact("first fact") is True
        assert _contents(mem.list_all_facts()) == ["first fact"]


class TestWrites:
    def test_a_failed_save_is_reported_not_claimed(self, store, monkeypatch):
        def refuse(*args, **kwargs):
            raise OSError(28, "No space left on device")
        monkeypatch.setattr("wisp.memory.os.replace", refuse)
        with pytest.raises(OSError):
            mem.add_fact("cannot be saved")
        assert not store.exists()
        assert [p.name for p in store.parent.iterdir() if ".tmp" in p.name] == []

    def test_the_store_is_readable_only_by_its_owner(self, store):
        mem.add_fact("a private preference")
        assert stat.S_IMODE(store.stat().st_mode) == 0o600

    def test_clearing_keeps_what_was_cleared(self, store):
        mem.add_fact("kept by the backup")
        mem.add_fact("project fact", workspace="/tmp/proj")
        mem.clear_memory()
        assert mem.list_all_facts() == []
        backups = sorted(store.parent.glob("memory.json.cleared-*"))
        assert len(backups) == 1
        saved = json.loads(backups[0].read_text())
        assert [f["content"] for f in saved["global_facts"]] == ["kept by the backup"]
        assert sum(len(v) for v in saved["workspace_facts"].values()) == 1


class TestRememberTool:
    def test_a_long_fact_is_refused_with_the_limit_and_the_remedy(self, store):
        from wisp.tools.errors import ToolError
        from wisp.tools.memory import tool_remember
        with pytest.raises(ToolError) as exc_info:
            tool_remember("x" * 734)
        msg = str(exc_info.value)
        assert "734" in msg and str(mem.MAX_FACT_CHARS) in msg
        assert "split" in msg.lower()
        assert mem.list_all_facts() == []

    def test_the_schema_states_the_limit(self):
        from wisp.tools.registry import TOOL_SCHEMAS
        schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "remember")
        assert str(mem.MAX_FACT_CHARS) in schema["function"]["parameters"]["properties"]["fact"]["description"]

    def test_a_save_failure_reaches_the_model_as_an_error(self, store, monkeypatch):
        from wisp.tools.errors import ToolError
        from wisp.tools.memory import tool_remember

        def refuse(*args, **kwargs):
            raise OSError(13, "Permission denied")
        monkeypatch.setattr("wisp.memory.os.replace", refuse)
        with pytest.raises(ToolError) as exc_info:
            tool_remember("a fact that cannot be saved")
        assert "not saved" in str(exc_info.value).lower()


class TestCli:
    def test_listing_an_unreadable_store_says_so(self, store, capsys):
        from wisp.__main__ import cmd_memory
        store.write_bytes(b"{ broken")
        for args in ([], ["list"]):
            with pytest.raises(SystemExit) as exc_info:
                cmd_memory(args)
            assert exc_info.value.code == 1
            out = capsys.readouterr().out
            assert "unreadable" in out and "No facts" not in out
        assert store.read_bytes() == b"{ broken"
