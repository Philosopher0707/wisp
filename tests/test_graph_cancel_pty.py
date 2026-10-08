"""End to end, in a real pty: Ctrl-C during the GRAPH path must interrupt that turn, not kill the REPL.

Field log O-3 (docs/harness/field-observations-2026-10-07.md): a prompt scored into the graph path (`graph path (complex): complexity score 2`), the owner pressed
Ctrl-C, and the REPL died with a traceback and returned to the shell. `_on_sigint` cancels cleanly only when a `_turn_task` exists; the engine path sets one, the graph
path (`coding.handle_prompt` -> `run_coding_template` -> `asyncio.run`) never does, so the handler raised KeyboardInterrupt from inside the signal handler, and
`_run_graph_turn` / `handle_prompt` catch `Exception` only. PR #90 fixed the engine path with the same stalled-provider stub used here."""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from tests.test_turn_cancel_pty import _clean, _drain, _Stalled

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")

# Scores 3 in `coding.decide_strategy` (hints: implement, module, refactor, across); not a question.
GRAPH_PROMPT = b"implement the new auth module and refactor the api across multiple files and add tests.\n"


def _alive(pid: int) -> bool:
    done, _ = os.waitpid(pid, os.WNOHANG)
    return done == 0


def test_the_prompt_really_takes_the_graph_path():
    from wisp import coding

    ctx = coding.task_context_from_prompt(GRAPH_PROMPT.decode().strip(), ".", None)
    assert coding.decide_strategy(ctx).strategy == coding.GRAPH


def test_ctrl_c_during_the_graph_path_interrupts_the_turn_and_the_repl_survives(tmp_path):
    import fcntl
    import pty
    import struct
    import termios

    from tests.test_cli_surface_e2e import cli_env

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Stalled)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    home, ws = tmp_path / "home", tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    env = cli_env(home, {"WISP_PROVIDER": "openai", "WISP_API_BASE": f"http://127.0.0.1:{server.server_port}/v1",
                         "WISP_API_KEY": "test-key", "WISP_MODEL": "qwen2.5-coder", "WISP_EXIT_GRACE_S": "3"})
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(ws)
        os.execvpe(sys.executable, [sys.executable, "-m", "wisp", "repl"], env)
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 110, 0, 0))
        out = _drain(fd, 9)
        os.write(fd, GRAPH_PROMPT)
        out += _drain(fd, 4)                       # the graph run is now stuck on the stalled provider
        assert "graph path" in _clean(out), f"the prompt did not take the graph path: {_clean(out)[-300:]!r}"
        os.write(fd, b"\x03")                      # Ctrl-C
        interrupted_at = time.time()
        seen = b""
        while time.time() - interrupted_at < 8 and b"Turn interrupted" not in seen and b"Traceback" not in seen:
            seen += _drain(fd, 0.5)
        text = _clean(out + seen)
        assert "Traceback" not in text, f"Ctrl-C killed the REPL with a traceback: {text[-600:]!r}"
        assert "Turn interrupted" in text, f"Ctrl-C did not interrupt the graph turn within 8 s: {text[-300:]!r}"
        assert time.time() - interrupted_at < 8
        assert _alive(pid), "the REPL process exited after Ctrl-C"

        os.write(fd, b"/exit\n")
        exited_at = time.time()
        while time.time() - exited_at < 12:
            _drain(fd, 0.5)
            if not _alive(pid):
                pid = 0
                break
        assert pid == 0, "the REPL did not exit within 12 s of /exit"
    finally:
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except OSError:
                pass
        server.shutdown()


def test_a_second_graph_turn_can_be_interrupted_too_and_a_normal_turn_still_works(tmp_path):
    """The handler must stay armed: interrupt, run another graph turn, interrupt again, then the REPL still answers a command."""
    import fcntl
    import pty
    import struct
    import termios

    from tests.test_cli_surface_e2e import cli_env

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Stalled)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    home, ws = tmp_path / "home", tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    env = cli_env(home, {"WISP_PROVIDER": "openai", "WISP_API_BASE": f"http://127.0.0.1:{server.server_port}/v1",
                         "WISP_API_KEY": "test-key", "WISP_MODEL": "qwen2.5-coder", "WISP_EXIT_GRACE_S": "3"})
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(ws)
        os.execvpe(sys.executable, [sys.executable, "-m", "wisp", "repl"], env)
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 110, 0, 0))
        text = _clean(_drain(fd, 9))
        for round_no in (1, 2):
            os.write(fd, GRAPH_PROMPT)
            text += _clean(_drain(fd, 4))
            os.write(fd, b"\x03")
            end = time.time() + 8
            while time.time() < end and text.count("Turn interrupted") < round_no and "Traceback" not in text:
                text += _clean(_drain(fd, 0.5))
            assert "Traceback" not in text, f"round {round_no}: the REPL died: {text[-500:]!r}"
            assert text.count("Turn interrupted") >= round_no, f"round {round_no}: no interruption within 8 s: {text[-300:]!r}"
            assert _alive(pid), f"round {round_no}: the REPL process exited"
        os.write(fd, b"/help\n")
        after = _clean(_drain(fd, 3))
        assert after.strip() and "Traceback" not in after and _alive(pid), "the REPL did not answer a command after two interrupted graph turns"
        os.write(fd, b"/exit\n")
        end = time.time() + 12
        while time.time() < end:
            _drain(fd, 0.5)
            if not _alive(pid):
                pid = 0
                break
        assert pid == 0, "the REPL did not exit within 12 s of /exit"
    finally:
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except OSError:
                pass
        server.shutdown()
