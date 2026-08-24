"""Bash execution tool for Wisp.

Security-hardened with dangerous command detection, timeout enforcement,
and output size limits.
"""

import asyncio
import logging
import os
import signal
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
from wisp.tools._utils_env import credential_free_env

logger = logging.getLogger(__name__)


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
    logger.info("Running bash (timeout=%ds): %.100s", timeout_val, command)

    # Deny-list scrub: LLM-generated commands must not read credentials
    # from the environment (API keys, cloud tokens, SSH agents).
    env, stripped_env_count = credential_free_env()
    if stripped_env_count:
        logger.debug("Stripped %d credential env vars from bash subprocess", stripped_env_count)

    start_time = time.time()
    proc = None
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_val,
        )
        stdout_str = stdout.decode("utf-8", errors="replace")
        stderr_str = stderr.decode("utf-8", errors="replace")
        returncode = proc.returncode

        # Build output with exit code at the TOP so truncation never loses it
        output = ""
        if returncode != 0:
            output = f"[exit code: {returncode}]\n"
        if stdout_str:
            output += stdout_str
        if stderr_str:
            if stdout_str:
                output += "\n--- stderr ---\n"
            output += stderr_str

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
            workspace, command, returncode, len(output), duration_ms,
        )
        return output or "(no output)"
    except asyncio.TimeoutError:
        logger.warning("Command timed out after %ds: %.100s", timeout_val, command)
        raise ToolError(f"Command timed out after {timeout_val}s: {command[:100]}...")
    except asyncio.CancelledError:
        logger.warning("Command execution cancelled: %.100s", command)
        raise
    except OSError as e:
        logger.error("Command failed with OSError: %s", e)
        raise ToolError(f"Command failed: {e}")
    except Exception as e:
        logger.error("Unexpected error in run_bash: %s", e)
        raise ToolError(f"Command failed: {e}")
    finally:
        if proc and proc.returncode is None:
            import platform
            try:
                if platform.system() == "Windows":
                    import subprocess
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
                else:
                    # With start_new_session=True, the process ID is exactly the process group ID.
                    # This avoids the race condition of os.getpgid() fetching the parent's group
                    # if the child process exited quickly and its PID was reused, which would nuke the Wisp server.
                    os.killpg(proc.pid, signal.SIGTERM)
                    # Wait up to 2 seconds for clean exit, otherwise SIGKILL
                    for _ in range(20):
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=0.1)
                            break
                        except asyncio.TimeoutError:
                            pass
                    else:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                            await proc.wait()
                        except ProcessLookupError:
                            pass
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.warning("Error terminating process group: %s", e)


def tool_run_bash(command: str, workspace: str, timeout: int = 60) -> str:
    """Run a bash command in the workspace directory (synchronous compatibility wrapper)."""
    from wisp.async_utils import run_sync_coro
    return run_sync_coro(async_tool_run_bash(command, workspace, timeout))


