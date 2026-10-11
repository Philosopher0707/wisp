"""Dynamic multi-tier sandbox router (Docker → isolated PTY → host).

``get_sandbox()`` answers one question — Docker or host — and shouts
UNCONFINED when the daemon is missing. The router adds the middle tier
that commit deserved: a local isolated PTY (own session, rlimits,
credential-stripped env) that beats raw host execution without needing a
daemon. Tiers are an ordered list, so a future MicroVM backend slots in
as one entry — no router changes.

Failover is silent to the model (logging only, never stdout): availability
is probed per call-chain, exceptions fall through to the next tier, and
total failure returns ``(-1, "", err)`` instead of raising.
"""

from __future__ import annotations

import errno
import logging
import os
import pty
import select
import signal
import subprocess
import time
from typing import Sequence

from wisp.sandbox import DockerSandbox, NoopSandbox, SandboxProvider, resolve_sandbox_cwd

logger = logging.getLogger(__name__)

try:
    import resource as _resource
except ImportError:  # non-POSIX: bounds degrade, isolation does not
    _resource = None  # type: ignore[assignment]

# Bound the middle tier: CPU seconds, address space, largest single file.
_PTY_RLIMIT_CPU_S = 600
_PTY_RLIMIT_AS_B = 2 * 1024 * 1024 * 1024
_PTY_RLIMIT_FSIZE_B = 100 * 1024 * 1024


def _pty_preexec() -> None:
    """Child-side confinement: new session + resource ceilings. Never raises."""
    try:
        os.setsid()
    except Exception:
        pass
    if _resource is None:
        return
    for limit, soft, hard in (
        (_resource.RLIMIT_CPU, _PTY_RLIMIT_CPU_S, _PTY_RLIMIT_CPU_S + 30),
        (_resource.RLIMIT_AS, _PTY_RLIMIT_AS_B, _PTY_RLIMIT_AS_B),
        (_resource.RLIMIT_FSIZE, _PTY_RLIMIT_FSIZE_B, _PTY_RLIMIT_FSIZE_B),
    ):
        try:
            _resource.setrlimit(limit, (soft, hard))
        except Exception:
            pass


def _head_and_tail(chunks: list[bytes], size: int, tail: bytes) -> bytes:
    """What was captured of a stream of `size` bytes: its start (`chunks`, recorded up to a cap) and its last bytes (`tail`), joined without a repeat
    when they overlap and with a plain gap line when they do not. Everything between is dropped by the caller's truncation, not here."""
    head = b"".join(chunks)
    if size <= len(head):
        return head
    tail_start = size - len(tail)
    if tail_start <= len(head):
        return head + tail[len(head) - tail_start:]
    return head + b"\n[middle of the output was not captured]\n" + tail


def _clean_pty_output(raw: bytes) -> str:
    """Decode pty bytes to model text: UTF-8, no ANSI, LF newlines."""
    from wisp.tools._utils import _ANSI_RE

    text = _ANSI_RE.sub("", raw.decode("utf-8", errors="replace"))
    return text.replace("\r\n", "\n").replace("\r", "\n")


