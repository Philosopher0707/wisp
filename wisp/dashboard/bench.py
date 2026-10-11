"""Measure how often the agent solves tasks, per model and per harness configuration, and write every attempt down.

It wraps the judge (`wisp.judge.core.run_one`): each task is run in a throwaway workspace by a child `wisp --print`, then judged by a hidden check the agent never
saw. What this adds is what a number needs to be believable: the child runs THIS checkout (PYTHONPATH is set, and the git identity is recorded, because the venv's own
`wisp` can be another checkout), a run that died of infrastructure (a 429, a 402, a timeout) is retried and kept apart from the pass rate instead of being scored as
the agent's failure, nothing secret is written, and one line per attempt lands in `<bench dir>/<run id>.jsonl` the moment it finishes.

It spends tokens when it runs, so only a person starts it: the dashboard never does.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from wisp.core.gates.secrets import scrub
from wisp.judge import core

#: What the judge cannot tell from the agent's own failure: the provider or the machine failed, not the agent.
_INFRA_TEXT = re.compile(r"\b(?:429|402|503|502)\b|rate.?limit|kept rejecting|timed? ?out|timeout after|connection (?:refused|reset)|no parseable result|no space left|enospc|disk (?:i/o|full)", re.I)
#: A real agent run takes tens of seconds. A run that "finished" and changed nothing in under this long never got to work: the child died at start (a full disk, a broken import).
INSTANT_FAILURE_S = 3.0
_SECRET_KEY = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)
_MESSAGE_CAP = 300


def default_results_dir() -> Path:
    override = os.environ.get("WISP_BENCH_DIR")
    if override:
        return Path(override).expanduser()
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "wisp" / "bench"


@dataclass
class BenchSpec:
    model: str
    provider: str = ""
    api_base: str = ""
    key_from: str = ""
    label: str = "default"
    env: dict[str, str] = field(default_factory=dict)  # extra environment for the child wisp: this is how a harness configuration is chosen
    tasks: list[str] = field(default_factory=list)  # empty = every task
    repeats: int = 1
    timeout: int = 900
    infra_retries: int = 3
    backoff_s: float = 45.0
    wisp_path: str = ""  # the checkout whose `wisp` the child must run
    min_free_mb: int = 500  # refuse to run (rather than record garbage) when the disk has less free space than this
    isolate_home: bool = True  # each attempt gets a fresh empty HOME, so one task's memory, facts and skills cannot leak into the next and your own ~/.config/wisp is never written

    def public(self) -> dict[str, Any]:
        """The spec as it is recorded: environment values for secret-looking names are dropped, and the key's NAME is all that is kept."""
        d = asdict(self)
        d["env"] = {k: v for k, v in self.env.items() if not _SECRET_KEY.search(k)}
        return d


def harness_identity(path: str) -> dict[str, Any]:
    """Which code the child ran: commit, branch, whether the tree had uncommitted changes. Best effort; unknown rather than wrong."""
    def git(*args: str) -> str:
        try:
            out = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
            return out.stdout.strip() if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""
    sha = git("rev-parse", "--short", "HEAD")
    return {"path": path, "sha": sha or "unknown", "branch": git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
            "dirty": bool(git("status", "--porcelain")) if sha else None}


@contextlib.contextmanager
def child_environment(spec: BenchSpec, home: Path | None = None) -> Iterator[None]:
    """The process environment the judge's child inherits: the chosen configuration, PYTHONPATH pointing at the checkout under test, and a private HOME."""
    changes = dict(spec.env)
    if home is not None:
        changes["HOME"] = str(home)
    if spec.wisp_path:
        existing = os.environ.get("PYTHONPATH", "")
        changes["PYTHONPATH"] = spec.wisp_path + (os.pathsep + existing if existing else "")
    saved = {k: os.environ.get(k) for k in changes}
    try:
        os.environ.update(changes)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def looks_like_infrastructure(verdict: core.Verdict) -> bool:
    """True when the run failed for a reason that says nothing about the agent: the judge already said INFRA, or the failure text names a 429, a 402, a timeout."""
    if verdict.verdict == "INFRA":
        return True
    if verdict.verdict == "SOLVED":
        return False
    if verdict.verdict in ("NO-OP", "FAILED") and isinstance(verdict.seconds, (int, float)) and verdict.seconds < INSTANT_FAILURE_S:
        return True
    return bool(_INFRA_TEXT.search(" ".join([verdict.message or "", *verdict.reasons])))


