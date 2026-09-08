"""Terminal-Bench adapter (GH#21).

Bridges the wisp runtime to terminal-centric evals (Terminal-Bench,
InterCode-style harnesses): ingest a task, run a provider observation
loop with backend-executed bash, evaluate the verification script,
serialize the scored artifact.

Deliberate architecture: the agent loop calls provider.generate()
directly (SWE-agent style) instead of core.turn() — core.turn's internal
dispatch cannot route tool execution through benchmark backends without
surgery, and eval harnesses need the tight command/observation loop with
per-turn telemetry the core does not emit.
"""

from __future__ import annotations

import json
import logging
import os
import pty
import re
import select
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "TerminalBenchTask",
    "ExecResult",
    "PTYRunner",
    "TerminalBenchAdapter",
    "sanitize_output",
    "truncate_lines",
    "DONE_SENTINEL",
]

DONE_SENTINEL = "DONE"
DEFAULT_DEADLOCK_AFTER_S = 5.0
DEFAULT_HEAD_LINES = 50
DEFAULT_TAIL_LINES = 50

# Superset of tools._utils._ANSI_RE (which strips SGR 'm' only): full CSI,
# OSC sequences, bells, carriage returns, and stray control chars.
_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
_BELL_RE = re.compile(r"\x07")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_output(text: str) -> str:
    """Strip ANSI/terminal control sequences; keep newlines and tabs."""
    if not text:
        return ""
    out = _OSC_RE.sub("", text)
    out = _CSI_RE.sub("", out)
    out = _BELL_RE.sub("", out)
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    return _CTRL_RE.sub("", out)


def truncate_lines(text: str, head: int = DEFAULT_HEAD_LINES,
                   tail: int = DEFAULT_TAIL_LINES) -> tuple[str, bool]:
    """Keep first `head` + last `tail` lines; collapse the middle.

    Returns (text, was_truncated). Short outputs pass through untouched.
    """
    lines = text.splitlines()
    if len(lines) <= head + tail:
        return text, False
    kept = lines[:head] + [f"[... {len(lines) - head - tail} lines truncated ...]"] + lines[-tail:]
    return "\n".join(kept), True


@dataclass
class TerminalBenchTask:
    """Normalized terminal-bench task definition."""

    task_id: str
    instruction: str
    environment: dict[str, Any] = field(default_factory=dict)
    verification_script: str | None = None
    max_turns: int = 30
    timeout_per_command: float = 45.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TerminalBenchTask":
        if not isinstance(data, dict):
            raise ValueError(f"task must be an object, got {type(data).__name__}")
        task_id = str(data.get("task_id", "") or "").strip()
        instruction = str(data.get("instruction", "") or "").strip()
        if not task_id or not instruction:
            raise ValueError("task requires non-empty task_id and instruction")
        env = data.get("environment") or {}
        if not isinstance(env, dict):
            raise ValueError("task environment must be an object")
        return cls(
            task_id=task_id,
            instruction=instruction,
            environment=dict(env),
            verification_script=data.get("verification_script"),
            max_turns=int(data.get("max_turns", 30) or 30),
            timeout_per_command=float(data.get("timeout_per_command", 45.0) or 45.0),
        )


@dataclass
class ExecResult:
    """One backend command execution, post-hygiene."""

    command: str
    exit_code: int
    output: str
    truncated: bool = False
    timed_out: bool = False
    deadlocked: bool = False
    duration_s: float = 0.0


def _backend_env(extra: dict[str, Any]) -> dict[str, str]:
    """Scrubbed process env overlaid with task environment (task wins)."""
    try:
        from wisp.tools._utils_env import credential_free_env
        env, _ = credential_free_env()
    except Exception:
        env = dict(os.environ)
    for k, v in (extra or {}).items():
        env[str(k)] = str(v)
    return env


class PTYRunner:
    """Local sandboxed PTY backend: bash -c under pty.openpty().

    Quiescence deadlock guard: when the child is alive but produces no
    output for ``deadlock_after_s``, it is assumed to be prompting for
    interactive stdin — SIGINT goes to the pty foreground group, then
    SIGKILL, and the observation advises non-interactive flags.
    """

    def __init__(self, workdir: str | Path,
                 deadlock_after_s: float = DEFAULT_DEADLOCK_AFTER_S) -> None:
        self.workdir = Path(workdir)
        self.deadlock_after_s = deadlock_after_s

    def run(self, command: str, timeout: float,
            env_extra: dict[str, Any] | None = None) -> ExecResult:
        start = time.monotonic()
        master, slave = pty.openpty()
        proc: subprocess.Popen | None = None
        chunks: list[bytes] = []
        deadlocked = False
        timed_out = False
        try:
            proc = subprocess.Popen(
                ["bash", "-c", command],
                stdin=slave, stdout=slave, stderr=slave,
                cwd=str(self.workdir), env=_backend_env(env_extra or {}),
                start_new_session=True, close_fds=True,
            )
            os.close(slave)
            slave = -1
            last_output = time.monotonic()
            int_sent = False
            while True:
                elapsed = time.monotonic() - start
                if proc.poll() is not None:
                    break
                if elapsed >= timeout:
                    timed_out = True
                    break
                try:
                    ready, _, _ = select.select([master], [], [], 0.2)
                except (OSError, ValueError):
                    break
                if ready:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    if not data:
                        if proc.poll() is not None:
                            break
                        continue
                    chunks.append(data)
                    last_output = time.monotonic()
                    int_sent = False
                elif (not int_sent
                      and time.monotonic() - last_output >= self.deadlock_after_s):
                    # Alive but silent: assume an interactive prompt.
                    try:
                        os.write(master, b"\x03")
                    except OSError:
                        pass
                    int_sent = True
                    deadlocked = True
                elif (int_sent
                      and time.monotonic() - last_output >= self.deadlock_after_s + 2.0):
                    break
            rc = proc.poll()
            if rc is None:
                if timed_out or deadlocked:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        try:
                            proc.kill()
                        except Exception:
                            pass
                try:
                    rc = proc.wait(timeout=5)
                except Exception:
                    rc = -1
            # Drain anything the child wrote before dying.
            try:
                while True:
                    r, _, _ = select.select([master], [], [], 0.1)
                    if not r:
                        break
                    data = os.read(master, 65536)
                    if not data:
                        break
                    chunks.append(data)
            except OSError:
                pass
        finally:
            try:
                os.close(master)
            except OSError:
                pass
            if slave != -1:
                try:
                    os.close(slave)
                except OSError:
                    pass
        raw = b"".join(chunks).decode("utf-8", "replace")
        text, truncated = truncate_lines(sanitize_output(raw))
        if deadlocked:
            text += ("\n[deadlock guard: command appeared to wait for interactive "
                     "stdin — sent SIGINT. Re-run with non-interactive flags "
                     "(e.g. -y/--yes, < /dev/null) or here-docs.]")
        return ExecResult(
            command=command, exit_code=int(rc if rc is not None else -1),
            output=text, truncated=truncated, timed_out=timed_out,
            deadlocked=deadlocked, duration_s=time.monotonic() - start)