class PtySandbox(SandboxProvider):
    """Local isolated PTY: middle tier between Docker and raw host exec.

    Own process session (grandchildren die with the group), rlimit ceilings,
    credential-stripped environment, non-interactive output streamed off the
    pty master with ANSI stripped. A pty (not pipes) defeats programs that
    buffer or misbehave when stdout isn't a tty.
    """

    def __init__(self, workspace: str):
        self.workspace = os.path.abspath(workspace)

    @property
    def name(self) -> str:
        return "pty"

    def is_available(self) -> bool:
        return hasattr(pty, "openpty") and os.name == "posix"

    async def run(self, command: str, cwd: str = "", timeout: int = 60) -> tuple[int, str, str]:
        import asyncio as _asyncio

        # to_thread: the master-fd read loop blocks; never stall the loop.
        return await _asyncio.to_thread(self._run_sync, command, cwd, timeout)

    def _run_sync(self, command: str, cwd: str, timeout: int) -> tuple[int, str, str]:
        from wisp.tools._utils import _MAX_BASH_OUTPUT, check_dangerous_command, truncate_output
        from wisp.tools._utils_env import credential_free_env

        danger = check_dangerous_command(command)
        if danger:
            return (-1, "", f"Dangerous command blocked: {danger}")
        # The workspace's virtualenv goes on PATH: the agent's `python`/`pytest` must be the
        # PROJECT's, or the command the prompt suggests cannot run in the env it runs in.
        env, _stripped = credential_free_env(workspace=self.workspace)
        workdir = resolve_sandbox_cwd(self.workspace, cwd)
        if workdir is None:
            return (-1, "", f"cwd escapes workspace: {cwd!r}")
        try:
            master, slave = pty.openpty()
        except OSError as exc:
            return (-1, "", f"pty unavailable: {exc}")
        proc: subprocess.Popen[bytes] | None = None
        try:
            proc = subprocess.Popen(
                ["bash", "-c", command],
                stdin=subprocess.DEVNULL, stdout=slave, stderr=subprocess.STDOUT,
                cwd=workdir, env=env, preexec_fn=_pty_preexec,
            )
            os.close(slave)
            slave = -1
            chunks: list[bytes] = []
            tail = bytearray()  # the last bytes of the whole stream: a test run's verdict is printed last
            size = 0
            deadline = time.monotonic() + timeout
            # ponytail: fixed 64 KiB cap per read; streams longer output
            # incrementally instead of sizing buffers up front.
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                    proc.wait(timeout=5)
                    return (-1, truncate_output(_clean_pty_output(_head_and_tail(chunks, size, bytes(tail)))),
                            f"Command timed out after {timeout}s")
                exited = proc.poll() is not None
                ready, _, _ = select.select([master], [], [], min(remaining, 0.2))
                if ready:
                    try:
                        data = os.read(master, 65536)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            data = b""  # slave closed: normal EOF on ptys
                        else:
                            raise
                    if not data:
                        break
                    # Past the cap, keep draining and discard: the child must run
                    # to completion or its exit status is never known.
                    if size <= _MAX_BASH_OUTPUT + 65536:
                        chunks.append(data)
                    size += len(data)
                    tail += data
                    if len(tail) > _MAX_BASH_OUTPUT:
                        del tail[:-_MAX_BASH_OUTPUT]
                elif exited:
                    # No more output and the child is gone — drain once more.
                    ready2, _, _ = select.select([master], [], [], 0.05)
                    if not ready2:
                        break
            out = truncate_output(_clean_pty_output(_head_and_tail(chunks, size, bytes(tail))))
            # EOF on the pty does not reap the child: without this wait
            # `returncode` is still None and a failing command reads as exit 0.
            try:
                returncode = proc.wait(
                    timeout=max(deadline - time.monotonic(), 0.1))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                proc.wait(timeout=5)
                return (-1, out, f"Command timed out after {timeout}s")
            # NOTE: pty merges stderr into stdout by construction.
            # Empty stays empty: "(no output)" is the formatter's job.
            return (returncode, out, "")
        except Exception as exc:
            return (-1, "", str(exc))
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

    def read_file(self, path: str) -> str:
        full = os.path.join(self.workspace, path)
        with open(full, encoding="utf-8") as f:
            return f.read()

    def write_file(self, path: str, content: str) -> None:
        full = os.path.join(self.workspace, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)


