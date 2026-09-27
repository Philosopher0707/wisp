"""The cost bound, wired — the gate fires, and refusing is not the same as failing.

`RunBounds.max_cost_usd` was a ceiling with no meter and no gate. `CostMeter` gave it
a meter; this is the gate, at `ToolExecutor.execute` — the boundary that already
exists for refusing a call, so no new mechanism was invented for it.

**The decision the gate encodes:** refuse the NEXT TOOL CALL rather than kill the
turn. The run can still finish and report what it did, so it ends honestly instead of
being cut off mid-flight with a partial transcript. That is the same shape as the
idempotency guard's CONFLICT — do not do the thing, and say why.

The load-bearing test is `test_the_tool_does_not_execute` — a gate that denies *and*
runs would be the worst possible answer.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wisp.config import PermissionMode, WispConfig
from wisp.core.events import DENIAL_BUDGET_EXCEEDED
from wisp.runtime.cost import CostMeter, Price, UnknownPolicy
from wisp.tool_executor import ToolExecutor


def _cfg(workspace: str):
    return WispConfig().replace(workspace=workspace, permission_mode=PermissionMode.FULL)


def _hooks():
    mgr = MagicMock()
    mgr.arun_hooks = AsyncMock(return_value=[])
    mgr.maybe_reload_hooks = MagicMock()
    mgr.load_project_hooks = MagicMock()
    return mgr


def _meter(spent: float) -> CostMeter:
    meter = CostMeter(on_unknown=UnknownPolicy.DECLARED, declared=Price(1.0, 1.0))
    meter.spent_usd = spent
    return meter


def _dispatch():
    return patch.object(ToolExecutor, "_run_blocking", new_callable=AsyncMock,
                        return_value='{"status": "ok"}')


async def _run(te, name="read_file", args=None, ws="."):
    return [ev async for ev in te.execute(name, args or {"path": "a.txt"}, ws)]


def _status(events):
    results = [e for e in events
               if getattr(e, "type", None) == "tool_result"
               or (isinstance(e, dict) and e.get("type") == "tool_result")]
    assert len(results) == 1, f"expected one tool_result, got {len(results)}"
    r = results[0]
    res = r.data.get("result", {}) if hasattr(r, "data") else r.get("result", {})
    return res.get("status") if isinstance(res, dict) else None


# ── The gate ────────────────────────────────────────────────────────────


class TestTheGateFires:
    @pytest.mark.asyncio
    async def test_an_exhausted_run_is_refused(self, tmp_path):
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks(),
                          cost_meter=_meter(5.0), max_cost_usd=2.0)
        with _dispatch():
            events = await _run(te, ws=str(tmp_path))
        assert _status(events) == DENIAL_BUDGET_EXCEEDED

    @pytest.mark.asyncio
    async def test_the_tool_does_not_execute(self, tmp_path):
        """**The forcing assertion.** A gate that denied *and* ran would be the worst
        possible answer — it would spend the money it just refused to spend."""
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks(),
                          cost_meter=_meter(5.0), max_cost_usd=2.0)
        with _dispatch() as seam:
            await _run(te, ws=str(tmp_path))
        seam.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_refusal_says_it_is_not_about_this_tool(self, tmp_path):
        """The model must not conclude the tool is broken and retry it."""
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks(),
                          cost_meter=_meter(5.0), max_cost_usd=2.0)
        with _dispatch():
            events = await _run(te, ws=str(tmp_path))
        text = str(events)
        assert "out of budget" in text
        assert "Finish and report" in text

    @pytest.mark.asyncio
    async def test_the_boundary_is_inclusive(self, tmp_path):
        """At exactly the ceiling the run has spent it."""
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks(),
                          cost_meter=_meter(2.0), max_cost_usd=2.0)
        with _dispatch():
            events = await _run(te, ws=str(tmp_path))
        assert _status(events) == DENIAL_BUDGET_EXCEEDED


# ── The paths that must be unchanged ────────────────────────────────────


class TestNothingElseChanged:
    @pytest.mark.asyncio
    async def test_a_run_under_its_ceiling_is_untouched(self, tmp_path):
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks(),
                          cost_meter=_meter(0.5), max_cost_usd=2.0)
        with _dispatch() as seam:
            await _run(te, ws=str(tmp_path))
        seam.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_meter_is_exactly_the_old_behaviour(self, tmp_path):
        """The safe rollout: passing neither is today's behaviour, byte for byte."""
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks())
        with _dispatch() as seam:
            await _run(te, ws=str(tmp_path))
        seam.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_meter_without_a_ceiling_measures_but_does_not_gate(self, tmp_path):
        """Both are required. A meter alone is observability; a ceiling alone cannot
        fire. Making either one sufficient would be a silent policy."""
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks(),
                          cost_meter=_meter(999.0))
        with _dispatch() as seam:
            await _run(te, ws=str(tmp_path))
        seam.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_ceiling_without_a_meter_cannot_fire(self, tmp_path):
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks(),
                          max_cost_usd=0.01)
        with _dispatch() as seam:
            await _run(te, ws=str(tmp_path))
        seam.assert_called_once()


# ── The charging half ───────────────────────────────────────────────────


class TestTelemetryChargesTheMeter:
    def test_record_turn_charges_when_a_model_is_named(self):
        from wisp.infra.telemetry import Telemetry

        meter = _meter(0.0)
        tel = Telemetry(cost_meter=meter)
        tel.record_turn(10.0, prompt_tokens=1000, completion_tokens=1000, model="x")
        assert meter.spent_usd == 2.0, "the meter was not charged"
        assert meter.charges == [("x", 2.0)]

    def test_record_turn_without_a_model_does_not_charge(self):
        """Keyword-only and optional, so every existing caller is unchanged."""
        from wisp.infra.telemetry import Telemetry

        meter = _meter(0.0)
        tel = Telemetry(cost_meter=meter)
        tel.record_turn(10.0, 1000, 1000)          # the pre-existing call shape
        assert meter.spent_usd == 0.0
        assert tel.prompt_tokens_total == 1000, "the counters still count"

    def test_record_turn_without_a_meter_still_counts(self):
        from wisp.infra.telemetry import Telemetry

        tel = Telemetry()
        tel.record_turn(10.0, 1000, 2000)
        assert (tel.prompt_tokens_total, tel.completion_tokens_total) == (1000, 2000)

    def test_the_two_halves_compose(self):
        """Charging raises the spend; the gate reads it. The property, end to end."""
        from wisp.infra.telemetry import Telemetry

        meter = _meter(0.0)
        Telemetry(cost_meter=meter).record_turn(1.0, 1000, 1000, model="x")
        assert meter.exhausted(2.0) is True
