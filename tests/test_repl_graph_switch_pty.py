"""`WISP_REPL_GRAPH=off` through the real REPL: the multi-part prompt that normally starts a graph is answered by the ordinary agent loop."""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from tests.test_graph_e2e_pty import ANSWER, GRAPH_PROMPT, _clean, _drain, _Provider

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")


def _screen(tmp_path, extra_env: dict[str, str]) -> tuple[str, list[dict]]:
    import pty

    from tests.test_cli_surface_e2e import cli_env

    _Provider.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    home, ws = tmp_path / "home", tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    (ws / "app.py").write_text("def f():\n    return 1\n")
    env = cli_env(home, {"WISP_PROVIDER": "openai", "WISP_API_BASE": f"http://127.0.0.1:{server.server_port}/v1", "WISP_API_KEY": "test-key",
                         "WISP_MODEL": "qwen2.5-coder", "WISP_EXIT_GRACE_S": "3", **extra_env})
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(ws)
        os.execvpe(sys.executable, [sys.executable, "-m", "wisp", "repl"], env)
    screen = ""
    try:
        _drain(fd, 9)
        os.write(fd, GRAPH_PROMPT)
        end = time.time() + 90
        while time.time() < end:
            screen += _clean(_drain(fd, 1.0))
            if ANSWER[:20] in screen or "trace:" in screen or "Traceback" in screen:
                break
        return screen, list(_Provider.requests)
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except OSError:
            pass
        server.shutdown()


def test_with_the_switch_off_the_prompt_is_answered_by_the_agent_loop(tmp_path):
    screen, requests = _screen(tmp_path, {"WISP_REPL_GRAPH": "off"})
    assert "graph path" not in screen and "Traceback" not in screen, screen[-1500:]
    assert ANSWER[:20] in screen, screen[-1500:]
    assert requests and any("implement the new auth module" in str(m.get("content", "")) for m in requests[0]["messages"])


def test_without_the_switch_the_same_prompt_starts_a_graph(tmp_path):
    screen, _ = _screen(tmp_path, {})
    assert "graph path" in screen, screen[-1500:]
