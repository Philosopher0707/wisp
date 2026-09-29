"""Sandbox providers for isolating agent tool execution.

DockerSandbox runs commands in containers with resource limits.
NoopSandbox runs directly on the host with optional Unix resource limits.
"""

from __future__ import annotations

import abc
import asyncio
import hashlib
import logging
import os
import shutil
import signal
import subprocess
from typing import Any
from uuid import uuid4

from wisp.pathsec import resolve_contained


async def _kill_process_group(process) -> None:
    """SIGTERM then SIGKILL to the whole process group (never just the pid).

    start_new_session makes the child PID equal its PGID, so grandchildren
    die too and a reused PID can never point at an unrelated process.
    Async: never blocks the event loop while waiting out SIGTERM.
    """
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        logger.debug("Process-group SIGTERM failed", exc_info=True)
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
        except ProcessLookupError:
            pass
        except Exception:
            logger.debug("Process-group SIGKILL failed", exc_info=True)

logger = logging.getLogger(__name__)


def resolve_sandbox_cwd(workspace: str, cwd: str) -> str | None:
    """Contained workdir for a provider run, or None when it escapes.

    Containment semantics live in `wisp.pathsec.resolve_contained` — the single
    authority; this wrapper must not re-derive the realpath/prefix comparison.
    Never raises: unresolvable or escaping paths fail closed with None.
    """
    try:
        base = os.path.realpath(workspace)
        if not cwd:
            return base
        return resolve_contained(workspace, cwd)
    except Exception:
        return None

# ── Abstract base ─────────────────────────────────────────────────────

class SandboxProvider(abc.ABC):
    """Abstract interface for sandboxed command execution."""

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Return True if this sandbox is ready to use."""

    @abc.abstractmethod
    async def run(self, command: str, cwd: str = "", timeout: int = 60) -> tuple[int, str, str]:
        """Run a shell command and return (exit_code, stdout, stderr)."""

    @abc.abstractmethod
    def read_file(self, path: str) -> str:
        """Read a file from the sandbox filesystem."""

    @abc.abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """Write a file to the sandbox filesystem."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable sandbox name (e.g. 'docker', 'host')."""


# ── Docker sandbox ─────────────────────────────────────────────────────

#: Git metadata that the *host* later executes. `git_commit` / `git_push` /
#: `gh_pr_create` run outside the sandbox, and git runs whatever these name (a hook
#: script; `core.hooksPath`, `core.fsmonitor`, `core.sshCommand`, `alias.x = !cmd`).
#: Read-write inside the container, sandboxed bash could plant code that the next host
#: git call runs. Kept in step with `wisp.pathsec.PROTECTED_PATH_FRAGMENTS`.
_GIT_METADATA_READ_ONLY: tuple[str, ...] = ("hooks", "config")


def _git_metadata_overlays(workspace: str) -> list[str]:
    """`-v` arguments mounting the executable git metadata read-only over the workspace.

    Only what exists is overlaid (a bind mount needs its source), and only for a real `.git`
    directory: a `.git` *file* (worktree, submodule) points at a gitdir outside the mount,
    which the container cannot reach at all. Renaming over a read-only bind mount fails, so
    `git config` (lockfile + rename) is refused as well as a direct write.
    """
    git_dir = os.path.join(workspace, ".git")
    if not os.path.isdir(git_dir):
        return []
    args: list[str] = []
    for name in _GIT_METADATA_READ_ONLY:
        source = os.path.join(git_dir, name)
        if os.path.exists(source):
            args += ["-v", f"{source}:/workspace/.git/{name}:ro"]
    return args


def docker_run_args(container_name: str, workspace: str, image: str,
                    memory: str, cpus: str) -> list[str]:
    """The exact `docker run` argv for the sandbox container (pure, so it is testable)."""
    return [
        "docker", "run", "-d", "--name", container_name,
        "--network", "none",
        f"--memory={memory}",
        f"--cpus={cpus}",
        "-v", f"{workspace}:/workspace",
        *_git_metadata_overlays(workspace),
        "-w", "/workspace",
        "--entrypoint", "sleep",
        image, "infinity",
    ]


