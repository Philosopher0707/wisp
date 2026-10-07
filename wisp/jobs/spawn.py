"""The ONE place a background job is started (BJ1). Callers run the same gates as for a foreground `run_bash` BEFORE calling this; `spawn_job`
repeats the tool's own validation (same functions) so that a refused command never creates a job directory.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import wisp
from wisp.jobs.store import JobStore, _write_atomic, reap_lost
from wisp.tools._utils import _MAX_CMD_LENGTH, _validate_string, check_dangerous_command
from wisp.tools.errors import ToolError
from wisp.tools._utils_env import credential_free_env


class JobLimitError(ToolError):
    """Too many live jobs (BJ7)."""


def _supervisor_env(workspace: str) -> dict[str, str]:
    env, _ = credential_free_env(workspace=workspace)  # the tiers strip credentials too; the supervisor itself never holds them
    env["PYTHONPATH"] = os.pathsep.join([str(Path(wisp.__file__).resolve().parent.parent), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])])
    return env


def spawn_job(store: JobStore, command: str, *, mutation_index: int = 0, max_runtime: int | None = None) -> str:
    _validate_string(command, "command", _MAX_CMD_LENGTH)
    if "\x00" in command:
        raise ToolError("Null bytes not allowed in command")
    danger = check_dangerous_command(command)
    if danger:
        raise ToolError(f"Dangerous command blocked: {danger}")
    lim = store.limits
    store.prune()
    reap_lost(store)
    if len(store.running_ids()) >= lim.max_running_per_workspace:
        raise JobLimitError(f"{lim.max_running_per_workspace} background jobs are already running in this workspace; wait for one or kill it")
    if len(store.running_ids(whole_store=True)) >= lim.max_running_total:
        raise JobLimitError(f"{lim.max_running_total} background jobs are already running for this user")
    runtime = max(1, min(int(max_runtime or lim.default_runtime_s), lim.hard_runtime_s))
    job_id, d = store.new_dir()
    _write_atomic(d / "meta.json", {"id": job_id, "command": command, "workspace": store.workspace, "created": time.time(), "mutation_index": int(mutation_index),
                                    "max_runtime": runtime, "version": 1})
    os.chmod(d / "meta.json", 0o600)
    env = _supervisor_env(store.workspace)
    env["WISP_JOB_ID"] = job_id  # in the launch environment, so it is inherited by everything the job starts (and visible to `ps` on Linux)
    err = open(d / "supervisor.err", "ab")
    try:
        subprocess.Popen([sys.executable, "-m", "wisp.jobs.supervisor", str(d)], cwd=store.workspace, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                         start_new_session=True, close_fds=True, env=env)
    finally:
        err.close()
    return job_id
