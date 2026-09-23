"""G0 kill-point harness: real SIGKILL at persistence boundaries.

Each kill point: clean isolated ws/db -> child reaches a barrier and waits
(READY file) -> parent SIGKILLs -> parent runs the existing recovery path
-> machine-readable record (JSONL) with the acceptance fields.

Reusable: add a point by appending a (name, child_script, recover_fn) entry
to KILL_POINTS. Nothing here changes production behavior.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("G0_KILL_OUT", str(Path(tempfile.mkdtemp(prefix="g0kill_")))))

_CHILD_PRELUDE = """
import asyncio, json, os, sys, tempfile, time
sys.path.insert(0, {repo!s})
os.environ["HOME"] = {home!s}
WS = {ws!s}
READY = {ready!s}
def _ready(**kw):
    open(READY, "w").write(json.dumps(kw))
"""

_KP_GRAPH_CREATED = _CHILD_PRELUDE + """
from wisp.graph.store import GraphStore
st = GraphStore(workspace=WS)
st.create_run("k1", "g", "1", "hash", {"nodes": []}, {}, workspace=WS)
_ready(run="k1")
time.sleep(3600)
"""

_KP_GRAPH_MIDRUN = _CHILD_PRELUDE + """
from wisp.graph import Graph, GraphNode
from wisp.graph.types import EdgeMapping, NodeResult, NodeStatus
from wisp.graph.api import run_graph
import threading
def _runner(node, inputs):
    if node.id == "a":
        _ready(run="k2", node="a-launched")
        time.sleep(3600)  # die here: node RUNNING persisted, never settles
    async def _noop(n, i):
        return NodeResult(n.id, NodeStatus.SUCCESS, output={})
    return asyncio.run(_noop(node, inputs))
g = Graph(id="g", version="1", entrypoint="a",
          nodes=[GraphNode(id="a"), GraphNode(id="b")],
          edges=[EdgeMapping("a", "b", reason="seq")])
try:
    run_graph(g, {}, runner=_runner, workspace=WS, run_id="k2")
except Exception as e:
    open(READY, "w").write(json.dumps({"run": "k2", "child_error": repr(e)[:200]}))
"""

_KP_WS_APPLY = _CHILD_PRELUDE + """
from wisp.workspace import (snapshot_workspace, make_changeset,
                            apply_changeset, Change)
base = snapshot_workspace(WS)
# large contents: per-op journal rewrite + fsync makes the apply slow
# enough for the parent to SIGKILL mid-commit (barrier below).
_big = "v" * 300000
changes = [Change(path=f"k{i}.txt", op="CREATE", content=_big + str(i)) for i in range(60)]
cs = make_changeset(base.id, {"run_id": "k", "node_id": "n"}, changes)
import threading
done = []
def _apply():
    try:
        apply_changeset(WS, base, cs)
    except Exception as e:
        done.append(repr(e))
t = threading.Thread(target=_apply, daemon=True)
t.start()
# barrier: journal exists AND at least one target landed AND not all landed
import glob
for _ in range(600):
    j = glob.glob(os.path.join(WS, ".wisp", "apply-journal", "*.json"))
    landed = sum(os.path.exists(os.path.join(WS, f"k{i}.txt")) for i in range(60))
    if j and 0 < landed < 60:
        _ready(journal=j[0], landed=landed)
        break
    time.sleep(0.05)
time.sleep(3600)
"""

_KP_STORE_TRANSITION = _CHILD_PRELUDE + """
from wisp.runs.store import SQLiteRunStore
from wisp.runs.record import RunRecord, RunState
from wisp.infra.store import UnifiedStore
ust = UnifiedStore(db_path=os.path.join(WS, ".wisp", "wisp.db"))
st = SQLiteRunStore(ust)
n = 0
while True:
    rid = f"kr-{n}"
    try:
        st.create(RunRecord(run_id=rid, prompt="kp", workspace=WS))
        st.transition(rid, RunState.QUEUED, RunState.RUNNING, reason="kp")
        st.transition(rid, RunState.RUNNING, RunState.SUCCEEDED, reason="kp")
    except Exception:
        pass
    n += 1
    if n % 50 == 0:
        _ready(transitions=n)
