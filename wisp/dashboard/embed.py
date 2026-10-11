"""Run the dashboard inside a REPL, on a background thread, so it is live next to the session being watched.

The same server and the same defences as `python -m wisp.dashboard serve` (loopback only, GET only); only the way it is started differs. Read-only means
read-only for everything the dashboard does: starting it from a session adds no way to change that session.
"""

from __future__ import annotations

import threading
import urllib.request
from http.server import ThreadingHTTPServer
from typing import Any

import wisp.dashboard.server as server

DEFAULT_PORT = 8765
_TRIES = 11
_state: dict[str, Any] = {"server": None, "thread": None}
_lock = threading.Lock()


def url(port: int) -> str:
    return f"http://127.0.0.1:{port}/#live"


def _serves_dashboard(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1.0) as r:  # noqa: S310 — loopback literal
            return b'"ok"' in r.read(64)
    except (OSError, ValueError):
        return False


def start(port: int | None = None) -> dict[str, Any]:
    """Start (or find) a dashboard. `started` is False when this process already runs one or another process already serves one on a port in range."""
    port = DEFAULT_PORT if port is None else port
    with _lock:
        running = _state["server"]
        if running is not None:
            return {"started": False, "port": running.server_port, "owner": "this session"}
        for candidate in range(port, port + _TRIES):
            if _serves_dashboard(candidate):
                return {"started": False, "port": candidate, "owner": "another process"}
            try:
                srv: ThreadingHTTPServer = server.make_server(candidate)
            except OSError:
                continue
            thread = threading.Thread(target=srv.serve_forever, name="wisp-dashboard", daemon=True)
            thread.start()
            _state["server"], _state["thread"] = srv, thread
            return {"started": True, "port": srv.server_port, "owner": "this session"}
    return {"started": False, "port": None, "owner": None, "error": f"no free port in {port}-{port + _TRIES - 1}"}


def stop() -> bool:
    with _lock:
        srv = _state["server"]
        if srv is None:
            return False
        srv.shutdown()
        srv.server_close()
        _state["server"], _state["thread"] = None, None
        return True


def status() -> dict[str, Any]:
    srv = _state["server"]
    return {"running": srv is not None, "port": srv.server_port if srv is not None else None}
