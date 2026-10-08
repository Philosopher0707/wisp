"""The graph path end to end, in a real pty, against a fast local OpenAI-compatible stub: does the graph run to a result, and does the agent carry on after it?

Field log (docs/harness/field-observations-2026-10-07.md): O-2/O-3 (routing, Ctrl-C), and the questions this file answers with a run instead of a reading:

* the graph path runs (`implement -> test -> review`, with correction cycles) and ends with a result line and a trace id;
* the REPL carries on: the next prompt reaches the provider and is answered;
* graph nodes are subagents that run `WispAgentCore.turn` (`multi_agent/_runner.py`), i.e. the same engine, floor and reasoning core as a normal turn;
* TWO GAPS, pinned as strict xfails so the day they are fixed the test says so: (O-19) the graph turn leaves no trace in the conversation the model sees on the
  next turn; (O-20) the headline result line is a success mark even when the graph's own verification says REJECT after its correction cycles are exhausted.
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
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")

GRAPH_PROMPT = b"implement the new auth module and refactor the api across multiple files and add tests.\n"
FOLLOW_UP = b"what did you just change?\n"
ANSWER = "OK. Nothing to change; the request is understood."


class _Provider(BaseHTTPRequestHandler):
    requests: list[dict] = []

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
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        type(self).requests.append({"t": time.time(), "messages": body.get("messages", []), "tools": len(body.get("tools") or [])})
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in ({"choices": [{"index": 0, "delta": {"content": ANSWER}, "finish_reason": None}]},
                          {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": usage}):
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        data = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop"}], "usage": usage}).encode()
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


@dataclass
class Run:
    graph_text: str = ""
    follow_up_text: str = ""
    posts_during_graph: int = 0
    posts_for_follow_up: int = 0
    follow_up_messages: list = field(default_factory=list)
    alive_after: bool = False
    exited: bool = False


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> Run:
    import fcntl
    import pty
    import struct
    import termios

    from tests.test_cli_surface_e2e import cli_env

    _Provider.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = tmp_path_factory.mktemp("pty_e2e")  # not "graph": the follow-up request carries the workspace path
    home, ws = root / "home", root / "ws"
    home.mkdir()
    ws.mkdir()
    (ws / "app.py").write_text("def f():\n    return 1\n")
    env = cli_env(home, {"WISP_PROVIDER": "openai", "WISP_API_BASE": f"http://127.0.0.1:{server.server_port}/v1",
                         "WISP_API_KEY": "test-key", "WISP_MODEL": "qwen2.5-coder", "WISP_EXIT_GRACE_S": "3"})
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(ws)
        os.execvpe(sys.executable, [sys.executable, "-m", "wisp", "repl"], env)
    result = Run()
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 120, 0, 0))
        _drain(fd, 9)
        before = len(_Provider.requests)
        os.write(fd, GRAPH_PROMPT)
        end = time.time() + 150
        while time.time() < end:
            result.graph_text += _clean(_drain(fd, 1.0))
            if "trace:" in result.graph_text or "graph run failed" in result.graph_text or "Traceback" in result.graph_text:
                break
        mid = len(_Provider.requests)
        result.posts_during_graph = mid - before
        time.sleep(1)
        os.write(fd, FOLLOW_UP)
        result.follow_up_text = _clean(_drain(fd, 12))
        result.posts_for_follow_up = len(_Provider.requests) - mid
        if result.posts_for_follow_up:
            result.follow_up_messages = _Provider.requests[-1]["messages"]
        result.alive_after = os.waitpid(pid, os.WNOHANG)[0] == 0
        os.write(fd, b"/exit\n")
        end = time.time() + 12
        while time.time() < end:
            _drain(fd, 0.5)
            if os.waitpid(pid, os.WNOHANG)[0] != 0:
                pid, result.exited = 0, True
                break
        return result
    finally:
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except OSError:
                pass
        server.shutdown()


class TestTheGraphRuns:
    def test_the_prompt_takes_the_graph_path_and_the_run_ends_with_a_result_and_a_trace(self, run):
        assert "graph path" in run.graph_text and "graph complete" in run.graph_text
        assert re.search(r"trace: /graph trace graph-[0-9a-f]+", run.graph_text), run.graph_text[-400:]
        assert "Traceback" not in run.graph_text and "graph run failed" not in run.graph_text

    def test_the_nodes_ran_in_order_through_the_provider(self, run):
        order = re.findall(r"… (implement|test|review) started", run.graph_text)
        assert order[:3] == ["implement", "test", "review"], order
        assert run.posts_during_graph >= 3  # at least one provider round per node

    def test_a_rejected_review_sends_the_work_back_a_bounded_number_of_times(self, run):
        assert 1 <= run.graph_text.count("↻ implement retrying") <= 4 and "correction cycle exhausted" in run.graph_text


class TestTheAgentCarriesOn:
    def test_the_next_prompt_reaches_the_provider_and_is_answered(self, run):
        assert run.posts_for_follow_up >= 1 and ANSWER[:20] in run.follow_up_text
        assert run.alive_after and "Traceback" not in run.follow_up_text

    def test_the_repl_still_exits_cleanly(self, run):
        assert run.exited


class TestKnownGaps:
    """Strict xfails: each records a gap seen on 2026-10-08. When one is fixed the test XPASSes and fails the build, which is the cue to remove the marker."""

    @pytest.mark.xfail(strict=True, reason="O-19: a graph turn is not added to the conversation, so the next turn's model cannot know it happened")
    def test_the_next_turn_can_see_that_a_graph_run_happened_and_what_it_concluded(self, run):
        conversation = [m for m in run.follow_up_messages if m.get("role") != "system"]  # today: the boot-context note and the follow-up question only
        assert "auth module" in json.dumps(conversation).lower() or len(conversation) >= 3

    @pytest.mark.xfail(strict=True, reason="O-20: the headline is a success mark although the graph's own verification says REJECT after the correction cycles")
    def test_the_headline_is_not_a_success_mark_when_verification_rejected(self, run):
        assert "verification: REJECT" in run.graph_text
        headline = next(ln for ln in run.graph_text.splitlines() if "correction cycle exhausted" in ln)
        assert "✓" not in headline
