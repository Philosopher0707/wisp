"""ToolExecutor — extracted tool execution logic from WispAgentCore.

Handles the complete lifecycle of a single tool call:
  1. Pre-tool hooks
  2. Plan mode guard
  3. Dangerous command blocking
  4. Circuit breaker check
  5. Approval gating
  6. Tool execution (native, MCP, or subagent)
  7. Metrics recording
  8. Post-tool hooks

Yields AgentEvent instances for tool_result and approval_request.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from wisp.config import WispConfig
from wisp.core.events import (
    AgentEvent,
    TYPE_TOOL_RESULT,
    TYPE_APPROVAL_REQUEST,
    tool_result as _tool_result_event,
    approval_request as _approval_request_event,
)
from wisp.tools import execute_tool, ToolError, check_dangerous_command

logger = logging.getLogger(__name__)

# (tool_name, args, danger_reason) -> (approved, modified_args_or_none)
ApprovalHandler = Callable[[str, dict, str], Awaitable[tuple[bool, Optional[dict]]]]

# Tools that modify workspace state and require approval when auto_approve=False
_WRITE_TOOLS: set[str] = {
    "write_file",
    "edit_file",
    "edit_file_multi",
    "run_bash",
    "git_branch",
    "git_commit",
    "git_push",
    "gh_pr_create",
    "plan_task",
    "mark_step_done",
    "update_plan",
}


def _should_block_hook(hook_results: list) -> bool:
    """Check if any hook result should block execution."""
    for r in (hook_results or []):
        if getattr(r, "is_blocking", False) or getattr(r, "action", "") == "block":
            return True
    return False


def _collect_hook_messages(hook_results: list) -> str:
    """Collect messages from hook results into a single string."""
    msgs: list[str] = []
    for r in (hook_results or []):
        msg = getattr(r, "message", "") or str(r)
        if msg:
            msgs.append(msg)
    return "; ".join(msgs)


def _get_modified_args(hook_results: list) -> Optional[dict]:
    """Return modified tool args from hook results, if any hook modified them."""
    for r in (hook_results or []):
        modified = getattr(r, "modified_args", None)
        if modified is not None:
            return modified
    return None


class ToolExecutor:
    """Execute a single tool call with all guards, hooks, and metrics.

    This class is stateless with respect to the conversation — it only
    handles one tool invocation at a time. The caller (WispAgentCore)
    is responsible for appending tool_result messages to the conversation.
    """

    def __init__(
        self,
        config: WispConfig,
        hook_manager: Any | None = None,
        metrics: Any | None = None,
        circuit_breaker: Any | None = None,
        mcp: Any | None = None,
        file_lock: Any | None = None,
        lsp_manager: Any | None = None,
    ):
        self.config = config
        self.hook_manager = hook_manager
        self.metrics = metrics
        self.circuit_breaker = circuit_breaker
        self.mcp = mcp
        self.file_lock = file_lock
        self.lsp_manager = lsp_manager

    # ── Public API ───────────────────────────────────────────────────

    async def execute(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        workspace: str,
        tool_call_id: str | None = None,
        approval_handler: ApprovalHandler | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Execute one tool call and yield events.

        Yields:
            - approval_request (if approval needed and not auto-approved)
            - tool_result (always, even on block/deny)
        """
        func_name = tool_name
        func_args = dict(tool_args) if tool_args else {}

        # ── Pre-tool hooks ──
        hook_block_msg = await self._run_pre_tool_hooks(func_name, func_args, workspace)
        if hook_block_msg:
            yield _tool_result_event(func_name, hook_block_msg)
            return

        # ── Plan mode guard ──
        plan_block_msg = self._check_plan_mode(func_name)
        if plan_block_msg:
            yield _tool_result_event(func_name, plan_block_msg)
            return

        # ── Dangerous command auto-block ──
        danger_block_msg = self._check_dangerous_command(func_name, func_args)
        if danger_block_msg:
            yield _tool_result_event(func_name, danger_block_msg)
            return

        # ── Circuit breaker ──
        circuit_block_msg = self._check_circuit_breaker(func_name)
        if circuit_block_msg:
            yield _tool_result_event(func_name, circuit_block_msg)
            return

        # ── Approval gating ──
        needs_approval = func_name in _WRITE_TOOLS
        if needs_approval and not getattr(self.config, "auto_approve", False) and approval_handler:
            reason = f"{func_name} modifies workspace state"
            yield _approval_request_event(func_name, func_args, reason)
            approved, modified = await approval_handler(func_name, func_args, reason)
            if modified is not None:
                func_args.clear()
                func_args.update(modified)
            if not approved:
                yield _tool_result_event(func_name, f"[Blocked: user declined {func_name}]")
                return

        # ── Execute tool ──
        result, duration_ms = await self._execute_tool(func_name, func_args, workspace)

        # ── Post-tool metrics ──
        self._record_metrics(func_name, duration_ms, result)

        yield _tool_result_event(func_name, result, duration_ms=duration_ms)

    async def build_tool_message(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        workspace: str,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        """Build the tool message dict for the conversation.

        This runs the full execution pipeline but returns the message dict
        instead of yielding events. Used when the caller needs synchronous
        access to the result message.
        """
        events: list[AgentEvent] = []
        async for event in self.execute(
            tool_name=tool_name,
            tool_args=tool_args,
            workspace=workspace,
            tool_call_id=tool_call_id,
        ):
            events.append(event)

        # The last event is always tool_result
        if not events:
            return {"role": "tool", "content": "[No result]", "name": tool_name}

        result_event = events[-1]
        result = result_event.data.get("result", "")

        # Extract human-readable summary from structured dict results
        if isinstance(result, dict) and "data" in result:
            msg_content = str(result["data"])
        else:
            msg_content = str(result)

        msg: dict[str, Any] = {
            "role": "tool",
            "content": msg_content,
            "name": tool_name,
        }
        if tool_call_id is not None:
            msg["tool_call_id"] = tool_call_id
        return msg

    # ── Internal guards ──────────────────────────────────────────────

    async def _run_pre_tool_hooks(
        self, func_name: str, func_args: dict, workspace: str
    ) -> str | None:
        """Run PRE_TOOL_USE hooks. Returns block message if blocked, else None."""
        if not self.hook_manager:
            return None
        try:
            from wisp.hooks import HookEvent
            context = {
                "tool_name": func_name,
                "tool_args": func_args,
                "workspace": self.config.workspace,
                "session_id": "",
                "cwd": str(Path(self.config.workspace or ".")),
            }
            hook_results = await self.hook_manager.run_hooks(HookEvent.PRE_TOOL_USE, context)
            if _should_block_hook(hook_results):
                return f"[Blocked by hook: {_collect_hook_messages(hook_results)}]"
            modified = _get_modified_args(hook_results)
            if modified is not None:
                func_args.clear()
                func_args.update(modified)
        except ImportError:
            pass
        except Exception as e:
            logger.warning("Pre-tool hook failed for %s: %s", func_name, e)
        return None

    def _check_plan_mode(self, func_name: str) -> str | None:
        """Check plan mode guard. Returns block message if blocked, else None."""
        if getattr(self.config, "plan_mode", False) and func_name in _WRITE_TOOLS:
            return f"[Blocked: plan mode — {func_name} requires write access]"
        return None

    def _check_dangerous_command(self, func_name: str, func_args: dict) -> str | None:
        """Check dangerous bash commands. Returns block message if blocked."""
        if func_name == "run_bash":
            danger_reason = check_dangerous_command(func_args.get("command", ""))
            if danger_reason:
                return f"[Blocked: dangerous command — {danger_reason}]"
        return None

    def _check_circuit_breaker(self, func_name: str) -> str | None:
        """Check circuit breaker. Returns block message if open, else None."""
        if self.circuit_breaker and self.circuit_breaker.is_open(func_name):
            status = self.circuit_breaker.status(func_name)
            if hasattr(self.metrics, "record_tool_block"):
                self.metrics.record_tool_block()
            return f"[Circuit breaker open for {func_name}: {status}]"
        return None

    async def _execute_tool(
        self, func_name: str, func_args: dict, workspace: str
    ) -> tuple[str | dict, float]:
        """Execute the actual tool. Returns (result, duration_ms)."""
        start = time.monotonic()
        result: str | dict = ""

        if func_name == "spawn_subagent":
            result = await self._spawn_subagent(func_args, workspace)
        elif self._is_mcp_tool(func_name):
            result = self._call_mcp_tool(func_name, func_args)
        else:
            try:
                result = execute_tool(
                    func_name,
                    func_args,
                    workspace,
                    max_data_chars=8000,
                    file_lock=self.file_lock,
                    lsp_manager=self.lsp_manager,
                )
            except ToolError as e:
                result = f"Error: {e}"
            except Exception as e:
                result = f"Unexpected error: {e}"

        duration_ms = (time.monotonic() - start) * 1000
        return result, duration_ms

    def _is_mcp_tool(self, name: str) -> bool:
        """Check if a tool name belongs to an MCP server."""
        if not self.mcp:
            return False
        try:
            for tool in self.mcp.get_all_tools():
                if getattr(tool, "name", None) == name:
                    return True
        except Exception:
            pass
        return False

    def _call_mcp_tool(self, func_name: str, func_args: dict) -> str:
        """Call an MCP tool and truncate if needed."""
        if not self.mcp:
            return f"MCP error: no MCP manager"
        try:
            result = self.mcp.call_tool(func_name, func_args)
            if isinstance(result, str) and len(result) > 8000:
                result = result[:8000] + f"\n... [truncated {len(result)} total chars]"
            return result
        except Exception as e:
            return f"MCP error: {e}"

    async def _spawn_subagent(self, func_args: dict, workspace: str) -> str:
        """Delegate to subagent orchestrator."""
        try:
            # We can't easily import WispAgentCore here without circular imports,
            # so we rely on the caller having set up the subagent infrastructure.
            # For now, return a placeholder that the caller can override.
            return "[Subagent execution not available in ToolExecutor]"
        except Exception as e:
            logger.error("Subagent spawn failed: %s", e, exc_info=True)
            return f"Subagent spawn failed: {e}"

    def _record_metrics(self, func_name: str, duration_ms: float, result: str | dict) -> None:
        """Record tool execution metrics."""
        if not self.metrics:
            return
        ok = (
            (isinstance(result, str) and '"status": "ok"' in result)
            or (isinstance(result, str) and not result.startswith("["))
            or (isinstance(result, dict) and result.get("status") == "ok")
        )
        if hasattr(self.metrics, "record_tool"):
            self.metrics.record_tool(func_name, duration_ms, success=ok)
        if hasattr(self.metrics, "circuit_breaker") and self.circuit_breaker:
            if ok:
                self.circuit_breaker.record_success(func_name)
            else:
                self.circuit_breaker.record_failure(func_name)