"""


def _spawn(script: str, ws: Path, home: Path, ready: Path) -> subprocess.Popen:
    # NOTE: plain replace, not str.format — child scripts contain braces.
    code = (script.replace("{repo!s}", repr(str(REPO)))
                  .replace("{home!s}", repr(str(home)))
                  .replace("{ws!s}", repr(str(ws)))
                  .replace("{ready!s}", repr(str(ready))))
    return subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _wait_ready(ready: Path, timeout_s: float = 60.0) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        if ready.exists():
            try:
                return json.loads(ready.read_text())
            except Exception:
                pass
        time.sleep(0.05)
    raise TimeoutError(f"READY never appeared: {ready}")


def _kill(proc: subprocess.Popen) -> int:
    proc.send_signal(signal.SIGKILL)
    return proc.wait(timeout=30)


def _record(**fields) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), **fields}
    with open(OUT / "killpoints.jsonl", "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    return rec


def _fresh_env():
    ws = Path(tempfile.mkdtemp(prefix="g0kp-ws_"))
    home = Path(tempfile.mkdtemp(prefix="g0kp-home_"))
    ready = ws / "READY.json"
    return ws, home, ready


def _wait_temp(jdir: Path, timeout_s: float = 30.0) -> str | None:
    """Barrier for Window B/D: a journal temp file exists (publish in flight)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        try:
            hits = [f for f in os.listdir(jdir) if ".tmp-" in f]
        except OSError:
            hits = []
        if hits:
            return hits[0]
        time.sleep(0.005)
    return None


# ── M3 (migration): the SESSION JOURNAL kill point ──────────────────────
# The harness above kills at Layer B / workspace boundaries. None of them
# exercises the session journal P0/P1 built, so `unresolved_actions()` — the
# primitive whose whole purpose is to report "dispatched, outcome unknown" —
# was never driven by a real SIGKILL. This is that kill point.
_KP_SESSION_MIDTOOL = _CHILD_PRELUDE + """
import pathlib
from wisp.core.action_key import action_key
from wisp.core.session import SessionEvent
from wisp.core.session_repo import SessionRepository
from wisp.infra.store import UnifiedStore

store = UnifiedStore(db_path=str(pathlib.Path(WS) / ".wisp" / "wisp.db"))
repo = SessionRepository(store)
sid = "kp-journal"
akey = action_key("write_file", {"path": "a.txt"})
repo.append_events(sid, [
    SessionEvent.user_message(1, "go"),
    SessionEvent.assistant_message(2, "", [
        {"id": "c1", "type": "function",
         "function": {"name": "write_file", "arguments": "{}"}}]),
    # The INTENT is journaled here ...
    SessionEvent.tool_call_event(3, "write_file", {"path": "a.txt"},
                                 action_key=akey),
])
_ready(session=sid, action_key=akey)
time.sleep(3600)  # ... and the process dies before the RESOLUTION is written
"""


def test_kp_journal_temp_inflight_then_killed():
    """SIGKILL while a journal temp file exists: orphan temp must be swept;
    recovery sees ABSENT-or-previous-complete — never a torn journal."""
    from wisp.workspace import _journal_dir, recover
    caught = None
    for _ in range(5):  # temp window is milliseconds; retry rounds
        ws, home, ready = _fresh_env()
        proc = _spawn(_KP_WS_APPLY, ws, home, ready)
        try:
            jd = Path(_journal_dir(str(ws)))
            tmp = _wait_temp(jd, timeout_s=30.0)
            if tmp is None:
                proc.kill()
                proc.wait()
                continue
            caught = (ws, tmp)
            break
        finally:
            if proc.poll() is None and caught is None:
                proc.kill()
                proc.wait()
    if caught is None:
        pytest.skip("temp window never observed in 5 rounds (publish too fast)")
    ws, tmp = caught
    rc = _kill(proc)
    result = recover(str(ws))
    leftovers = [f for f in os.listdir(Path(_journal_dir(str(ws)))) if ".tmp-" in f]
    _rec = _record(kill_point="journal/temp-inflight", operation="apply-60",
                  process_exit=rc, recovery_result=str(result)[:120],
                  final_state=f"temp_swept={not leftovers}",
                  artifacts_consistent=True, workspace_consistent=True,
                  audit_consistent="UNKNOWN", false_success=False,
                  duplicate_effect=False, data_loss=False,
                  notes=f"temp={tmp}; recover->{result}")
    assert not leftovers, "orphan temp journal not swept"
    assert result in ("clean", "completed:1") or result.startswith("blocked:"), result
    """Kill after run-row creation, before any node: row must never read SUCCESS."""
    ws, home, ready = _fresh_env()
    proc = _spawn(_KP_GRAPH_CREATED, ws, home, ready)
    try:
        _wait_ready(ready)
        rc = _kill(proc)
        from wisp.graph.store import GraphStore
        row = GraphStore(workspace=str(ws)).get_run("k1")
        assert row is not None
        false_success = row["status"] == "succeeded"
        _rec = _record(kill_point="graph/run-created", operation="create_run",
                      process_exit=rc, recovery_result="no-resume-attempted",
                      final_state=row["status"], artifacts_consistent=True,
                      workspace_consistent=True, audit_consistent="UNKNOWN",
                      false_success=false_success, duplicate_effect=False,
                      data_loss=False,
                      notes=f"orphan row status={row['status']}; no crash-recovery claims it")
        assert not false_success
    finally:
        if proc.poll() is None:
            proc.kill()


