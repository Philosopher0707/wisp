"""The Live tab: sessions that announce themselves, processes that do not, the model each got from its environment, the REPL wiring, and the bench defaults."""

from __future__ import annotations

import asyncio
import io
import json
import os
import socket
import sqlite3
import time
import urllib.request
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest

import wisp.dashboard.__main__ as cli
import wisp.dashboard.embed as embed
import wisp.dashboard.live as live
import wisp.dashboard.presence as presence
from tests.dashboard.conftest import SECRET_KEY, SECRET_PROMPT, make_workspace

NOW = 1_800_000_000.0


@pytest.fixture
def livedir(tmp_path, monkeypatch):
    d = tmp_path / "live"
    monkeypatch.setenv("WISP_LIVE_DIR", str(d))
    return d


def announce_file(d, pid, **over):
    d.mkdir(parents=True, exist_ok=True)
    body = {"pid": pid, "kind": "repl", "session_id": "sess-1", "model": "vendor/model-a", "provider": "openrouter", "model_source": "env WISP_MODEL",
            "workspace": "/w/proj", "started": NOW - 600, "heartbeat": NOW - 2}
    body.update(over)
    (d / f"{pid}.json").write_text(json.dumps(body))


class TestPresenceFile:
    def test_a_session_writes_only_what_a_list_of_sessions_needs(self, livedir, monkeypatch):
        monkeypatch.setenv("WISP_API_KEY", SECRET_KEY)
        monkeypatch.setenv("WISP_MODEL", "vendor/model-a")
        p = presence.Presence(livedir, interval=60)
        p.start(session_id="s1", model="vendor/model-a", provider="openrouter", workspace="/w/proj")
        try:
            text = p.path.read_text()
            body = json.loads(text)
            assert set(body) == {"pid", "kind", "session_id", "model", "provider", "model_source", "workspace", "started", "heartbeat"}
            assert body["model_source"] == "env WISP_MODEL" and body["pid"] == os.getpid()
            assert SECRET_KEY not in text and SECRET_PROMPT not in text
            assert oct(p.path.stat().st_mode & 0o777) == "0o600"
        finally:
            p.stop()
        assert not p.path.exists()

    def test_a_model_that_did_not_come_from_the_environment_says_so(self, livedir, monkeypatch):
        monkeypatch.delenv("WISP_MODEL", raising=False)
        p = presence.Presence(livedir, interval=60)
        p.start(model="vendor/from-config")
        try:
            assert json.loads(p.path.read_text())["model_source"] == "config"
        finally:
            p.stop()

    def test_the_heartbeat_advances(self, livedir):
        ticks = iter(range(100, 1000))
        p = presence.Presence(livedir, clock=lambda: float(next(ticks)), interval=0.01)
        p.start(model="m")
        try:
            first = json.loads(p.path.read_text())["heartbeat"]
            deadline = time.time() + 5
            while time.time() < deadline and json.loads(p.path.read_text())["heartbeat"] == first:
                time.sleep(0.02)
            assert json.loads(p.path.read_text())["heartbeat"] > first
        finally:
            p.stop()

    def test_starting_removes_the_file_of_a_dead_session_and_leaves_a_live_one(self, livedir):
        dead = _dead_pid()
        announce_file(livedir, dead)
        announce_file(livedir, os.getppid())
        p = presence.Presence(livedir, interval=60)
        p.start(model="m")
        try:
            assert not (livedir / f"{dead}.json").exists()
            assert (livedir / f"{os.getppid()}.json").exists()
        finally:
            p.stop()

    def test_an_unwritable_directory_never_raises(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        p = presence.Presence(blocker / "live", interval=60)
        p.start(model="m")
        p.stop()


def _dead_pid() -> int:
    pid = 4_000_000
    while presence.pid_alive(pid):
        pid += 1
    return pid


class TestReadingAnnouncements:
    def test_fresh_is_running_and_stale_is_silent_and_a_dead_process_is_gone(self, livedir):
        announce_file(livedir, 111)
        announce_file(livedir, 222, heartbeat=NOW - 300)
        announce_file(livedir, 333)
        rows = {r["pid"]: r for r in presence.read_all(livedir, NOW, alive=lambda pid: pid != 333)}
        assert set(rows) == {111, 222}
        assert rows[111]["state"] == "running" and rows[222]["state"] == "silent"
        assert rows[111]["uptime_s"] == 600 and rows[111]["heartbeat_age_s"] == 2

    def test_only_known_fields_are_copied_and_long_values_are_capped(self, livedir):
        announce_file(livedir, 111, model="m" * 5000, extra_secret=SECRET_KEY, argv=[SECRET_PROMPT])
        row = presence.read_all(livedir, NOW, alive=lambda pid: True)[0]
        assert len(row["model"]) == 200
        assert SECRET_KEY not in json.dumps(row) and SECRET_PROMPT not in json.dumps(row)

    @pytest.mark.parametrize("junk", ["{torn", "[]", '{"pid": "7"}', '{"pid": 7, "model": 5}'])
    def test_malformed_files_are_skipped_or_neutralised(self, livedir, junk):
        livedir.mkdir(parents=True)
        (livedir / "9.json").write_text(junk)
        rows = presence.read_all(livedir, NOW, alive=lambda pid: True)
        assert all(r["model"] is None for r in rows)

    def test_a_missing_directory_is_an_empty_list(self, tmp_path):
        assert presence.read_all(tmp_path / "nope", NOW) == []


class TestModelFromEnvironment:
    def test_the_process_environment_beats_the_dotenv_and_names_its_source(self, tmp_path):
        dotenv = tmp_path / ".env"
        dotenv.write_text("WISP_MODEL=file/model\nWISP_PROVIDER=fileprov\n")
        got = presence.resolve_model({"WISP_MODEL": "env/model"}, dotenv)
        assert got["model"] == "env/model" and got["model_source"] == "env WISP_MODEL"
        assert got["provider"] == "fileprov" and got["provider_source"] == "~/.config/wisp/.env"

    def test_no_secret_in_the_dotenv_is_ever_returned(self, tmp_path):
        dotenv = tmp_path / ".env"
        dotenv.write_text(f"export WISP_API_KEY={SECRET_KEY}\nOPENROUTER_API_KEY='{SECRET_KEY}'\nWISP_MODEL=\"m/x\"\n")
        got = presence.resolve_model({}, dotenv)
        assert got == {"model": "m/x", "model_source": "~/.config/wisp/.env"}
        assert SECRET_KEY not in json.dumps(got)

    def test_env_has_answers_yes_or_no_without_the_value(self, tmp_path):
        dotenv = tmp_path / ".env"
        dotenv.write_text(f"WISP_API_KEY={SECRET_KEY}\n")
        assert presence.env_has("WISP_API_KEY", {}, dotenv) is True
        assert presence.env_has("NOPE", {}, dotenv) is False
        assert presence.env_has("X", {"X": "1"}, tmp_path / "missing") is True


PS = """\
    1     16:50 /sbin/launchd
  501  01:02:03 /Users/u/.venvs/wisp/bin/python -m wisp repl
  502     05:10 /Users/u/.venvs/wisp/bin/python -m wisp run fix the bug
  503  2-01:00:00 /Users/u/.venvs/wisp/bin/python -m wisp swarm --n 3
  504     00:09 /Users/u/.venvs/wisp/bin/python -m wisp.dashboard serve
  505     00:09 /bin/zsh -c grep -m wisp file
  506     00:09 vim notes-about-wisp.md
  507     00:09 /Users/u/.venvs/wisp/bin/python -m wisp
  508     00:09 /Users/u/.venvs/wisp/bin/python -m wisp refactor the auth module
"""


class TestWhatPsSees:
    @pytest.mark.parametrize("text, seconds", [("00:09", 9), ("05:10", 310), ("01:02:03", 3723), ("2-01:00:00", 2 * 86400 + 3600), ("garbage", None)])
    def test_elapsed_time(self, text, seconds):
        assert live.parse_etime(text) == seconds

    def test_only_wisp_processes_are_picked_and_the_dashboard_itself_is_not(self):
        rows = live.parse_ps(PS)
        assert [(r["pid"], r["kind"]) for r in rows] == [(501, "repl"), (502, "run"), (503, "swarm"), (508, "run")]  # 507 is `wisp` alone: it only prints help

    def test_the_working_directory_is_looked_up_only_for_wisp_processes(self):
        asked = []
        rows = live.seen_processes(PS, cwd_of=lambda pid: asked.append(pid) or f"/w/{pid}")
        assert asked == [501, 502, 503, 508] and rows[0]["workspace"] == "/w/501"


class TestLiveData:
    def kw(self, livedir, tmp_path, **over):
        base = dict(now=NOW, live_directory=livedir, ps_text="", cwd_of=lambda pid: None, alive=lambda pid: True, environ={}, dotenv=tmp_path / "none", self_pid=-1)
        base.update(over)
        return base

    def test_announced_sessions_and_unannounced_processes_are_listed_apart(self, livedir, tmp_path):
        announce_file(livedir, 501)
        announce_file(livedir, 700, model="vendor/model-b", session_id="sess-2")
        got = live.live_data([], **self.kw(livedir, tmp_path, ps_text=PS, cwd_of=lambda pid: "/seen/" + str(pid)))
        by_pid = {s["pid"]: s for s in got["sessions"]}
        assert set(by_pid) == {501, 502, 503, 508, 700}
        assert by_pid[501]["announced"] and by_pid[501]["model"] == "vendor/model-a" and by_pid[501]["workspace"] == "/w/proj"
        assert not by_pid[502]["announced"] and by_pid[502]["model"] is None and by_pid[502]["state"] == "not announced"
        assert got["running"] == 2

    def test_this_session_comes_first_and_is_marked(self, livedir, tmp_path):
        announce_file(livedir, 501, started=NOW - 10)
        announce_file(livedir, 700, started=NOW - 5000)
        got = live.live_data([], **self.kw(livedir, tmp_path, self_pid=700))
        assert [s["pid"] for s in got["sessions"]] == [700, 501] and got["sessions"][0]["this"] and not got["sessions"][1]["this"]

    def test_the_default_model_comes_from_the_environment(self, livedir, tmp_path):
        got = live.live_data([], **self.kw(livedir, tmp_path, environ={"WISP_MODEL": "vendor/model-z", "WISP_PROVIDER": "openrouter"}))
        assert got["default_model"]["model"] == "vendor/model-z" and got["default_model"]["model_source"] == "env WISP_MODEL"

    def test_recently_touched_sessions_come_from_the_database_without_their_messages(self, livedir, tmp_path):
        ws = make_workspace(tmp_path, "proj")
        con = sqlite3.connect(ws / ".wisp" / "wisp.db")
        recent = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(NOW - 120))
        con.execute("update sessions set updated_at = ? where id = 's1'", (recent,))
        con.commit()
        con.close()
        got = live.live_data([ws], **self.kw(livedir, tmp_path))
        assert [r["session_id"] for r in got["recent"]] == ["s1"] and got["recent"][0]["model"] == "vendor/model-a"
        assert 100 <= got["recent"][0]["idle_s"] <= 140
        assert SECRET_PROMPT not in json.dumps(got)

    def test_a_workspace_without_a_database_is_skipped(self, livedir, tmp_path):
        assert live.live_data([tmp_path / "empty"], **self.kw(livedir, tmp_path))["recent"] == []


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def embedded(home):
    yield embed
    embed.stop()


