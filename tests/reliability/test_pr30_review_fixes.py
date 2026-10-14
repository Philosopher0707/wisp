"""Regression guards for the three defects the PR #30 review found.

1. **A decision-key approval bypassed the REST bridge** (`server/routes/agents.py`). The TUI
   answers `{type: tool_approval, id, decision: "y"}`; the route sent every `decision` frame to
   `WebSocketTransport.resolve_decision`, whose unknown-id fallback resolves the single pending
   *agent* approval. So a "yes" to a REST hook registration approved an unrelated agent tool
   call, and the REST request timed out to 403.
2. **The verdict reused a stale probe** (`core/turn_criteria.py`). `DeclaredCriteriaGate` cached
   its measurement and the runtime reused it — but a *withheld* `done` is followed by more work,
   and the engine skips the gate once the shared budget is spent, so the verdict described the
   workspace before the fix.
3. **A model-authored prompt reached the host's command probe** (`core/runtime.py`). A subagent
   task is written by the parent model; a `--- criteria ---` head block in it made the host
   `subprocess.run` its `command_succeeds` argv, outside ToolExecutor's approval and sandbox —
   ADR-0050 R6's "the objective comes from the caller" does not hold when the caller is the model.

Each test drives the production object (the real bridge, transport, gate and runtime), not a copy
of it (F96), and each has a control that shows the check can fail.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.core.runtime import AgentRuntime
from wisp.core.session_repo import SessionRepository
from wisp.core.turn_criteria import (
    MODEL_AUTHORED_PROMPT_KEY,
    declared_criteria_gate,
    turn_acceptance_verdict,
)
from wisp.core.verification import VerificationFloorGuard
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import SecurityPolicy
from wisp.infra.store import UnifiedStore
from wisp.infra.telemetry import Telemetry

REPO = pathlib.Path(__file__).resolve().parents[2]


# ── 2. the verdict does not reuse a probe taken before more work ────────────

APP_PY = "def parse_duration(s):\n    return s\n"
DECL = ("--- criteria ---\n"
        "symbol_defined: app.py::fixed_later\n"
        "--- /criteria ---\n"
        "Add fixed_later to app.py.\n")


class TestAWithheldProbeIsNotReused:
    def test_a_withheld_measurement_is_not_final(self, tmp_path):
        (tmp_path / "app.py").write_text(APP_PY)
        gate = declared_criteria_gate(DECL, str(tmp_path))
        assert gate is not None and gate() is False, "floor: the declaration must fail first"
        assert gate.last_measurement is not None
        assert gate.final_measurement is None, (
            "a withheld done is followed by more work — its probe must not be reused")

    def test_the_verdict_reflects_the_fix_made_after_the_withhold(self, tmp_path):
        """The scenario: withheld, the model fixes the code, the budget is spent so the engine
        skips the gate, and the verdict site runs with whatever the runtime hands it."""
        (tmp_path / "app.py").write_text(APP_PY)
        gate = declared_criteria_gate(DECL, str(tmp_path))
        assert gate() is False
        (tmp_path / "app.py").write_text(APP_PY + "def fixed_later():\n    return 1\n")
        guard = VerificationFloorGuard()
        verdict, tc = turn_acceptance_verdict(guard, DECL, str(tmp_path), enabled=True,
                                              measurement=gate.final_measurement)
        assert tc.declared
        failed = set(verdict.unmet_criteria or ())
        assert not any(c.startswith("declared:") for c in failed), (
            f"the verdict still fails the declared criterion ({sorted(failed)}) — it was "
            "computed from the probe taken before the fix")
        stale, _ = turn_acceptance_verdict(guard, DECL, str(tmp_path), enabled=True,
                                           measurement=gate.last_measurement)
        assert any(c.startswith("declared:") for c in (stale.unmet_criteria or ())), (
            "control: reusing the pre-fix probe must reproduce the stale FAIL")

    def test_a_passing_measurement_is_final_and_reused(self, tmp_path):
        (tmp_path / "app.py").write_text(APP_PY + "def fixed_later():\n    return 1\n")
        gate = declared_criteria_gate(DECL, str(tmp_path))
        assert gate() is True
        assert gate.final_measurement is gate.last_measurement, (
            "a passing probe is followed directly by done — reusing it is ADR-0054 R3's "
            "one-probe-per-turn and must be kept")

    def test_the_runtime_hands_over_only_the_final_measurement(self):
        src = (REPO / "wisp/core/runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                 and getattr(n.value, "id", None) == "declared_gate"}
        assert "final_measurement" in attrs, "the runtime no longer reads final_measurement"
        assert "last_measurement" not in attrs, (
            "the runtime reads last_measurement — a withheld, stale probe reaches the verdict")


# ── 3. a model-authored prompt cannot make the host run a command ───────────

class _Provider:
    def generate_stream_events(self, system_prompt, messages, tools=None):
        yield {"type": "content", "text": "done"}
        yield {"type": "done", "done_reason": "stop"}


def _run_turn(tmp_path, prompt: str, *, model_authored: bool) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    config = WispConfig().replace(workspace=str(ws), turn_criteria_source=True,
                                  acceptance_gate=True)
    store = UnifiedStore(tmp_path / "wisp.db")
    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
        telemetry=Telemetry(),
        core_factory=lambda: WispAgentCore(config=config, provider=_Provider(),
                                           security=SecurityPolicy(), tool_executor=None),
        session_repo=SessionRepository(store), config=config)
    session = {"id": "t", "model": "mock", "workspace": str(ws), "messages": []}
    if model_authored:
        session[MODEL_AUTHORED_PROMPT_KEY] = True

    async def main():
        return [ev async for ev in runtime.run_turn(session, prompt=prompt)]
    asyncio.run(main())


def _decl_touching(sentinel: pathlib.Path) -> str:
    return ("--- criteria ---\n"
            f"command_succeeds: touch {sentinel}\n"
            "--- /criteria ---\n"
            "Do the task.\n")


class TestAModelAuthoredPromptRunsNoCommand:
    def test_a_subagent_task_declaration_is_not_executed(self, tmp_path):
        sentinel = tmp_path / "PROBE_RAN"
        _run_turn(tmp_path, _decl_touching(sentinel), model_authored=True)
        assert not sentinel.exists(), (
            "the host executed a command from a model-authored prompt's criteria block — "
            "outside ToolExecutor's approval and sandbox")

    def test_control_a_caller_authored_declaration_is_measured(self, tmp_path):
        """Without this, the test above could pass because the probe never runs at all."""
        sentinel = tmp_path / "PROBE_RAN"
        _run_turn(tmp_path, _decl_touching(sentinel), model_authored=False)
        assert sentinel.exists(), "control: a caller's declaration must still be measured"

    def test_the_subagent_runner_marks_the_task_as_model_authored(self):
        """The runner is the one production path that feeds a model-written prompt to
        `run_turn`; it must stamp the marker before the call (AST, not text — F73)."""
        src = (REPO / "wisp/multi_agent/_runner.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        marks, turns = [], []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Subscript)
                    and getattr(node.targets[0].value, "id", None) == "runtime_session"
                    and getattr(node.targets[0].slice, "id", None) == "MODEL_AUTHORED_PROMPT_KEY"):
                marks.append(node.lineno)
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "run_turn"):
                turns.append(node.lineno)
        assert turns, "floor: the runner no longer calls run_turn"
        assert marks and min(marks) < min(turns), (
            "the subagent runner does not mark runtime_session as model-authored before "
            "run_turn — a delegated task's criteria block would be executed by the host")
