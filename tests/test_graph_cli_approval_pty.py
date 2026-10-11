"""The graph path through the REAL CLI: a worker asks the person at the terminal, and a yes lets it edit the file.

Observed with the real model before this: every graph worker answered `[Denied: edit_file requires a wired ToolExecutor]` (the graph built a bare orchestrator), and
after wiring the orchestrator the next refusal was `NO_APPROVER` (a child had nobody to ask). Here the whole chain runs in a pty: `python -m wisp repl` ->
`handle_prompt` -> graph template -> `SubagentNodeRunner` -> the composition root's orchestrator -> a child turn -> `ToolExecutor` -> the CLI prompt -> the test
types `y` -> the file changes. Only the model is a local stub.
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

PROMPT = b"fix the bug in app.py and the regression in tests, and the broken import\n"
EDIT_ARGS = {"path": "app.py", "old_text": "return 1", "new_text": "return 2"}


class _Provider(BaseHTTPRequestHandler):
    """Edits app.py when a worker is told to implement the change; answers with plain text otherwise (and after any tool result)."""

    posts: list[dict] = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        data = json.dumps({"data": [{"id": "qwen2.5-coder"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _wants_edit(self, messages: list[dict]) -> bool:
        if any(m.get("role") == "tool" for m in messages):
            return False
        last_user = next((m for m in reversed(messages) if m.get("role") == "user"), {})
        return "implement the change" in str(last_user.get("content", ""))

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        messages = body.get("messages", [])
        type(self).posts.append({"messages": messages})
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if self._wants_edit(messages):
            call = {"index": 0, "id": "call_1", "type": "function", "function": {"name": "edit_file", "arguments": json.dumps(EDIT_ARGS)}}
            chunks = [{"choices": [{"index": 0, "delta": {"tool_calls": [call]}, "finish_reason": None}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}], "usage": usage}]
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": call["function"]}]}
            finish = "tool_calls"
        else:
            chunks = [{"choices": [{"index": 0, "delta": {"content": "Done."}, "finish_reason": None}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": usage}]
            message = {"role": "assistant", "content": "Done."}
            finish = "stop"
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        data = json.dumps({"choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _clean(raw: bytes) -> str:
    return re.sub(rb"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07", b"", raw).decode("utf-8", "replace")


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


GRAPH_YAML = """version: "1"
graph:
  id: one-fix
  entry: fix
  nodes:
    fix:
      type: agent
      allowed_tools: [read_file, edit_file]
      responsibility: fix
      prompt: implement the change in app.py
  edges: []
"""


def _session(tmp_path, answer: bytes | None, argv: list[str] | None = None, done: str = "trace:", line: bytes = PROMPT):
    """Run `wisp` (the REPL, or `argv` from the shell) on a stub model, answer every approval prompt with `answer` (None = never answer), and return (screen text, app.py)."""
    import pty

    from tests.test_cli_surface_e2e import cli_env

    _Provider.posts = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    home, ws = tmp_path / "home", tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    (ws / "app.py").write_text("def f():\n    return 1\n")
    (ws / "one-fix.yaml").write_text(GRAPH_YAML)
    env = cli_env(home, {"WISP_PROVIDER": "openai", "WISP_API_BASE": f"http://127.0.0.1:{server.server_port}/v1", "WISP_API_KEY": "test-key",
                         "WISP_MODEL": "qwen2.5-coder", "WISP_EXIT_GRACE_S": "3"})
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(ws)
        os.execvpe(sys.executable, [sys.executable, "-m", "wisp", *(argv or ["repl"])], env)
    screen, answered = "", 0
    try:
        if argv is None:
            _drain(fd, 9)
            os.write(fd, line)
        end = time.time() + 150
        while time.time() < end:
            screen += _clean(_drain(fd, 0.5))
            if answer is not None and screen.count("[y] yes") > answered:
                answered = screen.count("[y] yes")
                os.write(fd, answer)
            if done in screen or "graph run failed" in screen or "Traceback" in screen:
                break
        screen += _clean(_drain(fd, 1.0))
        return screen, (ws / "app.py").read_text()
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except OSError:
            pass
        server.shutdown()


def test_a_yes_at_the_terminal_lets_the_graph_worker_edit_the_file(tmp_path):
    screen, app = _session(tmp_path, b"y")
    assert "graph path (repair)" in screen, screen[-1500:]
    assert "subagent 'implement' is asking" in screen, screen[-1500:]  # the person is told whose request it is
    assert "return 2" in app, (app, screen[-1500:])


def test_a_no_leaves_the_file_as_it_was(tmp_path):
    screen, app = _session(tmp_path, b"n")
    assert "subagent 'implement' is asking" in screen, screen[-1500:]
    assert app == "def f():\n    return 1\n", (app, screen[-1500:])


def test_wisp_graph_run_from_the_shell_asks_the_same_way(tmp_path):
    """`wisp graph run` starts no REPL; it builds the same wired workers, with the terminal as the approver."""
    screen, app = _session(tmp_path, b"y", argv=["graph", "run", "one-fix.yaml", "{}"], done="wall ")
    assert "subagent 'fix' is asking" in screen, screen[-1500:]
    assert "return 2" in app, (app, screen[-1500:])


def test_wisp_graph_run_from_the_shell_without_an_answer_changes_nothing(tmp_path):
    screen, app = _session(tmp_path, b"n", argv=["graph", "run", "one-fix.yaml", "{}"], done="wall ")
    assert app == "def f():\n    return 1\n", (app, screen[-1500:])


def test_slash_graph_run_inside_the_repl_asks_through_the_repls_own_orchestrator(tmp_path):
    """`/graph run` must reuse the REPL's wired workers (and the approver already given to them), not build a second root."""
    screen, app = _session(tmp_path, b"y", done="wall ", line=b"/graph run one-fix.yaml {}\n")
    assert "return 2" in app, (app, screen[-1500:])