def get_json(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return json.loads(r.read())


class TestEmbeddedDashboard:
    def test_it_serves_the_live_section_from_a_thread_and_stops_cleanly(self, embedded, livedir, monkeypatch):
        announce_file(livedir, os.getpid())
        port = free_port()
        res = embedded.start(port)
        assert res == {"started": True, "port": port, "owner": "this session"}
        got = get_json(port, "/api/live")
        assert any(s["pid"] == os.getpid() and s["this"] for s in got["sessions"])
        assert embedded.status() == {"running": True, "port": port}
        assert embedded.stop() is True and embedded.status()["running"] is False
        with pytest.raises(OSError):
            get_json(port, "/healthz")

    def test_a_second_start_does_not_start_a_second_server(self, embedded):
        port = free_port()
        embedded.start(port)
        again = embedded.start(port)
        assert again["started"] is False and again["owner"] == "this session" and again["port"] == port

    def test_a_dashboard_run_by_another_process_is_found_not_fought(self, embedded):
        import wisp.dashboard.server as server

        port = free_port()
        other = server.make_server(port)
        import threading

        threading.Thread(target=other.serve_forever, daemon=True).start()
        try:
            res = embedded.start(port)
            assert res["started"] is False and res["owner"] == "another process" and res["port"] == port
        finally:
            other.shutdown()
            other.server_close()

    def test_a_port_held_by_something_else_moves_to_the_next_one(self, embedded):
        port = free_port()
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", port))
            blocker.listen(1)
            res = embedded.start(port)
        assert res["started"] is True and res["port"] != port

    def test_it_is_loopback_only_like_the_standalone_server(self, embedded):
        port = free_port()
        embedded.start(port)
        assert embed._state["server"].server_address[0] == "127.0.0.1"


class TestSlashCommand:
    def run(self, text):
        from wisp.commands import dispatch

        buf = io.StringIO()
        with redirect_stdout(buf):
            result = dispatch(text, SimpleNamespace())
        return result, buf.getvalue()

    def test_dashboard_is_reached_through_the_same_dispatch_the_repl_uses(self, embedded, monkeypatch):
        port = free_port()
        monkeypatch.setattr(embed, "DEFAULT_PORT", port)
        result, out = self.run("/dashboard")
        assert result is True and f"http://127.0.0.1:{port}/#live" in out
        _, status = self.run("/dashboard status")
        assert f":{port}/" in status
        _, again = self.run("/dashboard")
        assert "already running" in again
        _, stopped = self.run("/dashboard stop")
        assert "stopped" in stopped.lower()

    def test_stop_without_a_server_says_so_and_a_bad_argument_prints_usage(self, embedded):
        _, out = self.run("/dashboard stop")
        assert "not serving" in out
        _, bad = self.run("/dashboard banana")
        assert "Usage" in bad

    def test_the_command_is_registered(self):
        from wisp.repl.commands import lookup

        assert lookup("dashboard") is not None


class TestReplWiring:
    """Drives the real `_run_repl`; only the runner (which would block on input) is replaced."""

    def drive(self, monkeypatch, livedir, env_dashboard=None):
        import wisp.cli.repl as repl_mod
        import wisp.entry as entry

        seen: dict[str, object] = {}

        class FakeRunner:
            def __init__(self, **kw):
                self.adapter = None

            def boot_env(self):
                return "/w/proj"

            async def preflight(self, ws):
                return None

            def banner(self, **kw):
                pass

            def run(self):
                seen["during"] = [json.loads(f.read_text()) for f in livedir.glob("*.json")]

        async def get_or_create_session(**kw):
            return {"messages": []}

        monkeypatch.setattr(repl_mod, "ReplRunner", FakeRunner)
        if env_dashboard:
            monkeypatch.setenv("WISP_DASHBOARD", env_dashboard)
        else:
            monkeypatch.delenv("WISP_DASHBOARD", raising=False)
        started = []
        monkeypatch.setattr(embed, "start", lambda *a, **k: started.append(1) or {"started": True, "port": 8765})
        root = SimpleNamespace(runtime=SimpleNamespace(get_or_create_session=get_or_create_session), bind_loop=lambda loop: None)
        config = SimpleNamespace(model="vendor/model-a", provider="openrouter", workspace="/w/proj")
        loop = asyncio.new_event_loop()
        try:
            entry._run_repl(SimpleNamespace(), root, config, loop=loop, session_id="sess-9")
        finally:
            loop.close()
        return seen, started

    def test_a_repl_is_listed_while_it_runs_and_removed_when_it_ends(self, livedir, monkeypatch):
        monkeypatch.setenv("WISP_MODEL", "vendor/model-a")
        seen, started = self.drive(monkeypatch, livedir)
        (during,) = seen["during"]
        assert during["pid"] == os.getpid() and during["session_id"] == "sess-9" and during["model"] == "vendor/model-a"
        assert during["provider"] == "openrouter" and during["workspace"] == "/w/proj" and during["model_source"] == "env WISP_MODEL"
        assert list(livedir.glob("*.json")) == []
        assert started == []

    def test_the_dashboard_starts_with_the_repl_only_when_asked(self, livedir, monkeypatch):
        _, started = self.drive(monkeypatch, livedir, env_dashboard="1")
        assert started == [1]


class TestBenchTakesTheModelFromTheEnvironment:
    def run(self, monkeypatch, tmp_path, argv, env, dotenv_text=""):
        import wisp.dashboard.bench as bench

        captured = {}
        monkeypatch.setattr(bench, "run_bench", lambda spec, out, log=print: captured.update(spec=spec) or tmp_path / "r.jsonl")
        for k in ("WISP_MODEL", "WISP_PROVIDER", "WISP_API_BASE", "WISP_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        cfg = tmp_path / "home" / ".config" / "wisp"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / ".env").write_text(dotenv_text)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["bench", "--out", str(tmp_path / "out"), *argv])
        return code, captured.get("spec"), buf.getvalue()

    def test_model_provider_base_and_key_name_come_from_the_environment(self, monkeypatch, tmp_path):
        env = {"WISP_MODEL": "vendor/model-a", "WISP_PROVIDER": "openrouter", "WISP_API_BASE": "https://example.invalid/v1", "WISP_API_KEY": SECRET_KEY}
        code, spec, out = self.run(monkeypatch, tmp_path, [], env)
        assert code == 0 and (spec.model, spec.provider, spec.api_base, spec.key_from) == ("vendor/model-a", "openrouter", "https://example.invalid/v1", "WISP_API_KEY")
        assert "env WISP_MODEL" in out and "spends tokens" in out and SECRET_KEY not in out

    def test_the_dotenv_is_the_second_source_and_a_flag_beats_both(self, monkeypatch, tmp_path):
        code, spec, out = self.run(monkeypatch, tmp_path, [], {}, f"WISP_MODEL=file/model\nWISP_API_KEY={SECRET_KEY}\n")
        assert spec.model == "file/model" and spec.key_from == "WISP_API_KEY" and "~/.config/wisp/.env" in out and SECRET_KEY not in out
        code, spec, out = self.run(monkeypatch, tmp_path, ["--model", "flag/model"], {"WISP_MODEL": "env/model"})
        assert spec.model == "flag/model" and "(--model)" in out

    def test_no_model_anywhere_is_an_error_not_a_guess(self, monkeypatch, tmp_path, capsys):
        code, spec, _ = self.run(monkeypatch, tmp_path, [], {})
        assert code == 2 and spec is None
        assert "WISP_MODEL" in capsys.readouterr().err

    def test_no_key_set_means_no_key_name_is_invented(self, monkeypatch, tmp_path):
        _, spec, _ = self.run(monkeypatch, tmp_path, [], {"WISP_MODEL": "m/x"})
        assert spec.key_from == ""
