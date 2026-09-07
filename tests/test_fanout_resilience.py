"""Fanout resilience and honesty — the live-run failure modes.

From a real NVIDIA session (2026-08-25): six children spawned against
vague relative paths ('autopipe/core' instead of 'active/autopipe/...')
all failed fast, were announced with ✓ anyway, the 429 rate limit killed
the parent turn, and the aggregate rendered as a raw JSON dump. These
tests pin each fix at its seam.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator
from wisp.multi_agent.task import SubagentContract


def _child_config():
    from wisp.config import WispConfig

    return WispConfig()


# ═══════════════════════════════════════════════════════════════════
# F1: transient failures (429 / rate limit) retry inside run_parallel
# ═══════════════════════════════════════════════════════════════════


class TestTransientRetry:
    def _orch(self, tmp_path):
        return SubagentOrchestrator(config=_child_config(), workspace=tmp_path)

    def test_429_failure_is_retried_and_succeeds(self, tmp_path):
        o = self._orch(tmp_path)
        contract = SubagentContract(name="f-0-coder", role="coder", task="t")
        calls = {"n": 0}

        async def flaky_run(c):
            calls["n"] += 1
            if calls["n"] == 1:
                return SubagentResult_fail(c.name, "API error 429: Too Many Requests")
            return SubagentResult_ok(c.name)

        o.run = flaky_run  # type: ignore[method-assign]
        results = asyncio.run(o.run_parallel([contract], max_concurrent=2))
        assert calls["n"] == 2, "transient failure was not retried"
        assert results[0].success is True

    def test_permanent_failure_not_retried(self, tmp_path):
        o = self._orch(tmp_path)
        contract = SubagentContract(name="f-0-coder", role="coder", task="t")
        calls = {"n": 0}

        async def failing_run(c):
            calls["n"] += 1
            return SubagentResult_fail(c.name, "Path not found: /nope")

        o.run = failing_run  # type: ignore[method-assign]
        results = asyncio.run(o.run_parallel([contract], max_concurrent=2))
        assert calls["n"] == 1, "permanent failure must not burn retries"
        assert results[0].success is False

    def test_retry_cap_respected(self, tmp_path):
        o = self._orch(tmp_path)
        contract = SubagentContract(name="f-0-coder", role="coder", task="t")
        calls = {"n": 0}

        async def always_429(c):
            calls["n"] += 1
            return SubagentResult_fail(c.name, "API error 429")

        o.run = always_429  # type: ignore[method-assign]
        results = asyncio.run(o.run_parallel([contract], max_concurrent=2))
        assert calls["n"] == 3, f"expected 1 attempt + 2 retries, got {calls['n']}"
        assert results[0].success is False


def SubagentResult_ok(name):
    from wisp.multi_agent.task import SubagentResult

    return SubagentResult(task_id=name, success=True, output="done",
                          elapsed_seconds=1.0)


def SubagentResult_fail(name, error):
    from wisp.multi_agent.task import SubagentResult

    return SubagentResult(task_id=name, success=False, output="",
                          error=error, elapsed_seconds=0.5)


# ═══════════════════════════════════════════════════════════════════
# F2: children get workspace grounding in their task text
# ═══════════════════════════════════════════════════════════════════


class TestChildGrounding:
    @pytest.mark.asyncio
    async def test_fanout_tasks_carry_workspace_root(self, tmp_path):
        from wisp.tool_executor import ToolExecutor

        captured = []

        class FakeOrch:
            async def run_parallel(self, contracts, max_concurrent=4):
                captured.extend(contracts)
                return []

        ex = ToolExecutor(_child_config(), subagent_orchestrator=FakeOrch())
        await ex._fanout(
            {"tasks": [{"task": "Analyze autopipe/core", "role": "coder"}],
             "mode": "blocking"},
            str(tmp_path),
        )
        assert captured, "run_parallel never called"
        task_text = captured[0].task
        assert str(tmp_path) in task_text, (
            f"child task lacks workspace root: {task_text!r}"
        )
        assert "relative" in task_text.lower()

    @pytest.mark.asyncio
    async def test_schema_description_mentions_full_paths(self):
        from wisp.tools.registry import TOOL_SCHEMAS

        # TOOL_SCHEMAS is OpenAI wire format: {"type": "function",
        # "function": {name, description, parameters}}
        fanout = next(
            s.get("function", s) for s in TOOL_SCHEMAS
            if s.get("function", s).get("name") == "fanout"
        )
        desc = json.dumps(fanout)
        assert "path" in desc.lower()


class TestChildToolFiltering:
    """Children must not be advertised tools their mode will hard-block."""

    def test_auto_edit_drops_bash_git_spawn(self):
        from wisp.infra.policy_engine import filter_allowed_for_mode

        allowed = filter_allowed_for_mode(
            "auto_edit",
            ["read_file", "write_file", "run_bash", "git_commit",
             "spawn", "fanout", "web_search"],
        )
        assert "run_bash" not in allowed
        assert "git_commit" not in allowed
        assert "spawn" not in allowed
        assert "fanout" not in allowed
        assert "read_file" in allowed
        assert "web_search" in allowed

    def test_full_mode_keeps_everything(self):
        from wisp.infra.policy_engine import filter_allowed_for_mode

        tools = ["read_file", "run_bash", "fanout"]
        assert filter_allowed_for_mode("full", tools) == tools

    def test_read_only_reduces_to_safe_reads(self):
        from wisp.infra.policy_engine import filter_allowed_for_mode

        allowed = filter_allowed_for_mode(
            "read_only", ["read_file", "write_file", "run_bash", "recall"],
        )
        assert allowed == ["read_file", "recall"]

    def test_effective_child_tools_all_expands_and_filters(self):
        from wisp.multi_agent._runner import _effective_child_tools

        tools = _effective_child_tools(None, "auto_edit")
        assert tools, "all-tools path must expand to schema names"
        assert "run_bash" not in tools
        assert "read_file" in tools
        assert "web_search" in tools

    def test_effective_child_tools_explicit_list_still_filtered(self):
        from wisp.multi_agent._runner import _effective_child_tools

        tools = _effective_child_tools(["read_file", "run_bash"], "auto_edit")
        assert tools == ["read_file"]


class TestStartedLineDetail:
    """task_started lines must show task intent, not preamble boilerplate."""

    def _started_detail(self, description: str) -> str:
        from wisp.multi_agent.task import OrchestratorEvent
        from wisp.tool_executor import orchestrator_event_to_agent_event

        ev = orchestrator_event_to_agent_event(
            OrchestratorEvent(
                event_type="task_started",
                task_id="child-0",
                payload={"role": "researcher", "description": description},
            )
        )
        return str(ev.data.get("detail", ""))

    def test_grounding_preamble_stripped_not_truncated_mid_sentence(self):
        grounded = (
            "[Workspace root: /Users/philosopher] Paths are relative to this "
            "root, exactly as they appear in the parent conversation. If a "
            "path is not found, list_files from the workspace root to locate "
            "it before proceeding.\n\nResearch Vespa ranking phases"
        )
        detail = self._started_detail(grounded)
        assert "[Workspace root" not in detail
        assert "Paths are relative" not in detail
        assert detail.startswith("Research Vespa ranking phases")

    def test_long_task_gets_ellipsis_not_hard_cut(self):
        detail = self._started_detail("word " * 60)
        assert len(detail) <= 101
        assert detail.endswith("…")

    def test_short_task_verbatim(self):
        assert self._started_detail("Tiny task") == "Tiny task"

    def test_whitespace_collapsed(self):
        detail = self._started_detail("a\n\n  b\tc")
        assert detail == "a b c"


# ═══════════════════════════════════════════════════════════════════
# G1: generator lifecycle — early close must not raise
# "RuntimeError: generator ignored GeneratorExit" during fanout teardown.
# ═══════════════════════════════════════════════════════════════════

_SSE_CONTENT = b'data: {"choices":[{"delta":{"content":"hi"}}]}'


def _openai_provider_with_sse(sse_lines):
    from unittest.mock import MagicMock, patch

    from wisp.providers.openai import OpenAIProvider

    prov = OpenAIProvider(model="gpt-4o", api_key="sk-test")
    resp = MagicMock()
    resp.status_code = 200
    resp.iter_lines.return_value = list(sse_lines)
    patcher = patch("requests.post", return_value=resp)
    return prov, resp, patcher


class TestGeneratorCloseHygiene:
    def test_close_mid_stream_raises_nothing_and_releases_connection(self):
        prov, resp, patcher = _openai_provider_with_sse(
            [_SSE_CONTENT, _SSE_CONTENT, b"data: [DONE]"]
        )
        with patcher:
            gen = prov.generate_stream_events("s", [{"role": "user", "content": "q"}])
            first = next(gen)
            assert first["type"] == "content"
            gen.close()  # must not raise RuntimeError: generator ignored GeneratorExit
        resp.close.assert_called()

    def test_full_consumption_still_yields_done_and_stats(self):
        prov, _resp, patcher = _openai_provider_with_sse(
            [_SSE_CONTENT, b"data: [DONE]"]
        )
        with patcher:
            types = [e["type"] for e in prov.generate_stream_events(
                "s", [{"role": "user", "content": "q"}])]
        assert "content" in types
        assert "stream_stats" in types
        assert types[-1] == "done"

    @pytest.mark.asyncio
    async def test_bridge_abandon_closes_sync_generator(self):
        closed = {"n": 0}

        def sync_gen():
            # Endless stream: the producer is guaranteed to be suspended
            # mid-iteration (not exhausted) when the consumer abandons.
            try:
                i = 0
                while True:
                    yield {"type": "content", "text": str(i)}
                    i += 1
            except GeneratorExit:
                closed["n"] += 1
                raise

        class P:
            def generate_stream_events(self, *a, **k):
                return sync_gen()

        from wisp.providers.protocol import Provider

        bridge = Provider.generate_stream_events_async(P(), "s", [])
        first = await bridge.__anext__()
        assert first["type"] == "content"
        await bridge.aclose()
        for _ in range(200):
            if closed["n"]:
                break
            await asyncio.sleep(0.01)
        assert closed["n"] == 1, "sync generator was never closed on abandon"


# ═══════════════════════════════════════════════════════════════════
# G2: cancellation cascades — Ctrl+C must join children on a deadline
# ═══════════════════════════════════════════════════════════════════

def _contract(name="g-child", task="t"):
    return SubagentContract(name=name, role="coder", task=task)


class TestCancelCascade:
    @pytest.mark.asyncio
    async def test_cooperative_cancel_cascades_to_all_children(self):
        from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator

        o = SubagentOrchestrator(config=_child_config(), workspace=".")

        async def slow(c):
            await asyncio.sleep(30)
            return SubagentResult_ok(c.name)

        o.run = slow  # type: ignore[method-assign]
        started = asyncio.get_running_loop().time()
        task = asyncio.create_task(
            o.run_parallel([_contract(f"c{i}") for i in range(3)]))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed < 5.0, f"teardown took {elapsed:.1f}s"
        assert all(t.done() for t in o._live_tasks), "orphan child tasks"

    @pytest.mark.asyncio
    async def test_stubborn_child_cannot_stall_teardown(self):
        from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator

        o = SubagentOrchestrator(config=_child_config(), workspace=".")

        async def stubborn(c):
            # Swallows the first two cancels (past the 2s join deadline),
            # honors the third so the test loop stays hermetic.
            ignores = 0
            while True:
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    ignores += 1
                    if ignores >= 3:
                        raise
                    continue
            return SubagentResult_ok(c.name)  # pragma: no cover

        o.run = stubborn  # type: ignore[method-assign]
        started = asyncio.get_running_loop().time()
        try:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(
                    _cancel_after(o, [_contract("stub")], 0.1), timeout=10.0)
            elapsed = asyncio.get_running_loop().time() - started
            assert elapsed < 5.0, f"stubborn child stalled teardown {elapsed:.1f}s"
        finally:
            # Reap the orphan so no pending task leaks into other tests.
            for _ in range(10):
                live = [t for t in o._live_tasks if not t.done()]
                if not live:
                    break
                for t in live:
                    t.cancel()
                await asyncio.sleep(0.1)
            assert all(t.done() for t in o._live_tasks)

    @pytest.mark.asyncio
    async def test_streaming_abandon_cancels_pending(self):
        from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator

        o = SubagentOrchestrator(config=_child_config(), workspace=".")
        started_ev = asyncio.Event()

        async def slow(c):
            started_ev.set()
            await asyncio.sleep(30)
            return SubagentResult_ok(c.name)

        o.run = slow  # type: ignore[method-assign]
        import contextlib

        stream = o.run_parallel_streaming([_contract("s1"), _contract("s2")])
        ait = stream.__aiter__()
        getter = asyncio.create_task(ait.__anext__())
        await asyncio.wait_for(started_ev.wait(), timeout=5.0)
        getter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await getter
        await ait.aclose()
        await asyncio.sleep(0.2)
        pending = [t for t in o._live_tasks if not t.done()]
        assert not pending, f"{len(pending)} orphan streaming subagents"


async def _cancel_after(orch, contracts, delay):
    task = asyncio.create_task(orch.run_parallel(contracts))
    await asyncio.sleep(delay)
    task.cancel()
    return await task


# ═══════════════════════════════════════════════════════════════════
# G3: stream hygiene — tool/provider warnings go to file, not console
# ═══════════════════════════════════════════════════════════════════

class TestLogRouting:
    def _console_capture(self, logger_name):
        import io
        import logging

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        lg = logging.getLogger(logger_name)
        lg.addHandler(handler)
        lg.setLevel(logging.WARNING)
        return lg, handler, stream

    def test_web_fetch_failure_warning_is_file_only(self, tmp_path, monkeypatch):
        import io  # noqa: F401
        import logging

        import agent.logger as alog

        monkeypatch.setattr(alog, "LOG_PATH", tmp_path / "runtime.log")
        alog.install()
        try:
            lg, handler, stream = self._console_capture("wisp.tools.registry")
            try:
                logging.getLogger("wisp.tools.registry").warning(
                    "Tool web_fetch failed: [WEB_FETCH_FAILED] HTTP 404: foo")
            finally:
                lg.removeHandler(handler)
            assert stream.getvalue() == "", (
                f"console polluted: {stream.getvalue()!r}")
            assert "web_fetch failed" in (tmp_path / "runtime.log").read_text()
        finally:
            alog.uninstall()

    def test_provider_warning_is_file_only(self, tmp_path, monkeypatch):
        import logging

        import agent.logger as alog

        monkeypatch.setattr(alog, "LOG_PATH", tmp_path / "runtime.log")
        alog.install()
        try:
            lg, handler, stream = self._console_capture("wisp.providers.openai")
            try:
                logging.getLogger("wisp.providers.openai").warning(
                    "OpenAI provider stream failed: boom")
            finally:
                lg.removeHandler(handler)
            assert stream.getvalue() == "", (
                f"console polluted: {stream.getvalue()!r}")
            assert "OpenAI provider stream failed" in (
                tmp_path / "runtime.log").read_text()
        finally:
            alog.uninstall()


# ═══════════════════════════════════════════════════════════════════
# G4: web tool policy — NL queries redirect, 404 loops trip a breaker
# ═══════════════════════════════════════════════════════════════════

class TestWebGuards:
    def test_natural_language_query_rejected_with_search_redirect(self, monkeypatch):
        import socket

        from wisp.tools.errors import ToolError
        from wisp.tools.web import tool_web_fetch

        def _no_network(*a, **k):
            raise AssertionError("must fail before any DNS/network I/O")

        monkeypatch.setattr(socket, "getaddrinfo", _no_network)
        with pytest.raises(ToolError) as exc:
            tool_web_fetch("quanta magazine how brain works")
        assert "web_search" in str(exc.value)

    def test_missing_scheme_redirects_to_search(self):
        from wisp.tools.errors import ToolError
        from wisp.tools.web import tool_web_fetch

        with pytest.raises(ToolError) as exc:
            tool_web_fetch("example.com/some/page")
        assert "web_search" in str(exc.value)

    @pytest.mark.asyncio
    async def test_fetch_breaker_trips_after_two_consecutive_404s(self, tmp_path):
        from wisp.config import PermissionMode, WispConfig
        from wisp.tool_executor import ToolExecutor
        from wisp.tools.errors import ToolError
        import wisp.tools.web as webmod

        calls = {"n": 0}

        def fake_fetch(url, workspace=".", max_chars=10000):
            calls["n"] += 1
            raise ToolError(
                f"[WEB_FETCH_FAILED] HTTP 404: {url} does not exist. "
                "Do NOT retry the same URL.")

        cfg = WispConfig().replace(
            workspace=str(tmp_path), permission_mode=PermissionMode.FULL,
            auto_approve=True)
        ex = ToolExecutor(config=cfg)
        monkey = pytest.MonkeyPatch()
        monkey.setattr(webmod, "tool_web_fetch", fake_fetch)
        try:
            async def _run(url):
                events = [e async for e in ex.execute(
                    "web_fetch", {"url": url}, str(tmp_path))]
                return events[-1]

            r1 = await _run("https://example.com/dead-1")
            r2 = await _run("https://example.com/dead-2")
            assert "404" in json.dumps(r1.data) + json.dumps(r2.data)
            assert calls["n"] == 2
            r3 = await _run("https://example.com/dead-3")
            assert calls["n"] == 2, "breaker did not short-circuit the 3rd fetch"
            assert "web_search" in json.dumps(r3.data)
        finally:
            monkey.undo()

    @pytest.mark.asyncio
    async def test_fetch_breaker_resets_on_success(self, tmp_path):
        from wisp.config import PermissionMode, WispConfig
        from wisp.tool_executor import ToolExecutor
        from wisp.tools.errors import ToolError
        import wisp.tools.web as webmod

        state = {"fail": True}

        def fake_fetch(url, workspace=".", max_chars=10000):
            if state["fail"]:
                raise ToolError("[WEB_FETCH_FAILED] HTTP 404: gone. Do NOT retry.")
            return "fresh content"

        cfg = WispConfig().replace(
            workspace=str(tmp_path), permission_mode=PermissionMode.FULL,
            auto_approve=True)
        ex = ToolExecutor(config=cfg)
        monkey = pytest.MonkeyPatch()
        monkey.setattr(webmod, "tool_web_fetch", fake_fetch)
        try:
            async def _run(url):
                events = [e async for e in ex.execute(
                    "web_fetch", {"url": url}, str(tmp_path))]
                return events[-1]

            await _run("https://example.com/dead-1")
            state["fail"] = False
            ok = await _run("https://example.com/live")
            assert "fresh content" in json.dumps(ok.data)
            state["fail"] = True
            # One failure after a success must NOT trip the breaker.
            await _run("https://example.com/dead-2")
            r = await _run("https://example.com/dead-3")
            assert "fresh content" not in json.dumps(r.data)
            assert "404" in json.dumps(r.data), (
                "single post-success failure must still dispatch, not block")
        finally:
            monkey.undo()
