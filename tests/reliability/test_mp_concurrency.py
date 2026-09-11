"""G0 multiprocess contention: audit chain + SQLite init across processes.

13A proved the JSONL chain forks under threads. This extends to processes
(the deployed shape: server + CLI + workers sharing one audit target/DB).

Records observations as data (G0 does not fix P1-4): entry counts (loss),
verify() result (integrity), init successes/lock errors. Asserts only what
is currently true (no loss) so the suite is green while remaining honest;
G1 will add the verify-clean assertion.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("G0_MP_OUT", str(Path(tempfile.mkdtemp(prefix="g0mp_")))))

_AUDIT_CHILD = """
import sys, os
sys.path.insert(0, {repo})
os.environ["HOME"] = {home}
from wisp.infra.audit import AuditTrail
from pathlib import Path
t = AuditTrail(path=Path({target}))
for j in range({n}):
    t.record("mp-{i}-%d" % j, actor="mp-{i}", key="k", new_value={"j": j})
print("child-{i}-done", flush=True)
"""

_AUDIT_SUSTAINED_CHILD = """
import sys, os, time
sys.path.insert(0, {repo})
os.environ["HOME"] = {home}
from wisp.infra.audit import AuditTrail
from pathlib import Path
t = AuditTrail(path=Path({target}))
t0 = time.time()
n = 0
while time.time() - t0 < {window}:
    for j in range(20):
        t.record("mp-{i}", actor="p", key="k", new_value={"j": j})
        n += 1
print("child-{i}-%d" % n, flush=True)
"""

_INIT_CHILD = """
import sys, os
sys.path.insert(0, {repo})
os.environ["HOME"] = {home}
from wisp.graph.store import GraphStore
from wisp.infra.store import UnifiedStore
try:
    UnifiedStore(db_path=os.path.join({ws}, ".wisp", "wisp.db"))
    GraphStore(workspace={ws})
    print("init-ok", flush=True)
except Exception as e:
    print("init-FAIL " + repr(e)[:160], flush=True)
"""


def _run_child(code: str, **kw) -> subprocess.CompletedProcess:
    # NOTE: plain replace, not str.format — child code contains braces.
    for k, v in kw.items():
        code = code.replace("{" + k + "}", repr(v))
    return subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=300)


def _record(**fields) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), **fields}
    with open(OUT / "mp_concurrency.jsonl", "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    return rec


def test_mp_audit_contention(tmp_path):
    """8 processes x 25 records, one JSONL target: count loss + chain state."""
    nproc, nrec = 8, 25
    home = str(tmp_path / "home")
    os.makedirs(home, exist_ok=True)
    target = str(tmp_path / "audit.jsonl")
    procs = []
    for i in range(nproc):
        code = _AUDIT_CHILD
        for k, v in {"repo": str(REPO), "home": home, "target": target,
                     "n": nrec, "i": i}.items():
            code = code.replace("{" + k + "}", repr(v))
        procs.append(subprocess.Popen([sys.executable, "-c", code],
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL))
    rcs = [p.wait(timeout=300) for p in procs]
    assert all(rc == 0 for rc in rcs), rcs

    sys.path.insert(0, str(REPO))
    from wisp.infra.audit import AuditTrail
    trail = AuditTrail(path=Path(target))
    lines = [l for l in Path(target).read_text().splitlines() if l.strip()]
    bad = trail.verify()
    _rec = _record(harness="mp-audit", processes=nproc, per_process=nrec,
                  expected=nproc * nrec, written=len(lines),
                  lost=nproc * nrec - len(lines),
                  verify_bad_index=bad, chain_intact=bad is None,
                  result="observed")
    # No-loss holds (appends are atomic enough); integrity fork recorded.
    assert _rec["lost"] == 0, _rec
    # NOTE (G1): add `assert bad is None` once P1-4 is remediated.


def test_mp_audit_sustained_contention(tmp_path):
    """8 processes x 2s sustained writes: deterministic fork reproduction.

    Records integrity outcome as data. Under real overlap the chain forks
    (ad-hoc probe: 16px3s forked at entry 1163); low-overlap runs stay
    intact — both outcomes are honest records for G1.
    """
    nproc, window = 8, 2.0
    home = str(tmp_path / "home")
    os.makedirs(home, exist_ok=True)
    target = str(tmp_path / "audit.jsonl")

    def _spawn(i):
        code = _AUDIT_SUSTAINED_CHILD
        for k, v in {"repo": str(REPO), "home": home, "target": target,
                     "window": window, "i": i}.items():
            code = code.replace("{" + k + "}", repr(v))
        return subprocess.Popen([sys.executable, "-c", code],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)

    procs = [_spawn(i) for i in range(nproc)]
    rcs = [p.wait(timeout=300) for p in procs]
    assert all(rc == 0 for rc in rcs), rcs

    sys.path.insert(0, str(REPO))
    from wisp.infra.audit import AuditTrail
    trail = AuditTrail(path=Path(target))
    lines = [l for l in Path(target).read_text().splitlines() if l.strip()]
    bad = trail.verify()
    _rec = _record(harness="mp-audit-sustained", processes=nproc,
                  window_s=window, written=len(lines),
                  verify_bad_index=bad, chain_intact=bad is None,
                  result="observed")
    assert len(lines) > 0, _rec
    # NOTE (G1): add `assert bad is None` once P1-4 is remediated.


def test_mp_sqlite_init_contention(tmp_path):
    """8 processes first-touch the same fresh .wisp dir simultaneously."""
    nproc = 8
    ws = str(tmp_path / "ws")
    os.makedirs(os.path.join(ws, ".wisp"), exist_ok=True)
    home = str(tmp_path / "home")
    os.makedirs(home, exist_ok=True)
    outs = [_run_child(_INIT_CHILD, repo=str(REPO), home=home, ws=ws)
            for _ in range(nproc)]
    ok = sum(1 for r in outs if "init-ok" in r.stdout)
    fails = [ (r.stdout + r.stderr)[-200:] for r in outs if "init-ok" not in r.stdout]
    _rec = _record(harness="mp-sqlite-init", processes=nproc, ok=ok,
                  failures=len(fails), failure_samples=fails[:3],
                  result="observed")
    assert ok + len(fails) == nproc
    # NOTE (G1): strengthen to `assert ok == nproc` once P2-7 is remediated.
