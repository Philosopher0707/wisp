"""CLI surface E2E harness (GH#11): every flag/subcommand wired to runtime.

Strategy: subprocess ``python -m wisp`` in an isolated HOME + workspace,
``WISP_PROVIDER=mock`` for hermetic model-free turns. No local machine
state is touched — with one critical subtlety: redirecting HOME hides
``~/.local`` user-site packages (where ``requests`` lives), so the real
user-site paths are carried over explicitly via PYTHONPATH.

Spec/reality gaps (audited, not papered over):
- G1 ``tui --ink``: no such flag exists; unknown flags are ignored for tui.
- G2 ``config`` persists to ``~/.config/wisp/config.json``, not
  ``.agent/config.json``; keys are schema-validated (unknown keys rejected).
- G3 ``--quiet`` emits compact single-line JSON, not bare final text.
- G4 ``session *`` reads the HOME db while turns persist to the workspace
  db (GH#12) — visibility pinned xfail(strict); command logic seeded
  directly through the same store the commands read.
- G5 ``bench`` was a dead branch (silently exit 0) + two dict/config bugs;
  fixed under this harness (see git log): Group 6 pins the wiring.
"""

from __future__ import annotations

import json
import os
import pty
import select
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUN_TIMEOUT = 120
PTY_TIMEOUT = 150


def _site_paths() -> list[str]:
    """Real user-site + venv site dirs (survive the HOME redirect)."""
    seen: list[str] = []
    for p in sys.path:
        if "site-packages" in p and p not in seen and Path(p).is_dir():
            seen.append(p)
    return seen


def cli_env(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Hermetic env: isolated HOME, carried-over imports, mock provider."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("WISP_API_KEY", "VIRTUAL_ENV", "PYTHONHOME")}
    env["HOME"] = str(home)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO), *_site_paths(),
                                         env.get("PYTHONPATH", "")])
    # Hermetic default: callers override via `extra` (e.g. offline-ollama).
    # NOTE: setdefault is wrong here — the ambient env may carry a real
    # WISP_PROVIDER (e.g. nvidia) that would leak into the sandbox.
    env["WISP_PROVIDER"] = "mock"
    env["TERM"] = env.get("TERM", "xterm-256color")
    if extra:
        env.update(extra)
    return env


