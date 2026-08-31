"""Range-based chunked file reader + bounded async tool runner.

Bottlenecks addressed:
  * read_file(path) on 1000-line files floods context (50k LOC → 200k tokens)
  * sequential tool calls in subagents hit RPM/TPM walls

API:
  * read_file_range(path, start_line, end_line, workspace) -> str  (chunks)
  * BoundedRunner(max_concurrent=4, timeout_s=30) via asyncio.Semaphore + Queue
  * parallel_map(items, fn, max_concurrent) -> list via gather
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, TypeVar

from wisp.tools._utils import _resolve_path, _safe_read_text
from wisp.tools.errors import ToolError

logger = logging.getLogger(__name__)

T = TypeVar("T")
U = TypeVar("U")

# ── Range reader ─────────────────────────────────────────────────

def read_file_range(
    path: str,
    workspace: str = ".",
    start_line: int = 1,
    end_line: Optional[int] = None,
    *,
    max_lines: int = 300,
    max_chars: int = 20_000,
) -> str:
    """Read targeted line range — enforced for files >300 lines.

    Lines are 1-indexed inclusive. If file >300 lines and caller omits
    range (start_line=1, end_line=None), we cap to first `max_lines`.
    """
    if start_line < 1:
        raise ToolError("start_line must be >=1")
    if end_line is not None and end_line < start_line:
        raise ToolError("end_line must be >= start_line")

    content = _safe_read_text(path, workspace, encoding="utf-8")
    lines = content.splitlines()
    total = len(lines)

    # Auto-cap large files when range not specified
    if total > 300 and end_line is None and start_line == 1:
        end_line = min(total, max_lines)

    s = max(1, start_line)
    e = min(total, end_line if end_line is not None else total)
    # Enforce max_lines window
    if (e - s + 1) > max_lines:
        e = s + max_lines - 1

    chosen = lines[s - 1 : e]
    out = "\n".join(chosen)
    if len(out) > max_chars:
        out = out[: max_chars - 40] + "\n… [range truncated — see .agent/runtime.log]"

    header_lines = max_lines if total > 300 else total  # not used, keep for compat
    header = f"--- FILE: {path} | LINES: {total} | SHOWING: {s}-{e} ---\n"
    return header + out


async def aread_file_range(
    path: str,
    workspace: str = ".",
    start_line: int = 1,
    end_line: Optional[int] = None,
    *,
    max_lines: int = 300,
    max_chars: int = 20_000,
) -> str:
    return await asyncio.to_thread(read_file_range, path, workspace, start_line, end_line, max_lines=max_lines, max_chars=max_chars)


# ── Bounded async runner ─────────────────────────────────────────

@dataclass
class ToolCallResult:
    ok: bool
    data: str
    latency_s: float
    error: Optional[str] = None


class BoundedRunner:
    """Strict worker pool for tool execution — avoids provider rate limits.

    Uses asyncio.Queue + Semaphore(4) so fanout never bursts >4 concurrent
    external calls (rg, read, provider streaming).
    """

    def __init__(self, max_concurrent: int = 4, timeout_s: float = 30.0, queue_max: int = 100):
        self.max_concurrent = max(1, max_concurrent)
        self.timeout_s = timeout_s
        self._sem = asyncio.Semaphore(self.max_concurrent)
        self._queue: asyncio.Queue[Tuple[Callable[..., Awaitable[Any]], Tuple, Dict, asyncio.Future]] = asyncio.Queue(maxsize=queue_max)
        self._workers: List[asyncio.Task] = []
        self._running = False

    async def __aenter__(self) -> "BoundedRunner":
        self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.stop()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        for i in range(self.max_concurrent):
            self._workers.append(asyncio.create_task(self._worker(f"worker-{i}")))

    async def stop(self) -> None:
        self._running = False
        for w in self._workers:
            w.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        # drain queue
        while not self._queue.empty():
            try:
                _, _, _, fut = self._queue.get_nowait()
                if not fut.done():
                    fut.cancel()
            except Exception:
                break

    async def _worker(self, name: str) -> None:
        while self._running:
            try:
                fn, args, kwargs, fut = await self._queue.get()
            except asyncio.CancelledError:
                break
            async with self._sem:
                start = time.monotonic()
                try:
                    res = await asyncio.wait_for(fn(*args, **kwargs), timeout=self.timeout_s)
                    if not fut.done():
                        fut.set_result(res)
                except asyncio.TimeoutError:
                    if not fut.done():
                        fut.set_exception(TimeoutError(f"{fn.__name__} timeout after {self.timeout_s}s"))
                except asyncio.CancelledError:
                    if not fut.done():
                        fut.cancel()
                    break
                except Exception as e:
                    if not fut.done():
                        fut.set_exception(e)
                finally:
                    self._queue.task_done()

    async def submit(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        """Enqueue one call; awaits result (bounded by pool + timeout)."""
        if not self._running:
            self.start()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        await self._queue.put((fn, args, kwargs, fut))
        return await fut

    async def map(
        self,
        items: List[T],
        fn: Callable[[T], Awaitable[U]],
        *,
        return_exceptions: bool = True,
    ) -> List[U | BaseException]:
        """Parallel map with bounded concurrency via gather."""
        # Use semaphore directly for map — simpler than queue for this shape
        sem = self._sem

        async def _one(it: T) -> U | BaseException:
            async with sem:
                try:
                    return await asyncio.wait_for(fn(it), timeout=self.timeout_s)
                except Exception as e:
                    if return_exceptions:
                        return e  # type: ignore
                    raise

        # asyncio.gather preserves order
        coros = [_one(it) for it in items]
        return await asyncio.gather(*coros, return_exceptions=return_exceptions)


# ── Convenience parallel helpers for subagents ───────────────────

async def parallel_reads(
    paths: List[str],
    workspace: str,
    *,
    max_concurrent: int = 4,
    max_lines: int = 300,
) -> Dict[str, str]:
    """Concurrent range reads — each file capped to first 300 lines."""
    sem = asyncio.Semaphore(max_concurrent)

    async def _one(p: str) -> Tuple[str, str]:
        async with sem:
            try:
                txt = await aread_file_range(p, workspace, 1, max_lines, max_lines=max_lines)
                return p, txt
            except Exception as e:
                return p, f"Error reading {p}: {e}"

    results = await asyncio.gather(*[_one(p) for p in paths])
    return dict(results)


async def parallel_ripgrep(
    patterns: List[str],
    workspace: str,
    *,
    max_concurrent: int = 4,
    max_results: int = 20,
) -> Dict[str, Any]:
    """Concurrent ripgrep — one rg per pattern."""
    from agent.indexer import ripgrep_search  # local import to avoid cycle

    sem = asyncio.Semaphore(max_concurrent)

    async def _one(pat: str):
        async with sem:
            try:
                hits = await ripgrep_search(pat, workspace, max_results=max_results, timeout_s=6.0)
                return pat, hits
            except Exception as e:
                return pat, e

    outs = await asyncio.gather(*[_one(p) for p in patterns])
    return {k: v for k, v in outs}


__all__ = [
    "read_file_range",
    "aread_file_range",
    "BoundedRunner",
    "ToolCallResult",
    "parallel_reads",
    "parallel_ripgrep",
]
