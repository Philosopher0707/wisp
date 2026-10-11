"""The dashboard's HTTP server: loopback only, GET/HEAD only, no write endpoint, no outbound request.

Defences, each with a test: it binds 127.0.0.1 and has no option to bind anything else; a request whose Host header is not this server's own is refused (a page on
another site cannot read it through DNS rebinding); every response says `no-store`, `nosniff`, a CSP that allows only this page's own script, and no referrer; any method
but GET/HEAD is 405; only whitelisted `/api/<section>` names are served, so no path from the request ever reaches the filesystem.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import wisp.dashboard.bench as bench
import wisp.dashboard.collect as collect
import wisp.dashboard.live as live
import wisp.dashboard.ui as ui

SECTIONS = ("overview", "live", "bench", "usage", "runtime", "harness", "overhead", "findings", "learning")
_TTL_S = 8.0
_TTL_BY_SECTION = {"live": 2.0}  # what is running changes in seconds; the databases behind the other sections do not


def alerts(sections: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """What a person should look at first, from the sections' own numbers. Each alert names the evidence, never a guess about the cause."""
    out: list[dict[str, str]] = []
    b, u, h, learn = sections["bench"], sections["usage"], sections["harness"], sections["learning"]
    scored_groups = [g for g in b["groups"] if g["summary"]["scored"] > 0]
    if not scored_groups:
        out.append({"level": "critical", "text": "There is no measured accuracy yet: no benchmark attempts have been recorded. Without that number, every other panel is anecdote.", "where": "accuracy"})
    for g in scored_groups:
        s = g["summary"]
        if s["infra_rate"] and s["infra_rate"] > 0.3:
            out.append({"level": "warn", "text": f"{g['group']}: {round(s['infra_rate'] * 100)}% of attempts hit infrastructure trouble (rate limits, timeouts); the scored runs are a biased sample.", "where": "accuracy"})
        if s["scored"] < 10:
            out.append({"level": "info", "text": f"{g['group']}: only {s['scored']} scored runs; the interval is {round(s['ci_low'] * 100)}% to {round(s['ci_high'] * 100)}%.", "where": "accuracy"})
        if s["overclaims"]:
            out.append({"level": "warn", "text": f"{g['group']}: {s['overclaims']} time(s) the agent reported success on work that was not solved.", "where": "accuracy"})
    for c in b["comparisons"]:
        if c["significant"]:
            out.append({"level": "info", "text": f"{c['model']}: '{c['a']}' vs '{c['b']}' differ by {round(c['diff'] * 100)} points (95% interval {round(c['low'] * 100)} to {round(c['high'] * 100)}).", "where": "accuracy"})
    if any(r["in_progress"] for r in b["runs"]):
        out.append({"level": "info", "text": "A benchmark run is in progress; numbers will move.", "where": "accuracy"})
    if learn.get("facts") == 0 and any((bk.get("facts") or 0) > 0 for bk in learn.get("backups", [])):
        best = max((bk.get("facts") or 0) for bk in learn["backups"])
        out.append({"level": "warn", "text": f"The fact store is empty but a backup holds {best} facts: what the agent learned was lost.", "where": "learning"})
    rem = learn.get("remember", {})
    if rem.get("calls", 0) >= 10 and rem["error"] / rem["calls"] > 0.3:
        out.append({"level": "warn", "text": f"`remember` failed {rem['error']} of {rem['calls']} times: the agent tries to learn and cannot.", "where": "learning"})
    if (learn.get("session_summaries") or 0) >= 100:
        out.append({"level": "info", "text": "The session-summary store is at its 100-row cap: older sessions are being dropped.", "where": "learning"})
    stuck = u.get("graph_runs", {}).get("running", 0)
    if stuck:
        out.append({"level": "warn", "text": f"{stuck} graph run(s) are recorded as 'running'; one that never finished (for example after a crash) stays that way.", "where": "usage"})
    tc = u.get("turn_completion", {})
    if tc.get("ratio") is not None and tc["ratio"] < 0.8:
        out.append({"level": "info", "text": f"Only {round(tc['ratio'] * 100)}% of user messages ended with a `done` event; the rest ended in an error, an interruption or a crash.", "where": "usage"})
    br = u.get("background_runs", {})
    if br.get("failed", 0) and br["failed"] / max(1, sum(br.values())) > 0.2:
        out.append({"level": "info", "text": f"{br['failed']} of {sum(br.values())} background runs failed.", "where": "usage"})
    disk = h.get("disk") or {}
    if disk.get("free_gb") is not None and disk["free_gb"] < 1:
        out.append({"level": "critical", "text": f"The disk has {disk['free_gb']} GB free ({disk['used_pct']}% used). Writes fail, so tests error and benchmark runs look like the agent doing nothing.", "where": "harness"})
    elif disk.get("free_gb") is not None and disk["free_gb"] < 5:
        out.append({"level": "warn", "text": f"The disk has only {disk['free_gb']} GB free.", "where": "harness"})
    if h.get("dirty"):
        out.append({"level": "info", "text": "The checkout shown has uncommitted changes; the flags and findings may not match any commit.", "where": "harness"})
    order = {"critical": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda a: order.get(a["level"], 3))  # stable: within a level, the order the checks ran in