def _row(spec: BenchSpec, run_id: str, ident: dict[str, Any], task: str, repeat: int, attempt: int, final: bool, v: core.Verdict, now: float) -> dict[str, Any]:
    infra = looks_like_infrastructure(v)
    return {
        "run_id": run_id, "ts": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "model": spec.model, "provider": spec.provider or "default", "label": spec.label,
        "harness_sha": ident["sha"], "harness_dirty": ident["dirty"],
        "task": task, "repeat": repeat, "attempt": attempt, "final": final,
        "verdict": "INFRA" if infra and v.verdict != "BROKEN" else v.verdict,
        "original_verdict": v.verdict if infra and v.verdict not in ("INFRA", "BROKEN") else None,
        "reasons": [scrub(r).text[:200] for r in v.reasons], "changed": len(v.changed),
        "visible_rc": v.visible_rc, "hidden_rc": v.hidden_rc, "claim": v.claim, "claim_honest": v.claim_honest,
        "seconds": v.seconds, "message": scrub((v.message or "")[:_MESSAGE_CAP]).text,
    }


class DiskTooFull(RuntimeError):
    """Raised before an attempt when the disk is too full for a run to mean anything: the child cannot write, and the judge would score that as the agent's NO-OP."""


def free_megabytes(path: Path | str) -> float:
    try:
        return shutil.disk_usage(path).free / 1048576
    except OSError:
        return float("inf")


def run_bench(spec: BenchSpec, out_dir: Path, *, free_mb: Callable[[Path | str], float] = free_megabytes, runner: Callable[..., core.Verdict] = core.run_one, sleep: Callable[[float], None] = time.sleep,
              clock: Callable[[], float] = time.time, log: Callable[[str], None] = lambda s: None) -> Path:
    """Run every task `repeats` times, retrying infrastructure failures, and append one JSON line per attempt. Returns the results file."""
    tasks = core.load_tasks()
    wanted = spec.tasks or sorted(tasks)
    unknown = [t for t in wanted if t not in tasks]
    if unknown:
        raise ValueError(f"unknown task(s) {unknown}; known {sorted(tasks)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    started = clock()
    run_id = datetime.fromtimestamp(started, timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + re.sub(r"[^A-Za-z0-9_.-]+", "-", spec.label)[:40]
    ident = harness_identity(spec.wisp_path or ".")
    results = out_dir / f"{run_id}.jsonl"
    meta = out_dir / f"{run_id}.meta.json"
    expected = len(wanted) * spec.repeats

    def write_meta(finished: float | None) -> None:
        meta.write_text(json.dumps({"run_id": run_id, "spec": spec.public(), "harness": ident, "expected_outcomes": expected,
                                    "started": started, "finished": finished}, sort_keys=True), encoding="utf-8")

    write_meta(None)
    cfg = core.RunConfig(model=spec.model, provider=spec.provider, api_base=spec.api_base, key_from=spec.key_from, timeout=spec.timeout)
    with results.open("a", encoding="utf-8") as fh:
        for repeat in range(1, spec.repeats + 1):
            for tid in wanted:
                for attempt in range(1, spec.infra_retries + 2):
                    free = free_mb(tempfile.gettempdir())
                    if free < spec.min_free_mb:
                        raise DiskTooFull(f"only {free:.0f} MB free in {tempfile.gettempdir()} (minimum {spec.min_free_mb} MB): not running {tid}; free some space and start again")
                    home = Path(tempfile.mkdtemp(prefix="wispbench-home-")) if spec.isolate_home else None  # a directory this function made, so removing it is safe
                    try:
                        with child_environment(spec, home):
                            verdict = runner(tasks[tid], cfg)
                    finally:
                        if home is not None:
                            shutil.rmtree(home, ignore_errors=True)
                    infra = looks_like_infrastructure(verdict)
                    last = attempt == spec.infra_retries + 1
                    final = (not infra) or last
                    row = _row(spec, run_id, ident, tid, repeat, attempt, final, verdict, clock())
                    fh.write(json.dumps(row, sort_keys=True) + "\n")
                    fh.flush()
                    log(f"{tid} #{repeat} attempt {attempt}: {row['verdict']}{' (final)' if final else ' (retrying)'}")
                    if final:
                        break
                    sleep(spec.backoff_s * attempt)
    write_meta(clock())
    return results


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Apply today's infrastructure rules to a row written under older ones, without touching the file: the original verdict is kept beside the new one.
    (Nine rows of the first real run were an instant NO-OP in 0.3 s because the disk was full; they were scored as the agent's failure.)"""
    r = dict(row)
    seconds = r.get("seconds")
    if r.get("verdict") in ("NO-OP", "FAILED") and isinstance(seconds, (int, float)) and seconds < INSTANT_FAILURE_S:
        r["original_verdict"] = r.get("original_verdict") or r["verdict"]
        r["verdict"] = "INFRA"
        r["reasons"] = [*r.get("reasons", []), f"instant failure: the child ended in {seconds:g} s, too fast to have worked"]
    return r


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Rows of one results file, with the current infrastructure rules applied; a torn or foreign line is skipped, never fatal."""
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and "task" in d and "verdict" in d:
                rows.append(normalize_row(d))
    except OSError:
        pass
    return rows
