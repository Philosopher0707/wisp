"""Bash execution tool for Wisp.

Security-hardened with dangerous command detection, timeout enforcement,
and output size limits.
"""

import asyncio
import os
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from wisp.tools._utils import (
    ToolError,
    _validate_string,
    _validate_int,
    _MAX_CMD_LENGTH,
    _MAX_BASH_OUTPUT,
    _ANSI_RE,
    check_dangerous_command,
)
from wisp.auth.secrets import redact
from wisp.sandbox import get_sandbox
from wisp.sandbox.router import get_router  # the multi-tier router, not `get_sandbox`:
# `get_sandbox` answers Docker-or-host and falls to raw host execution when the daemon
# is missing; the router adds the isolated-PTY tier that works WITHOUT a daemon.

logger = logging.getLogger(__name__)

# Provider names that mean "unconfined host execution" — anything else is
# treated as a real confinement boundary for logging purposes.
_HOST_PROVIDER_NAMES = frozenset({"host", "noop"})


def _format_bash_output(returncode: int, stdout_str: str, stderr_str: str) -> str:
    """Shape raw (exit, stdout, stderr) into the model-facing result.

    Shared by the host and sandbox paths so confinement never changes the
    tool contract: exit code on TOP (truncation-proof), then stdout,
    then a stderr section, ANSI-stripped and length-capped.
    """
    output = ""
    if returncode != 0:
        output = f"[exit code: {returncode}]\n"
    if stdout_str:
        output += stdout_str
    if stderr_str:
        if stdout_str:
            output += "\n--- stderr ---\n"
        output += stderr_str
    output = _ANSI_RE.sub('', output)
    if len(output) > _MAX_BASH_OUTPUT:
        logger.debug("Bash output truncated (%d chars)", len(output))
        output = output[:_MAX_BASH_OUTPUT] + "\n... [output truncated]"
    return output or "(no output)"


@dataclass(frozen=True)
class BashRun:
    """One confined command's raw result, before it is shaped for the model."""

    returncode: int
    stdout: str
    stderr: str
    provider: str
    duration_ms: int
    timed_out: bool


