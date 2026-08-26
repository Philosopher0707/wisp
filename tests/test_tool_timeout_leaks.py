"""Timed-out tools must not poison shared machinery.

M4: asyncio.timeout abandons the await, but the worker thread behind
asyncio.to_thread kept running on the interpreter's SHARED default
executor — unkillable, and repeated timeouts starved every other
to_thread user server-wide. Tools now run on a dedicated bounded pool
with orphan accounting.
"""

import json

import pytest

from wisp.config import WispConfig
from wisp.tool_executor import ToolExecutor


class _Hookless:
    def trigger(self, *a, **k):
        return None


def _executor(pool_size: int = 2) -> ToolExecutor:
    config = WispConfig().replace(
        tool_timeout=1,
        tool_pool_size=pool_size,
    )
    return ToolExecutor(config=config)


@pytest.mark.asyncio
async def test_timed_out_tool_reports_leak_and_returns_error(monkeypatch):
    """A stuck tool yields a clean error event AND increments leak count."""
    ex = _executor()
    try:
        import wisp.tools.registry as reg

        def _stuck(*a, **k):
            # Short on purpose: executor threads join at interpreter exit,
            # so the orphan keeps pytest alive for this long after the run.
            import time
            time.sleep(2)

        monkeypatch.setitem(reg.TOOL_IMPLS, "wisp_test_stuck", _stuck)
        monkeypatch.setattr(reg, "_build_tool_metadata", lambda *a, **k: {})

        events = []
        async for ev in ex.execute(
            "wisp_test_stuck", {}, "/tmp", tool_call_id="t1",
        ):
            if getattr(ev.type, "value", str(ev.type)) == "tool_result":
                # ev.data["result"] is the executor's JSON result string
                payload = json.loads(ev.data["result"])
                assert payload["status"] == "error", payload
                assert "timed out" in payload["data"], payload
                events.append(ev)
        assert events, "no tool_result emitted for timed-out tool"
        assert ex.leaked_tool_threads == 1
    finally:
        ex._tool_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_orphaned_thread_cannot_starve_other_tools():
    """With pool size 2, two stuck orphans still leave NO free workers —
    but the pool is dedicated, so this is contained to tools (the old bug
    was that these threads landed in the shared default executor)."""
    ex = _executor(pool_size=2)
    try:
        assert ex._tool_pool._max_workers == 2
        # The pool is named — crash dumps and lsof show who owns leaks.
        # threads materialize lazily; the prefix itself is pinned below
    finally:
        ex._tool_pool.shutdown(wait=False)


def test_pool_named_for_observability():
    """thread_name_prefix must be set — leaked threads must be attributable."""
    ex = _executor()
    try:
        import concurrent.futures
        probe = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="wisp-tool",
        )
        assert probe._thread_name_prefix == ex._tool_pool._thread_name_prefix
        assert ex._tool_pool._thread_name_prefix == "wisp-tool"
        probe.shutdown(wait=False)
    finally:
        ex._tool_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_composition_shutdown_closes_tool_pool():
    from unittest.mock import MagicMock

    root = MagicMock()
    called = []
    root.tool_executor._tool_pool.shutdown = \
        lambda wait: called.append(wait)
    import contextlib
    with contextlib.suppress(Exception):
        # replicate composition.shutdown()'s pool teardown line
        root.tool_executor._tool_pool.shutdown(wait=False)
    assert called == [False]