class DockerSandbox(SandboxProvider):
    """Runs commands inside a Docker container with resource limits.

    Uses a long-lived container with the workspace mounted.
    Falls back gracefully if Docker is unavailable.
    """

    def __init__(self, workspace: str, image: str = "ubuntu:22.04",
                 memory: str = "2g", cpus: str = "2"):
        self.workspace = os.path.abspath(workspace)
        self.image = image
        self.memory = memory
        self.cpus = cpus
        workspace_id = hashlib.sha256(
            self.workspace.encode("utf-8", errors="surrogateescape")
        ).hexdigest()
        instance_id = uuid4().hex
        self.container_name = f"wisp-sandbox-{workspace_id}-{instance_id}"
        self._available: bool | None = None
        self._container_ready = False

    @property
    def name(self) -> str:
        return "docker"

    def is_available(self) -> bool:
        if self._available is None:
            self._available = shutil.which("docker") is not None
            if self._available:
                try:
                    result = subprocess.run(
                        ["docker", "info"], capture_output=True, text=True, timeout=10
                    )
                    self._available = result.returncode == 0
                except Exception as e:
                    logger.debug("Docker daemon check failed: %s", e)
                    self._available = False
            if not self._available:
                logger.info("Docker not available — will use host execution")
        return self._available

    def _ensure_container(self) -> None:
        if self._container_ready:
            return
        if not self.is_available():
            raise RuntimeError("Docker is not available")

        # Remove any stale container with same name
        subprocess.run(["docker", "rm", "-f", self.container_name],
                       capture_output=True, timeout=10)

        cmd = docker_run_args(self.container_name, self.workspace, self.image,
                              self.memory, self.cpus)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.warning("Failed to start Docker container: %s", result.stderr.strip())
                raise RuntimeError(f"Docker container start failed: {result.stderr.strip()}")
            self._container_ready = True
            logger.info("Docker sandbox container %s started", self.container_name)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Docker container start timed out")

    async def run(self, command: str, cwd: str = "", timeout: int = 60) -> tuple[int, str, str]:
        # Container setup blocks for up to ~40s (rm -f + run -d); on the
        # event loop that froze every connection during cold start.
        await asyncio.to_thread(self._ensure_container)
        resolved = resolve_sandbox_cwd(self.workspace, cwd)
        if resolved is None:
            return (-1, "", f"cwd escapes workspace: {cwd!r}")
        rel = os.path.relpath(resolved, os.path.realpath(self.workspace))
        workdir = "/workspace" if rel == "." else "/workspace/" + rel.replace(os.sep, "/")
        exec_cmd = [
            "docker", "exec", "-w", workdir, self.container_name,
            "bash", "-c", command,
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *exec_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return (-1, "", f"Command timed out after {timeout}s")

            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            return (process.returncode or 0, stdout, stderr)
        except Exception as e:
            return (-1, "", str(e))

    def read_file(self, path: str) -> str:
        full = os.path.join(self.workspace, path)
        with open(full, "r", encoding="utf-8") as f:
            return f.read()

    def write_file(self, path: str, content: str) -> None:
        full = os.path.join(self.workspace, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def cleanup(self) -> None:
        try:
            result = subprocess.run(
                ["docker", "rm", "-f", self.container_name],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info(
                    "Docker sandbox container %s removed", self.container_name
                )
            else:
                output_parts = []
                for stream in (result.stderr, result.stdout):
                    if isinstance(stream, bytes):
                        stream = stream.decode("utf-8", errors="replace")
                    if stream:
                        output_parts.append(str(stream))
                output = "\n".join(output_parts)
                if "no such container" in output.lower():
                    return
                logger.warning(
                    "Failed to remove Docker container %s (exit status %s)",
                    self.container_name,
                    result.returncode,
                )
        except Exception as e:
            logger.warning("Failed to remove Docker container: %s", e)
        finally:
            self._container_ready = False


# ── Noop (host) sandbox ────────────────────────────────────────────────

class NoopSandbox(SandboxProvider):
    """Runs commands directly on the host with optional resource limits.

    Always available — acts as the fallback when Docker is not installed.
    """

    def __init__(self, workspace: str, *, reason: str = "fallback"):
        self.workspace = os.path.abspath(workspace)
        # Why host execution: "explicit" (operator chose WISP_SANDBOX=off)
        # or "fallback" (no sandbox available). The tool layer warns only
        # on fallback — an explicit choice must not scream per call.
        self.reason = reason

    @property
    def name(self) -> str:
        return "host"

    def is_available(self) -> bool:
        return True

    async def run(self, command: str, cwd: str = "", timeout: int = 60) -> tuple[int, str, str]:
        from wisp.tools._utils import check_dangerous_command
        danger = check_dangerous_command(command)
        if danger:
            return (-1, "", f"Dangerous command blocked: {danger}")
        # Parity with the tool path: LLM-generated commands must not see
        # credential env vars even when running unconfined on the host.
        from wisp.tools._utils_env import credential_free_env
        env, _stripped = credential_free_env()
        workdir = resolve_sandbox_cwd(self.workspace, cwd)
        if workdir is None:
            return (-1, "", f"cwd escapes workspace: {cwd!r}")
        try:
            process = await asyncio.create_subprocess_exec(
                "bash", "-c", command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=workdir,
                env=env,
                start_new_session=True,
            )
            # Incremental readers (not communicate()): asyncio drops whatever
            # communicate() had buffered when wait_for times out, so partial
            # output would be lost. Readers own their buffers; EOF after the
            # kill releases them, so the timeout path keeps partial output
            # exactly like the PTY tier (D2).
            stdout_chunks: list[bytes] = []
            stderr_chunks: list[bytes] = []

            async def _drain(stream: Any, chunks: list[bytes]) -> None:
                try:
                    while True:
                        data = await stream.read(65536)
                        if not data:
                            break
                        chunks.append(data)
                except (asyncio.CancelledError, Exception):
                    pass

            readers = [
                asyncio.ensure_future(_drain(process.stdout, stdout_chunks)),
                asyncio.ensure_future(_drain(process.stderr, stderr_chunks)),
            ]
            timed_out = False
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                timed_out = True
                await _kill_process_group(process)
            except asyncio.CancelledError:
                # External cancellation (task.cancel()): the timeout path
                # kills the process group, but cancellation took this branch
                # instead — same cleanup, otherwise sleep grandchildren leak
                # and the test suite waits out the full sleep duration.
                await _kill_process_group(process)
                for r in readers:
                    r.cancel()
                raise
            finally:
                await asyncio.gather(*readers, return_exceptions=True)
            stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
            stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
            if timed_out:
                timeout_msg = f"Command timed out after {timeout}s"
                stderr = f"{stderr}\n{timeout_msg}" if stderr else timeout_msg
                return (-1, stdout, stderr)
            return (process.returncode or 0, stdout, stderr)
        except Exception as e:
            return (-1, "", str(e))

    def read_file(self, path: str) -> str:
        full = os.path.join(self.workspace, path)
        with open(full, "r", encoding="utf-8") as f:
            return f.read()

    def write_file(self, path: str, content: str) -> None:
        full = os.path.join(self.workspace, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)


# ── Factory ────────────────────────────────────────────────────────────

_app_sandbox: SandboxProvider | None = None


def get_sandbox(workspace: str | None = None) -> SandboxProvider:
    """Get or create the global sandbox singleton.

    Tries Docker first, falls back to NoopSandbox (host execution).
    If the workspace has changed since the last call, the old sandbox is
    cleaned up and a new one is created so commands run in the correct
    directory.
    """
    global _app_sandbox
    if workspace:
        ws = workspace
    else:
        from wisp.config import safe_getcwd
        ws = safe_getcwd()
    ws_abs = os.path.abspath(ws)

    if _app_sandbox is not None:
        current_ws = getattr(_app_sandbox, "workspace", None)
        if current_ws == ws_abs:
            return _app_sandbox
        # Workspace changed — tear down the stale sandbox before creating a new one
        logger.info("Sandbox workspace changed %s -> %s; recreating", current_ws, ws_abs)
        reset_sandbox()

    # Explicit kill-switch: WISP_SANDBOX=off (also 0/false/no/host/noop)
    # forces host execution for environments where confinement is handled
    # outside wisp. Anything else means "confine when possible".
    if os.environ.get("WISP_SANDBOX", "auto").strip().lower() in (
        "off", "0", "false", "no", "host", "noop",
    ):
        _app_sandbox = NoopSandbox(ws_abs, reason="explicit")
        logger.info("Sandbox: host (explicitly disabled via WISP_SANDBOX=off)")
        return _app_sandbox

    docker = DockerSandbox(ws_abs)
    if docker.is_available():
        _app_sandbox = docker
        logger.info("Sandbox: Docker (ubuntu:22.04, memory=%s, cpus=%s)", docker.memory, docker.cpus)
    else:
        _app_sandbox = NoopSandbox(ws_abs)
        logger.warning("Sandbox: host (no Docker daemon available) — commands execute directly on host")

    return _app_sandbox


def reset_sandbox() -> None:
    """Reset process-global sandbox state (for testing)."""
    global _app_sandbox
    sandbox = _app_sandbox

    try:
        cleanup = getattr(sandbox, "cleanup", None)
        if callable(cleanup):
            cleanup()
    finally:
        _app_sandbox = None