class Dashboard:
    """Computes sections on demand with a short cache, so a page that polls every few seconds does not rescan every database each time."""

    def __init__(self, bench_dir: Path, workspaces: list[Path] | None, root: Path, clock: Callable[[], float] = time.monotonic) -> None:
        self.bench_dir, self._fixed, self.root, self._clock = bench_dir, workspaces, root, clock
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._ws: tuple[float, list[Path]] = (-1e9, [])

    def workspaces(self) -> list[Path]:
        if self._fixed:
            return self._fixed
        now = self._clock()
        if now - self._ws[0] > 60:
            self._ws = (now, collect.find_workspaces())
        return self._ws[1]

    def _compute(self, name: str) -> dict[str, Any]:
        ws = self.workspaces()
        if name == "live":
            return live.live_data(ws)
        if name == "bench":
            return collect.bench_data(self.bench_dir)
        if name == "usage":
            return collect.usage_data(ws)
        if name == "runtime":
            return collect.runtime_log_data(ws)
        if name == "harness":
            return collect.harness_data(self.root)
        if name == "overhead":
            return collect.overhead_data(ws[0] if ws else Path.cwd())
        if name == "findings":
            return collect.findings_data(self.root)
        if name == "learning":
            return collect.learning_data(ws)
        if name == "overview":
            secs = {k: self.get(k) for k in ("bench", "usage", "harness", "learning", "findings", "overhead")}
            running = self.get("live")
            return {"alerts": alerts(secs), "generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "workspaces": [collect.tilde(w) for w in ws],
                    "headline": [{"group": g["group"], "model": g["model"], "label": g["label"], "summary": g["summary"], "trust": g["trust"], "harness_shas": g["harness_shas"]}
                                 for g in secs["bench"]["groups"]],
                    "in_progress": [r for r in secs["bench"]["runs"] if r["in_progress"]],
                    "running": running.get("running", 0), "findings": secs["findings"]["counts"], "sha": secs["harness"].get("sha"), "dirty": secs["harness"].get("dirty"),
                    "bench_command": "python -m wisp.dashboard bench --label default --repeats 3   (model, provider and base URL come from WISP_MODEL, WISP_PROVIDER, WISP_API_BASE; --model overrides)"}
        raise KeyError(name)

    def get(self, name: str) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            hit = self._cache.get(name)
            if hit and now - hit[0] < _TTL_BY_SECTION.get(name, _TTL_S):
                return hit[1]
        try:
            value = self._compute(name)
        except Exception as exc:  # noqa: BLE001 — one broken source must not blank the page
            value = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            if name == "overview":
                value.update(alerts=[{"level": "warn", "text": f"overview could not be computed: {value['error']}", "where": "overview"}], headline=[], in_progress=[], findings={}, workspaces=[])
        with self._lock:
            self._cache[name] = (now, value)
        return value


def make_handler(dash: Dashboard, port_ref: Callable[[], int]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "WispDashboard"

        def log_message(self, *a: Any) -> None:  # quiet by default: the access log would print every poll
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip().lower()
            port = port_ref()
            return host in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")

        def _send(self, status: int, body: bytes, ctype: str, nonce: str = "") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            if nonce:
                self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload, default=str).encode("utf-8"), "application/json; charset=utf-8")

        def _route(self) -> None:
            if not self._host_ok():
                self._json(403, {"error": "forbidden host"})
                return
            path = self.path.split("?", 1)[0]
            if path == "/":
                nonce = secrets.token_urlsafe(16)
                self._send(200, ui.render_index(nonce).encode("utf-8"), "text/html; charset=utf-8", nonce)
            elif path == "/healthz":
                self._json(200, {"ok": True})
            elif path.startswith("/api/") and path[5:] in SECTIONS:
                self._json(200, dash.get(path[5:]))
            else:
                self._json(404, {"error": "not found"})

        def do_GET(self) -> None:  # noqa: N802
            self._route()

        def do_HEAD(self) -> None:  # noqa: N802
            self._route()

        def _refuse(self) -> None:
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse  # noqa: N815

    return Handler


def make_server(port: int = 8765, bench_dir: Path | None = None, workspaces: list[Path] | None = None, root: Path | None = None) -> ThreadingHTTPServer:
    dash = Dashboard(bench_dir or bench.default_results_dir(), workspaces or None, root or Path(__file__).resolve().parents[2])
    holder: list[ThreadingHTTPServer] = []
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(dash, lambda: holder[0].server_port))
    server.daemon_threads = True
    holder.append(server)
    return server


def serve(port: int = 8765, bench_dir: Path | None = None, workspaces: list[Path] | None = None) -> int:
    try:
        server = make_server(port, bench_dir, workspaces)
    except OSError as exc:
        print(f"cannot listen on 127.0.0.1:{port}: {exc}")
        return 1
    print(f"Wisp dashboard: http://127.0.0.1:{server.server_port}  (read-only, local only; Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
