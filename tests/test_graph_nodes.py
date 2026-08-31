"""Unit tests for graph nodes — planner_coder, sandbox_executor, verifier, human_approval."""

import asyncio

import pytest

from wisp.core.graph_nodes import (
    ApprovalDeps,
    PlannerCoderDeps,
    SandboxDeps,
    VerifierDeps,
    human_approval_node,
    planner_coder_node,
    sandbox_executor_node,
    verifier_node,
)
from wisp.core.graph_state import ExecutionLog, GraphState, GraphStatus


# ── planner_coder ────────────────────────────────────────────────


class TestPlannerCoderNode:
    @pytest.mark.asyncio
    async def test_identity_when_no_provider(self):
        s = GraphState.initial()
        new_state, result = await planner_coder_node(s)
        assert result.success is True
        assert new_state.status == GraphStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_collects_tool_calls_and_content(self):
        async def fake_stream(system_prompt, messages, tools=None):
            yield {"type": "tool_call", "name": "run_bash", "arguments": {"command": "pytest"}}
            yield {"type": "content", "text": "hello"}

        s = GraphState.initial()
        deps = PlannerCoderDeps(stream_provider=fake_stream)
        _, result = await planner_coder_node(s, prompt="fix", deps=deps)
        assert result.success
        assert result.data["has_tool_calls"] is True
        assert any(ev["type"] == "tool_call" for ev in result.data["events"])

    @pytest.mark.asyncio
    async def test_timeout_graceful(self):
        async def stalled(system_prompt, messages, tools=None):
            await asyncio.sleep(10)
            yield {"type": "content", "text": "late"}

        s = GraphState.initial()
        deps = PlannerCoderDeps(stream_provider=stalled)
        _, result = await planner_coder_node(s, prompt="hi", deps=deps, timeout_s=0.1)
        assert result.success is False
        assert "timed out" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_provider_exception_graceful(self):
        async def boom(system_prompt, messages, tools=None):
            raise RuntimeError("provider down")
            yield  # make it an async generator

        s = GraphState.initial()
        deps = PlannerCoderDeps(stream_provider=boom)
        _, result = await planner_coder_node(s, deps=deps, timeout_s=1.0)
        assert result.success is False
        assert "provider failed" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_build_system_prompt_exception_does_not_crash(self):
        async def fake_stream(system_prompt, messages, tools=None):
            yield {"type": "content", "text": "ok"}

        def bad_builder(state, prompt):
            raise RuntimeError("prompt build boom")

        s = GraphState.initial()
        deps = PlannerCoderDeps(stream_provider=fake_stream, build_system_prompt=bad_builder)
        _, result = await planner_coder_node(s, prompt="hi", deps=deps, timeout_s=2)
        assert result.success is True


# ── sandbox_executor ─────────────────────────────────────────────


