"""Docker backend for terminal-bench tasks (GH#21).

Manages one container per task: pull/run with limits and mounts, stream
commands via ``docker exec`` (non-tty: interactive prompts fail fast
instead of hanging), destroy on teardown.

The Docker daemon is unavailable in this dev environment, so every Docker
CLI interaction flows through the injectable ``exec_fn`` seam — unit
tests pin the full lifecycle without a daemon, and the default seam
shells out for real. Live-Docker execution is explicitly unverified here.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from wisp.benchmark.adapters.terminal_bench import (
    ExecResult,
    sanitize_output,
    truncate_lines,
)

logger = logging.getLogger(__name__)

__all__ = ["DockerBackend", "DockerUnavailable"]

#: exec_fn(cmd: list[str], timeout_s: float) -> (rc, stdout, stderr)
ExecFn = Callable[[list[str], float], tuple[int, str, str]]


class DockerUnavailable(RuntimeError):
    """Raised when no Docker daemon answers (setup path, never silent)."""


def _default_exec(cmd: list[str], timeout_s: float) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_s)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"docker exec timed out after {timeout_s:g}s"
    except FileNotFoundError:
        raise DockerUnavailable("docker binary not found")
    except Exception as exc:
        raise DockerUnavailable(f"docker invocation failed: {exc}")


@dataclass
class DockerBackend:
    """One container per benchmark task."""

    image: str
    workdir: str | Path = "/workspace"
    env: dict[str, str] = field(default_factory=dict)
    memory: str = "2g"
    cpus: str = "2"
    network: str = "none"
    exec_fn: ExecFn | None = None
    container_name: str = ""
    _ready: bool = False

    def _exec(self, cmd: list[str], timeout_s: float) -> tuple[int, str, str]:
        fn = self.exec_fn or _default_exec
        return fn(cmd, timeout_s)

    def _docker_available(self) -> bool:
        try:
            rc, _, _ = self._exec(["docker", "info"], 10.0)
            return rc == 0
        except DockerUnavailable:
            return False

    def setup(self, timeout_s: float = 120.0) -> None:
        """Pull (best-effort), create and start the container. Raises
        DockerUnavailable instead of silently degrading."""
        if not self._docker_available():
            raise DockerUnavailable("no Docker daemon answered `docker info`")
        if not self.container_name:
            import uuid
            self.container_name = f"wisp-tbench-{uuid.uuid4().hex[:8]}"
        self._exec(["docker", "rm", "-f", self.container_name], 15.0)
        create = ["docker", "run", "-d", "--name", self.container_name,
                  "--network", self.network, f"--memory={self.memory}",
                  f"--cpus={self.cpus}", "-w", str(self.workdir)]
        for k, v in self.env.items():
            create += ["-e", f"{k}={v}"]
        create += [self.image, "sleep", "infinity"]
        rc, _out, err = self._exec(create, timeout_s)
        if rc != 0:
            raise DockerUnavailable(f"container start failed: {err[:200]}")
        self._ready = True
        logger.info("tbench container %s ready (%s)", self.container_name, self.image)

    def run(self, command: str, timeout: float,
            env_extra: dict[str, Any] | None = None) -> ExecResult:
        """Execute via docker exec; same ExecResult contract as PTYRunner."""
        import time

        start = time.monotonic()
        if not self._ready:
            return ExecResult(command=command, exit_code=-1, output="",
                              duration_s=0.0)
        exec_cmd = ["docker", "exec", "-i", "-w", str(self.workdir),
                    self.container_name, "bash", "-c", command]
        try:
            rc, out, err = self._exec(exec_cmd, timeout)
        except DockerUnavailable as exc:
            return ExecResult(command=command, exit_code=-1, output=str(exc),
                              duration_s=time.monotonic() - start)
        timed_out = rc == -1 and "timed out" in (err or "")
        combined = (out or "")
        if err:
            combined += ("\n--- stderr ---\n" + err) if combined else err
        text, truncated = truncate_lines(sanitize_output(combined))
        return ExecResult(command=command, exit_code=int(rc), output=text,
                          truncated=truncated, timed_out=timed_out,
                          duration_s=time.monotonic() - start)

    def teardown(self) -> None:
        """Destroy the container; never raises (teardown must not fail runs)."""
        if not self.container_name:
            return
        try:
            self._exec(["docker", "rm", "-f", self.container_name], 15.0)
        except Exception as exc:
            logger.debug("tbench teardown failed: %s", exc)
        finally:
            self._ready = False