def run_cli(args: list[str], home: Path, ws: Path,
            extra_env: dict[str, str] | None = None,
            timeout: int = RUN_TIMEOUT,
            stdin_text: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run ``python -m wisp`` as a subprocess; never raises on exit code."""
    return subprocess.run(
        [sys.executable, "-m", "wisp", *args],
        env=cli_env(home, extra_env),
        cwd=str(ws),
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture()
def e2e(tmp_path: Path) -> dict[str, Path]:
    """Isolated HOME + workspace with one seeded skill."""
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    (home / ".config" / "wisp").mkdir(parents=True)
    skill = ws / ".agents" / "skills" / "e2e-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: e2e-skill\ndescription: Harness probe skill.\n---\n\nProbe.\n")
    return {"home": home, "ws": ws}


def seed_home_session(home: Path, ws: Path, sid: str, turns: int = 4) -> None:
    """Seed the HOME store (the store session * commands read) in-process
    of a throwaway subprocess — no in-test env mutation."""
    code = (
        "import sys;"
        "from wisp.infra.store import UnifiedStore;"
        f"mgr = UnifiedStore({str(home / '.config' / 'wisp' / 'wisp.db')!r});"
        f"s = mgr.create_session({sid!r}, 'mock-model', {str(ws)!r}, 'e2e');"
        "msgs = [];"
        f"[(msgs.append({{'role': 'user', 'content': f'q{{i}}'}}),"
        f" msgs.append({{'role': 'assistant', 'content': f'a{{i}}'}})) for i in range({turns})];"
        "s['messages'] = msgs;"
        "mgr.save_session(s);"
        "print('seeded', s['id'], len(msgs))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=cli_env(home), cwd=str(ws),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert f"seeded {sid}" in proc.stdout


def home_msg_count(home: Path, sid: str) -> int | None:
    con = sqlite3.connect(str(home / ".config" / "wisp" / "wisp.db"))
    row = con.execute("SELECT messages FROM sessions WHERE id = ?", (sid,)).fetchone()
    return None if row is None else len(json.loads(row[0]))


# ── PTY driver (Group 4) ──────────────────────────────────────────────


def pty_session(home: Path, ws: Path, args: list[str]) -> tuple[int, object]:
    """Fork ``wisp <args>`` under a real pty; returns (pid, fd)."""
    env = cli_env(home)
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable,
                   [sys.executable, "-m", "wisp", *args], env)
    return pid, fd


def pty_read_until(fd: int, marker: bytes, timeout: int) -> bytes:
    out = b""
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.5)
        if not r:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
        if marker in out:
            break
    return out


def pty_drain(fd: int, total_s: float = 3.0) -> bytes:
    """Read everything for at most total_s seconds.

    NOTE: never idle-reset the deadline — a live Textual app redraws
    continuously, so idle-based draining never returns (hung Group 4).
    """
    out = b""
    end = time.time() + total_s
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if not r:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    return out


def pty_reap(pid: int, fd: int, timeout: int = 20) -> int:
    """SIGTERM-then-wait; returns exit code (0 on clean exit)."""
    import signal as _signal

    code: int | None = None
    end = time.time() + timeout
    while time.time() < end:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            code = os.waitstatus_to_exitcode(status)
            break
        time.sleep(0.2)
    if code is None:
        os.kill(pid, _signal.SIGKILL)
        _, status = os.waitpid(pid, 0)
        code = os.waitstatus_to_exitcode(status)
    try:
        os.close(fd)
    except OSError:
        pass
    return code


def strip_ansi(data: bytes) -> str:
    import re

    text = data.decode("utf-8", "replace").replace("\x00", "")
    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][0-9A-Z]", "", text)


# ══════════════════════════════════════════════════════════════════════
# Group 1: Baseline metadata & flags
# ══════════════════════════════════════════════════════════════════════


class TestGroup1Baseline:
    def test_version_semver(self, e2e) -> None:
        proc = run_cli(["--version"], e2e["home"], e2e["ws"])
        assert proc.returncode == 0
        import re

        assert re.match(r"wisp \d+\.\d+\.\d+", proc.stdout.strip()), proc.stdout

    def test_help_lists_subcommands(self, e2e) -> None:
        proc = run_cli(["--help"], e2e["home"], e2e["ws"])
        assert proc.returncode == 0
        for name in ("run", "repl", "tui", "skills", "config", "check",
                     "models", "session", "agents", "swarm", "bench"):
            assert name in proc.stdout, f"missing subcommand: {name}"

    def test_subcommand_help(self, e2e) -> None:
        proc = run_cli(["session", "--help"], e2e["home"], e2e["ws"])
        assert proc.returncode == 0
        assert "session" in proc.stdout.lower()

    def test_global_flags_reach_run(self, e2e) -> None:
        """-w/-m/-S/-y flow into a real headless turn (mock provider)."""
        home, ws = e2e["home"], e2e["ws"]
        proc = run_cli(["-w", str(ws), "-m", "mock-model", "-S", "e2e-flags",
                        "-y", "run", "say hi"], home, ws)
        assert proc.returncode == 0, proc.stderr
        assert "Traceback" not in proc.stderr
        # Turn persisted under the workspace store with the given id.
        con = sqlite3.connect(str(ws / ".wisp" / "wisp.db"))
        rows = con.execute("SELECT id FROM sessions WHERE id = ?",
                           ("e2e-flags",)).fetchall()
        assert rows, "session id from -S did not persist"


# ══════════════════════════════════════════════════════════════════════
# Group 2: Diagnostics & provider subcommands
# ══════════════════════════════════════════════════════════════════════


class TestGroup2Diagnostics:
    def test_check_healthy_mock(self, e2e) -> None:
        proc = run_cli(["check"], e2e["home"], e2e["ws"])
        assert proc.returncode == 0, proc.stderr
        assert "available" in proc.stdout

    def test_check_offline_port_diagnostic(self, e2e) -> None:
        proc = run_cli(["check"], e2e["home"], e2e["ws"],
                       extra_env={"WISP_PROVIDER": "ollama",
                                  "WISP_OLLAMA_URL": "http://127.0.0.1:9"})
        assert proc.returncode == 1
        assert "127.0.0.1:9" in proc.stderr or "onnect" in proc.stderr

    def test_models_lists_inventory(self, e2e) -> None:
        proc = run_cli(["models"], e2e["home"], e2e["ws"])
        assert proc.returncode == 0, proc.stderr
        assert "mock-model" in proc.stdout
        assert "Traceback" not in proc.stderr

    def test_config_set_and_persist(self, e2e) -> None:
        home, ws = e2e["home"], e2e["ws"]
        proc = run_cli(["config", "--set", "model=e2e-probe-model"], home, ws)
        assert proc.returncode == 0, proc.stderr
        assert "e2e-probe-model" in proc.stdout
        # G2 file contract: ~/.config/wisp/config.json (isolated HOME).
        persisted = json.loads((home / ".config" / "wisp" / "config.json").read_text())
        assert persisted.get("model") == "e2e-probe-model"
        reread = run_cli(["config"], home, ws)
        assert reread.returncode == 0
        assert "e2e-probe-model" in reread.stdout

    def test_config_rejects_unknown_key(self, e2e) -> None:
        proc = run_cli(["config", "--set", "nope.notreal=1"], e2e["home"], e2e["ws"])
        assert "Unknown setting" in proc.stdout + proc.stderr

    def test_config_validate(self, e2e) -> None:
        proc = run_cli(["config", "--validate"], e2e["home"], e2e["ws"])
        assert proc.returncode == 0, proc.stderr


# ══════════════════════════════════════════════════════════════════════
# Group 3: Single-shot & headless execution
# ══════════════════════════════════════════════════════════════════════


class TestGroup3Headless:
    def test_implicit_prompt(self, e2e) -> None:
        proc = run_cli(["say hi"], e2e["home"], e2e["ws"])
        assert proc.returncode == 0, proc.stderr
        assert "Traceback" not in proc.stderr

    def test_run_subcommand(self, e2e) -> None:
        proc = run_cli(["run", "say hi"], e2e["home"], e2e["ws"])
        assert proc.returncode == 0, proc.stderr
        assert "Turn 1" in proc.stdout + proc.stderr

    def test_print_json_clean_stdout(self, e2e) -> None:
        proc = run_cli(["--print", "say hi", "--output-format", "json"],
                       e2e["home"], e2e["ws"])
        assert proc.returncode == 0, proc.stderr
        assert "\x1b" not in proc.stdout, "ANSI bled into stdout"
        payload = json.loads(proc.stdout)  # raises if polluted
        assert payload.get("ok") is True
        assert payload.get("prompt") == "say hi"
        assert "session_id" in payload

    def test_print_stream_json(self, e2e) -> None:
        proc = run_cli(["--print", "say hi", "--output-format", "stream-json"],
                       e2e["home"], e2e["ws"])
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload.get("ok") is True
        assert "complete" in proc.stderr  # status line stays on stderr

    def test_print_quiet_single_line_json(self, e2e) -> None:
        """G3 contract: --quiet emits compact single-line JSON (not bare text)."""
        proc = run_cli(["--print", "say hi", "--quiet"], e2e["home"], e2e["ws"])
        assert proc.returncode == 0, proc.stderr
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        assert len(lines) == 1, proc.stdout
        assert json.loads(lines[0]).get("ok") is True


# ══════════════════════════════════════════════════════════════════════
# Group 4: Interactive REPL & TUI entrypoints
# ══════════════════════════════════════════════════════════════════════


class TestGroup4Interactive:
    def test_repl_help_then_exit(self, e2e) -> None:
        pid, fd = pty_session(e2e["home"], e2e["ws"], ["repl"])
        try:
            pty_read_until(fd, b"wisp", PTY_TIMEOUT)
            os.write(fd, b"/help\n")
            out = pty_read_until(fd, b"Available commands", PTY_TIMEOUT)
            assert b"Available commands" in out
            os.write(fd, b"exit\n")
            out += pty_drain(fd, total_s=3.0)
        finally:
            code = pty_reap(pid, fd)
        text = strip_ansi(out)
        assert code == 0, f"repl exit code {code}"
        assert "Traceback" not in text
        assert "GeneratorExit" not in text

    def test_repl_eof_clean_exit(self, e2e) -> None:
        """Ctrl+D (EOF) exits 0 with no hung threads/tracebacks."""
        pid, fd = pty_session(e2e["home"], e2e["ws"], ["repl"])
        try:
            pty_read_until(fd, b"wisp", PTY_TIMEOUT)
            os.write(fd, b"\x04")
            out = pty_drain(fd, total_s=4.0)
        finally:
            code = pty_reap(pid, fd)
        text = strip_ansi(out)
        assert code == 0, f"repl EOF exit code {code}"
        assert "Traceback" not in text

    def test_repl_session_resume(self, e2e) -> None:
        """-S loads a pre-seeded workspace session (continuation path)."""
        home, ws = e2e["home"], e2e["ws"]
        seeded = run_cli(["-S", "e2e-resume", "run", "say hi"], home, ws)
        assert seeded.returncode == 0, seeded.stderr
        pid, fd = pty_session(home, ws, ["repl", "-S", "e2e-resume"])
        try:
            out = pty_read_until(fd, b"wisp", PTY_TIMEOUT)
            os.write(fd, b"exit\n")
            out += pty_drain(fd, total_s=3.0)
        finally:
            code = pty_reap(pid, fd)
        assert code == 0
        assert "Traceback" not in strip_ansi(out)

    def test_tui_boots_and_quits(self, e2e) -> None:
        """Textual app boots, advances past splash, exits on quit binding.

        WispTUIApp binds quit to ctrl+q (wisp/tui/app.py BINDINGS, served
        by textual's App.action_quit). Key discipline: marker-driven reads —
        fixed sleeps race boot and swallow the quit keystroke.
        """
        pid, fd = pty_session(e2e["home"], e2e["ws"], ["tui"])
        try:
            out = pty_read_until(fd, b"Press any key", PTY_TIMEOUT)
            assert b"Press any key" in out, "splash screen did not render"
            os.write(fd, b"\x1b")  # escape -> session picker
            out += pty_read_until(fd, b"Search sessions", 30)
            assert b"Search sessions" in out, "session picker did not render"
            os.write(fd, b"\x11")  # ctrl+q -> quit
            out += pty_drain(fd, total_s=8.0)
        finally:
            code = pty_reap(pid, fd)
        text = strip_ansi(out)
        assert "Traceback" not in text
        assert code == 0, f"tui exit code {code}"

    def test_tui_ink_flag_has_no_backend(self, e2e) -> None:
        """G1 gap pin: --ink is not a real flag (ignored, tui boots anyway).

        Documents that the spec's 'Node.js prerequisite check' does not
        exist — this test locks current behavior, not the desired one.
        """
        proc = run_cli(["tui", "--help"], e2e["home"], e2e["ws"])
        assert proc.returncode == 0
        assert "--ink" not in proc.stdout


# ══════════════════════════════════════════════════════════════════════
# Group 5: Session subsystem wiring
# ══════════════════════════════════════════════════════════════════════


class TestGroup5Sessions:
    def test_list_shows_seeded(self, e2e) -> None:
        seed_home_session(e2e["home"], e2e["ws"], "e2e-list", turns=2)
        proc = run_cli(["session", "list"], e2e["home"], e2e["ws"])
        assert proc.returncode == 0
        assert "e2e-list" in proc.stdout

    def test_show_displays_turns(self, e2e) -> None:
        seed_home_session(e2e["home"], e2e["ws"], "e2e-show", turns=3)
        proc = run_cli(["session", "show", "e2e-show"], e2e["home"], e2e["ws"])
        assert proc.returncode == 0, proc.stderr
        assert "e2e-show" in proc.stdout
        assert "Traceback" not in proc.stderr

    def test_trim_prunes_to_limit(self, e2e) -> None:
        home = e2e["home"]
        seed_home_session(home, e2e["ws"], "e2e-trim", turns=4)
        assert home_msg_count(home, "e2e-trim") == 8
        proc = run_cli(["session", "trim", "e2e-trim", "2"], home, e2e["ws"])
        assert proc.returncode == 0, proc.stderr
        assert home_msg_count(home, "e2e-trim") == 4
        # Storage uncorrupted: still readable + listed.
        assert run_cli(["session", "show", "e2e-trim"], home, e2e["ws"]).returncode == 0

    def test_compact_reduces_messages(self, e2e) -> None:
        home = e2e["home"]
        seed_home_session(home, e2e["ws"], "e2e-compact", turns=6)
        assert home_msg_count(home, "e2e-compact") == 12
        proc = run_cli(["session", "compact", "e2e-compact", "2"], home, e2e["ws"])
        assert proc.returncode == 0, proc.stderr
        assert home_msg_count(home, "e2e-compact") < 12

    def test_delete_evicts(self, e2e) -> None:
        home = e2e["home"]
        seed_home_session(home, e2e["ws"], "e2e-del", turns=1)
        proc = run_cli(["session", "delete", "e2e-del"], home, e2e["ws"])
        assert proc.returncode == 0, proc.stderr
        assert home_msg_count(home, "e2e-del") is None
        assert "e2e-del" not in run_cli(["session", "list"], home, e2e["ws"]).stdout

    def test_unknown_session_errors(self, e2e) -> None:
        proc = run_cli(["session", "show", "nope-missing"], e2e["home"], e2e["ws"])
        assert "not found" in (proc.stdout + proc.stderr).lower()

    def test_workspace_session_visible_in_list(self, e2e) -> None:
        """GH#12: a session created by `run -S` shows in `session list`.

        Resolution: explicit -w wins, else the cwd workspace store when one
        exists, else the legacy home store.
        """
        home, ws = e2e["home"], e2e["ws"]
        proc = run_cli(["-S", "e2e-visible", "run", "say hi"], home, ws)
        assert proc.returncode == 0, proc.stderr
        listed = run_cli(["session", "list"], home, ws)
        assert "e2e-visible" in listed.stdout


# ══════════════════════════════════════════════════════════════════════
# Group 6: Skills & benchmarking
# ══════════════════════════════════════════════════════════════════════


class TestGroup6SkillsBench:
    def test_skills_discovers_workspace(self, e2e) -> None:
        proc = run_cli(["skills", "-w", str(e2e["ws"])], e2e["home"], e2e["ws"])
        assert proc.returncode == 0, proc.stderr
        assert "e2e-skill" in proc.stdout

    def test_bench_runs_task_and_scoreboard(self, e2e) -> None:
        """bench wiring: invalid task -> rc 2; real task -> per-task line
        + scoreboard + rc reflecting pass/fail (mock fails verify by design)."""
        home, ws = e2e["home"], e2e["ws"]
        bad = run_cli(["bench", "-t", "nope-task", "--workdir", str(ws / "bench")],
                      home, ws)
        assert bad.returncode == 2
        assert "Unknown task" in bad.stdout
        run = run_cli(["bench", "--models", "mock-model", "-t", "json-edit",
                       "--timeout", "60", "--workdir", str(ws / "bench")],
                      home, ws, timeout=180)
        combined = run.stdout + run.stderr
        assert "json-edit" in combined
        assert "PASS" in combined or "FAIL" in combined
        assert "Traceback" not in run.stderr


# ══════════════════════════════════════════════════════════════════════
# Group 7: Swarm & multi-agent subcommands
# ══════════════════════════════════════════════════════════════════════


class TestGroup7Swarm:
    def test_agents_list_roles(self, e2e) -> None:
        proc = run_cli(["agents", "list"], e2e["home"], e2e["ws"])
        assert proc.returncode == 0, proc.stderr
        for role in ("coder", "reviewer", "tester", "researcher", "planner", "debugger"):
            assert role in proc.stdout, f"missing role: {role}"

    def test_agents_status_idle(self, e2e) -> None:
        proc = run_cli(["agents", "status"], e2e["home"], e2e["ws"])
        assert proc.returncode == 0, proc.stderr
        assert "No active swarm" in proc.stdout

    def test_swarm_dispatches_and_settles(self, e2e) -> None:
        """Mock-provider swarm: dispatch -> run -> settled summary."""
        proc = run_cli(["swarm", "write hello", "--roles", "coder",
                        "--max-parallel", "1"], e2e["home"], e2e["ws"],
                       timeout=240)
        assert proc.returncode == 0, proc.stderr
        combined = proc.stdout + proc.stderr
        assert "Swarm execution complete" in combined
        assert "Traceback" not in proc.stderr

    def test_swarm_requires_goal(self, e2e) -> None:
        proc = run_cli(["swarm"], e2e["home"], e2e["ws"])
        assert "Usage" in proc.stdout
