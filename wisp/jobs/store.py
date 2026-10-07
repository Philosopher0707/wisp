"""The job store: one directory per job, bound to the workspace that started it.

INVARIANTS (docs/harness/background-jobs-design.md, section 4)
  BJ2  A job is reachable only through its workspace: ids are `bj-` plus 16 hex digits (nothing else ever becomes a path) and live under
       `<root>/<workspace hash>/`, so another workspace's id, `..` or an absolute path resolves to nothing.
  BJ4  A result is never reported as success unless the supervisor wrote one: a missing, truncated or unreadable `state.json` is `lost`
       (supervisor gone) or `unknown`, never `exited 0`.
  BJ7  Resources are finite: `limits` caps live jobs per workspace and per store, and finished jobs are pruned by age and count.
  BJ9  The store is bounded in age: `prune` removes finished jobs, never a running one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import wisp.jobs.procs as procs

ID_RE = re.compile(r"^bj-[0-9a-f]{16}$")
RUNNING, EXITED, KILLED, TIMED_OUT, LOST, UNKNOWN = "running", "exited", "killed", "timed_out", "lost", "unknown"
FINISHED = frozenset({EXITED, KILLED, TIMED_OUT, LOST})
TAIL_CHARS = 20_000


@dataclass(frozen=True)
class Limits:
    max_running_per_workspace: int = 4
    max_running_total: int = 16
    max_finished: int = 50
    max_age_s: float = 7 * 24 * 3600.0
    default_runtime_s: int = 3600
    hard_runtime_s: int = 3600  # `run_bash_confined` validates timeouts to 1..3600: the same ceiling, not a new one


def default_root() -> Path:
    """Runtime data, so the XDG *state* directory, not configuration and not the workspace."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "wisp" / "jobs"


def workspace_hash(workspace: str) -> str:
    return hashlib.sha256(os.path.realpath(workspace).encode("utf-8", errors="surrogateescape")).hexdigest()[:16]


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class JobView:
    id: str
    status: str
    command: str
    exit_code: int | None = None
    started: float = 0.0
    finished: float | None = None
    tier: str = ""
    output: str = ""
    truncated: bool = False
    notified: bool = False
    mutation_index: int = 0
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_finished(self) -> bool:
        return self.status in FINISHED


