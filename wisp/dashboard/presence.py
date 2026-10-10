"""A running session announces itself so the dashboard can show every session that is alive at the same time.

One small file per process, `<state>/live/<pid>.json`, rewritten every few seconds and removed on exit. It holds only what a person looking at a list of
sessions needs: the pid, the workspace, the model and where that model came from (the session's own environment), when it started and when it last
said it was alive. It never holds a prompt, a message, a key or any environment variable except the two named below. macOS and Linux will not
show another process's environment to a script, which is why the session writes it down itself.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

HEARTBEAT_S = 5.0
STALE_S = 20.0
_ENV_KEYS = ("WISP_MODEL", "WISP_PROVIDER")
_MAX = 200  # no field read from a file is longer than this


def live_dir() -> Path:
    override = os.environ.get("WISP_LIVE_DIR")
    if override:
        return Path(override).expanduser()
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "wisp" / "live"


def _dotenv_values(path: Path, keys: tuple[str, ...]) -> dict[str, str]:
    """Only the named keys from a `.env` file; every other line (keys, tokens) is skipped without being kept."""
    found: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return found
    for line in lines:
        name, sep, value = line.strip().partition("=")
        name = name.removeprefix("export ").strip()
        if sep and name in keys:
            found[name] = value.strip().strip("'\"")
    return found


def resolve_model(environ: Mapping[str, str] | None = None, dotenv: Path | None = None) -> dict[str, str]:
    """The model a session started now would use, from its own environment first and the user's `.env` second, with where each value came from."""
    env = os.environ if environ is None else environ
    out: dict[str, str] = {}
    from_file = _dotenv_values(dotenv if dotenv is not None else Path.home() / ".config" / "wisp" / ".env", _ENV_KEYS + ("WISP_API_BASE",))
    for key, field in (("WISP_MODEL", "model"), ("WISP_PROVIDER", "provider"), ("WISP_API_BASE", "api_base")):
        if env.get(key):
            out[field], out[field + "_source"] = env[key], f"env {key}"
        elif from_file.get(key):
            out[field], out[field + "_source"] = from_file[key], "~/.config/wisp/.env"
    return out


def env_has(name: str, environ: Mapping[str, str] | None = None, dotenv: Path | None = None) -> bool:
    """Whether a variable is set, in the environment or in the user's `.env`, without returning (or printing) its value."""
    env = os.environ if environ is None else environ
    return bool(env.get(name)) or bool(_dotenv_values(dotenv if dotenv is not None else Path.home() / ".config" / "wisp" / ".env", (name,)).get(name))


def _clean(value: Any) -> str | None:
    return value[:_MAX] if isinstance(value, str) and value else None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class Presence:
    """The announcing side. Every failure is swallowed: a dashboard problem must never reach the session that is being watched."""

    def __init__(self, directory: Path | None = None, clock: Callable[[], float] = time.time, interval: float = HEARTBEAT_S) -> None:
        self.dir = directory or live_dir()
        self._clock, self._interval = clock, interval
        self._info: dict[str, Any] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.path = self.dir / f"{os.getpid()}.json"

    def _write(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._info["heartbeat"] = self._clock()
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._info), encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _sweep(self) -> None:
        """Remove files left by sessions that died without cleaning up (a crash, a kill -9). Only a dead pid's file is touched."""
        try:
            for f in self.dir.glob("*.json"):
                if f.stem.isdigit() and int(f.stem) != os.getpid() and not pid_alive(int(f.stem)):
                    f.unlink(missing_ok=True)
        except OSError:
            pass

    def start(self, session_id: str = "", model: str = "", provider: str = "", workspace: str = "", kind: str = "repl") -> None:
        env = os.environ
        model_source = "env WISP_MODEL" if env.get("WISP_MODEL") and env.get("WISP_MODEL") == model else "config"
        self._info = {"pid": os.getpid(), "kind": kind, "session_id": session_id, "model": model, "provider": provider, "model_source": model_source,
                      "workspace": workspace, "started": self._clock()}
        self._sweep()
        self._write()
        self._thread = threading.Thread(target=self._loop, name="wisp-presence", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self._write()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


def read_all(directory: Path | None = None, now: float | None = None, alive: Callable[[int], bool] = pid_alive) -> list[dict[str, Any]]:
    """Every announced session whose process still exists. `state` is `running` while the heartbeat is fresh and `silent` when the process lives but has stopped
    reporting (a stalled loop, a suspended laptop). Fields are copied one by one, capped in length, so a malformed file cannot inject anything else."""
    stamp = time.time() if now is None else now
    rows: list[dict[str, Any]] = []
    try:
        files = sorted((directory or live_dir()).glob("*.json"))
    except OSError:
        return rows
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or not isinstance(d.get("pid"), int) or not alive(d["pid"]):
            continue
        beat, started = d.get("heartbeat"), d.get("started")
        beat = float(beat) if isinstance(beat, (int, float)) else None
        started = float(started) if isinstance(started, (int, float)) else None
        rows.append({"pid": d["pid"], "kind": _clean(d.get("kind")) or "repl", "session_id": _clean(d.get("session_id")), "model": _clean(d.get("model")),
                     "provider": _clean(d.get("provider")), "model_source": _clean(d.get("model_source")), "workspace": _clean(d.get("workspace")),
                     "started": started, "heartbeat": beat, "uptime_s": (stamp - started) if started else None,
                     "heartbeat_age_s": (stamp - beat) if beat else None,
                     "state": "running" if beat is not None and stamp - beat <= STALE_S else "silent", "announced": True})
    return rows


_current: Presence | None = None


def announce(session_id: str = "", model: str = "", provider: str = "", workspace: str = "", kind: str = "repl") -> None:
    global _current
    try:
        if _current is None:
            _current = Presence()
            _current.start(session_id, model, provider, workspace, kind)
    except Exception:  # noqa: BLE001 — see Presence
        _current = None


def withdraw() -> None:
    global _current
    if _current is not None:
        try:
            _current.stop()
        finally:
            _current = None