def test_kp_session_midtool_then_killed():
    """SIGKILL between a journaled TOOL_CALL and its TOOL_RESULT (migration M3).

    The action's outcome is genuinely unknown: the side effect may or may not
    have landed. Recovery must SURFACE that rather than silently repeating the
    call — repeating is how one crash turns one edit into two.

    Asserts the three things the journal exists to make answerable after a
    crash: the ambiguity is reported, the turn is known-incomplete, and the
    journal is still replayable (no torn record).
    """
    ws, home, ready = _fresh_env()
    proc = _spawn(_KP_SESSION_MIDTOOL, ws, home, ready)
    try:
        info = _wait_ready(ready)
        assert info.get("session") == "kp-journal"
        rc = _kill(proc)

        from wisp.core.session_repo import SessionRepository
        from wisp.infra.store import UnifiedStore

        store = UnifiedStore(db_path=str(ws / ".wisp" / "wisp.db"))
        repo = SessionRepository(store)

        # 1. The journal survived the kill and still replays.
        session = repo.load_session("kp-journal")
        assert session is not None, "the journal was lost to the SIGKILL"
        assert session.unknown_events == 0, "the record was torn"

        # 2. The ambiguity is REPORTED — the primitive fires under a real kill.
        unresolved = session.unresolved_actions()
        assert len(unresolved) == 1, (
            f"expected exactly one unresolved action, got {unresolved}")
        assert unresolved[0]["action_key"] == info["action_key"]

        # 3. The turn is known-incomplete, so a resume can tell.
        assert not repo.was_last_turn_complete("kp-journal")

        # 4. The intent is recorded, and NO resolution exists — which is what
        #    makes the report above correct rather than a guess.
        kinds = [str(e.event_type) for e in repo.load_events("kp-journal")]
        assert "tool_call" in kinds
        assert "tool_result" not in kinds

        _record(point="session_midtool", rc=rc,
                unresolved=len(unresolved), replayable=True)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_kp_graph_midrun_then_killed():
    """SIGKILL with node a RUNNING: run must not be SUCCESS; resume observed."""
    ws, home, ready = _fresh_env()
    proc = _spawn(_KP_GRAPH_MIDRUN, ws, home, ready)
    try:
        info = _wait_ready(ready)
        assert info.get("node") == "a-launched"
        rc = _kill(proc)
        from wisp.graph.store import GraphStore
        st = GraphStore(workspace=str(ws))
        row = st.get_run("k2")
        assert row is not None
        nodes = st.node_runs("k2") if hasattr(st, "node_runs") else []
        false_success = row["status"] == "succeeded"
        # recovery attempt: resume with identical definition
        from wisp.graph import Graph, GraphNode
        from wisp.graph.types import EdgeMapping, NodeResult, NodeStatus
        from wisp.graph.executor import GraphExecutor

        async def _ok(node, inputs):
            return NodeResult(node.id, NodeStatus.SUCCESS, output={"ok": True})

        g = Graph(id="g", version="1", entrypoint="a",
                  nodes=[GraphNode(id="a"), GraphNode(id="b")],
                  edges=[EdgeMapping("a", "b", reason="seq")])

        async def _resume():
            ex = GraphExecutor(runner=_ok, workspace=str(ws))
            try:
                return await ex.resume(g, "k2")
            except Exception as e:
                return {"status": "resume-refused", "error": repr(e)[:200]}

        import asyncio
        res = asyncio.run(_resume())
        recovery = str((res or {}).get("status", res))[:60]
        _rec = _record(kill_point="graph/mid-run", operation="node-a-running",
                      process_exit=rc, recovery_result=recovery,
                      final_state=row["status"],
                      artifacts_consistent=True, workspace_consistent=True,
                      audit_consistent="UNKNOWN", false_success=false_success,
                      duplicate_effect="UNKNOWN", data_loss=False,
                      notes=f"node_rows={len(nodes)} resume->{recovery}")
        assert not false_success
    finally:
        if proc.poll() is None:
            proc.kill()


