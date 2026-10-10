"""The objective-level loop through the REAL CLI REPL: `python -m wisp repl` in a pty, the mock provider, a real project, a real pytest probe.

The mock model never edits anything, so these tests pin what the REPL and the harness do around it: the auto-route announcement, an honest "not proven" when
nothing changed, a "proven" that comes from the harness measuring the repository (the test edits the file itself, as a person would), the kill switch, and
that a question never starts a loop. The scripted-model scenarios (fixed on attempt 2, weakening the check, resume) are in `test_repl_converge.py`.
"""

from __future__ import annotations

import os
import pty
import re
import select
import signal
import sys
import time
from pathlib import Path

import pytest

BUG = "def total(xs):\n    return sum(xs) - 1\n"
FIXED = "def total(xs):\n    return sum(xs)\n"
TEST = "from totals import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n"
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
REPO = str(Path(__file__).resolve().parents[1])

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a pty")


class Repl:
    def __init__(self, ws: Path, extra_env: dict[str, str] | None = None):
        home = ws / "home"
        home.mkdir()
        env = {"PATH": os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""), "HOME": str(home), "TERM": "xterm", "PYTHONPATH": REPO,
               "WISP_PROVIDER": "mock", "WISP_MODEL": "mock/m", **(extra_env or {})}
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(ws)
            os.execve(sys.executable, [sys.executable, "-m", "wisp", "repl"], env)
        self.buf = ""
        self.expect("wisp ❯", 90)

    def _pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self.fd], [], [], 0.2)
            if ready:
                try:
                    self.buf += ANSI.sub("", os.read(self.fd, 65536).decode(errors="replace"))
                except OSError:
                    return

    def expect(self, pattern: str, timeout: float = 60, since: int = 0) -> str:
        end = time.time() + timeout
        while time.time() < end:
            m = re.search(pattern, self.buf[since:])
            if m:
                return m.group(0)
            self._pump(0.3)
        raise AssertionError(f"timed out waiting for {pattern!r}; the REPL printed:\n{self.buf[since:][-1500:]}")

    def send(self, line: str) -> int:
        mark = len(self.buf)
        os.write(self.fd, line.encode() + b"\r")
        return mark

    def close(self) -> None:
        try:
            os.write(self.fd, b"/exit\r")
            self._pump(2)
        finally:
            try:
                os.kill(self.pid, signal.SIGKILL)
                os.waitpid(self.pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass


@pytest.fixture
def project(tmp_path):
    (tmp_path / "totals.py").write_text(BUG)
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_totals.py").write_text(TEST)
    return tmp_path


OBJECTIVE = "Fix the failing tests. The bug is in totals.py."


def test_a_question_goes_to_the_model_and_an_objective_goes_to_the_loop_which_is_honest(project):
    repl = Repl(project)
    try:
        mark = repl.send("why are the tests failing?")
        repl.expect("wisp ❯", 60, since=mark)
        assert "loop:" not in repl.buf[mark:]

        mark = repl.send(OBJECTIVE)
        repl.expect(r"loop: the objective states that the tests must pass", 60, since=mark)
        repl.expect(r"▶ attempt 1/3", 60, since=mark)
        repl.expect(r"(not proven|needs you)", 240, since=mark)
        repl.expect("wisp ❯", 60, since=mark)
        out = repl.buf[mark:]
        assert "proven by the harness" not in out
        assert "journal:" in out
        assert (project / "totals.py").read_text() == BUG  # the mock changed nothing, and the loop did not pretend otherwise

        mark = repl.send("/converge status")
        repl.expect(r"latest journal: .*\.jsonl", 30, since=mark)
    finally:
        repl.close()


def test_the_harness_proves_a_fix_by_measuring_the_repository_not_by_believing_the_model(project):
    repl = Repl(project)
    try:
        mark = repl.send(OBJECTIVE)
        repl.expect(r"▶ attempt 1/3", 60, since=mark)
        (project / "totals.py").write_text(FIXED)  # a person fixes it while the loop runs; the mock model did nothing
        repl.expect(r"✓ proven by the harness after [12] attempts?", 240, since=mark)
        repl.expect("wisp ❯", 60, since=mark)
    finally:
        repl.close()


def test_the_kill_switch_restores_the_ordinary_turn(project):
    repl = Repl(project, {"WISP_REPL_CONVERGE": "off"})
    try:
        mark = repl.send(OBJECTIVE)
        repl.expect("wisp ❯", 90, since=mark)
        assert "loop:" not in repl.buf[mark:] and "▶ attempt" not in repl.buf[mark:]
        # the explicit command still works with the switch off
        mark = repl.send("/converge")
        repl.expect("Usage: /converge", 30, since=mark)
    finally:
        repl.close()


def test_a_prompt_with_no_checkable_end_is_refused_by_the_command_and_left_to_the_model_otherwise(project):
    repl = Repl(project)
    try:
        mark = repl.send("/converge make totals.py nicer")
        repl.expect("nothing to prove", 30, since=mark)
        mark = repl.send("make totals.py nicer")
        repl.expect("wisp ❯", 60, since=mark)
        assert "loop:" not in repl.buf[mark:]
    finally:
        repl.close()
