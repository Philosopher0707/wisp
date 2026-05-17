"""Bash execution tool for Wisp.

Security-hardened with dangerous command detection, timeout enforcement,
and output size limits.
"""

import logging
import subprocess
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

logger = logging.getLogger(__name__)


def tool_run_bash(command: str, workspace: str, timeout: int = 60) -> str:
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
    logger.info("Running bash (timeout=%ds): %.100s", timeout_val, command)

    start_time = time.time()
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_val,
        )
        # Build output with exit code at the TOP so truncation never loses it
        output = ""
        if result.returncode != 0:
            output = f"[exit code: {result.returncode}]\n"
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if result.stdout:
                output += "\n--- stderr ---\n"
            output += result.stderr

        # Strip ANSI escape codes
        output = _ANSI_RE.sub('', output)

        # Truncate for the model (but log the full output length)
        full_len = len(output)
        if full_len > _MAX_BASH_OUTPUT:
            logger.debug("Bash output truncated (%d chars)", full_len)
            output = output[:_MAX_BASH_OUTPUT] + "\n... [output truncated]"

        duration_ms = round((time.time() - start_time) * 1000)
        logger.info(
            "Bash execution — workspace=%s command=%.100s exit_code=%d output_len=%d duration_ms=%d",
            workspace, command, result.returncode, len(output), duration_ms,
        )
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        logger.warning("Command timed out after %ds: %.100s", timeout_val, command)
        raise ToolError(f"Command timed out after {timeout_val}s: {command[:100]}...")
    except KeyboardInterrupt:
        raise  # Let the signal propagate; do NOT bury it as a ToolError
    except OSError as e:
        logger.error("Command failed with OSError: %s", e)
        raise ToolError(f"Command failed: {e}")
    except Exception as e:
        logger.error("Unexpected error in run_bash: %s", e)
        raise ToolError(f"Command failed: {e}")
