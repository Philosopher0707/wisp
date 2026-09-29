"""A scripted stand-in for Ollama's /api/chat, for exercising the whole agent pipeline without a model.

It plays a minimal but honest model: on the first request it asks for a structured tool call
(`mcp__net__net_alerts`); on the next it reads the tool result it was sent back and writes its answer
*only from that result* (the first device name and interface name found in it). It records every
request, so a test can assert that the tools were actually offered to the model.

Protocol: newline-delimited JSON, one object per chunk, the last with `"done": true`.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeOllama:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _send(self, chunks: list[dict]) -> None:
                body = ("\n".join(json.dumps(c) for c in chunks) + "\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # model listing / health probes
                body = json.dumps({"models": [{"name": "fake:1b", "model": "fake:1b"}], "version": "0"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if not self.path.endswith("/api/chat"):
                    return self._send([{"done": True}])
                outer.requests.append(payload)
                tool_messages = [m for m in payload.get("messages", []) if m.get("role") == "tool"]
                if not tool_messages:
                    return self._send([
                        {"model": "fake:1b", "message": {"role": "assistant", "content": "", "tool_calls": [
                            {"function": {"name": "mcp__net__net_alerts", "arguments": {}}}]}, "done": False},
                        {"model": "fake:1b", "message": {"role": "assistant", "content": ""}, "done": True,
                         "done_reason": "stop"},
                    ])
                seen = str(tool_messages[-1].get("content", ""))
                device = (re.search(r"\b(?:leaf|spine|core)\d\b", seen) or [None])[0]
                iface = (re.search(r"Ethernet\d+", seen) or [None])[0]
                answer = (f"Per net_alerts the fault is on {device} {iface}. Evidence: {seen[:600]}"
                          if device else "No alerts were returned.")
                return self._send([
                    {"model": "fake:1b", "message": {"role": "assistant", "content": answer}, "done": False},
                    {"model": "fake:1b", "message": {"role": "assistant", "content": ""}, "done": True,
                     "done_reason": "stop"},
                ])

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeOllama":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
