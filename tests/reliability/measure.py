"""External resource sampling for the G0 soak harness.

Stdlib + psutil (already a test-env dependency) + read-only SQLite.
No production imports: everything is observed from outside the runtime.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


def sample_process(pid: int | None = None) -> dict:
    """RSS, fds, threads, children, connections, CPU for one process."""
    if psutil is None:
        return {"psutil": False}
    p = psutil.Process(pid or os.getpid())
    with p.oneshot():
        try:
            conns = len(p.net_connections())
        except Exception:
            conns = -1
        return {
            "rss": p.memory_info().rss,
            "vms": p.memory_info().vms,
            "fds": _num_fds(p),
            "threads": p.num_threads(),
            "children": len(p.children()),
            "sockets": conns,
            "cpu_pct": p.cpu_percent(interval=None),
        }


def _num_fds(p) -> int:
    try:
        return p.num_fds()  # posix
    except Exception:
        try:
            return len(p.open_files())
        except Exception:
            return -1


def count_async_tasks(loop: asyncio.AbstractEventLoop | None = None) -> int:
    try:
        loop = loop or asyncio.get_running_loop()
    except RuntimeError:
        return -1
    return sum(1 for t in asyncio.all_tasks(loop) if not t.done())


_DB_TABLES = (
    "sessions", "runs", "events", "session_events", "memory",
    "background_runs", "run_transitions", "trace_spans", "task_plans",
    "graph_runs", "graph_node_runs", "graph_artifacts", "graph_events",
    "graph_checkpoints", "graph_proposals", "audit_log",
)


def sample_db(db_path: str | Path) -> dict:
    """Read-only size + per-table row counts. Missing DB -> {"exists": False}."""
    db_path = Path(db_path)
    if not db_path.exists():
        return {"exists": False}
    out: dict = {
        "exists": True,
        "bytes": db_path.stat().st_size,
        "wal_bytes": _sfx(db_path, "-wal") + _sfx(db_path, "-shm"),
    }
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    except Exception as e:
        return {**out, "error": repr(e)}
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {}
        for t in _DB_TABLES:
            if t in tables:
                try:
                    counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                except Exception:
                    counts[t] = -1
        out["tables"] = counts
    except Exception as e:
        out["error"] = repr(e)
    finally:
        con.close()
    return out


def _sfx(p: Path, sfx: str) -> int:
    q = Path(str(p) + sfx)
    return q.stat().st_size if q.exists() else 0


def sample_files(root: str | Path) -> dict:
    """Audit-file bytes + artifact counts under a workspace/config root."""
    root = Path(root)
    audit = list(root.rglob("audit*.jsonl")) + list(root.rglob("audit*.json"))
    arts = list(root.rglob("artifacts"))
    n_arts = sum(1 for a in arts for _ in a.glob("*.json")) if arts else 0
    return {
        "audit_bytes": sum(f.stat().st_size for f in audit),
        "artifacts": n_arts,
    }
