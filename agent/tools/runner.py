"""run_bash raw sink — writes full stdout/stderr to disk before UI truncation.

Contract §1.2:
  * Every invocation writes `.agent/logs/last_command.log` (overwrite) + `.agent/logs/run_<sanitized>_<ts>.log`
  * UI receives DisplayPayload with preview (≤10 lines) + artifact link
  * No bytes dropped — disk holds original, context sees bounded preview

Wires into wisp/tool_executor.py: ToolExecutor._run_bash_tool and
wisp/tools/bash.py:async_tool_run_bash are wrapped; fallback runs
subprocess directly if wisp not installed.

Uses agent/ui/formatter.collapse() for preview generation.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

try:
    from agent.ui.formatter import collapse, DisplayPayload, ARTIFACT_DIR, LAST_PATH
except Exception:
    from ui.formatter import collapse, DisplayPayload, ARTIFACT_DIR, LAST_PATH  # type: ignore

__all__ = ["RunResult", "run_bash_with_sink", "install_sink", "LOG_DIR"]

LOG_DIR = ARTIFACT_DIR
_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9._-]+")


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    payload: DisplayPayload
    log_path: Path
    last_path: Path
    duration_ms: int


def _sanitize(name: str, max_len: int = 40) -> str:
    s = _SANITIZE_RE.sub("_", name.strip())[:max_len].strip("_")
    return s or "cmd"


def _write_artifacts(cmd: str, stdout: str, stderr: str, exit_code: int, duration_ms: int) -> tuple[Path, Path]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    slug = _sanitize(cmd.split()[0] if cmd.strip() else "cmd")
    # include first 20 chars of sanitized full cmd to disambiguate `pytest` runs
    slug_full = _sanitize(cmd, 20)
    run_path = LOG_DIR / f"run_{slug}_{slug_full}_{ts}.log"
    last_path = LOG_DIR / "last_command.log"

    header = (
        f"# cmd: {cmd}\n"
        f"# exit: {exit_code}  duration_ms: {duration_ms}  ts: {ts}\n"
        f"# cwd: {Path.cwd()}\n"
        f"# ── stdout ──\n"
    )
    body = header + stdout
    if stderr:
        body += f"\n# ── stderr ──\n{stderr}\n"
    body += f"\n# ── end (exit {exit_code}) ──\n"

    # Atomic write via tmp rename
    for p in (run_path, last_path):
        tmp = p.with_suffix(".tmp")
        try:
            tmp.write_text(body, encoding="utf-8", errors="replace")
            tmp.replace(p)
        except Exception:
            p.write_text(body, encoding="utf-8", errors="replace")
    return run_path, last_path


async def run_bash_with_sink(
    cmd: str,
    cwd: str | Path = ".",
    timeout_s: float = 30.0,
    max_preview_lines: Optional[int] = None,
) -> RunResult:
    """Run shell cmd, sink full logs to .agent/logs/, return DisplayPayload preview.

    Always succeeds in writing disk artifacts before returning — even on timeout
    or non-zero exit.  Caller (ToolExecutor) should render `result.payload.preview`
    + badge and keep `result.payload` for `/expand` via ToggleController.
    """
    start = time.monotonic()
    cwd_p = Path(cwd).resolve()
    cwd_p.mkdir(parents=True, exist_ok=True)

    # Resolve max lines from config/env like formatter
    if max_preview_lines is None:
        try:
            max_preview_lines = int(os.getenv("WISP_MAX_TOOL_DISPLAY_LINES", "10"))
        except Exception:
            max_preview_lines = 10
        if os.getenv("WISP_VERBOSE_TOOLS", "").lower() in ("1", "true", "yes"):
            max_preview_lines = 10_000

    proc: Optional[asyncio.subprocess.Process] = None
    stdout = ""
    stderr = ""
    exit_code = 0
    timed_out = False
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(cwd_p),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            stdout = out_b.decode(errors="replace") if out_b else ""
            stderr = err_b.decode(errors="replace") if err_b else ""
            exit_code = proc.returncode or 0
        except asyncio.TimeoutError:
            timed_out = True
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            # try to collect partial output
            try:
                out_b, err_b = await proc.communicate()
                stdout = (out_b or b"").decode(errors="replace")
                stderr = (err_b or b"").decode(errors="replace") + f"\n[timeout after {timeout_s}s]"
            except Exception:
                stderr = f"[timeout after {timeout_s}s — no output captured]"
            exit_code = 124  # timeout sentinel
    except FileNotFoundError as e:
        stderr = str(e)
        exit_code = 127
    except Exception as e:
        stderr = f"runner error: {e}"
        exit_code = 1

    duration_ms = int((time.monotonic() - start) * 1000)
    if timed_out and exit_code != 124:
        exit_code = 124

    # ── Disk sink first (never lost) ──
    full = stdout + (f"\n--- stderr ---\n{stderr}" if stderr else "")
    if timed_out:
        full = f"[timeout {timeout_s}s]\n" + full
    run_path, last_path = _write_artifacts(cmd, stdout, stderr, exit_code, duration_ms)

    # ── UI preview (bounded) — collapse after sink
    payload = collapse(full, tool="run_bash", max_lines=max_preview_lines, full_text=full, artifact_path=run_path)

    return RunResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        payload=payload,
        log_path=run_path,
        last_path=last_path,
        duration_ms=duration_ms,
    )


def run_bash_with_sink_sync(
    cmd: str, cwd: str | Path = ".", timeout_s: float = 30.0, max_preview_lines: Optional[int] = None
) -> RunResult:
    """Sync wrapper for non-async callers (tests, CLI)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_bash_with_sink(cmd, cwd, timeout_s, max_preview_lines))
    # already in loop — run in thread
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(asyncio.run, run_bash_with_sink(cmd, cwd, timeout_s, max_preview_lines))
        return fut.result()


def install_sink() -> None:
    """Monkey-patch wisp tool layers to sink-then-preview.

    Patches:
      * wisp.tools.bash.async_tool_run_bash — underlying bash tool
      * wisp.tool_executor.ToolExecutor._run_bash_tool — executor wrapper
    Idempotent; no-op if wisp not installed.
    """
    patched = 0
    # 1) wisp/tools/bash.py
    try:
        import wisp.tools.bash as _bash

        orig = getattr(_bash, "async_tool_run_bash", None)
        if orig and not getattr(orig, "_sink_patched", False):

            async def _wrapped(cmd: str, workspace: str = ".", timeout: int = 30, **kw):  # type: ignore
                res = await run_bash_with_sink(cmd, cwd=workspace, timeout_s=float(timeout))
                # Return preview text for LLM history, but disk holds full
                # Preserve original contract: caller expects str output
                # We embed badge + link so UI shows preview; full stays on disk.
                preview = res.payload.preview
                # Append exit code sentinel for downstream verifier
                if res.exit_code != 0 and "[exit" not in preview[:30]:
                    preview = f"[exit code: {res.exit_code}]\n" + preview
                return preview

            _wrapped._sink_patched = True  # type: ignore
            _wrapped._orig = orig  # type: ignore
            _bash.async_tool_run_bash = _wrapped  # type: ignore
            patched += 1
    except Exception:
        pass

    # 2) wisp/tool_executor.py — ensure its string-coercion path also benefits
    # ToolExecutor._run_bash_tool already delegates to async_tool_run_bash, so (1) covers it.
    # As fallback, truncate its returned JSON data field via agent.logger hook (already installed).

    if patched:
        import logging

        logging.getLogger("agent.tools.runner").info("run_bash sink installed (%d patches) → %s", patched, LOG_DIR)
