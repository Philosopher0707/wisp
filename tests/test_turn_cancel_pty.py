"""End to end, in a real pty: Ctrl-C during a turn that is stuck on a stalled provider must cancel it at once.

The provider is a local HTTP stub that accepts the request and then says nothing for a minute (a stalled upstream). Before the fix the
REPL printed "Interrupted — cancelling turn…" and then kept waiting until the provider answered: the SIGINT handler's bare
`task.cancel()` never woke the event loop. Now the turn is cancelled within a second and the REPL is back at its prompt, and `/exit`
leaves promptly even though the stalled request is still blocked in its (daemon) thread.
"""
from __future__ import annotations

import json
import os
import re
import select
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")


class _Stalled(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):  # the model listing
        data = json.dumps({"data": [{"id": "qwen2.5-coder"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        time.sleep(60)  # a stalled upstream


def _drain(fd: int, secs: float) -> bytes:
    buf, end = b"", time.time() + secs
    while time.time() < end:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
    return buf


def _clean(raw: bytes) -> str:
    return re.sub(rb"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07", b"", raw).decode("utf-8", "replace")


def test_ctrl_c_cancels_a_turn_stuck_on_a_stalled_provider_and_the_repl_exits_promptly(tmp_path):
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
        os.write(fd, b"hello there\n")
        out += _drain(fd, 3)                       # the turn is now stuck on the stalled provider
        os.write(fd, b"\x03")                      # Ctrl-C
        interrupted_at = time.time()
        seen = b""
        while time.time() - interrupted_at < 6 and b"Turn interrupted" not in seen:
            seen += _drain(fd, 0.5)
        text = _clean(out + seen)
        assert "Turn interrupted" in text, ("Ctrl-C did not cancel the turn within 6 s; the REPL is still waiting on the "
                                            f"provider. Last output: {text[-300:]!r}")
        assert time.time() - interrupted_at < 5

        os.write(fd, b"/exit\n")
        exited_at = time.time()
        while time.time() - exited_at < 10:
            _drain(fd, 0.5)
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done:
                pid = 0
                break
        assert pid == 0, "the REPL did not exit within 10 s of /exit"
    finally:
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except OSError:
                pass
        server.shutdown()
