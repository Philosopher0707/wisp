"""Unit tests for the agentic graph orchestrator — END vs fallback, circuit breaker, oscillation, timeouts, rollback."""

import asyncio

import pytest

from wisp.core.agentic_graph import GraphConfig, GraphRunner, _default_extract_command
from wisp.core.graph_nodes import NodeResult, SandboxDeps, VerifierDeps, ApprovalDeps
from wisp.core.graph_state import ExecutionLog, GraphState, GraphStatus


# ── helpers ──────────────────────────────────────────────────────


def _ok_sandbox(cmd, ws, timeout):
    return (0, "ok", "")


def _fail_sandbox(cmd, ws, timeout):
    return (1, "failed", "error detail")


async def _async_ok_sandbox(cmd, ws, timeout):
    return (0, "ok", "")


async def _async_fail_sandbox(cmd, ws, timeout):
    return (1, "failed", "err")


# ── END vs fallback routing ─────────────────────────────────────


class TestEndVsFallbackRouting:
    @pytest.mark.asyncio
    async def test_completes_when_verifier_passes(self):
        s = GraphState.initial(max_iterations=5)

        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=3, enable_oscillation_guard=False),
            sandbox_deps=SandboxDeps(run_command=_ok_sandbox),
        )

        async def fake_planner(prompt=""):
            return runner.state, NodeResult(success=True, output="planner ok", data={"events": [{"type": "tool_call", "name": "run_bash", "arguments": {"command": "echo ok"}}]})

        runner._run_planner_coder = fake_planner  # type: ignore[method-assign]
        final = await runner.run(prompt="task", extract_command=lambda *_: "echo ok")
        assert final.status == GraphStatus.COMPLETED
        payload = runner.done_payload()
        assert payload.reason == "natural"
        assert payload.status == GraphStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_fallback_then_eventual_success(self):
        s = GraphState.initial(max_iterations=4)
        attempts = {"n": 0}

        async def flaky_runner(cmd, ws, timeout):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return (1, "", "first fail")
            return (0, "ok", "")

        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=4, enable_oscillation_guard=False),
            sandbox_deps=SandboxDeps(run_command=flaky_runner),
        )

        async def fake_planner(prompt=""):
            return runner.state, NodeResult(success=True, output="ok", data={"events": [{"type": "tool_call", "name": "run_bash", "arguments": {"command": "pytest"}}]})

        runner._run_planner_coder = fake_planner  # type: ignore[method-assign]
        final = await runner.run(prompt="fix", extract_command=lambda state, res: "pytest")
        assert final.status == GraphStatus.COMPLETED
        assert final.iteration_count == 2
        assert len(final.execution_logs) == 2
        assert final.execution_logs[-1].exit_code == 0

    @pytest.mark.asyncio
    async def test_fallback_exhaustion_failed_with_max_iterations_reason(self):
        s = GraphState.initial(max_iterations=2)
        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=2, enable_oscillation_guard=False),
            sandbox_deps=SandboxDeps(run_command=_fail_sandbox),
        )

        async def fake_planner(prompt=""):
            return runner.state, NodeResult(success=True, output="ok", data={"events": [{"type": "tool_call", "name": "run_bash", "arguments": {"command": "pytest"}}]})

        runner._run_planner_coder = fake_planner  # type: ignore[method-assign]
        final = await runner.run(prompt="task", extract_command=lambda *_: "pytest")
        assert final.status == GraphStatus.FAILED
        assert "Max graph iterations" in (final.error or "")
        payload = runner.done_payload()
        assert payload.reason == "max_iterations"
        assert payload.should_fallback is True

    @pytest.mark.asyncio
    async def test_no_command_still_verifies_no_logs_fails_then_eventually_completes_via_custom_verifier(self):
        s = GraphState.initial(max_iterations=3)
        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=3, enable_oscillation_guard=False),
            verifier_deps=VerifierDeps(custom_check=lambda state: (True, "custom ok", {})),
            sandbox_deps=SandboxDeps(run_command=_ok_sandbox),
        )

        async def fake_planner(prompt=""):
            return runner.state, NodeResult(success=True, output="no tool calls", data={"events": []})

        runner._run_planner_coder = fake_planner  # type: ignore[method-assign]
        final = await runner.run(prompt="answer question", extract_command=lambda *_: None)
        assert final.status == GraphStatus.COMPLETED


