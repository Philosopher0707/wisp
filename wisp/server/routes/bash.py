"""Bash router.

Handles bash execution.
"""

import logging
import os
import re
import subprocess
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key, RATE_LIMITER
from wisp.server.routes.workspace import WORKSPACE_ROOT
from wisp.server.routes.files import _resolve_path

logger = logging.getLogger(__name__)
MAX_BASH_OUTPUT = 50_000

router = APIRouter()


class BashRequest(BaseModel):
    command: str = Field(..., max_length=4096)
    cwd: str | None = None


@router.post("/api/bash", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def run_bash(req: BashRequest):
    """Run a bash command inside the workspace. Restricted for safety."""
    from wisp.tools import check_dangerous_command

    if not req.command or not isinstance(req.command, str):
        raise HTTPException(status_code=400, detail="Command must be a non-empty string")
    if "\x00" in req.command:
        raise HTTPException(status_code=400, detail="Null bytes not allowed in command")
    if len(req.command) > 4096:
        raise HTTPException(status_code=400, detail="Command too long (max 4096 chars)")

    danger = check_dangerous_command(req.command)
    if danger:
        logger.warning("Dangerous bash blocked: %s", danger[:200])
        raise HTTPException(status_code=400, detail=f"Dangerous command blocked: {danger}")

    cwd = req.cwd or "."
    target_cwd = _resolve_path(cwd)
    if not target_cwd.is_dir():
        raise HTTPException(status_code=400, detail="Invalid cwd")

    from wisp.sandbox import get_sandbox
    sandbox = get_sandbox(str(WORKSPACE_ROOT))

    start = time.time()
    try:
        timeout = int(os.environ.get("WISP_BASH_TIMEOUT", "60"))
        exit_code, stdout, stderr = await sandbox.run(
            req.command,
            cwd=cwd,
            timeout=timeout,
        )
        duration = round(time.time() - start, 3)

        def _strip_ansi(text: str) -> str:
            return re.sub(r"\x1b\[[0-9;]*m", "", text)

        stdout = _strip_ansi(stdout[:MAX_BASH_OUTPUT])
        stderr = _strip_ansi(stderr[:MAX_BASH_OUTPUT])

        logger.info(
            "bash_exec sandbox=%s exit=%d duration=%.3fs cmd_prefix=%s",
            sandbox.name,
            exit_code,
            duration,
            req.command[:100].replace("\n", "\\n"),
        )
        return {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": False,
            "sandbox": sandbox.name,
        }
    except HTTPException:
        raise
    except subprocess.CalledProcessError:
        logger.exception("bash_exec failed")
        raise HTTPException(status_code=500, detail="Command execution failed")
    except Exception:
        logger.exception("bash_exec failed")
        raise HTTPException(status_code=500, detail="Internal error")