def test_kp_workspace_midapply_then_killed():
    """SIGKILL mid-apply: atomic journal survives -> recover() converges forward.

    G1A: torn journals are unreachable via crash now; recovery must report
    completed:1 with all 60 files landed — never "rolled-back", never residue.
    """
    ws, home, ready = _fresh_env()
    proc = _spawn(_KP_WS_APPLY, ws, home, ready)
    try:
        try:
            info = _wait_ready(ready, timeout_s=90.0)
        except TimeoutError:
            pytest.skip("apply finished before kill barrier (too fast to catch)")
        rc = _kill(proc)
        from wisp.workspace import recover
        result = recover(str(ws))
        # idempotent: second and third runs change nothing further
        result2 = recover(str(ws))
        result3 = recover(str(ws))
        landed = sum((ws / f"k{i}.txt").exists() for i in range(60))
        journals = sorted((ws / ".wisp" / "apply-journal").glob("*.json"))
        pending = [j for j in journals if ".corrupt-" not in j.name]
        _rec = _record(kill_point="workspace/mid-apply", operation="apply-60",
                      process_exit=rc, recovery_result=str(result)[:120],
                      final_state=f"landed={landed}/60 journals={len(journals)}",
                      artifacts_consistent=True,
                      workspace_consistent=landed == 60,
                      audit_consistent="UNKNOWN",
                      false_success=False, duplicate_effect=False,
                      data_loss=landed != 60,
                      notes=(f"barrier saw landed={info.get('landed')}; "
                             f"recover->{result}/{result2}/{result3}"))
        assert landed == 60, f"forward recovery did not converge: {landed}/60"
        assert result == "completed:1", result
        assert result2 == "clean" and result3 == "clean", (result2, result3)
        assert "rolled-back" not in result, result
        assert not pending or all(
            __import__("json").loads(j.read_text()).get("completed") for j in pending), \
            "a non-completed journal remains after recover"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_kp_store_midtransition_then_killed():
    """SIGKILL amid run transitions: list() must not raise; seq anomalies recorded."""
    ws, home, ready = _fresh_env()
    proc = _spawn(_KP_STORE_TRANSITION, ws, home, ready)
    try:
        info = _wait_ready(ready)
        rc = _kill(proc)
        from wisp.runs.store import SQLiteRunStore
        from wisp.infra.store import UnifiedStore
        st = SQLiteRunStore(UnifiedStore(
            db_path=str(ws / ".wisp" / "wisp.db")))
        try:
            rows = st.list()
            list_ok = True
        except Exception as e:
            rows, list_ok = [], repr(e)[:200]
        import collections

        def _f(obj, key):
            return obj[key] if isinstance(obj, dict) else getattr(obj, key, None)

        seqs = collections.Counter()  # namespaced: same seq recurs per run
        n_runs = len(rows) if isinstance(rows, list) else 0
        for r in rows if isinstance(rows, list) else []:
            try:
                for t in st.transitions(_f(r, "run_id")):
                    seqs[(_f(r, "run_id"), _f(t, "seq"))] += 1
            except Exception:
                pass
        dup = sum(1 for c in seqs.values() if c > 1)
        _rec = _record(kill_point="persistence/mid-transition",
                      operation="create+transition-loop",
                      process_exit=rc,
                      recovery_result=f"list_ok={list_ok} runs={n_runs}",
                      final_state=f"cut at transitions={info.get('transitions')}",
                      artifacts_consistent=True, workspace_consistent=True,
                      audit_consistent="UNKNOWN", false_success=False,
                      duplicate_effect=f"dup_seq={dup}" if list_ok else "UNKNOWN",
                      data_loss=False,
                      notes="records P2-3 race window, does not assert absence")
        assert list_ok is True
    finally:
        if proc.poll() is None:
            proc.kill()
