"""Who is running right now: the sessions that announced themselves, the wisp processes that did not, and the sessions touched in the last few minutes.

Three sources, because each one alone is wrong in a way the others fix. An announcement (presence.py) knows the model and workspace but only exists for sessions
that run a version that announces. `ps` sees every `python -m wisp` process but knows neither. The workspace database knows the model of a session that has
since closed. The result keeps them apart (`source`) instead of merging them into one confident row.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import wisp.dashboard.presence as presence

RECENT_S = 15 * 60
_ETIME = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$")
_SUBCOMMANDS = ("repl", "tui", "run", "swarm", "graph", "bench", "converge", "server", "acp", "agents", "task")


def parse_etime(text: str) -> float | None:
    """`ps` elapsed time `[[dd-]hh:]mm:ss` in seconds."""
    m = _ETIME.match(text.strip())
    if not m:
        return None
    days, hours, minutes, seconds = (int(x) if x else 0 for x in m.groups())
    return float(((days * 24 + hours) * 60 + minutes) * 60 + seconds)


def wisp_kind(command: str) -> str | None:
    """`repl`, `run`, ... for a `python -m wisp [subcommand]` command line; None when the line is not a wisp process (or is the dashboard itself)."""
    tokens = command.split()
    if not tokens or not os.path.basename(tokens[0]).startswith("python"):  # `grep -m wisp` and an editor open on a wisp file are not sessions
        return None
    for i, tok in enumerate(tokens[:-1]):
        if tok == "-m" and tokens[i + 1] == "wisp":
            rest = [t for t in tokens[i + 2:] if not t.startswith("-")]
            if not rest:
                return None  # `wisp` alone prints its help and exits: not a session
            return rest[0] if rest[0] in _SUBCOMMANDS else "run"  # anything else is a one-shot prompt
    return None


def parse_ps(text: str) -> list[dict[str, Any]]:
    rows = []
    for line in text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        kind = wisp_kind(parts[2])
        if kind:
            rows.append({"pid": int(parts[0]), "uptime_s": parse_etime(parts[1]), "kind": kind})
    return rows


def _run(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def process_cwd(pid: int) -> str | None:
    for line in _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"]).splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def seen_processes(ps_text: str | None = None, cwd_of: Callable[[int], str | None] = process_cwd) -> list[dict[str, Any]]:
    rows = parse_ps(ps_text if ps_text is not None else _run(["ps", "-axo", "pid=,etime=,command="]))
    for r in rows:
        r["workspace"] = cwd_of(r["pid"])
    return rows


def _epoch(stamp: Any) -> float | None:
    if isinstance(stamp, (int, float)):
        return float(stamp)
    if isinstance(stamp, str):
        try:
            dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    return None


def recent_sessions(workspaces: list[Path], now: float, window_s: float = RECENT_S) -> list[dict[str, Any]]:
    """Sessions whose database row changed within the window. Read-only; the title and messages are never selected."""
    out: list[dict[str, Any]] = []
    for ws in workspaces:
        db = ws / ".wisp" / "wisp.db"
        if not db.is_file():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
        except sqlite3.Error:
            continue
        try:
            for sid, model, updated, count in con.execute("select id, model, updated_at, coalesce(msg_count,0) from sessions order by updated_at desc limit 20"):
                ts = _epoch(updated)
                if ts is not None and now - ts <= window_s:
                    out.append({"session_id": str(sid)[:200], "model": str(model or "unknown")[:200], "workspace": str(ws), "messages": int(count), "idle_s": now - ts})
        except sqlite3.Error:
            pass
        finally:
            con.close()
    return sorted(out, key=lambda r: r["idle_s"])


def live_data(workspaces: list[Path], *, now: float | None = None, live_directory: Path | None = None, ps_text: str | None = None,
              cwd_of: Callable[[int], str | None] = process_cwd, alive: Callable[[int], bool] = presence.pid_alive,
              environ: dict[str, str] | None = None, dotenv: Path | None = None, self_pid: int | None = None) -> dict[str, Any]:
    stamp = time.time() if now is None else now
    me = os.getpid() if self_pid is None else self_pid
    sessions = presence.read_all(live_directory, stamp, alive)
    known = {s["pid"] for s in sessions}
    for p in seen_processes(ps_text, cwd_of):
        if p["pid"] in known:
            continue
        sessions.append({"pid": p["pid"], "kind": p["kind"], "session_id": None, "model": None, "provider": None, "model_source": None, "workspace": p["workspace"],
                         "started": None, "heartbeat": None, "uptime_s": p["uptime_s"], "heartbeat_age_s": None, "state": "not announced", "announced": False})
    for s in sessions:
        s["this"] = s["pid"] == me
    sessions.sort(key=lambda s: (not s["this"], s["uptime_s"] if s["uptime_s"] is not None else 1e12))
    default = presence.resolve_model(environ, dotenv)
    return {"now": stamp, "sessions": sessions, "running": sum(1 for s in sessions if s["state"] == "running"),
            "recent": recent_sessions(workspaces, stamp), "recent_window_s": RECENT_S, "default_model": default,
            "notes": ["`running` = announced itself within 20 s; `silent` = the process exists but stopped reporting; `not announced` = found by `ps`, an older version that does not announce, so its model is unknown.",
                      "A model shown here is what that session's own environment resolved to when it started; a later /model switch is not reflected."]}
