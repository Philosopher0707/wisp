"""Bash execution tool for Wisp.

Security-hardened with dangerous command detection, timeout enforcement,
and output size limits.
"""

import asyncio
import logging
import time
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


async def async_tool_run_bash(command: str, workspace: str, timeout: int = 60) -> str:
    """Run a bash command in the workspace directory.

    Security: validates command length, checks for dangerous commands,
    rejects null bytes, strips ANSI codes, enforces timeout, caps output.
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
    sandbox = get_sandbox(str(cwd))
    provider_name = getattr(sandbox, "name", "host") or "host"
    if provider_name in _HOST_PROVIDER_NAMES:
        logger.warning(
            "run_bash UNCONFINED: no sandbox provider — executing on host: %.100s",
            redact(command),
        )
    else:
        logger.info("run_bash sandboxed via %s: %.100s", provider_name, redact(command))

    start_time = time.time()
    try:
        returncode, stdout_str, stderr_str = await sandbox.run(
            command, cwd="", timeout=timeout_val)
        if returncode == -1 and "timed out" in stderr_str.lower():
            safe_command = redact(command)[:100]
            logger.warning("Command timed out after %ds: %.100s", timeout_val, safe_command)
            raise ToolError(f"Command timed out after {timeout_val}s: {safe_command}...")
        output = _format_bash_output(returncode, stdout_str, stderr_str)

        duration_ms = round((time.time() - start_time) * 1000)
        logger.info(
            "Bash execution — workspace=%s sandbox=%s command=%.100s exit_code=%d output_len=%d duration_ms=%d",
            workspace, provider_name, redact(command), returncode, len(output), duration_ms,
        )
        return output
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


def tool_run_bash(command: str, workspace: str, timeout: int = 60) -> str:
    """Run a bash command in the workspace directory (synchronous compatibility wrapper)."""
    from wisp.async_utils import run_sync_coro
    return run_sync_coro(async_tool_run_bash(command, workspace, timeout))


