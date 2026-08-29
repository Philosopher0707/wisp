"""Sandbox providers for isolating agent tool execution.

DockerSandbox runs commands in containers with resource limits.
NoopSandbox runs directly on the host with optional Unix resource limits.
"""

from __future__ import annotations

import abc
import asyncio
import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

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
        self.container_name = f"wisp-sandbox-{os.getpid()}"
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

        cmd = [
            "docker", "run", "-d", "--name", self.container_name,
            "--network", "none",
            f"--memory={self.memory}",
            f"--cpus={self.cpus}",
            "-v", f"{self.workspace}:/workspace",
            "-w", "/workspace",
            "--entrypoint", "sleep",
            self.image, "infinity",
        ]
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
        workdir = os.path.join("/workspace", cwd) if cwd else "/workspace"
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
        if not self._container_ready:
            return
        try:
            subprocess.run(["docker", "rm", "-f", self.container_name],
                           capture_output=True, timeout=10)
            logger.info("Docker sandbox container %s removed", self.container_name)
        except Exception as e:
            logger.warning("Failed to remove Docker container: %s", e)
        self._container_ready = False


# ── Noop (host) sandbox ────────────────────────────────────────────────

class NoopSandbox(SandboxProvider):
    """Runs commands directly on the host with optional resource limits.

    Always available — acts as the fallback when Docker is not installed.
    """

    def __init__(self, workspace: str):
        self.workspace = os.path.abspath(workspace)

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
        workdir = os.path.join(self.workspace, cwd) if cwd else self.workspace
        try:
            process = await asyncio.create_subprocess_exec(
                "bash", "-c", command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=workdir,
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

    docker = DockerSandbox(ws_abs)
    if docker.is_available():
        _app_sandbox = docker
        logger.info("Sandbox: Docker (ubuntu:22.04, memory=%s, cpus=%s)", docker.memory, docker.cpus)
    else:
        _app_sandbox = NoopSandbox(ws_abs)
        logger.warning("Sandbox: host (no Docker daemon available) — commands execute directly on host")

    return _app_sandbox


def reset_sandbox() -> None:
    """Reset the global sandbox singleton (for testing)."""
    global _app_sandbox
    if _app_sandbox and hasattr(_app_sandbox, "cleanup"):
        _app_sandbox.cleanup()
    _app_sandbox = None