class SandboxRouter:
    """Ordered failover across sandbox tiers. First available wins.

    Default chain: Docker (real confinement) → PTY (isolated local) →
    Noop (host, credential-stripped). Custom chains (e.g. a MicroVM tier)
    inject via ``tiers`` — anything with the SandboxProvider shape.
    """

    def __init__(self, workspace: str,
                 tiers: Sequence[SandboxProvider] | None = None):
        self.workspace = os.path.abspath(workspace)
        self.tiers: list[SandboxProvider] = list(tiers) if tiers is not None else [
            DockerSandbox(self.workspace),
            PtySandbox(self.workspace),
            NoopSandbox(self.workspace),
        ]
        self._routed: SandboxProvider | None = None
        self._routed_at: float = 0.0
        # Re-probing Docker means a `docker info` round-trip (up to 10s
        # against a dead daemon) — cache the decision for a minute.
        self._route_ttl_s: float = 60.0

    def _available(self, tier: SandboxProvider) -> bool:
        try:
            return bool(tier.is_available())
        except Exception:
            logger.debug("Sandbox tier %s probe failed", tier.name, exc_info=True)
            return False

    def route(self) -> SandboxProvider:
        """Pick the highest-priority available tier (cached per workspace)."""
        if (self._routed is not None
                and time.monotonic() - self._routed_at < self._route_ttl_s):
            return self._routed
        if self._routed is not None and self._available(self._routed):
            self._routed_at = time.monotonic()
            return self._routed
        for tier in self.tiers:
            if self._available(tier):
                if self._routed is not tier:
                    logger.info("Sandbox: %s (tier failover settled)", tier.name)
                self._routed = tier
                self._routed_at = time.monotonic()
                return tier
        # Unreachable with the default chain (Noop is always available),
        # but a custom all-unavailable chain must still never raise.
        logger.warning("Sandbox: no tier available — refusing without a provider")
        raise RuntimeError("no sandbox tier available")

    async def run(self, command: str, cwd: str = "",
                  timeout: int = 60) -> tuple[int, str, str]:
        """Run through the routed tier, failing over mid-call on error."""
        tried: list[str] = []
        for tier in self._tier_order():
            try:
                return await tier.run(command, cwd=cwd, timeout=timeout)
            except Exception as exc:
                tried.append(tier.name)
                logger.debug("Sandbox tier %s failed, failing over: %s",
                             tier.name, exc, exc_info=True)
                continue
        return (-1, "", f"all sandbox tiers failed ({', '.join(tried)})")

    def _tier_order(self) -> list[SandboxProvider]:
        """Routed tier first, then the rest — failover without re-probing."""
        try:
            first = self.route()
        except RuntimeError:
            return list(self.tiers)
        return [first, *[t for t in self.tiers if t is not first]]


_routers: dict[str, SandboxRouter] = {}


def _cleanup_router(router: SandboxRouter) -> None:
    """Release resources owned by the providers in a discarded router."""
    cleaned: set[int] = set()
    for tier in router.tiers:
        cleanup = getattr(tier, "cleanup", None)
        if not callable(cleanup) or id(tier) in cleaned:
            continue
        cleaned.add(id(tier))
        try:
            cleanup()
        except Exception:
            logger.warning("Failed to clean up sandbox tier %s", tier.name,
                           exc_info=True)


def get_router(workspace: str | None = None) -> SandboxRouter:
    """Process-global router per resolved workspace (mirrors get_sandbox)."""
    if workspace:
        ws = workspace
    else:
        from wisp.config import safe_getcwd
        ws = safe_getcwd()
    key = os.path.abspath(ws)
    router = _routers.get(key)
    if router is None:
        router = _routers[key] = SandboxRouter(key)
    return router


def reset_router(workspace: str | None = None) -> None:
    """Drop cached routers (tests), optionally for one workspace."""
    if workspace:
        from wisp.config import safe_getcwd
        key = os.path.abspath(workspace or safe_getcwd())
        router = _routers.get(key)
        discarded = [(key, router)] if router is not None else []
    else:
        discarded = list(_routers.items())
    for _, router in discarded:
        _cleanup_router(router)
    for key, router in discarded:
        if _routers.get(key) is router:
            _routers.pop(key, None)
