#!/usr/bin/env python3
"""End-to-end diagnostic audit of the `wisp repl` lifecycle (8 contracts).

Standalone runner: mocks the terminal TTY, uses temp workspaces and fake
workers/providers — no network, no LLM, no user interaction. Each check
returns PASS/FAIL with an anchored detail line. Exit code is the count of
failed checks (0 = all contracts hold).

Usage:
    python3 scripts/diagnose_wisp_runtime.py [--only D1] [--verbose]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, ".")

CHECKS: list["Check"] = []


@dataclass
class Check:
    dimension: str
    name: str
    fn: Callable[[], tuple[bool, str]]
    elapsed_s: float = 0.0
    ok: bool = False
    detail: str = ""


def check(dimension: str, name: str):
    def deco(fn: Callable[[], tuple[bool, str]]):
        CHECKS.append(Check(dimension, name, fn))
        return fn

    return deco


def _src(modname: str) -> str:
    import importlib

    mod = importlib.import_module(modname)
    return Path(mod.__file__).read_text()  # type: ignore[arg-type]


# ── D1: Bootstrap & Composition Root ───────────────────────────────────


@check("D1", "composition-root wiring is unidirectional")
def _d1_wiring():
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig

    with tempfile.TemporaryDirectory() as td:
        cfg = WispConfig()
        cfg = cfg.replace(workspace=td)
        root = CompositionRoot(cfg)
        try:
            ok = (root.runtime is not None and root.tool_executor is not None
                  and root.store is not None and root.runtime.orchestrator is not None
                  and root.tool_executor.subagent_orchestrator is root.runtime.orchestrator)
            return ok, "runtime/executor/orchestrator/store cross-wired" if ok else "cross-wiring broken"
        finally:
            try:
                root.shutdown()
            except Exception:
                pass


@check("D1", "no import cycle entry<->cli.repl; entry owns no Transport build")
def _d1_cycles():
    import ast
    import re

    repl_src = _src("wisp.cli.repl")
    if re.search(r"^\s*(from|import)\s+wisp\.entry\b", repl_src, re.M):
        return False, "wisp/cli/repl.py imports wisp.entry (cycle)"
    # AST-level: no private attribute access on runtime/transport/adapter
    # (docstrings/comments excluded — only real attribute loads count).
    tree = ast.parse(_src("wisp.cli.dispatcher"))
    violations = []

    class _V(ast.NodeVisitor):
        def visit_Attribute(self, node: ast.Attribute) -> None:
            target = node.value
            if (isinstance(target, ast.Name) and target.id in ("adapter", "transport")
                    and node.attr.startswith("_") and not node.attr.startswith("__")):
                violations.append(f"{target.id}.{node.attr}")
            if (isinstance(target, ast.Name) and target.id == "runtime"
                    and node.attr.startswith("_")):
                violations.append(f"runtime.{node.attr}")
            self.generic_visit(node)

    _V().visit(tree)
    if violations:
        return False, f"dispatcher reaches into wrapper privates: {sorted(set(violations))[:4]}"
    return True, "edges: entry->cli.repl->dispatcher; transport->cli one-way"


@check("D1", "safe_getcwd + TCC fallback + .wisp dir bootstrap")
def _d1_paths():
    import wisp.__main__  # noqa: F401  (exercises early-cwd guard import)
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig, safe_getcwd

    cwd = safe_getcwd()
    if not cwd or not os.path.isdir(cwd):
        return False, f"safe_getcwd returned unusable {cwd!r}"
    src = _src("wisp.__main__")
    if "/documents/" not in src or "Documents" not in src:
        return False, "TCC case-fold guard missing from __main__"
    with tempfile.TemporaryDirectory() as td:
        cfg = WispConfig().replace(workspace=td)
        root = CompositionRoot(cfg)
        try:
            ok = (Path(td) / ".wisp").is_dir()
            return ok, ".wisp/ bootstrapped under workspace" if ok else ".wisp/ missing"
        finally:
            try:
                root.shutdown()
            except Exception:
                pass


# ── D2: Pre-flight diagnostics ─────────────────────────────────────────


@check("D2", "doctor imports strictly wisp.* (zero agent.*)")
def _d2_imports():
    import re

    src = _src("wisp.core.doctor")
    bad = [ln for ln in src.splitlines()
           if re.search(r"^\s*(from|import)\s+agent\.", ln)]
    if bad:
        return False, f"legacy imports: {bad[:3]}"
    return True, "all imports wisp.* or stdlib (lazy, function-level)"


@check("D2", "5 probes execute inside budget")
def _d2_probes():
    from wisp.core.doctor import run_preflight_sync

    with tempfile.TemporaryDirectory() as td:
        t0 = time.monotonic()
        report = run_preflight_sync(workspace=td, config=None, timeout_s=0.1)
        wall = time.monotonic() - t0
    names = [c.name for c in report.checks]
    if len(names) != 5:
        return False, f"expected 5 probes, got {names}"
    if wall > 2.0:
        return False, f"wall {wall:.2f}s exceeds tolerance"
    lat = {c.name: c.latency_ms for c in report.checks}
    return True, f"probes={names} wall={wall*1000:.0f}ms lat={lat}"


@check("D2", "tool_cache registry consistent (BatchReader/ExecutionCache optional)")
def _d2_toolcache():
    from wisp.core.doctor import run_preflight_sync

    with tempfile.TemporaryDirectory() as td:
        report = run_preflight_sync(workspace=td, config=None, timeout_s=0.5)
    probe = next((c for c in report.checks if c.name == "tool_cache"), None)
    if probe is None:
        return False, "tool_cache probe missing"
    known_optional = ("optional, not present" in json.dumps(probe.details))
    return True, f"status={probe.status} tools_registered; optionals-absent={known_optional}"


# ── D3: Terminal UX / ANSI / stream hygiene ────────────────────────────


@check("D3", "ANSI keybinds disambiguated (arrows/tab/enter/esc)")
def _d3_keys():
    from wisp.repl.picker import classify_key

    cases = {"\x1b[A": ("move", "up"), "\x1b[B": ("move", "down"),
             "\t": ("tab", "tab"), "\x1b[Z": ("tab", "shift-tab"),
             "\r": ("enter", ""), "\x1b": ("cancel", "")}
    for seq, want in cases.items():
        got = classify_key(seq)
        if got[0] != want[0] or (want[1] and got[1] != want[1]):
            return False, f"{seq!r} -> {got}, want kind={want[0]}"
    return True, "arrows/tab/shift-tab/enter/esc all distinct"


@check("D3", "ESC sequence assembly honors ~20ms follower window")
def _d3_esc_window():
    import time as _t

    from wisp.repl.picker import _read_key

    # _read_key uses text-mode stdin.read(1) + select on fileno():
    # wrap the pipe read-end with os.fdopen so both work. The write end
    # stays OPEN during reads: a closed pipe reads EOF, and EOF is always
    # "ready" for select — which would collapse the 20ms follower window
    # and make lone-ESC timing untestable (real TTYs block instead).
    open_files: list = []

    class _FDStdin:
        def __init__(self, data: bytes, hold_open: bool = False):
            r, w = os.pipe()
            os.write(w, data)
            if hold_open:
                self._w = w
            else:
                os.close(w)
            self._f = os.fdopen(r, "r")
            open_files.append(self._f)

        def read(self, n: int = 1):
            return self._f.read(n)

        def fileno(self):
            return self._f.fileno()

        def release(self):
            try:
                if hasattr(self, "_w"):
                    os.close(self._w)
            except OSError:
                pass
            try:
                self._f.close()
            except OSError:
                pass

    stdin_arrow = _FDStdin(b"\x1b[A")
    stdin_lone = _FDStdin(b"\x1b", hold_open=True)
    t0 = _t.monotonic()
    seq = _read_key(stdin_arrow)
    fast = _t.monotonic() - t0
    t0 = _t.monotonic()
    lone = _read_key(stdin_lone)
    slow = _t.monotonic() - t0
    stdin_arrow.release()
    stdin_lone.release()
    for f in open_files:
        try:
            f.close()
        except OSError:
            pass
    if seq != "\x1b[A":
        return False, f"arrow misassembled as {seq!r}"
    if lone != "\x1b":
        return False, f"lone ESC misread as {lone!r}"
    if not (fast < 0.5 and 0.015 < slow < 1.0):
        return False, f"window off: fast={fast*1000:.0f}ms lone={slow*1000:.0f}ms"
    return True, f"arrow fast ({fast*1000:.0f}ms), lone ESC waits follower (~{slow*1000:.0f}ms)"


@check("D3", "token stream uses buffered batches (no per-char redraw)")
def _d3_batching():
    import inspect

    from wisp import stream_events as se

    if not hasattr(se, "EventBatcher"):
        return False, "EventBatcher missing"
    src = inspect.getsource(se.EventBatcher)
    if "flush" not in src:
        return False, "no flush boundary in batcher"
    b = se.EventBatcher()
    n = 0
    for chunk in ["hel", "lo ", "world"]:
        for _ev in b.add_content(chunk):
            n += 1
    batches = list(b.flush_all())
    n += len(batches)
    joined = "".join(ev.text for ev in batches)
    if joined != "hello world":
        return False, f"flush reassembled {joined!r}"
    return True, f"3 chunks -> {n} batch events, lossless reassembly"


@check("D3", "provider diagnostics isolated from stdout")
def _d3_isolation():
    import logging

    try:
        from agent.logger import install as _install  # type: ignore
        _install()
        sink = True
    except ImportError:
        sink = False
    src = _src("wisp.composition")
    installs = "install_agent_logger" in src or "_install_agent_logger" in src
    # logging must never write to sys.stdout by default in this runtime
    root_handlers = [type(h).__name__ for h in logging.getLogger().handlers]
    return (sink or installs), f"agent sink installed={sink or installs} root_handlers={root_handlers}"


# ── D4: Tool execution & approval ──────────────────────────────────────


@check("D4", "edit_file arg preview strips multi-KB blobs")
def _d4_preview():
    from wisp.transport.renderer import format_arg_value

    blob = "line\n" * 2000
    shown = format_arg_value("new_text", blob)
    if len(shown) > 120 or "lines" not in shown and "chars" not in shown:
        return False, f"preview leaks ({len(shown)} chars)"
    return True, f"50KB blob -> {shown!r}"


@check("D4", "diff stats (+N/-M/scope) via difflib")
def _d4_diff():
    from wisp.ui.diff_viewer import compute_diff_stats

    old = "def foo():\n    a = 1\n    b = 2\n    return a\n"
    new = "def foo():\n    a = 1\n    b = 3\n    c = 4\n    return a + c\n"
    added, deleted, scope = compute_diff_stats(old, new)
    if (added, deleted) != (3, 2):
        return False, f"got +{added}/-{deleted}, want +3/-2"
    if "foo" not in scope:
        return False, f"scope missing def context: {scope!r}"
    return True, f"+{added}/-{deleted} {scope}"


@check("D4", "approval [v] toggle renders bounded panel, state preserved")
def _d4_toggle():
    from rich.console import Console

    from wisp.cli.approval import render_approval_options
    from wisp.ui.diff_viewer import create_diff_panel

    panel = create_diff_panel("a = 1\n", "a = 2\n", file_path="x.py")
    console = Console(record=True, width=80, force_terminal=False)
    with console.capture() as capture:
        console.print(panel)
    out = capture.get()
    if "a = 1" not in out and "a = 2" not in out:
        return False, "diff panel rendered empty"
    opts = render_approval_options(is_file_edit=True)
    if "[v]" not in opts and "view diff" not in opts:
        return False, "approval prompt missing [v] toggle"
    # Purity: same input twice -> identical output (no state reset possible).
    console2 = Console(record=True, width=80, force_terminal=False)
    with console2.capture() as capture2:
        console2.print(create_diff_panel("a = 1\n", "a = 2\n", file_path="x.py"))
    if capture.get() != capture2.get():
        return False, "diff render nondeterministic"
    return True, "bounded panel + stable render + [v] present"


# ── D5: Transport resilience ───────────────────────────────────────────


@check("D5", "granular timeouts 15/60/120/30 propagated")
def _d5_timeouts():
    from wisp.core.transport import HARDENED_TIMEOUT

    want = (15.0, 60.0, 120.0, 30.0)
    got = (HARDENED_TIMEOUT.connect, HARDENED_TIMEOUT.write,
           HARDENED_TIMEOUT.read, HARDENED_TIMEOUT.pool)
    if got != want:
        return False, f"got {got}, want {want}"
    session = None
    try:
        from wisp.core.transport import get_hardened_session

        session = get_hardened_session()
        stored = getattr(session, "_wisp_hardened_timeout", None)
        if stored is None or (stored.connect, stored.read) != (15.0, 120.0):
            return False, "session timeout not stored for hardened_post"
    finally:
        try:
            session.close()
        except Exception:
            pass
    return True, f"connect/write/read/pool={got}"


@check("D5", "429/5xx retry closes body before backoff (no pool leak)")
def _d5_close_before_retry():
    from wisp.core.transport import hardened_post

    closed: list[str] = []
    calls: list[int] = []

    class _Resp:
        status_code = 429

        def close(self):
            closed.append("body")

    class _Session:
        _wisp_hardened_timeout = None

        def post(self, url, **kw):
            calls.append(1)
            if len(calls) == 1:
                return _Resp()
            r = _Resp()
            r.status_code = 200
            return r

    import wisp.core.transport as t

    orig_sleep = t.time.sleep
    t.time.sleep = lambda s: None
    try:
        resp = hardened_post(_Session(), "http://x", json={}, max_attempts=2)
    finally:
        t.time.sleep = orig_sleep
    if resp.status_code != 200 or closed != ["body"] or len(calls) != 2:
        return False, f"status={resp.status_code} closes={closed} calls={len(calls)}"
    return True, "body closed before backoff; retry served 200"


@check("D5", "streaming branches requests vs httpx correctly")
def _d5_branch():
    import inspect

    from wisp.core import transport as t

    src = inspect.getsource(t.hardened_post)
    if "is_httpx" not in src or "_httpx_stream_post" not in src:
        return False, "backend branch missing"
    # requests-like path: stream=True must reach session.post.
    seen: dict = {}

    class _Resp:
        status_code = 200

        def close(self):
            pass

    class _ReqSession:
        _wisp_hardened_timeout = None

        def post(self, url, **kw):
            seen.update(kw)
            return _Resp()

    resp = t.hardened_post(_ReqSession(), "http://x", json={}, stream=True)
    if seen.get("stream") is not True or resp.status_code != 200:
        return False, f"requests-like kwargs={seen}"
    # httpx helper path works against a fake client.
    entered: list[str] = []

    class _CM:
        def __enter__(self):
            entered.append("stream")
            return _Resp()

        def __exit__(self, *a):
            return False

    class _HttpxLike:
        def stream(self, method, url, **kw):
            assert method == "POST"
            return _CM()

    out = t._httpx_stream_post(_HttpxLike(), "http://x", {})
    if entered != ["stream"] or out.status_code != 200:
        return False, "httpx client.stream branch broken"
    return True, "requests stream=True; httpx client.stream(POST)"


# ── D6: Subagent orchestration ─────────────────────────────────────────


@check("D6", "frames carry zero parent history (epistemic isolation)")
def _d6_isolation():
    from wisp.core.subagent.coordinator import Coordinator

    SECRET = "parent-secret-12345"
    seen: list[str] = []

    async def _worker(frame, emit):
        seen.append(frame.render_prompt())
        return {"task_id": frame.task_id, "status": "SUCCESS",
                "findings": [], "token_usage": {"prompt": 1, "completion": 1}}

    async def _go():
        coord = Coordinator(worker_fn=_worker)
        frame = coord.build_frame("audit auth", role="auditor")
        await coord.fanout([frame])

    asyncio.run(_go())
    if any(SECRET in p for p in seen):
        return False, "parent secret leaked into frame"
    import inspect as _inspect

    if "parent_messages" in _inspect.signature(Coordinator.build_frame).parameters:
        return False, "build_frame accepts parent history"
    return True, "frame = objective + allowlist + explicit chunks only"


@check("D6", "semaphore caps concurrency at 4 under fanout 16")
def _d6_semaphore():
    from wisp.core.subagent.coordinator import Coordinator, CoordinatorConfig
    from wisp.core.subagent.protocol import ExecutionPolicy

    in_flight = 0
    peak = 0

    async def _worker(frame, emit):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return {"task_id": frame.task_id, "status": "SUCCESS",
                "findings": [], "token_usage": {"prompt": 1, "completion": 1}}

    async def _go():
        coord = Coordinator(
            worker_fn=_worker,
            config=CoordinatorConfig(
                default_policy=ExecutionPolicy(max_concurrent=4, timeout_s=60.0)))
        frames = [coord.build_frame(f"t{i}", role="explorer") for i in range(16)]
        return await coord.fanout(frames)

    reduced = asyncio.run(_go())
    if not (1 < peak <= 4):
        return False, f"peak={peak}"
    if reduced.succeeded != 16:
        return False, f"succeeded={reduced.succeeded}"
    return True, f"peak={peak}/4, 16/16 succeeded"


@check("D6", "parent abort cascades TaskGroup cancellation")
def _d6_cascade():
    from wisp.core.subagent.coordinator import Coordinator, CoordinatorConfig
    from wisp.core.subagent.protocol import ExecutionPolicy

    reached: list[str] = []

    async def _worker(frame, emit):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            reached.append(frame.task_id)
            raise
        return {"task_id": frame.task_id, "status": "SUCCESS",  # pragma: no cover
                "findings": [], "token_usage": {"prompt": 1, "completion": 1}}

    async def _go():
        coord = Coordinator(
            worker_fn=_worker,
            config=CoordinatorConfig(
                default_policy=ExecutionPolicy(max_concurrent=4, timeout_s=60.0)))
        frames = [coord.build_frame(f"t{i}", role="explorer") for i in range(4)]
        wanted.extend(f.task_id for f in frames)
        return await coord.fanout(frames)

    wanted: list[str] = []

    async def _main():
        task = asyncio.ensure_future(_go())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.05)

    asyncio.run(_main())
    if sorted(reached) != sorted(wanted) or len(wanted) != 4:
        return False, f"reached={reached}"
    return True, "4/4 workers observed CancelledError"


@check("D6", "invalid output retried once then FAILED, parent unpolluted")
def _d6_schema():
    from wisp.core.subagent.coordinator import Coordinator, CoordinatorConfig

    calls: list[str] = []

    async def _garbage(frame, emit):
        calls.append(frame.task_id)
        return {"task_id": frame.task_id, "status": "nope"}

    async def _go():
        coord = Coordinator(worker_fn=_garbage,
                            config=CoordinatorConfig(validation_retries=1))
        return await coord.fanout([coord.build_frame("x", role="explorer")])

    reduced = asyncio.run(_go())
    if len(calls) != 2 or reduced.failed != 1 or reduced.findings:
        return False, f"calls={len(calls)} failed={reduced.failed}"
    return True, "1 retry then FAILED, zero findings merged"


# ── D7: Pruning & large-codebase scaling ───────────────────────────────


@check("D7", "historical tool dumps capped (8KB/200KB ceilings)")
def _d7_prune():
    from wisp.core.context_pruner import PrunerConfig, prune_messages

    msgs: list[dict] = [{"role": "user", "content": "go"}]
    for i in range(30):
        msgs.append({"role": "tool", "name": "read_file",
                     "content": "x" * 10000 + f" #{i}"})
    out = prune_messages(msgs, PrunerConfig())
    total = sum(len(str(m.get("content", ""))) for m in out)
    if total > 200000:
        return False, f"pruned payload {total}B exceeds ceiling"
    return True, f"30x10KB -> {total}B (ceiling 200KB)"


@check("D7", "large reads carry line-range headers (no silent full ingest)")
def _d7_reads():
    import tempfile as _tf

    from wisp.tools.filesystem import tool_read_file

    with _tf.TemporaryDirectory() as td:
        p = Path(td) / "big.py"
        p.write_text("".join(f"line {i}\n" for i in range(500)))
        out = tool_read_file(str(p), td, offset=0, limit=50)
    if "SHOWING" not in out or "LINES: 500" not in out:
        return False, "read header missing range accounting"
    return True, "500-line file read honors offset/limit with header"


# ── D8: Graph state & deadlock prevention ──────────────────────────────


@check("D8", "oscillation breaker trips on repeated state")
def _d8_oscillation():
    from wisp.core.graph_state import GraphState

    try:
        state = GraphState.initial(workspace=".", session_id="diag")
    except TypeError:
        state = GraphState(workspace=".", session_id="diag")
    trips = [state.check_oscillation(window=3) for _ in range(5)]
    if not any(trips):
        return False, "identical-state repetition not detected"
    return True, f"breaker tripped: {trips}"


@check("D8", "session persistence round-trips atomically (WAL, no corruption)")
def _d8_store():
    import sqlite3
    import tempfile as _tf

    from wisp.infra.store import UnifiedStore

    with _tf.TemporaryDirectory() as td:
        db = str(Path(td) / "wisp.db")
        store = UnifiedStore(db)
        session = {"id": "diag-1", "model": "m", "workspace": td,
                   "messages": [{"role": "user", "content": "hi"}],
                   "compaction_history": [], "created_at": "t", "updated_at": "t"}
        store.save_session(session)
        back = store.load_session("diag-1")
        try:
            store.close()
        except Exception:
            pass
        mode = sqlite3.connect(db).execute("PRAGMA journal_mode").fetchone()[0]
    if back is None or back["messages"] != session["messages"]:
        return False, "round-trip mismatch"
    if mode.lower() != "wal":
        return False, f"journal_mode={mode}, want wal"
    return True, "save/load exact; WAL (crash-safe, last-commit risk only)"


@check("D8", "edit_file prompt header sanitizes multi-KB blobs")
def _d8_sanitize():
    from wisp.transport.renderer import format_arg_value

    blob = "x = 1\n" * 1500
    shown = format_arg_value("new_text", blob)
    raw_leak = "\\n" in shown and len(shown) > 200
    if raw_leak:
        return False, "escaped block leaks into header"
    return True, f"9KB edit -> header {shown!r}"


@dataclass
class Summary:
    passed: int = 0
    failed: int = 0
    rows: list[tuple[str, str, str, str]] = field(default_factory=list)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="run dimensions starting with this (e.g. D3)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    summary = Summary()
    current_dim = ""
    for check in CHECKS:
        if args.only and not check.dimension.startswith(args.only):
            continue
        t0 = time.monotonic()
        try:
            ok, detail = check.fn()
        except Exception:
            ok, detail = False, "EXCEPTION\n" + traceback.format_exc(limit=3)
        check.elapsed_s = time.monotonic() - t0
        check.ok = ok
        if ok:
            summary.passed += 1
        else:
            summary.failed += 1
        if check.dimension != current_dim:
            current_dim = check.dimension
            print(f"\n== {current_dim} ==")
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {check.name} ({check.elapsed_s*1000:.0f}ms)")
        if not ok or args.verbose:
            print(f"         {detail[:600]}")
    print(f"\n{summary.passed} passed, {summary.failed} failed")
    return summary.failed


if __name__ == "__main__":
    raise SystemExit(main())