async def run_bash_confined(command: str, workspace: str, timeout: int = 60) -> BashRun:
    """Validate a command and run it through the sandbox tier router.

    The single place a run_bash command executes. Callers that only change where the
    OUTPUT goes (the `agent.tools.runner` disk sink) call this rather than starting a
    process themselves, so they cannot skip the validation, the danger gate, the tier
    router or the UNCONFINED warning. A timeout is reported, not raised, so such a caller
    can record the partial output before raising the tool's own timeout error.
    """
    _validate_string(command, "command", _MAX_CMD_LENGTH)
    timeout_val = _validate_int(timeout, "timeout", 1, 3600)

    # Reject null bytes
    if "\x00" in command:
        raise ToolError("Null bytes not allowed in command")

    # Check dangerous commands
    danger = check_dangerous_command(command)
    if danger:
        raise ToolError(f"Dangerous command blocked: {danger}")

    cwd = Path(workspace).resolve()
    logger.info("Running bash (timeout=%ds): %.100s", timeout_val, redact(command))

    # Confinement (GH#13): route through the sandbox provider — Docker when
    # available, host fallback otherwise. Validation above still applies
    # first so the tool contract (ToolError shapes) never changes.
    # GH#13 follow-up: route through the TIER ROUTER (Docker -> isolated PTY -> host) rather than
    # `get_sandbox`, which knows only Docker-or-host. On a daemon-less host the router lands on
    # `PtySandbox` — own session, rlimits, credential-stripped env — instead of unconfined host
    # execution. `route()` is asked only for the name/reason the log lines report; the call itself
    # goes through the router so failover stays inside it.
    # `WISP_SANDBOX=off` is an OPERATOR'S EXPLICIT CHOICE and the router does not consult it —
    # its tiers are Docker -> PTY -> host unconditionally. Swapping `get_sandbox` for the router
    # without this branch silently overrode the setting (caught by
    # `test_explicit_off_is_info_not_warning`, which asserts an explicit choice must not scream).
    # So: explicit off -> `get_sandbox`, which returns the host provider with `reason="explicit"`;
    # anything else -> the tier router.
    if os.environ.get("WISP_SANDBOX", "").strip().lower() == "off":
        sandbox = get_sandbox(str(cwd))
        # It IS the provider — read name/reason from it directly. Going through `route()` here
        # returned None (NoopSandbox has no `route`), which lost `reason="explicit"` and made an
        # OPERATOR'S EXPLICIT CHOICE emit the UNCONFINED warning the contract says it must not.
        _active = sandbox
    else:
        sandbox = get_router(str(cwd))
        try:
            _active = sandbox.route()
        except Exception:
            _active = None
    provider_name = getattr(_active, "name", "host") or "host"
    if provider_name == "pty":
        # The router's middle tier. It bounds RESOURCE USE and CREDENTIAL EXPOSURE (own session,
        # rlimits, credential-stripped env) but NOT FILESYSTEM REACH — a command here can still
        # write anywhere the user can. Warning rather than going silent: before the router was
        # wired this path shouted UNCONFINED, and a silent downgrade from "shouting" to "nothing"
        # would overstate the confinement. `test_fallback_host_warns_at_tool_layer` asserts it.
        # `UNCONFINED` is kept as the leading token ON PURPOSE: it is the word
        # `test_fallback_host_warns_at_tool_layer` asserts and the word anyone greps for when
        # auditing host execution. The qualifier is what changed — the command IS unconfined with
        # respect to the filesystem, and saying only "UNCONFINED" would overstate the router's win
        # just as saying nothing would.
        logger.warning(
            "run_bash UNCONFINED (filesystem): no Docker daemon — confined by an isolated PTY, so "
            "resource use and credentials are bounded, but filesystem reach is NOT: %.100s",
            redact(command))
    elif provider_name in _HOST_PROVIDER_NAMES and getattr(_active, "reason", "") != "explicit":
        logger.warning(
            "run_bash UNCONFINED: no sandbox provider — executing on host: %.100s",
            redact(command),
        )
    else:
        logger.info(
            "run_bash on %s: %.100s",
            "explicit host" if provider_name in _HOST_PROVIDER_NAMES else f"sandboxed via {provider_name}",
            redact(command),
        )

    start_time = time.time()
    try:
        returncode, stdout_str, stderr_str = await sandbox.run(
            command, cwd="", timeout=timeout_val)
    except asyncio.CancelledError:
        logger.warning("Command execution cancelled: %.100s", redact(command))
        raise
    except ToolError:
        raise
    except OSError as e:
        logger.error("Command failed with OSError: %s", e)
        raise ToolError(f"Command failed: {e}")
    except Exception as e:
        logger.error("Unexpected error in run_bash: %s", e)
        raise ToolError(f"Command failed: {e}")
    timed_out = returncode == -1 and "timed out" in stderr_str.lower()
    if timed_out:
        logger.warning("Command timed out after %ds: %.100s", timeout_val, redact(command)[:100])
    return BashRun(
        returncode=returncode, stdout=stdout_str, stderr=stderr_str, provider=provider_name,
        duration_ms=round((time.time() - start_time) * 1000), timed_out=timed_out)


def timeout_error(command: str, timeout: int) -> ToolError:
    return ToolError(f"Command timed out after {timeout}s: {redact(command)[:100]}...")


async def async_tool_run_bash(command: str, workspace: str, timeout: int = 60) -> str:
    """Run a bash command in the workspace directory.

    Security: validates command length, checks for dangerous commands,
    rejects null bytes, strips ANSI codes, enforces timeout, caps output.
    """
    run = await run_bash_confined(command, workspace, timeout)
    if run.timed_out:
        raise timeout_error(command, _validate_int(timeout, "timeout", 1, 3600))
    output = _format_bash_output(run.returncode, run.stdout, run.stderr)
    logger.info(
        "Bash execution — workspace=%s sandbox=%s command=%.100s exit_code=%d output_len=%d duration_ms=%d",
        workspace, run.provider, redact(command), run.returncode, len(output), run.duration_ms,
    )
    return output


def tool_run_bash(command: str, workspace: str, timeout: int = 60) -> str:
    """Run a bash command in the workspace directory (synchronous compatibility wrapper)."""
    from wisp.async_utils import run_sync_coro
    return run_sync_coro(async_tool_run_bash(command, workspace, timeout))