class JobStore:
    def __init__(self, root: Path | str, workspace: str, limits: Limits = Limits()) -> None:
        self.root = Path(root)
        self.workspace = os.path.realpath(workspace)
        self.limits = limits
        self.dir = self.root / workspace_hash(self.workspace)

    # ── ids and paths (BJ2) ──
    def path(self, job_id: str) -> Path | None:
        """The job's directory, or None for anything that is not an id of this workspace's store."""
        if not isinstance(job_id, str) or not ID_RE.match(job_id):
            return None
        p = self.dir / job_id
        return p if p.is_dir() else None

    def new_dir(self) -> tuple[str, Path]:
        self.dir.mkdir(parents=True, exist_ok=True)
        for p in (self.root, self.dir):
            os.chmod(p, 0o700)
        job_id = "bj-" + secrets.token_hex(8)
        d = self.dir / job_id
        d.mkdir(mode=0o700)
        return job_id, d

    # ── counting (BJ7) ──
    def running_ids(self, whole_store: bool = False) -> list[str]:
        dirs = [self.root] if whole_store else [self.dir]
        out: list[str] = []
        for base in dirs:
            if not base.is_dir():
                continue
            ws_dirs = [d for d in base.iterdir() if d.is_dir()] if whole_store else [base]
            for ws in ws_dirs:
                for d in ws.iterdir():
                    if ID_RE.match(d.name) and d.is_dir() and self._status(d)[0] == RUNNING:
                        out.append(d.name)
        return out

    # ── reading (BJ4) ──
    def _status(self, d: Path) -> tuple[str, dict[str, Any]]:
        state = read_json(d / "state.json")
        if state is not None and state.get("status") in (EXITED, KILLED, TIMED_OUT, LOST):
            return str(state["status"]), state
        sup = read_json(d / "supervisor.json")
        meta = read_json(d / "meta.json")
        if meta is None:
            return UNKNOWN, {}
        if sup is None:
            # The supervisor has not announced itself yet: young jobs are starting, old ones never started.
            return (RUNNING, {}) if time.time() - float(meta.get("created", 0)) < 30 else (LOST, {"note": "the supervisor never started"})
        if procs.alive(int(sup.get("pid", 0)), str(sup.get("start", ""))):
            return RUNNING, {}
        # The supervisor is gone and wrote no final state (killed hard, machine restart): lost, never success.
        return LOST, {"note": "the supervisor exited without recording a result"}

    def view(self, job_id: str) -> JobView | None:
        d = self.path(job_id)
        if d is None:
            return None
        meta = read_json(d / "meta.json")
        if meta is None:
            return JobView(job_id, UNKNOWN, "", note="job metadata is unreadable")
        status, state = self._status(d)
        out = ""
        truncated = False
        log = d / "out.log"
        if log.is_file():
            try:
                text = log.read_text(encoding="utf-8", errors="replace")
                truncated = len(text) > TAIL_CHARS
                out = text[-TAIL_CHARS:]
            except OSError:
                pass
        code = state.get("exit_code")
        return JobView(job_id, status, str(meta.get("command", "")), code if isinstance(code, int) else None, float(meta.get("created", 0)),
                       state.get("finished"), str(state.get("tier", "")), out, truncated, bool((read_json(d / "notified.json") or {}).get("notified")),
                       int(meta.get("mutation_index", 0)), str(state.get("note", "")))

    def jobs(self) -> list[JobView]:
        if not self.dir.is_dir():
            return []
        views = [self.view(d.name) for d in sorted(self.dir.iterdir()) if ID_RE.match(d.name)]
        return sorted((v for v in views if v is not None), key=lambda v: v.started)

    def mark_notified(self, job_id: str) -> None:
        d = self.path(job_id)
        if d is not None:
            _write_atomic(d / "notified.json", {"notified": True})

    def unnotified_finished(self) -> list[JobView]:
        return [v for v in self.jobs() if v.is_finished and not v.notified]

    # ── pruning (BJ9) ──
    def prune(self, now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        finished = [v for v in self.jobs() if v.is_finished]
        doomed = [v.id for v in finished if v.finished is not None and now - float(v.finished) > self.limits.max_age_s]
        keep = [v for v in finished if v.id not in doomed]
        if len(keep) > self.limits.max_finished:
            doomed += [v.id for v in sorted(keep, key=lambda v: v.started)[: len(keep) - self.limits.max_finished]]
        for job_id in doomed:
            d = self.path(job_id)
            if d is not None:
                shutil.rmtree(d, ignore_errors=True)
        return doomed


# ── control: kill and reap (BJ3, BJ8) ──
def _pids_from(d: Path) -> list[tuple[int, str]]:
    kids = read_json(d / "children.json") or {}
    out: list[tuple[int, str]] = []
    for item in kids.get("children", []):
        if isinstance(item, list) and len(item) == 2 and isinstance(item[0], int):
            out.append((item[0], str(item[1])))
    return out


def _remove_container(name: str) -> None:
    if not name:
        return
    import subprocess

    try:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
    except Exception:  # noqa: BLE001 — best effort
        pass


def kill_job(store: JobStore, job_id: str, wait_s: float = 8.0) -> JobView | None:
    """SIGTERM the supervisor (it kills the tree and removes its container), wait for it to record the result, and force the rest if it does not."""
    d = store.path(job_id)
    if d is None:
        return None
    if store._status(d)[0] != RUNNING:
        return store.view(job_id)
    sup = read_json(d / "supervisor.json")
    if sup is None:  # not started yet: leave a final state it will see
        _write_atomic(d / "state.json", {"status": KILLED, "finished": time.time(), "exit_code": None, "note": "killed before it started"})
        return store.view(job_id)
    pid, start = int(sup.get("pid", 0)), str(sup.get("start", ""))
    if procs.alive(pid, start):
        try:
            os.kill(pid, 15)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline and not (d / "state.json").exists() and procs.alive(pid, start):
        time.sleep(0.05)
    if not (d / "state.json").exists():
        procs.kill_all([(pid, start), *_pids_from(d), *procs.find_by_env(job_id)], grace=1.0)
        _remove_container(str(sup.get("container", "")))
        _write_atomic(d / "state.json", {"status": KILLED, "finished": time.time(), "exit_code": None, "note": "force-killed: the supervisor did not stop in time"})
    return store.view(job_id)


def reap_lost(store: JobStore) -> list[str]:
    """Settle jobs whose supervisor died without a result: kill what it left behind, remove its container, and record `lost` (BJ3)."""
    settled: list[str] = []
    if not store.dir.is_dir():
        return settled
    for d in sorted(store.dir.iterdir()):
        if not (ID_RE.match(d.name) and d.is_dir()) or (d / "state.json").exists():
            continue
        status, info = store._status(d)
        if status != LOST:
            continue
        procs.kill_all([*_pids_from(d), *procs.find_by_env(d.name)], grace=1.0)
        _remove_container(str((read_json(d / "supervisor.json") or {}).get("container", "")))
        _write_atomic(d / "state.json", {"status": LOST, "finished": time.time(), "exit_code": None, "note": info.get("note", "lost")})
        settled.append(d.name)
    return settled

