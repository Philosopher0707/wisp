"""Network-pool isolation: stuck NETWORK tools must not starve the main pool.

Issue #4: timed-out tools leak their worker thread (unkillable by design).
With a single shared pool, stuck network tools (web_fetch/web_search) could
exhaust the pool and starve every other tool. NETWORK-risk tools therefore
run on their own bounded pool with separate orphan accounting.
"""

import json
import time

import pytest

from wisp.config import WispConfig
from wisp.tool_executor import ToolExecutor


def _executor(pool_size: int = 2, network_size: int = 1) -> ToolExecutor:
    config = WispConfig().replace(
        tool_timeout=1,
        tool_pool_size=pool_size,
        tool_pool_network_size=network_size,
    )
    return ToolExecutor(config=config)


def _teardown(ex: ToolExecutor) -> None:
    for pool in (getattr(ex, "_tool_pool", None), getattr(ex, "_network_pool", None)):
        if pool is not None:
            try:
                pool.shutdown(wait=False)
            except Exception:
                pass


async def _run_tool(ex: ToolExecutor, name: str, call_id: str = "t1") -> dict:
    """Drive one tool call through execute(); return the parsed result payload."""
    payloads = []
    async for ev in ex.execute(name, {}, "/tmp", tool_call_id=call_id):
        if getattr(ev.type, "value", str(ev.type)) == "tool_result":
            payloads.append(json.loads(ev.data["result"]))
    assert payloads, f"no tool_result emitted for {name}"
    return payloads[-1]


@pytest.mark.asyncio
async def test_stuck_network_tool_does_not_starve_main_pool(monkeypatch):
    """A leaked web_fetch worker must not block a main-pool EXEC tool."""
    ex = _executor()
    try:
        import wisp.tools.registry as reg

        def _stuck(*a, **k):
            # Short on purpose: orphan threads join at interpreter exit,
            # so keep the leak cheap for the suite.
            import time as _t
            _t.sleep(3)

        def _fast(*a, **k):
            return "fast-ok"

        monkeypatch.setitem(reg.TOOL_IMPLS, "web_fetch", _stuck)
        monkeypatch.setitem(reg.TOOL_IMPLS, "wisp_test_fast", _fast)
        monkeypatch.setattr(reg, "_build_tool_metadata", lambda *a, **k: {})

        payload = await _run_tool(ex, "web_fetch")
        assert payload["status"] == "error", payload
        assert "timed out" in payload["data"], payload
        assert ex.network_leaked_tool_threads == 1
        assert ex.leaked_tool_threads == 0

        start = time.monotonic()
        fast_payload = await _run_tool(ex, "wisp_test_fast", call_id="t2")
        elapsed = time.monotonic() - start
        assert fast_payload["status"] == "ok", fast_payload
        assert elapsed < 1.5, f"main pool starved by network leak: {elapsed:.2f}s"
        assert ex.leaked_tool_threads == 0
    finally:
        _teardown(ex)


def test_pool_stats_shape():
    """pool_stats() reports both pools without raising."""
    ex = _executor()
    try:
        stats = ex.pool_stats()
        assert set(stats) == {"default", "network"}
        for entry in stats.values():
            assert set(entry) == {"max_workers", "queued", "threads", "leaked"}
        assert stats["default"]["max_workers"] == 2
        assert stats["network"]["max_workers"] == 1
        assert stats["network"]["leaked"] == 0
        assert stats["default"]["leaked"] == 0
    finally:
        _teardown(ex)


@pytest.mark.asyncio
async def test_pool_timeout_metric_increments(monkeypatch):
    """A timed-out tool bumps AgentMetrics.pool_timeouts_total."""
    from wisp.metrics import AgentMetrics

    metrics = AgentMetrics()
    config = WispConfig().replace(tool_timeout=1, tool_pool_size=2, tool_pool_network_size=1)
    ex = ToolExecutor(config=config, metrics=metrics)
    try:
        import wisp.tools.registry as reg

        def _stuck(*a, **k):
            import time as _t
            _t.sleep(3)

        monkeypatch.setitem(reg.TOOL_IMPLS, "wisp_test_stuck_metric", _stuck)
        monkeypatch.setattr(reg, "_build_tool_metadata", lambda *a, **k: {})

        payload = await _run_tool(ex, "wisp_test_stuck_metric")
        assert payload["status"] == "error", payload
        assert metrics.pool_timeouts_total == 1
    finally:
        _teardown(ex)


def test_record_pool_timeout_unit():
    """record_pool_timeout increments under the metrics lock."""
    from wisp.metrics import AgentMetrics

    m = AgentMetrics()
    assert m.pool_timeouts_total == 0
    m.record_pool_timeout("network")
    m.record_pool_timeout("default")
    assert m.pool_timeouts_total == 2
