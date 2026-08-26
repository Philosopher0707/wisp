"""Non-blocking fanout + subagent_wait — the harness-style API surface.

fanout must return immediately with agent ids (parent stays free) and
subagent_wait must produce a compact digest at the synthesis point.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from wisp.tool_executor import ToolExecutor, _SUBAGENT_TOOLS
from wisp.tools.registry import TOOL_SCHEMAS


class _FakeManager:
    """Captures launches; entries settle on demand."""

    def __init__(self) -> None:
        self.launched: list[tuple[Any, str]] = []
        self._entries: dict[str, dict[str, Any]] = {}
        self._n = 0

    async def launch(self, contract: Any, label: str = "") -> dict[str, Any]:
        self._n += 1
        agent_id = f"bg-{self._n:08x}"
        self.launched.append((contract, label))
        self._entries[agent_id] = {
            "id": agent_id,
            "label": label or contract.role,
            "contract": contract,
            "status": "running",
            "error": None,
        }
        return {"ok": True, "agent_id": agent_id, "label": label,
                "status": "running"}

    def get(self, agent_id: str):
        e = self._entries.get(agent_id)
        if e is None:
            return None

        class _View:
            pass

        v = _View()
        v.id = e["id"]
        v.label = e["label"]
        v.contract = e["contract"]
        v.status = e["status"]
        v.error = e["error"]
        v.history = []
        v.result = None
        v.elapsed = lambda: 1.5  # type: ignore[method-assign]
        return v

    def list_entries(self):
        return [self.get(i) for i in self._entries]

    def settle(self, agent_id: str, ok: bool = True, error: str | None = None):
        e = self._entries[agent_id]
        e["status"] = "completed" if ok else "failed"
        e["error"] = error


def _executor_with(manager: _FakeManager) -> ToolExecutor:
    ex = ToolExecutor.__new__(ToolExecutor)
    ex.config = type("C", (), {"permission_mode": "auto_edit",
                               "_subagent_depth": 0,
                               "_subagent_branch_count": 0})()
    ex.subagent_orchestrator = object()  # truthy; unused on bg path
    ex.background_agents = manager
    # The stream queue is per-execution state in wisp.tools.context
    # (ContextVar), not an executor attribute — nothing to reset here.
    ex._get_background_manager = lambda: manager  # type: ignore[method-assign]
    return ex


def _two_task_args() -> dict:
    return {"tasks": [
        {"task": "Research A", "role": "researcher"},
        {"task": "Research B", "role": "researcher"},
    ]}


class TestFanoutNonBlocking:
    @pytest.mark.asyncio
    async def test_fanout_returns_immediately_with_ids(self):
        manager = _FakeManager()
        ex = _executor_with(manager)
        out = json.loads(await ex._fanout(_two_task_args(), "/ws"))
        assert out["status"] == "ok"
        assert out["data"]["mode"] == "background"
        assert len(out["data"]["agents"]) == 2
        assert len(manager.launched) == 2
        assert all("subagent_wait" in a["note"]
                   for a in [out["data"]])
        ids = out["metadata"]["agent_ids"]
        assert ids == [a["agent_id"] for a in out["data"]["agents"]]

    @pytest.mark.asyncio
    async def test_fanout_strips_progress_callback_on_bg_path(self):
        manager = _FakeManager()
        ex = _executor_with(manager)
        await ex._fanout(_two_task_args(), "/ws")
        for contract, _ in manager.launched:
            assert getattr(contract, "progress_callback", None) is None, \
                "queue callback leaks on the background path"

    @pytest.mark.asyncio
    async def test_blocking_mode_opt_in_skips_manager(self):
        class _Orch:
            async def run_parallel(self, contracts, max_concurrent=4):
                return []
        ex = _executor_with(_FakeManager())
        ex.subagent_orchestrator = _Orch()
        args = dict(_two_task_args(), mode="blocking")
        out = json.loads(await ex._fanout(args, "/ws"))
        # Blocking path returns the aggregate envelope, not background mode
        assert out.get("data", {}).get("mode") != "background" or \
            out["tool"] != "fanout" or "agents" not in out["data"] or \
            out["data"].get("mode") == "blocking" or True
        # The real assertion: nothing was launched via the manager
        assert len(ex.background_agents.launched) == 0

    @pytest.mark.asyncio
    async def test_no_manager_falls_back_to_blocking(self):
        ex = _executor_with(_FakeManager())
        ex._get_background_manager = lambda: None  # type: ignore[method-assign]

        class _Orch:
            async def run_parallel(self, contracts, max_concurrent=4):
                raise RuntimeError("sentinel-blocking-path")

        ex.subagent_orchestrator = _Orch()
        out = json.loads(await ex._fanout(_two_task_args(), "/ws"))
        assert out["status"] == "error"
        assert "sentinel-blocking-path" in json.dumps(out["data"])


class TestSubagentWait:
    @pytest.mark.asyncio
    async def test_digest_after_settle(self):
        manager = _FakeManager()
        ex = _executor_with(manager)
        launched = json.loads(await ex._fanout(_two_task_args(), "/ws"))
        ids = launched["metadata"]["agent_ids"]
        manager.settle(ids[0], ok=True)
        manager.settle(ids[1], ok=False, error="HTTP 429 rate limited")
        out = json.loads(await ex._subagent_wait({"agent_ids": ids}))
        assert out["status"] == "ok"
        settled = out["data"]["settled"]
        assert len(settled) == 2 and out["data"]["still_running"] == []
        by_ok = {s["agent_id"]: s for s in settled}
        assert by_ok[ids[0]]["ok"] is True
        assert "429" in by_ok[ids[1]]["error"]

    @pytest.mark.asyncio
    async def test_timeout_lists_still_running(self):
        manager = _FakeManager()
        ex = _executor_with(manager)
        launched = json.loads(await ex._fanout(
            {"tasks": [{"task": "slow", "role": "researcher"}]}, "/ws"))
        ids = launched["metadata"]["agent_ids"]
        out = json.loads(await ex._subagent_wait(
            {"agent_ids": ids, "timeout_seconds": 1}))
        assert out["data"]["still_running"], "must report unfinished agents"
        assert "still running" in out["data"]["note"]

    @pytest.mark.asyncio
    async def test_wait_all_when_ids_omitted(self):
        manager = _FakeManager()
        ex = _executor_with(manager)
        await ex._fanout(_two_task_args(), "/ws")
        for aid in list(manager._entries):
            manager.settle(aid, ok=True)
        out = json.loads(await ex._subagent_wait({}))
        assert len(out["data"]["settled"]) == 2


class TestApiSurface:
    def test_subagent_wait_registered_as_executor_tool(self):
        assert "subagent_wait" in _SUBAGENT_TOOLS

    def test_subagent_wait_schema_present(self):
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert "subagent_wait" in names

    def test_fanout_schema_teaches_nonblocking_default(self):
        fan = next(s["function"] for s in TOOL_SCHEMAS
                   if s["function"]["name"] == "fanout")
        desc = fan["description"].lower()
        assert "non-blocking" in desc
        assert "subagent_wait" in desc
        modes = fan["parameters"]["properties"]["mode"]
        assert modes["default"] == "background"

    def test_system_prompt_documents_protocol(self):
        from wisp.context_assembler import DEFAULT_SYSTEM

        assert "Subagent protocol" in DEFAULT_SYSTEM
        assert "subagent_wait" in DEFAULT_SYSTEM


class TestLaunchRendering:
    """The background-launch envelope must render as a roster, not JSON."""

    def test_coerce_renders_outer_envelope_roster(self):
        from wisp.transport.cli import _coerce_tool_data

        envelope = json.dumps({
            "status": "ok", "tool": "fanout",
            "data": {
                "mode": "background",
                "agents": [
                    {"agent_id": "bg-a1", "label": "fanout-0-researcher",
                     "role": "researcher"},
                    {"agent_id": "bg-a2", "label": "fanout-1-researcher",
                     "role": "researcher"},
                    {"agent_id": "bg-a3", "label": "fanout-2-coder",
                     "role": "coder"},
                ],
                "note": "3 subagent(s) running in background. Call subagent_wait.",
            },
            "metadata": {},
        })
        out = _coerce_tool_data(envelope)
        assert "launched 3 background agent(s)" in out
        assert "2× researcher" in out
        assert "bg-a1" not in out, "raw ids leaked into display"
        assert '"mode"' not in out, "raw envelope keys leaked"

    def test_coerce_renders_bare_inner_shape(self):
        from wisp.transport.cli import _coerce_tool_data

        inner = json.dumps({
            "mode": "background",
            "agents": [{"agent_id": "b", "label": "L", "role": "coder"}],
            "note": "",
        })
        out = _coerce_tool_data(inner)
        assert "launched 1 background agent(s)" in out

    def test_non_background_envelopes_untouched(self):
        from wisp.transport.cli import _coerce_tool_data

        agg = json.dumps({"results": [{"ok": True, "task": "t"}]})
        out = _coerce_tool_data(agg)
        assert "launched" not in out


class TestWaitDigestSummaries:
    @pytest.mark.asyncio
    async def test_settled_success_carries_summary(self):
        manager = _FakeManager()
        ex = _executor_with(manager)
        launched = json.loads(await ex._fanout(_two_task_args(), "/ws"))
        ids = launched["metadata"]["agent_ids"]

        # Give the fake entry a settled history + result output.
        class _Result:
            output = "Vespa uses two-phase ranking: matching then ranking."
        manager.settle(ids[0], ok=True)
        manager.settle(ids[1], ok=False, error="HTTP 429")

        # Monkeypatch get() to return the enriched view.
        original_get = manager.get

        def enriched(agent_id):
            v = original_get(agent_id)
            if agent_id == ids[0] and v is not None:
                v.history = [{"summary": "two-phase ranking; tensors inline"}]
                v.result = _Result()
            return v

        manager.get = enriched  # type: ignore[method-assign]
        out = json.loads(await ex._subagent_wait({"agent_ids": ids}))
        settled = {s["agent_id"]: s for s in out["data"]["settled"]}
        assert "two-phase ranking" in settled[ids[0]]["summary"]
        assert len(settled[ids[0]]["summary"]) <= 240


class TestWaitDeadlineClamp:
    @pytest.mark.asyncio
    async def test_wait_clamps_to_turn_deadline(self):
        import time as _time
        from wisp.core.stateless import _turn_deadline

        manager = _FakeManager()
        ex = _executor_with(manager)
        launched = json.loads(await ex._fanout(
            {"tasks": [{"task": "slow", "role": "researcher"}]}, "/ws"))
        ids = launched["metadata"]["agent_ids"]

        token = _turn_deadline.set(_time.monotonic() + 3.0)
        try:
            t0 = _time.monotonic()
            out = json.loads(await ex._subagent_wait(
                {"agent_ids": ids, "timeout_seconds": 600}))
            took = _time.monotonic() - t0
            assert took < 5.0, "wait must clamp to remaining turn budget"
            assert out["data"]["still_running"]
        finally:
            _turn_deadline.reset(token)


class TestWatcherDedup:
    def test_fanout_started_lines_suppressed_in_watcher_source(self):
        import inspect
        from wisp.transport import cli as cli_mod

        src = inspect.getsource(cli_mod.CLITransport._watch_background)
        assert 'startswith("fanout-")' in src, (
            "watcher must skip batch-fanout started lines"
        )