class TestSandboxExecutorNode:
    @pytest.mark.asyncio
    async def test_empty_command_skipped_gracefully(self):
        s = GraphState.initial()
        new_state, result = await sandbox_executor_node(s, "")
        assert result.success is False
        assert "empty command" in (result.error or "").lower()
        assert len(new_state.execution_logs) == 0

    @pytest.mark.asyncio
    async def test_dangerous_command_blocked_and_logged(self):
        from wisp.tools._utils import check_dangerous_command

        s = GraphState.initial(session_id="sid-danger")
        deps = SandboxDeps(check_dangerous=check_dangerous_command)
        new_state, result = await sandbox_executor_node(s, "sudo rm -rf /", deps=deps)
        assert result.success is False
        assert "Dangerous" in (result.error or "") or "blocked" in (result.error or "").lower()
        assert len(new_state.execution_logs) == 1
        assert new_state.execution_logs[0].exit_code == -1
        assert "blocked_reason" in result.data

    @pytest.mark.asyncio
    async def test_success_structured_log(self):
        async def ok_runner(cmd, ws, timeout):
            return (0, "all good", "")

        s = GraphState.initial()
        deps = SandboxDeps(run_command=ok_runner)
        new_state, result = await sandbox_executor_node(s, "echo hi", deps=deps, timeout_s=5)
        assert result.success is True
        assert result.data["exit_code"] == 0
        assert len(new_state.execution_logs) == 1
        assert new_state.last_succeeded is True

    @pytest.mark.asyncio
    async def test_failure_structured_log(self):
        async def fail_runner(cmd, ws, timeout):
            return (1, "out", "err detail")

        s = GraphState.initial()
        deps = SandboxDeps(run_command=fail_runner)
        new_state, result = await sandbox_executor_node(s, "pytest", deps=deps)
        assert result.success is False
        assert result.data["exit_code"] == 1
        assert new_state.last_exit_code == 1
        assert new_state.execution_logs[0].stderr == "err detail"

    @pytest.mark.asyncio
    async def test_timeout_strict_and_logged(self):
        async def slow_runner(cmd, ws, timeout):
            await asyncio.sleep(10)
            return (0, "late", "")

        s = GraphState.initial()
        deps = SandboxDeps(run_command=slow_runner)
        new_state, result = await sandbox_executor_node(s, "sleep 10", deps=deps, timeout_s=0.2)
        assert result.success is False
        assert "timed out" in (result.error or "").lower()
        assert len(new_state.execution_logs) == 1
        assert new_state.execution_logs[0].exit_code == -1

    @pytest.mark.asyncio
    async def test_runner_exception_becomes_failed_log_not_raise(self):
        async def boom(cmd, ws, timeout):
            raise RuntimeError("runner boom")

        s = GraphState.initial()
        deps = SandboxDeps(run_command=boom)
        new_state, result = await sandbox_executor_node(s, "echo hi", deps=deps)
        assert result.success is False
        assert len(new_state.execution_logs) == 1

    @pytest.mark.asyncio
    async def test_string_result_parsed(self):
        async def str_runner(cmd, ws, timeout):
            return "[exit code: 2]\nfailed output"

        s = GraphState.initial()
        deps = SandboxDeps(run_command=str_runner)
        new_state, result = await sandbox_executor_node(s, "cmd", deps=deps)
        assert result.data["exit_code"] == 2
        assert new_state.last_exit_code == 2

    @pytest.mark.asyncio
    async def test_sync_runner_via_to_thread(self):
        def sync_runner(cmd, ws, timeout):
            return (0, "sync ok", "")

        s = GraphState.initial()
        deps = SandboxDeps(run_command=sync_runner)
        new_state, result = await sandbox_executor_node(s, "echo hi", deps=deps, timeout_s=5)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_dict_result_shape(self):
        async def dict_runner(cmd, ws, timeout):
            return {"status": "ok", "data": "hello", "metadata": {"exit_code": 1, "truncated": True}}

        s = GraphState.initial()
        deps = SandboxDeps(run_command=dict_runner)
        new_state, result = await sandbox_executor_node(s, "cmd", deps=deps)
        assert result.data["exit_code"] == 1
        assert result.data["truncated"] is True


# ── verifier ─────────────────────────────────────────────────────


