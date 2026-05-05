#!/usr/bin/env python3
"""Example: Webhook server — stream agent events over HTTP.

This example shows how to build a simple HTTP server that accepts
prompts and streams AgentEvent instances as Server-Sent Events (SSE).

Run:
    python examples/webhook_server.py
    curl -N http://localhost:8000/run?prompt=hello

Use case: Web dashboards, monitoring, custom frontends.
"""

import asyncio
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from wisp import WispAgentCore, WispConfig


class SSEHandler(BaseHTTPRequestHandler):
    """HTTP handler that streams agent events as Server-Sent Events."""

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/run":
            self.send_error(404)
            return

        params = parse_qs(parsed.query)
        prompt = params.get("prompt", [""])[0]
        if not prompt:
            self.send_error(400, "Missing 'prompt' parameter")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        # Create agent
        config = WispConfig()
        config.model = "llama3.2"
        config.workspace = "."
        config.auto_approve = True
        core = WispAgentCore(config=config)

        # Stream events
        async def _stream():
            async for event in core.run(prompt):
                data = json.dumps(event.to_dict(), default=str)
                self.wfile.write(f"data: {data}\n\n".encode())
                self.wfile.flush()

        asyncio.run(_stream())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, format, *args):
        pass  # Suppress logs


def main():
    server = HTTPServer(("localhost", 8000), SSEHandler)
    print("🌐 SSE server running at http://localhost:8000")
    print("   Try: curl -N 'http://localhost:8000/run?prompt=hello'")
    server.serve_forever()


if __name__ == "__main__":
    main()