# ── Circuit breaker (max_iterations) ─────────────────────────────


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_circuit_breaker_fails_gracefully_not_raise(self):
        s = GraphState.initial(max_iterations=1)
        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=1, enable_oscillation_guard=False),
            sandbox_deps=SandboxDeps(run_command=_fail_sandbox),
        )

        async def fake_planner(prompt=""):
            return runner.state, NodeResult(success=True, output="ok", data={"events": [{"type": "tool_call", "name": "run_bash", "arguments": {"command": "pytest"}}]})

        runner._run_planner_coder = fake_planner  # type: ignore[method-assign]
        final = await runner.run(prompt="x", extract_command=lambda *_: "pytest")
        assert isinstance(final, GraphState)
        assert final.status == GraphStatus.FAILED
        assert "circuit breaker" in (final.error or "").lower() or "Max graph iterations" in (final.error or "")

    @pytest.mark.asyncio
    async def test_already_at_budget_breaks_immediately(self):
        s = GraphState.initial(max_iterations=2)
        s.iteration_count = 2
        runner = GraphRunner(state=s, config=GraphConfig(max_iterations=2))
        final = await runner.run(prompt="x")
        assert final.status == GraphStatus.FAILED


# ── Oscillation / infinite-loop detection ────────────────────────


class TestOscillationGuard:
    @pytest.mark.asyncio
    async def test_oscillation_trips_before_max_iterations(self):
        s = GraphState.initial(max_iterations=10)
        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=10, oscillation_window=2, enable_oscillation_guard=True),
            sandbox_deps=SandboxDeps(run_command=_fail_sandbox),
        )

        async def fake_planner(prompt=""):
            return runner.state, NodeResult(success=True, output="ok", data={"events": [{"type": "tool_call", "name": "run_bash", "arguments": {"command": "same_cmd"}}]})

        runner._run_planner_coder = fake_planner  # type: ignore[method-assign]
        final = await runner.run(prompt="loop", extract_command=lambda *_: "same_cmd")
        assert final.status == GraphStatus.FAILED
        assert "Oscillation" in (final.error or "")
        payload = runner.done_payload()
        assert payload.reason == "oscillation"
        assert final.iteration_count < 10

    @pytest.mark.asyncio
    async def test_oscillation_guard_disabled_does_not_trip(self):
        s = GraphState.initial(max_iterations=3)
        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=3, enable_oscillation_guard=False),
            sandbox_deps=SandboxDeps(run_command=_fail_sandbox),
        )

        async def fake_planner(prompt=""):
            return runner.state, NodeResult(success=True, output="ok", data={"events": [{"type": "tool_call", "name": "run_bash", "arguments": {"command": "same"}}]})

        runner._run_planner_coder = fake_planner  # type: ignore[method-assign]
        final = await runner.run(prompt="x", extract_command=lambda *_: "same")
        assert "Oscillation" not in (final.error or "")
        assert "Max graph iterations" in (final.error or "")

    @pytest.mark.asyncio
    async def test_oscillation_tolerance_when_state_evolves(self):
        s = GraphState.initial(max_iterations=5)
        n = {"i": 0}

        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=3, enable_oscillation_guard=True, oscillation_window=3),
            sandbox_deps=SandboxDeps(run_command=_fail_sandbox),
        )

        async def fake_planner(prompt=""):
            # Mutate state so hash varies — patch after construction
            runner.state.upsert_code_file(f"file_{n['i']}.py", f"content {n['i']}")
            n["i"] += 1
            return runner.state, NodeResult(success=True, output="ok", data={"events": [{"type": "tool_call", "name": "run_bash", "arguments": {"command": "pytest"}}]})

        runner._run_planner_coder = fake_planner  # type: ignore[method-assign]
        final = await runner.run(prompt="x", extract_command=lambda *_: "pytest")
        assert "Max graph iterations" in (final.error or "")


# ── Log pruning & rollback on fatal error ────────────────────────


