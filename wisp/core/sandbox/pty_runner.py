"""Headless raw-PTY shell runner with hard timeouts and dual logging.

Interactive commands deadlock headless agents when they block on ``stdin``
(``read``, password prompts, pagers). This harness:
  - allocates a raw PTY pair (``pty.openpty``) so curses-style programs
    find a terminal instead of failing or hanging on termios ioctls;
  - wires ``stdin`` to ``/dev/null`` — nothing can ever block on input;
  - merges the child's ``stdout``/``stderr`` onto the PTY slave and drains
    the master with ``select`` until EOF or a strict deadline
    (default 30s), killing the whole process group on expiry;
  - returns ANSI-stripped output for model prompts while appending the RAW
    stream (escapes intact) to ``.agent/logs/commands.log``.

POSIX-only (``pty``/``termios`` do not exist on Windows); construction on
other platforms raises ``RuntimeError`` immediately instead of failing
mid-run. All liner notes: timeouts use ``time.monotonic``.
"""

from __future__ import annotations

import logging
import os
import re
import select
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 30.0
READ_CHUNK = 65536
SELECT_SLICE_S = 0.05
TIMEOUT_EXIT_CODE = 124

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-B]|\r")


@dataclass(frozen=True)
class PTYResult:
    """Outcome of one PTY-backed shell execution."""

    command: str
    exit_code: int
    output_clean: str
    output_raw: str
    timed_out: bool
    duration_s: float


def strip_ansi(text: str) -> str:
    """Remove terminal escape sequences for model-facing prompts."""
    return _ANSI_RE.sub("", text)


def _append_command_log(workspace: str, command: str, raw: str, exit_code: int) -> None:
    try:
        log_path = Path(workspace) / ".agent" / "logs" / "commands.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"\n$ {command} [exit={exit_code}]\n{raw}")
            if not raw.endswith("\n"):
                handle.write("\n")
    except OSError:
        logger.debug("command log append failed", exc_info=True)


def run_in_pty(command: str, workspace: str = ".",
               timeout_s: float = DEFAULT_TIMEOUT_S) -> PTYResult:
    """Run *command* under a raw PTY; never blocks on stdin; hard deadline.

    Args:
        command: Shell command line (executed via ``/bin/sh -c``).
        workspace: Working directory and anchor for the command log.
        timeout_s: Wall-clock budget; expiry kills the process group and
            reports ``timed_out=True`` with ``exit_code=124``.

    Raises:
        RuntimeError: on non-POSIX platforms (no PTY support).
        ValueError: on empty command or non-positive timeout.
    """
    if os.name != "posix":
        raise RuntimeError("run_in_pty requires POSIX PTY support")
    if not command or not command.strip():
        raise ValueError("command must not be empty")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")

    import pty
    import tty

    started = time.monotonic()
    master, slave = pty.openpty()
    try:
        tty.setraw(slave)
    except Exception:
        logger.debug("pty setraw failed; continuing in cooked mode", exc_info=True)

    with open(os.devnull, "rb") as devnull:
        proc = subprocess.Popen(
            ["/bin/sh", "-c", command],
            stdin=devnull,
            stdout=slave,
            stderr=slave,
            cwd=workspace,
            start_new_session=True,
            close_fds=True,
        )
    os.close(slave)
    slave = -1

    chunks: list[bytes] = []
    timed_out = False
    deadline = started + timeout_s
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            ready, _, _ = select.select([master], [], [], min(SELECT_SLICE_S, remaining))
            if ready:
                try:
                    data = os.read(master, READ_CHUNK)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
            if proc.poll() is not None:
                # Process exited — drain whatever the PTY still holds,
                # then stop (one extra slice, bounded by the deadline).
                try:
                    ready, _, _ = select.select([master], [], [], SELECT_SLICE_S)
                    if ready:
                        try:
                            data = os.read(master, READ_CHUNK)
                        except OSError:
                            data = b""
                        if data:
                            chunks.append(data)
                except (OSError, ValueError):
                    pass
                break
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

    if timed_out:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass
        exit_code = TIMEOUT_EXIT_CODE
    else:
        try:
            exit_code = proc.wait(timeout=2.0)
        except Exception:
            exit_code = proc.returncode if proc.returncode is not None else 1

    duration_s = time.monotonic() - started
    raw = b"".join(chunks).decode("utf-8", errors="replace")
    clean = strip_ansi(raw)
    _append_command_log(workspace, command, raw, exit_code)
    return PTYResult(command=command, exit_code=exit_code, output_clean=clean,
                     output_raw=raw, timed_out=timed_out, duration_s=duration_s)
