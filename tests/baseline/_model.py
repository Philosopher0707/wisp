"""A scripted, OpenAI-compatible model on localhost, for driving the real `wisp` command.

The latest user message that names a script (`BASELINE{name}`) picks it; the step is the number of
tool results since that message (harness nudges, which arrive as user messages, do not
restart it), so each request gets the script's next action. Every request
is recorded (which script, which tools were offered, the messages), so a test can assert on what
Wisp actually sent as well as on what it did.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_MARK = re.compile(r"BASELINE\{(\w+)\}")

#: name -> actions. ("tool", name, args), ("text", reply), or ("hang", seconds): send the response
#: headers, then nothing (a stalled stream). Past the end, the last text repeats.
Scripts = dict[str, list[tuple]]


class ScriptedModel:
    def __init__(self, scripts: Scripts, fail_first: dict[str, list[int]] | None = None):
        self.scripts = scripts
        #: name -> HTTP statuses returned, in order, before the script answers (a 402, a 429, a 503).
        self.fail_first = {k: list(v) for k, v in (fail_first or {}).items()}
        self.requests: list[dict] = []
        model = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self._json({"object": "list", "data": [{"id": "baseline-model", "object": "model"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                msgs = body.get("messages", [])
                users = [str(m.get("content")) for m in msgs if m.get("role") == "user"]
                hit = next((m for m in (_MARK.search(u) for u in reversed(users)) if m), None)
                name = hit.group(1) if hit else "none"
                # Count from the message that named the script: the harness's own nudges arrive as user messages too.
                start = max((i for i, m in enumerate(msgs) if m.get("role") == "user" and _MARK.search(str(m.get("content")))),
                            default=-1)
                step = sum(1 for m in msgs[start + 1:] if m.get("role") == "tool")
                tools = sorted(t["function"]["name"] for t in body.get("tools") or [])
                model.requests.append({"script": name, "step": step, "tools": tools, "messages": msgs})
                failures = model.fail_first.get(name)
                if failures:
                    code = failures.pop(0)
                    model.requests[-1]["status"] = code
                    data = json.dumps({"error": {"message": f"scripted HTTP {code}", "code": code}}).encode()
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    if code == 429:
                        self.send_header("Retry-After", "1")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                script = model.scripts.get(name, [("text", f"no script {name}")])
                action = script[step] if step < len(script) else script[-1]
                if action[0] == "tool" and step >= len(script):
                    action = ("text", "done")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if action[0] == "hang":
                    self.wfile.flush()
                    time.sleep(action[1])
                    return
                if action[0] == "tool":
                    self._send({"tool_calls": [{"index": 0, "id": f"call_{name}_{step}", "type": "function",
                                                "function": {"name": action[1], "arguments": json.dumps(action[2])}}]})
                    self._send({}, "tool_calls")
                else:
                    self._send({"role": "assistant", "content": action[1]})
                    self._send({}, "stop")
                self.wfile.write(b"data: [DONE]\n\n")

            def _json(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send(self, delta, finish=None):
                chunk = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def of(self, name: str) -> list[dict]:
        return [r for r in self.requests if r["script"] == name]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def wisp_env(home: Path, model: ScriptedModel, **extra: str) -> dict[str, str]:
    """The environment a user's shell would give `wisp`, pointed at the scripted model, nothing inherited
    that could change the result (no real HOME, no keys, no user guidelines)."""
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")}
    repo = str(Path(__file__).resolve().parents[2])
    env = {**keep, "HOME": str(home), "PYTHONPATH": repo, "WISP_PROVIDER": "openai",
           "WISP_API_BASE": model.url, "WISP_API_KEY": "baseline", "WISP_MODEL": "baseline-model",
           "WISP_USER_GUIDELINES": "off", "WISP_SANDBOX": "off", "NO_COLOR": "1", "TERM": "dumb"}
    env.update(extra)
    return env


WISP = [sys.executable, "-P", "-m", "wisp"]