class TestLogPruningAndRollback:
    @pytest.mark.asyncio
    async def test_execution_logs_capped(self):
        from wisp.core.graph_state import DEFAULT_MAX_LOGS

        s = GraphState.initial(max_iterations=DEFAULT_MAX_LOGS + 10)
        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=DEFAULT_MAX_LOGS + 10, enable_oscillation_guard=False),
            sandbox_deps=SandboxDeps(run_command=_fail_sandbox),
        )

        async def fake_planner(prompt=""):
            return runner.state, NodeResult(success=True, output="ok", data={"events": [{"type": "tool_call", "name": "run_bash", "arguments": {"command": "pytest"}}]})

        runner._run_planner_coder = fake_planner  # type: ignore[method-assign]
        final = await runner.run(prompt="x", extract_command=lambda *_: "pytest")
        assert len(final.execution_logs) <= DEFAULT_MAX_LOGS

    @pytest.mark.asyncio
    async def test_state_rollback_on_fatal_planner_exception(self):
        s = GraphState.initial(max_iterations=3)
        s.upsert_code_file("keep.py", "original")
        s.snapshot()
        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=3, enable_oscillation_guard=False),
            sandbox_deps=SandboxDeps(run_command=_ok_sandbox),
        )

        async def boom_planner(prompt=""):
            runner.state.upsert_code_file("keep.py", "corrupted")
            raise RuntimeError("planner exploded")

        runner._run_planner_coder = boom_planner  # type: ignore[method-assign]
        final = await runner.run(prompt="x", extract_command=lambda *_: "echo ok")
        assert isinstance(final, GraphState)
        assert final.status == GraphStatus.FAILED

    def test_graph_state_snapshot_rollback_preserves_logs(self):
        s = GraphState.initial(session_id="rollback-test")
        s.add_execution_log(ExecutionLog(command="first", exit_code=0, stdout="ok"))
        s.snapshot()
        s.add_execution_log(ExecutionLog(command="second", exit_code=1, stdout="fail"))
        assert len(s.execution_logs) == 2
        s.rollback()
        assert len(s.execution_logs) == 1
        assert s.execution_logs[0].command == "first"

    def test_graph_state_log_char_pruning(self):
        from wisp.core.graph_state import DEFAULT_MAX_LOG_CHARS

        s = GraphState.initial()
        huge = "x" * (DEFAULT_MAX_LOG_CHARS + 1000)
        s.add_execution_log(ExecutionLog(command="big", stdout=huge))
        assert s.execution_logs[0].truncated is True
        assert len(s.execution_logs[0].stdout) <= DEFAULT_MAX_LOG_CHARS + 50


# ── Sandbox timeout strictness ───────────────────────────────────


class TestSandboxTimeout:
    @pytest.mark.asyncio
    async def test_sandbox_timeout_does_not_hang_graph(self):
        s = GraphState.initial(max_iterations=2)

        async def slow_runner(cmd, ws, timeout):
            await asyncio.sleep(5)
            return (0, "late", "")

        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=2, enable_oscillation_guard=False, sandbox_timeout_s=0.3),
            sandbox_deps=SandboxDeps(run_command=slow_runner),
        )

        async def fake_planner(prompt=""):
            return runner.state, NodeResult(success=True, output="ok", data={"events": [{"type": "tool_call", "name": "run_bash", "arguments": {"command": "sleep 5"}}]})

        runner._run_planner_coder = fake_planner  # type: ignore[method-assign]
        import time

        start = time.monotonic()
        final = await asyncio.wait_for(runner.run(prompt="x", extract_command=lambda *_: "sleep 5"), timeout=4.0)
        elapsed = time.monotonic() - start
        assert elapsed < 4.0
        assert isinstance(final, GraphState)
        assert any("timed out" in (log.stderr or log.raw or "").lower() for log in final.execution_logs) or any(log.exit_code == -1 for log in final.execution_logs)

    @pytest.mark.asyncio
    async def test_graph_wall_clock_timeout(self):
        s = GraphState.initial(max_iterations=20)
        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=20, enable_oscillation_guard=False, graph_timeout_s=0.5),
            sandbox_deps=SandboxDeps(run_command=_fail_sandbox),
        )

        async def fake_planner(prompt=""):
            await asyncio.sleep(0.2)
            return runner.state, NodeResult(success=True, output="ok", data={"events": [{"type": "tool_call", "name": "run_bash", "arguments": {"command": "pytest"}}]})

        runner._run_planner_coder = fake_planner  # type: ignore[method-assign]
        final = await runner.run(prompt="x", extract_command=lambda *_: "pytest")
        assert final.status == GraphStatus.FAILED
        assert "wall-clock" in (final.error or "").lower() or "timeout" in (final.error or "").lower()


# ── human_approval breakpoint ────────────────────────────────────


