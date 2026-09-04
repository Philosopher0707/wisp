# tests/test_trace_otlp.py — tier-gated OTLP/HTTP-JSON export (M5 T3).
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from wisp.trace.otlp import DataTier, export_spans
from wisp.trace.span import Span


class _Sink(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        _Sink.received.append(json.loads(self.rfile.read(length)))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def _server():
    srv = HTTPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _span(**overrides):
    base = {"trace_id": "t1", "span_id": "s1", "kind": "tool_call",
            "name": "read_file", "started_at": 1.0, "finished_at": 2.0,
            "attrs": {"path": "a.py", "content": "secret-free"}}
    base.update(overrides)
    return Span(**base)


def test_metrics_only_strips_attrs():
    srv = _server()
    try:
        _Sink.received.clear()
        n = export_spans([_span()], f"http://127.0.0.1:{srv.server_port}/v1/traces",
                         tier=DataTier.METRICS_ONLY)
        assert n == 1 and len(_Sink.received) == 1
        span = _Sink.received[0]["spans"][0]
        assert span["attrs"] == {}
        assert span["duration_ms"] == 1000.0
    finally:
        srv.shutdown()


def test_metadata_keeps_names_not_args():
    srv = _server()
    try:
        _Sink.received.clear()
        export_spans([_span()], f"http://127.0.0.1:{srv.server_port}/v1/traces",
                     tier=DataTier.METADATA)
        span = _Sink.received[0]["spans"][0]
        assert span["name"] == "read_file" and span["attrs"] == {}
    finally:
        srv.shutdown()


def test_redacted_content_keeps_scrubbed_attrs():
    srv = _server()
    try:
        _Sink.received.clear()
        export_spans([_span(attrs={"path": "a.py",
                                   "token": "ghp_abcdefghijklmnopqrstuvwxyZ1234567890"})],
                     f"http://127.0.0.1:{srv.server_port}/v1/traces",
                     tier=DataTier.REDACTED_CONTENT)
        span = _Sink.received[0]["spans"][0]
        assert "ghp_" not in json.dumps(span["attrs"])
        assert span["attrs"]["path"] == "a.py"
    finally:
        srv.shutdown()


def test_local_only_full_refuses_export():
    from wisp.trace.otlp import ExportRefused
    import pytest
    with pytest.raises(ExportRefused):
        export_spans([_span()], "http://127.0.0.1:1/v1/traces",
                     tier=DataTier.LOCAL_ONLY_FULL)


def test_unreachable_endpoint_reports_zero():
    n = export_spans([_span()], "http://127.0.0.1:1/v1/traces",
                     tier=DataTier.METRICS_ONLY, timeout_s=1)
    assert n == 0