SYSTEM_PROMPT = (
    "You are a terminal task agent. Achieve the objective using bash commands.\n"
    "Reply with EXACTLY ONE bash command per turn and nothing else "
    "(no fences, no prose). When the objective is complete, reply with exactly: DONE"
)


def _parse_command(content: str) -> str:
    """Extract the model's command: fenced block preferred, else raw text."""
    text = (content or "").strip()
    if not text:
        return ""
    m = re.search(r"```(?:bash|sh)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Single backtick line or plain text — first non-empty line wins for
    # safety against prose spill; the loop shows the model the result.
    for line in text.splitlines():
        line = line.strip().strip("`").strip()
        if line:
            return line
    return ""


class TerminalBenchAdapter:
    """Runs TerminalBenchTasks against a provider + backend, scores them."""

    def __init__(self, provider: Any, workdir: str | Path,
                 backend: Any | None = None,
                 results_dir: str | Path | None = None) -> None:
        self.provider = provider
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.backend = backend or PTYRunner(self.workdir)
        self.results_dir = Path(results_dir) if results_dir else (
            self.workdir / "benchmark_results" / "terminal_bench")

    def _estimate_tokens(self, text: str) -> int:
        return max(1, len(text or "") // 4)  # repo chars_per_token convention

    def run(self, task: TerminalBenchTask) -> dict[str, Any]:
        """Execute task to DONE/max_turns, verify, serialize artifact."""
        started = time.monotonic()
        messages = [{"role": "user", "content": task.instruction}]
        trace: list[dict[str, Any]] = []
        commands: list[str] = []
        breakdown = {"deadlocks": 0, "syntax_errors": 0, "timeouts": 0}
        total_tokens = self._estimate_tokens(SYSTEM_PROMPT + task.instruction)
        turns_used = 0

        for turn in range(1, task.max_turns + 1):
            turns_used = turn
            try:
                reply = self.provider.generate(SYSTEM_PROMPT, messages)
                content = ((reply.get("message") or {}).get("content") or "")
            except Exception as exc:
                trace.append({"turn": turn, "error": f"provider failed: {exc}"})
                break
            total_tokens += self._estimate_tokens(content)
            command = _parse_command(content)
            if not command or command.strip() == DONE_SENTINEL:
                trace.append({"turn": turn, "action": "done"})
                break
            commands.append(command)
            res = self.backend.run(
                command, timeout=task.timeout_per_command,
                env_extra=task.environment.get("env", {}))
            total_tokens += self._estimate_tokens(res.output)
            if res.deadlocked:
                breakdown["deadlocks"] += 1
            if res.timed_out:
                breakdown["timeouts"] += 1
            if res.exit_code != 0 and "syntax error" in res.output.lower():
                breakdown["syntax_errors"] += 1
            trace.append({
                "turn": turn,
                "timestamp": time.time(),
                "command": command,
                "exit_code": res.exit_code,
                "output": res.output,
                "truncated": res.truncated,
                "timed_out": res.timed_out,
                "deadlocked": res.deadlocked,
                "duration_s": round(res.duration_s, 2),
            })
            messages.append({"role": "assistant", "content": command})
            messages.append({
                "role": "user",
                "content": f"[exit {res.exit_code}]\n{res.output}"})

        resolved, verify_code = self._verify(task)
        artifact = {
            "task_id": task.task_id,
            "resolved": resolved,
            "exit_code": verify_code,
            "total_turns": turns_used,
            "total_tokens": total_tokens,
            "duration_seconds": round(time.monotonic() - started, 2),
            "commands_executed": commands,
            "error_breakdown": breakdown,
            "trace": trace,
        }
        self._write_artifact(task.task_id, artifact)
        return artifact

    def _verify(self, task: TerminalBenchTask) -> tuple[bool, int]:
        """Run the verification script in the target environment."""
        if not (task.verification_script or "").strip():
            return False, -1
        res = self.backend.run(
            task.verification_script or "", timeout=task.timeout_per_command,
            env_extra=task.environment.get("env", {}))
        return res.exit_code == 0, res.exit_code

    def _write_artifact(self, task_id: str, artifact: dict[str, Any]) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id) or "task"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        path = self.results_dir / f"{safe}.json"
        path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        return path