class TestHumanApprovalBreakpoint:
    @pytest.mark.asyncio
    async def test_breakpoint_pauses_to_needs_review(self):
        s = GraphState.initial(max_iterations=5)

        def needs_review(tool_name, args):
            return (True, "needs approval for testing")

        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=5, enable_oscillation_guard=False),
            sandbox_deps=SandboxDeps(run_command=_ok_sandbox),
        )

        async def fake_planner(prompt=""):
            return runner.state, NodeResult(success=True, output="ok", data={"events": [{"type": "tool_call", "name": "run_bash", "arguments": {"command": "danger"}}]})

        runner._run_planner_coder = fake_planner  # type: ignore[method-assign]
        final = await runner.run(prompt="x", extract_command=lambda *_: "danger", requires_approval=needs_review)
        assert final.status == GraphStatus.NEEDS_HUMAN_REVIEW
        assert final.pending_approval is not None
        payload = runner.done_payload()
        assert payload.reason == "needs_human_review"

    @pytest.mark.asyncio
    async def test_breakpoint_approved_continues(self):
        s = GraphState.initial(max_iterations=3)

        def needs_review(tool_name, args):
            return (True, "gate")

        async def approved_handler(name, args, reason):
            return (True, None)

        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=3, enable_oscillation_guard=False),
            sandbox_deps=SandboxDeps(run_command=_ok_sandbox),
            approval_deps=ApprovalDeps(approval_handler=approved_handler),
        )

        async def fake_planner(prompt=""):
            return runner.state, NodeResult(success=True, output="ok", data={"events": [{"type": "tool_call", "name": "run_bash", "arguments": {"command": "echo ok"}}]})

        runner._run_planner_coder = fake_planner  # type: ignore[method-assign]
        final = await runner.run(prompt="x", extract_command=lambda *_: "echo ok", requires_approval=needs_review)
        assert final.status == GraphStatus.COMPLETED

    def test_resume_after_human_review(self):
        s = GraphState.initial(session_id="resume-sid")
        s.mark_needs_review("run_bash", {"command": "danger"}, reason="gate")
        assert s.status == GraphStatus.NEEDS_HUMAN_REVIEW
        s.clear_review()
        assert s.status == GraphStatus.IN_PROGRESS
        assert s.pending_approval is None
        s.upsert_code_file("a.py", "fixed")
        s.add_execution_log(ExecutionLog(command="pytest", exit_code=0))


# ── done payload & command extraction ────────────────────────────


class TestDonePayloadAndExtraction:
    def test_default_extract_from_tool_call(self):
        res = NodeResult(success=True, output="ok", data={"events": [{"type": "tool_call", "name": "run_bash", "arguments": {"command": "pytest -q"}}]})
        assert _default_extract_command(res) == "pytest -q"

    def test_default_extract_none_when_no_run_bash(self):
        res = NodeResult(success=True, output="ok", data={"events": [{"type": "tool_call", "name": "read_file", "arguments": {"path": "a.py"}}]})
        assert _default_extract_command(res) is None

    def test_default_extract_from_batched_calls(self):
        res = NodeResult(success=True, output="ok", data={"events": [{"type": "tool_calls", "calls": [{"id": "1", "type": "function", "function": {"name": "run_bash", "arguments": {"command": "make test"}}}]}]})
        assert _default_extract_command(res) == "make test"

    def test_done_payload_reason_mapping(self):
        for status, expect in [
            (GraphStatus.COMPLETED, "natural"),
            (GraphStatus.NEEDS_HUMAN_REVIEW, "needs_human_review"),
        ]:
            s = GraphState.initial()
            s.status = status
            runner = GraphRunner(state=s)
            assert runner.done_payload().reason == expect
        s = GraphState.initial()
        s.status = GraphStatus.FAILED
        s.error = "Oscillation detected"
        assert GraphRunner(state=s).done_payload().reason == "oscillation"
        s2 = GraphState.initial()
        s2.status = GraphStatus.FAILED
        s2.error = "Max graph iterations (5) reached"
        assert GraphRunner(state=s2).done_payload().reason == "max_iterations"

    @pytest.mark.asyncio
    async def test_verifier_disabled_route_always_complete(self):
        s = GraphState.initial(max_iterations=3)
        runner = GraphRunner(
            state=s,
            config=GraphConfig(max_iterations=3, enable_verifier_gate=False, enable_oscillation_guard=False),
            sandbox_deps=SandboxDeps(run_command=_fail_sandbox),
        )

        async def fake_planner(prompt=""):
            return runner.state, NodeResult(success=True, output="ok", data={"events": []})

        runner._run_planner_coder = fake_planner  # type: ignore[method-assign]
        final = await runner.run(prompt="x", extract_command=lambda *_: None)
        assert final.status == GraphStatus.COMPLETED
