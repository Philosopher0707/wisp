"""`/review` and `/triage` in the REPL: native commands (so `/help` lists them), and through the real `python -m wisp repl` in a pty."""

from __future__ import annotations

import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.review.helpers import fake_token
from wisp.cli.dispatcher import CommandResult, Dispatcher, ReplContext

REPO = str(Path(__file__).resolve().parents[2])
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a pty")


@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "work"
    work.mkdir()

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=work, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    (work / "app.py").write_text("def load(path):\n    return open(path).read()\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    (tmp_path / "home").mkdir()
    return work


def ctx_for(workspace: Path) -> ReplContext:
    return ReplContext(runtime=None, transport=None, session={"id": "s", "messages": []}, config={"workspace": str(workspace)})


class TestTheDispatcherCommands:
    def test_review_runs_and_emits_the_report(self, repo):
        (repo / "cfg.py").write_text(f"K = '{fake_token('openrouter')}'\n")
        ctx = ctx_for(repo)
        assert Dispatcher().dispatch(ctx, "/review --no-model") is CommandResult.CONSUMED
        text = "\n".join(ctx.out)
        assert "BLOCKED" in text and fake_token("openrouter") not in text

    def test_a_typo_is_an_error_line_and_does_not_exit_the_process(self, repo):
        ctx = ctx_for(repo)
        assert Dispatcher().dispatch(ctx, "/review --bogus") is CommandResult.CONSUMED
        assert "wisp review:" in "\n".join(ctx.out)

    def test_review_help_is_text_not_an_exit(self, repo):
        ctx = ctx_for(repo)
        Dispatcher().dispatch(ctx, "/review --help")
        assert "--staged" in "\n".join(ctx.out)

    def test_triage_without_gh_says_so(self, repo, monkeypatch):
        monkeypatch.setenv("PATH", "/nonexistent")
        ctx = ctx_for(repo)
        assert Dispatcher().dispatch(ctx, "/triage") is CommandResult.CONSUMED
        assert "gh" in "\n".join(ctx.out)

    def test_both_are_listed_by_help(self, repo):
        ctx = ctx_for(repo)
        Dispatcher().dispatch(ctx, "/help")
        text = "\n".join(ctx.out)
        assert "/review" in text and "/triage" in text
        ctx2 = ctx_for(repo)
        Dispatcher().dispatch(ctx2, "/help review")
        assert "usage" in "\n".join(ctx2.out).lower()

    def test_bad_quoting_is_reported(self, repo):
        ctx = ctx_for(repo)
        Dispatcher().dispatch(ctx, '/review "unterminated')
        assert "quot" in "\n".join(ctx.out).lower()


class Repl:
    def __init__(self, cwd: Path, home: Path):
        env = {"PATH": os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""), "HOME": str(home), "TERM": "xterm", "PYTHONPATH": REPO, "WISP_PROVIDER": "mock", "WISP_MODEL": "mock/m"}
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(cwd)
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


def test_the_real_repl_reviews_a_change_and_survives_mistakes(repo):
    (repo / "cfg.py").write_text(f"K = '{fake_token('github')}'\n")
    repl = Repl(repo, repo.parent / "home")
    try:
        mark = repl.send("/review --no-model")
        repl.expect("BLOCKED", 120, since=mark)
        repl.expect("wisp ❯", 60, since=mark)
        assert fake_token("github") not in repl.buf[mark:]

        mark = repl.send("/review --bogus")
        repl.expect("wisp review:", 60, since=mark)
        repl.expect("wisp ❯", 60, since=mark)

        mark = repl.send("/review --lens tests")  # the mock model answers in prose: that is a gap in the review, shown even though the secret already blocks it
        repl.expect("Not fully reviewed", 180, since=mark)
        repl.expect("the reply was not a JSON object", 30, since=mark)
        repl.expect("wisp ❯", 60, since=mark)
        assert ".wisp/wisp.db" not in repl.buf[mark:] and ".agent/runtime.log" not in repl.buf[mark:]

        mark = repl.send("/help review")
        repl.expect("usage", 30, since=mark)
    finally:
        repl.close()