class TestVerifierNode:
    @pytest.mark.asyncio
    async def test_no_logs_fails(self):
        s = GraphState.initial()
        new_state, result = await verifier_node(s)
        assert result.success is False
        assert "no verification" in (result.error or "").lower()
        assert new_state.status == GraphStatus.IN_PROGRESS  # verifier does not transition itself

    @pytest.mark.asyncio
    async def test_non_zero_exit_fails(self):
        s = GraphState.initial()
        s.add_execution_log(ExecutionLog(command="pytest", exit_code=1, stdout="3 failed"))
        _, result = await verifier_node(s)
        assert result.success is False
        assert result.data["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_zero_exit_passes(self):
        s = GraphState.initial()
        s.add_execution_log(ExecutionLog(command="pytest", exit_code=0, stdout="all passed"))
        _, result = await verifier_node(s)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_custom_check_overrides_default(self):
        s = GraphState.initial()
        # No logs but custom check says pass
        def custom(state):
            return (True, "custom passed", {"hint": "ok"})

        deps = VerifierDeps(custom_check=custom)
        _, result = await verifier_node(s, deps=deps)
        assert result.success is True
        assert result.data["hint"] == "ok"

    @pytest.mark.asyncio
    async def test_custom_check_failure(self):
        s = GraphState.initial()
        s.add_execution_log(ExecutionLog(command="pytest", exit_code=0))
        deps = VerifierDeps(custom_check=lambda state: (False, "custom reason", {}))
        _, result = await verifier_node(s, deps=deps)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_custom_check_exception_graceful(self):
        s = GraphState.initial()

        def boom(state):
            raise RuntimeError("check boom")

        deps = VerifierDeps(custom_check=boom)
        _, result = await verifier_node(s, deps=deps)
        assert result.success is False
        assert "raised" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_require_exit_zero_false_allows_nonzero(self):
        s = GraphState.initial()
        s.add_execution_log(ExecutionLog(command="pytest", exit_code=1))
        _, result = await verifier_node(s, require_exit_zero=False)
        assert result.success is True


# ── human_approval ───────────────────────────────────────────────


class TestHumanApprovalNode:
    @pytest.mark.asyncio
    async def test_no_handler_safe_tool_auto_approves(self):
        s = GraphState.initial()
        _, result = await human_approval_node(s, "read_file", {"path": "a.py"})
        assert result.success is True
        assert "auto-approved" in result.output

    @pytest.mark.asyncio
    async def test_no_handler_gated_tool_needs_review(self):
        s = GraphState.initial(session_id="sid-approval")
        new_state, result = await human_approval_node(s, "run_bash", {"command": "rm -rf /tmp/x"})
        assert result.success is False
        assert result.should_continue is False
        assert new_state.status == GraphStatus.NEEDS_HUMAN_REVIEW
        assert new_state.pending_approval is not None

    @pytest.mark.asyncio
    async def test_security_check_blocked_needs_review(self):
        def blocked_check(name, args):
            return (False, "policy says no")

        s = GraphState.initial()
        deps = ApprovalDeps(security_check=blocked_check)
        new_state, result = await human_approval_node(s, "write_file", {"path": "x.py"}, deps=deps)
        assert result.success is False
        assert new_state.status == GraphStatus.NEEDS_HUMAN_REVIEW

    @pytest.mark.asyncio
    async def test_handler_approved(self):
        s = GraphState.initial()

        async def handler(name, args, reason):
            return (True, None)

        deps = ApprovalDeps(approval_handler=handler)
        new_state, result = await human_approval_node(s, "run_bash", {"command": "ls"}, deps=deps)
        assert result.success is True
        assert result.data["approved"] is True
        assert new_state.status == GraphStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_handler_denied_needs_review(self):
        s = GraphState.initial()

        async def handler(name, args, reason):
            return (False, None)

        deps = ApprovalDeps(approval_handler=handler)
        new_state, result = await human_approval_node(s, "run_bash", {"command": "ls"}, deps=deps)
        assert result.success is False
        assert new_state.status == GraphStatus.NEEDS_HUMAN_REVIEW

    @pytest.mark.asyncio
    async def test_handler_bool_shape(self):
        s = GraphState.initial()
        deps = ApprovalDeps(approval_handler=lambda n, a, r: True)  # type: ignore[arg-type]
        # wrap sync bool in async by node — it handles non-coroutine
        # Actually node does iscoroutine check, so we need async
        async def bool_handler(name, args, reason):
            return True

        deps2 = ApprovalDeps(approval_handler=bool_handler)
        _, result = await human_approval_node(s, "run_bash", {"command": "ls"}, deps=deps2)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_handler_timeout_needs_review(self):
        s = GraphState.initial()

        async def slow_handler(name, args, reason):
            await asyncio.sleep(10)
            return (True, None)

        deps = ApprovalDeps(approval_handler=slow_handler)
        new_state, result = await human_approval_node(s, "run_bash", {"command": "ls"}, deps=deps, timeout_s=0.1)
        assert result.success is False
        assert "timed out" in (result.error or "").lower()
        assert new_state.status == GraphStatus.NEEDS_HUMAN_REVIEW

    @pytest.mark.asyncio
    async def test_handler_modified_args(self):
        s = GraphState.initial()

        async def mod_handler(name, args, reason):
            return (True, {"command": "ls -la"})

        deps = ApprovalDeps(approval_handler=mod_handler)
        _, result = await human_approval_node(s, "run_bash", {"command": "ls"}, deps=deps)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_handler_exception_needs_review(self):
        s = GraphState.initial()

        async def boom(name, args, reason):
            raise RuntimeError("handler boom")

        deps = ApprovalDeps(approval_handler=boom)
        new_state, result = await human_approval_node(s, "run_bash", {"command": "ls"}, deps=deps)
        assert result.success is False
        assert new_state.status == GraphStatus.NEEDS_HUMAN_REVIEW

    @pytest.mark.asyncio
    async def test_handler_clears_prior_bookmark_on_approval(self):
        s = GraphState.initial()
        s.mark_needs_review("run_bash", {"command": "x"}, reason="prior")
        assert s.status == GraphStatus.NEEDS_HUMAN_REVIEW

        async def handler(name, args, reason):
            return (True, None)

        deps = ApprovalDeps(approval_handler=handler)
        new_state, result = await human_approval_node(s, "run_bash", {"command": "x"}, deps=deps)
        assert result.success is True
        assert new_state.status == GraphStatus.IN_PROGRESS
        assert new_state.pending_approval is None
