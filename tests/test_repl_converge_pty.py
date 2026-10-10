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


def test_the_loop_announces_keep_or_revert_and_the_switch_removes_it(project, tmp_path_factory):
    repl = Repl(project)
    try:
        mark = repl.send(OBJECTIVE)
        repl.expect(r"keep-or-revert: an attempt that does not improve the measurement is undone", 60, since=mark)
        repl.expect(r"(not proven|needs you)", 240, since=mark)
        repl.expect("wisp ❯", 60, since=mark)
    finally:
        repl.close()
    other = tmp_path_factory.mktemp("off")
    for name in ("totals.py", "pytest.ini"):
        (other / name).write_text((project / name).read_text())
    (other / "tests").mkdir()
    (other / "tests" / "test_totals.py").write_text(TEST)
    off = Repl(other, {"WISP_REPL_CONVERGE_REVERT": "off"})
    try:
        mark = off.send(OBJECTIVE)
        off.expect(r"▶ attempt 1/3", 60, since=mark)
        off.expect(r"(not proven|needs you)", 240, since=mark)
        assert "keep-or-revert" not in off.buf[mark:] and "↩" not in off.buf[mark:]
    finally:
        off.close()


def test_an_attempt_that_changes_files_without_moving_the_measurement_is_undone_in_the_real_repl(project):
    """The mock model cannot edit, so a writer thread stands in for 'the attempt changed files and the tests still fail'. A concurrent writer is, by design, indistinguishable from the attempt: the loop owns the workspace while it runs."""
    import threading

    stop = threading.Event()

    def noise():
        i = 0
        while not stop.is_set():
            (project / f"noise-{i % 40}.txt").write_text(f"{i}\n")
            i += 1
            time.sleep(0.02)

    writer = threading.Thread(target=noise, daemon=True)
    repl = Repl(project)  # fork the REPL before any helper thread exists: forkpty in a multi-threaded process can deadlock the child
    try:
        writer.start()
        mark = repl.send(OBJECTIVE)
        repl.expect(r"↩ reverted: the attempt did not improve the measurement; put back \d+ file\(s\)", 240, since=mark)
        repl.expect(r"(not proven|needs you)", 240, since=mark)
        stop.set()
        repl.expect("wisp ❯", 60, since=mark)
        out = repl.buf[mark:]
        assert "the attempt's own version is kept in" in out and ".discarded" in out
        assert (project / "totals.py").read_text() == BUG
        kept = list((project / ".wisp" / "converge").glob("*.discarded/attempt-1/noise-*.txt"))
        assert kept, "the attempt's own files were not kept aside"
    finally:
        stop.set()
        if writer.is_alive():
            writer.join(timeout=5)
        repl.close()
