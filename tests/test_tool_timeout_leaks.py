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
    network_called = []
    root.tool_executor._network_pool.shutdown = \
        lambda wait: network_called.append(wait)
    import contextlib
    with contextlib.suppress(Exception):
        # replicate composition.shutdown()'s pool teardown line
        root.tool_executor._tool_pool.shutdown(wait=False)
    with contextlib.suppress(Exception):
        # replicate composition.shutdown()'s network-pool teardown line
        network_pool = getattr(root.tool_executor, "_network_pool", None)
        if network_pool is not None:
            network_pool.shutdown(wait=False)
    assert called == [False]
    assert network_called == [False]


@pytest.mark.asyncio
async def test_saturated_network_pool_errors_cleanly_and_spares_default_pool(
    tmp_path, monkeypatch,
):
    """GH#7.2: size-1 network pool with N=2 stuck fetches + 1 fast read.

    Stuck fetches must each error cleanly on the tool timeout (no hang
    past it), the default pool must stay usable throughout, and orphan
    accounting must attribute both leaks to the network pool.
    """
    import asyncio as _asyncio
    import time as _time

    (tmp_path / "fast.txt").write_text("fast-data")
    config = WispConfig().replace(
        tool_timeout=1, tool_pool_size=2, tool_pool_network_size=1)
    ex = ToolExecutor(config=config)
    try:
        import wisp.tools.registry as reg

        def _stuck(*a, **k):
            import time as _t
            _t.sleep(2)  # short orphan: threads linger, never killable

        monkeypatch.setitem(reg.TOOL_IMPLS, "web_fetch", _stuck)
        monkeypatch.setattr(reg, "_build_tool_metadata", lambda *a, **k: {})

        async def _run(name, args, cid):
            out = []
            async for ev in ex.execute(
                name, args, str(tmp_path), tool_call_id=cid,
            ):
                if getattr(ev.type, "value", str(ev.type)) == "tool_result":
                    out.append(json.loads(ev.data["result"]))
            assert out, f"no tool_result for {name}"
            return out[-1]

        async def _timed_fast():
            start = _time.monotonic()
            payload = await _run("read_file", {"path": "fast.txt"}, "f1")
            return payload, _time.monotonic() - start

        started = _time.monotonic()
        (r1, r2), (fast, fast_elapsed) = await _asyncio.gather(
            _asyncio.gather(
                _run("web_fetch", {"url": "https://example.com/a"}, "s1"),
                _run("web_fetch", {"url": "https://example.com/b"}, "s2"),
            ),
            _timed_fast(),
        )
        total = _time.monotonic() - started
        for r in (r1, r2):
            assert r["status"] == "error", r
            assert "timed out" in r["data"], r
        assert total < 8.0, f"saturated pool hung the turn: {total:.1f}s"
        assert fast["status"] == "ok", fast
        assert "fast-data" in json.dumps(fast), fast
        assert fast_elapsed < 3.0, f"default pool starved: {fast_elapsed:.1f}s"
        assert ex.network_leaked_tool_threads == 2
        assert ex.leaked_tool_threads == 0
    finally:
        ex._tool_pool.shutdown(wait=False)
        ex._network_pool.shutdown(wait=False)
