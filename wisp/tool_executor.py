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

import asyncio
import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from wisp.config import WispConfig
from wisp.infra.security import PermissionMode
from wisp.core.events import (
    AgentEvent,
    TYPE_TOOL_RESULT,
    TYPE_APPROVAL_REQUEST,
    tool_result as _tool_result_event,
    approval_request as _approval_request_event,
)
from wisp.tools.errors import ToolError
from wisp.tools._utils import check_dangerous_command
from wisp.tools.registry import execute_tool
from wisp.tools.audit import AuditLog

logger = logging.getLogger(__name__)

# (tool_name, args, danger_reason) -> (approved, modified_args_or_none)
ApprovalHandler = Callable[[str, dict, str], Awaitable[tuple[bool, Optional[dict]]]]

# Tools that modify workspace state and require approval when auto_approve=False
_DEFAULT_WRITE_TOOLS: set[str] = {
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
    "spawn",
    "fanout",
}


def _get_write_tools(config: Any = None) -> set[str]:
    """Resolve write-classification tools from config (env: WISP_WRITE_TOOLS)."""
    if config is not None and hasattr(config, "write_tools") and config.write_tools:
        return set(config.write_tools)
    return _DEFAULT_WRITE_TOOLS


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
        mcp: Any | None = None,
        file_lock: Any | None = None,
        lsp_manager: Any | None = None,
        subagent_orchestrator: Any | None = None,
        audit_trail: Any | None = None,
    ):
        self.config = config
        self.hook_manager = hook_manager
        self.metrics = metrics
        self.mcp = mcp
        self.file_lock = file_lock
        self.lsp_manager = lsp_manager
        self.subagent_orchestrator = subagent_orchestrator
        self.audit_trail = audit_trail

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

        # ── Permission mode guard ──
        perm_block_msg = self._check_permission_mode(func_name)
        if perm_block_msg:
            yield _tool_result_event(func_name, perm_block_msg)
            return

        # ── Approval gating ──
        needs_approval = func_name in _get_write_tools(self.config)
        forced_approval = self._needs_forced_approval(func_name)
        is_full_mode = getattr(self.config, "permission_mode", PermissionMode.AUTO_EDIT) == PermissionMode.FULL
        was_auto_approved = False
        if needs_approval and not is_full_mode and (not getattr(self.config, "auto_approve", False) or forced_approval):
            if not approval_handler:
                if forced_approval:
                    yield _tool_result_event(
                        func_name,
                        f"[Blocked: {getattr(self.config, 'permission_mode', 'auto_edit')} mode "
                        f"requires approval for {func_name}, but no approval handler is available]",
                    )
                    return
                # auto_approve=True + no handler + not forced = pass through
            else:
                reason = f"{func_name} modifies workspace state"
                yield _approval_request_event(func_name, func_args, reason)
                approved, modified = await approval_handler(func_name, func_args, reason)
                if modified is not None:
                    func_args.clear()
                    func_args.update(modified)
                if not approved:
                    yield _tool_result_event(func_name, f"[Blocked: user declined {func_name}]")
                    return
        elif needs_approval and getattr(self.config, "auto_approve", False):
            was_auto_approved = True

        # ── Event-specific pre-hooks (PRE_BASH, PRE_FILE_WRITE) ──
        if func_name == "run_bash":
            event_block = await self._run_pre_bash_hooks(func_args, workspace)
            if event_block:
                yield _tool_result_event(func_name, event_block)
                return
        elif func_name in ("write_file", "edit_file", "edit_file_multi"):
            event_block = await self._run_pre_file_hooks(func_name, func_args, workspace)
            if event_block:
                yield _tool_result_event(func_name, event_block)
                return

        # ── Execute tool ──
        result, duration_ms = await self._execute_tool(func_name, func_args, workspace)

        # ── Audit logging (Q22) ──
        if needs_approval and self.config is not None:
            try:
                pm = self.config.permission_mode
                mode = pm.value if hasattr(pm, "value") else str(pm)
            except Exception:
                mode = "auto_edit"
            try:
                if self.audit_trail is not None:
                    audit = AuditLog(store=self.audit_trail._store)
                else:
                    audit = AuditLog(Path(workspace).resolve() / ".wisp" / "audit.jsonl")
                if was_auto_approved:
                    audit.log_auto_approved(
                        func_name, func_args, workspace, result, duration_ms,
                        mode=mode, forced=forced_approval,
                    )
                else:
                    audit.log_explicit_approved(
                        func_name, func_args, workspace, result, duration_ms,
                        mode=mode,
                    )
            except Exception:
                logger.warning("Audit write failed for %s", func_name, exc_info=True)

        # ── Post-tool metrics ──
        self._record_metrics(func_name, duration_ms, result)

        # ── Post-tool event hooks (best-effort, non-blocking) ──
        await self._run_post_tool_hooks(func_name, func_args, result, workspace)

        yield _tool_result_event(func_name, result, duration_ms=duration_ms, tool_call_id=tool_call_id)

    async def build_tool_message(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        workspace: str,
        tool_call_id: str | None = None,
        result: str | dict | None = None,
    ) -> dict[str, Any]:
        """Build the tool message dict for the conversation.

        If ``result`` is provided, uses it directly without re-executing the
        tool. This is the normal path when the caller has already run
        :meth:`execute` and captured the result. When ``result`` is *None*
        (legacy path, mainly for external callers that don't go through
        :meth:`execute` first), the method falls back to running the full
        execution pipeline again.
        """
        if result is None:
            # Legacy fallback: re-run the tool.  **This should not happen**
            # inside the normal agent loop because stateful tools (e.g.
            # edit_file) would fail on the second run.
            events: list[AgentEvent] = []
            async for event in self.execute(
                tool_name=tool_name,
                tool_args=tool_args,
                workspace=workspace,
                tool_call_id=tool_call_id,
            ):
                events.append(event)

            if not events:
                return {"role": "tool", "content": "[No result]", "name": tool_name}

            result_event = events[-1]
            result = result_event.data.get("result", "")

        # Extract human-readable summary from structured results.
        # execute_tool returns JSON strings with {"status": ..., "data": ...};
        # we parse them so the LLM sees the actual tool output, not the JSON wrapper.
        msg_content: str
        if isinstance(result, str) and result.strip().startswith("{"):
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict) and "data" in parsed:
                    msg_content = str(parsed["data"])
                else:
                    msg_content = result
            except (json.JSONDecodeError, ValueError):
                # Not actually JSON — pass through unchanged
                msg_content = str(result)
        elif isinstance(result, dict) and "data" in result:
            # MCP tools and other paths may return a dict directly
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

    # ── Event-specific hook helpers ──────────────────────────────────────

    async def _run_pre_bash_hooks(
        self, func_args: dict, workspace: str
    ) -> str | None:
        """Run PRE_BASH hooks.  Returns block message or None."""
        if not self.hook_manager:
            return None
        try:
            from wisp.infra.hook_types import HookEvent, build_hook_context
            context = build_hook_context(
                event=HookEvent.PRE_BASH,
                tool_name="run_bash",
                tool_args=func_args,
                workspace=self.config.workspace,
                session_id="",
                cwd=str(Path(self.config.workspace or ".")),
            )
            hook_results = await self.hook_manager.arun_hooks(HookEvent.PRE_BASH, context)
            if _should_block_hook(hook_results):
                return f"[Blocked by PRE_BASH hook: {_collect_hook_messages(hook_results)}]"
            modified = _get_modified_args(hook_results)
            if modified is not None:
                func_args.clear()
                func_args.update(modified)
        except ImportError:
            pass
        except Exception as e:
            logger.warning("PRE_BASH hook failed: %s", e)
        return None

    async def _run_pre_file_hooks(
        self, func_name: str, func_args: dict, workspace: str
    ) -> str | None:
        """Run PRE_FILE_WRITE hooks.  Returns block message or None."""
        if not self.hook_manager:
            return None
        try:
            from wisp.infra.hook_types import HookEvent, build_hook_context
            context = build_hook_context(
                event=HookEvent.PRE_FILE_WRITE,
                tool_name=func_name,
                tool_args=func_args,
                workspace=self.config.workspace,
                session_id="",
                cwd=str(Path(self.config.workspace or ".")),
            )
            hook_results = await self.hook_manager.arun_hooks(HookEvent.PRE_FILE_WRITE, context)
            if _should_block_hook(hook_results):
                return f"[Blocked by PRE_FILE_WRITE hook: {_collect_hook_messages(hook_results)}]"
            modified = _get_modified_args(hook_results)
            if modified is not None:
                func_args.clear()
                func_args.update(modified)
        except ImportError:
            pass
        except Exception as e:
            logger.warning("PRE_FILE_WRITE hook failed: %s", e)
        return None

    async def _run_post_tool_hooks(
        self, func_name: str, func_args: dict, result: str | dict, workspace: str
    ) -> None:
        """Fire POST_TOOL_USE / POST_BASH hooks (best-effort — failures are logged)."""
        if not self.hook_manager:
            return
        try:
            from wisp.infra.hook_types import HookEvent, build_hook_context
            ctx = build_hook_context(
                event=HookEvent.POST_TOOL_USE,
                tool_name=func_name,
                tool_args=func_args,
                workspace=self.config.workspace,
                session_id="",
                cwd=str(Path(self.config.workspace or ".")),
                extra={"result": str(result)[:4000]},  # cap size
            )
            # POST_TOOL_USE always fires for every tool
            await self.hook_manager.arun_hooks(HookEvent.POST_TOOL_USE, ctx)
            if func_name == "run_bash":
                await self.hook_manager.arun_hooks(HookEvent.POST_BASH, ctx)
        except ImportError:
            pass
        except Exception as e:
            logger.debug("Post-tool hook failed: %s", e)

    # ── Internal guards ──────────────────────────────────────────────

    async def _run_pre_tool_hooks(
        self, func_name: str, func_args: dict, workspace: str
    ) -> str | None:
        """Run PRE_TOOL_USE hooks. Returns block message if blocked, else None."""
        if not self.hook_manager:
            return None
        try:
            # Refresh hook registry before each tool call so that a
            # hook written earlier this session can still fire.
            if hasattr(self.hook_manager, "maybe_reload_hooks"):
                self.hook_manager.maybe_reload_hooks()
            from wisp.infra.hook_types import HookEvent
            context = {
                "tool_name": func_name,
                "tool_args": func_args,
                "workspace": self.config.workspace,
                "session_id": "",
                "cwd": str(Path(self.config.workspace or ".")),
            }
            hook_results = await self.hook_manager.arun_hooks(HookEvent.PRE_TOOL_USE, context)
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
        if getattr(self.config, "plan_mode", False) and func_name in _get_write_tools(self.config):
            return f"[Blocked: plan mode — {func_name} requires write access]"
        return None

    def _check_dangerous_command(self, func_name: str, func_args: dict) -> str | None:
        """Check dangerous bash commands. Returns block message if blocked."""
        if func_name == "run_bash":
            danger_reason = check_dangerous_command(func_args.get("command", ""))
            if danger_reason:
                return f"[Blocked: dangerous command — {danger_reason}]"
        return None

    def _check_permission_mode(self, func_name: str) -> str | None:
        """Check permission mode guard.

        Returns a block message if the tool is hard-blocked by the current
        permission mode, or None to allow (possibly falling through to the
        approval handler).

        Hard blocks (no approval_handler can override):
          read_only -> all write/edit/bash/git operations AND all MCP tools
        """
        mode = getattr(self.config, "permission_mode", PermissionMode.AUTO_EDIT)
        if mode == PermissionMode.READ_ONLY and func_name in _get_write_tools(self.config):
            return f"[Blocked: read_only mode - {func_name} is not allowed]"
        # MCP tools are external code — always gated in READ_ONLY mode
        if mode == PermissionMode.READ_ONLY and func_name.startswith("mcp:"):
            return f"[Blocked: read_only mode - MCP tool {func_name} is not allowed]"
        return None

    def _needs_forced_approval(self, func_name: str) -> bool:
        """Return True if this tool must go through the approval handler,
        even when auto_approve is True.

        This lets permission modes override the auto_approve shortcut:
          ask_all   -> all write tools need approval
          auto_edit -> bash and git writes need approval (file ops are free)
          full      -> auto_approve governs normally
          read_only -> already caught by hard block above

        MCP tools are treated as ALWAYS requiring approval because the agent
        cannot inspect their internal behavior; they are external code.
        """
        # MCP tools = external code = always require explicit approval
        if func_name.startswith("mcp:"):
            return True
        mode = getattr(self.config, "permission_mode", PermissionMode.AUTO_EDIT)
        if mode == PermissionMode.ASK_ALL:
            return func_name in _get_write_tools(self.config)
        if mode == PermissionMode.AUTO_EDIT:
            return func_name in ("run_bash", "git_branch", "git_commit", "git_push", "gh_pr_create")
        return False

    async def _execute_tool(
        self, func_name: str, func_args: dict, workspace: str
    ) -> tuple[str | dict, float]:
        """Execute the actual tool. Returns (result, duration_ms)."""
        start = time.monotonic()
        result: str | dict = ""

        # reload hooks on every tool call so new / removed files are picked up
        if self.hook_manager:
            try:
                self.hook_manager.load_project_hooks()
            except Exception:
                pass

        if func_name == "spawn":
            result = await self._spawn(func_args, workspace)
        elif func_name == "fanout":
            result = await self._fanout(func_args, workspace)
        elif self._is_mcp_tool(func_name):
            result = await self._call_mcp_tool(func_name, func_args)
        elif func_name == "run_bash":
            try:
                from wisp.tools.bash import async_tool_run_bash
                from wisp.tools.registry import _build_tool_metadata
                raw_result = await async_tool_run_bash(
                    command=func_args.get("command", ""),
                    workspace=workspace,
                    timeout=int(func_args.get("timeout", 60)),
                )
                metadata = _build_tool_metadata(func_name, func_args, raw_result)
                structured = {
                    "status": "ok",
                    "tool": func_name,
                    "data": raw_result,
                    "metadata": metadata,
                }
                result = json.dumps(structured, ensure_ascii=False)
            except ToolError as e:
                from wisp.tools.registry import _build_tool_metadata
                tb = traceback.format_exc()
                logger.error(
                    "Tool %s raised ToolError: %s", func_name, str(e)
                )
                structured = {
                    "status": "error",
                    "tool": func_name,
                    "data": f"ToolError: {e}",
                    "traceback": tb,
                    "metadata": _build_tool_metadata(func_name, func_args, ""),
                }
                result = json.dumps(structured, ensure_ascii=False)
            except Exception as e:
                from wisp.tools.registry import _build_tool_metadata
                tb = traceback.format_exc()
                logger.error(
                    "Tool %s raised unexpected exception: %s\n%s",
                    func_name,
                    str(e),
                    tb,
                )
                structured = {
                    "status": "error",
                    "tool": func_name,
                    "data": f"Unexpected error: {e}",
                    "traceback": tb,
                    "metadata": _build_tool_metadata(func_name, func_args, ""),
                }
                result = json.dumps(structured, ensure_ascii=False)
        else:
            try:
                result = await asyncio.to_thread(
                    execute_tool,
                    func_name,
                    func_args,
                    workspace,
                    max_data_chars=8000,
                    file_lock=self.file_lock,
                    lsp_manager=self.lsp_manager,
                )
            except ToolError as e:
                tb = traceback.format_exc()
                from wisp.tools.registry import _build_tool_metadata
                logger.error("Tool %s raised ToolError: %s", func_name, str(e))
                structured = {
                    "status": "error",
                    "tool": func_name,
                    "data": f"ToolError: {e}",
                    "traceback": tb,
                    "metadata": _build_tool_metadata(func_name, func_args, ""),
                }
                result = json.dumps(structured, ensure_ascii=False)
            except Exception as e:
                tb = traceback.format_exc()
                from wisp.tools.registry import _build_tool_metadata
                logger.error(
                    "Tool %s raised unexpected exception: %s\n%s",
                    func_name,
                    str(e),
                    tb,
                )
                structured = {
                    "status": "error",
                    "tool": func_name,
                    "data": f"Unexpected error: {e}",
                    "traceback": tb,
                    "metadata": _build_tool_metadata(func_name, func_args, ""),
                }
                result = json.dumps(structured, ensure_ascii=False)

        duration_ms = (time.monotonic() - start) * 1000

        # ── Write-verify: auto-lint after file writes ─────────────────
        if func_name in ("write_file", "edit_file", "edit_file_multi"):
            file_path = func_args.get("path", "")
            if file_path and '"status": "ok"' in str(result):
                try:
                    lint_feedback = await self._run_write_verify(file_path, workspace)
                    if lint_feedback:
                        if isinstance(result, str):
                            try:
                                parsed = json.loads(result)
                                if isinstance(parsed, dict) and "data" in parsed:
                                    parsed["data"] = str(parsed["data"]) + lint_feedback
                                    result = json.dumps(parsed, ensure_ascii=False)
                                else:
                                    result = str(result) + lint_feedback
                            except json.JSONDecodeError:
                                result = str(result) + lint_feedback
                        elif isinstance(result, dict):
                            result["data"] = str(result.get("data", "")) + lint_feedback
                except Exception:
                    logger.debug("Write-verify lint failed for %s", file_path, exc_info=True)

        # fire post-hooks (non-blocking, best-effort)
        await self._run_post_tool_hooks(func_name, func_args, result, workspace)

        return result, duration_ms

    def _is_mcp_tool(self, name: str) -> bool:
        """Check if a tool name belongs to an MCP server.

        Accepts both the canonical prefixed form ``mcp:server/tool``
        and legacy bare names.
        """
        if not self.mcp:
            return False
        # Fast path: canonical namespace prefix
        if name.startswith("mcp:"):
            return True
        # Legacy bare-name search — must match a tool on some MCP server
        try:
            for tool in self.mcp.get_all_tools():
                if getattr(tool, "name", None) == name:
                    return True
        except Exception:
            pass
        return False

    async def _call_mcp_tool(self, func_name: str, func_args: dict) -> str:
        """Call an MCP tool and truncate if needed.  Runs in a thread so stdio doesn't block the loop."""
        if not self.mcp:
            return json.dumps({
                "status": "error",
                "tool": func_name,
                "data": "MCP error: no MCP manager",
            }, ensure_ascii=False)
        try:
            result = await asyncio.to_thread(self.mcp.call_tool, func_name, func_args)
            if isinstance(result, str) and len(result) > 8000:
                result = result[:8000] + f"\n... [truncated {len(result)} total chars]"
            return result
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("MCP tool %s failed: %s\n%s", func_name, str(e), tb)
            return json.dumps({
                "status": "error",
                "tool": func_name,
                "data": f"MCP error: {e}",
                "traceback": tb,
            }, ensure_ascii=False)

    async def _spawn(self, func_args: dict, workspace: str) -> str:
        """Execute spawn tool — role-driven subagent.

        Builds a SubagentContract from role defaults with optional overrides
        for advanced cases (explicit tools, output_format, output_schema, etc.).
        Returns structured JSON: {ok, summary, files, error, elapsed_seconds, role}.
        """
        if not self.subagent_orchestrator:
            return json.dumps({
                "status": "error",
                "tool": "spawn",
                "data": "Subagent orchestrator not available — wire it via CompositionRoot",
                "metadata": {},
            }, ensure_ascii=False)

        task = func_args.get("task", func_args.get("prompt", ""))
        if not task:
            return json.dumps({
                "status": "error",
                "tool": "spawn",
                "data": "spawn requires a 'task' argument",
                "metadata": {},
            }, ensure_ascii=False)

        role = func_args.get("role", "generalist")

        # Resolve role config for defaults
        try:
            from wisp.multi_agent.roles import ROLE_CONFIGS
            role_cfg = ROLE_CONFIGS.get(role)
        except Exception:
            role_cfg = None

        if role_cfg is None:
            valid = ["coder", "reviewer", "tester", "researcher", "planner", "debugger", "generalist"]
            return json.dumps({
                "status": "error",
                "tool": "spawn",
                "data": f"Unknown role '{role}'. Valid roles: {', '.join(valid)}",
                "metadata": {},
            }, ensure_ascii=False)

        # Resolve params: explicit args > role defaults
        timeout = func_args.get("timeout_seconds") or role_cfg.timeout_seconds
        max_iter = func_args.get("max_iterations") or role_cfg.max_iterations
        worktree = func_args.get("worktree_isolated", False)
        model_override = func_args.get("model") or role_cfg.model
        tools = func_args.get("tools") or role_cfg.allowed_tools
        output_format = func_args.get("output_format", "text")
        output_schema = func_args.get("output_schema")
        max_tokens = func_args.get("max_tokens")
        auto_retry = func_args.get("auto_retry", True)

        try:
            from wisp.multi_agent.task import SubagentContract

            contract = SubagentContract(
                name=f"spawn-{role}",
                role=role,
                task=task,
                tools=tools,
                max_iterations=int(max_iter),
                timeout_seconds=float(timeout),
                worktree_isolated=worktree,
                model=model_override,
                workspace=workspace,
                auto_approve=func_args.get("auto_approve", False),
                output_format=output_format,
                output_schema=output_schema,
                max_tokens=max_tokens,
                max_retries=int(auto_retry) * 2,
            )

            result = await self.subagent_orchestrator._run_with_retry(contract)

            return json.dumps({
                "status": "ok",
                "tool": "spawn",
                "data": {
                    "ok": result.success,
                    "summary": result.output[:2000] if result.output else "",
                    "files": result.files_changed or [],
                    "error": result.error,
                    "elapsed_seconds": round(result.elapsed_seconds, 1),
                    "role": role,
                },
                "metadata": {
                    "role": role,
                    "task_id": result.task_id,
                    "elapsed_seconds": result.elapsed_seconds,
                    "files_changed": result.files_changed,
                    "tokens_used": result.tokens_used,
                    "timed_out": result.timed_out,
                },
            }, ensure_ascii=False)
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("spawn failed: %s\n%s", str(e), tb)
            return json.dumps({
                "status": "error",
                "tool": "spawn",
                "data": {
                    "ok": False,
                    "summary": "",
                    "files": [],
                    "error": str(e),
                    "elapsed_seconds": 0,
                    "role": role,
                },
                "metadata": {"traceback": tb},
            }, ensure_ascii=False)

    async def _fanout(self, func_args: dict, workspace: str) -> str:
        """Execute fanout tool — parallel role-driven subagents.

        Each task in the tasks array gets its own role-driven SubagentContract.
        All run concurrently via run_parallel. Returns aggregated structured results.
        """
        if not self.subagent_orchestrator:
            return json.dumps({
                "status": "error",
                "tool": "fanout",
                "data": "Subagent orchestrator not available — wire it via CompositionRoot",
                "metadata": {},
            }, ensure_ascii=False)

        tasks_spec = func_args.get("tasks", [])
        if not tasks_spec:
            return json.dumps({
                "status": "error",
                "tool": "fanout",
                "data": "fanout requires a 'tasks' array",
                "metadata": {},
            }, ensure_ascii=False)

        max_concurrent = int(func_args.get("max_concurrent", 4))

        try:
            from wisp.multi_agent.roles import ROLE_CONFIGS
            from wisp.multi_agent.task import SubagentContract
        except Exception as e:
            return json.dumps({
                "status": "error",
                "tool": "fanout",
                "data": f"Failed to import multi_agent modules: {e}",
                "metadata": {},
            }, ensure_ascii=False)

        contracts = []
        for i, spec in enumerate(tasks_spec):
            if not isinstance(spec, dict):
                return json.dumps({
                    "status": "error",
                    "tool": "fanout",
                    "data": f"tasks[{i}] must be an object with 'task' and optional 'role'",
                    "metadata": {},
                }, ensure_ascii=False)

            task = spec.get("task", "")
            if not task:
                return json.dumps({
                    "status": "error",
                    "tool": "fanout",
                    "data": f"tasks[{i}] requires a 'task' field",
                    "metadata": {},
                }, ensure_ascii=False)

            role = spec.get("role", "generalist")
            role_cfg = ROLE_CONFIGS.get(role)
            if role_cfg is None:
                valid = ["coder", "reviewer", "tester", "researcher", "planner", "debugger", "generalist"]
                return json.dumps({
                    "status": "error",
                    "tool": "fanout",
                    "data": f"tasks[{i}]: unknown role '{role}'. Valid: {', '.join(valid)}",
                    "metadata": {},
                }, ensure_ascii=False)

            timeout = spec.get("timeout_seconds") or role_cfg.timeout_seconds
            max_iter = spec.get("max_iterations") or role_cfg.max_iterations
            worktree = spec.get("worktree_isolated", False)
            model_override = spec.get("model") or None

            contracts.append(SubagentContract(
                name=f"fanout-{i}-{role}",
                role=role,
                task=task,
                tools=role_cfg.allowed_tools,
                max_iterations=int(max_iter),
                timeout_seconds=float(timeout),
                worktree_isolated=worktree,
                model=model_override,
                workspace=workspace,
                auto_approve=spec.get("auto_approve", False),
            ))

        try:
            results = await self.subagent_orchestrator.run_parallel(
                contracts, max_concurrent=max_concurrent,
            )
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("fanout run_parallel failed: %s\n%s", str(e), tb)
            return json.dumps({
                "status": "error",
                "tool": "fanout",
                "data": {
                    "ok": False,
                    "results": [],
                    "error": str(e),
                    "total_elapsed_seconds": 0,
                },
                "metadata": {"traceback": tb},
            }, ensure_ascii=False)

        result_items = []
        for r in results:
            result_items.append({
                "task": r.task_id,
                "ok": r.success,
                "summary": r.output[:2000] if r.output else "",
                "files": r.files_changed or [],
                "error": r.error,
                "elapsed_seconds": round(r.elapsed_seconds, 1),
            })

        all_ok = all(r.success for r in results)
        total_elapsed = sum(r.elapsed_seconds for r in results)
        total_files = list({f for r in results for f in (r.files_changed or [])})

        return json.dumps({
            "status": "ok",
            "tool": "fanout",
            "data": {
                "ok": all_ok,
                "results": result_items,
                "total_elapsed_seconds": round(total_elapsed, 1),
                "all_files": total_files,
                "summary": f"{sum(1 for r in results if r.success)}/{len(results)} subagents succeeded",
            },
            "metadata": {
                "total": len(results),
                "succeeded": sum(1 for r in results if r.success),
                "failed": sum(1 for r in results if not r.success),
                "total_tokens": sum(r.tokens_used for r in results),
                "total_elapsed_seconds": round(total_elapsed, 1),
            },
        }, ensure_ascii=False)

    async def _run_write_verify(self, file_path: str, workspace: str) -> str:
        """Run lint + affected tests after a file write/edit. Returns feedback or ''."""
        feedback_parts: list[str] = []

        # ── Lint ──────────────────────────────────────────────────────
        try:
            from wisp.tools.lsp import tool_lsp_diagnostics
            lint_result = await asyncio.to_thread(
                tool_lsp_diagnostics, path=file_path, workspace=workspace
            )
            if lint_result and "No issues found" not in lint_result \
               and "No diagnostics available" not in lint_result \
               and not lint_result.startswith("Error:"):
                feedback_parts.append(f"[Lint: {lint_result.strip()[:500]}]")
        except Exception:
            pass

        # ── Affected tests ────────────────────────────────────────────
        try:
            from wisp.tools.tests import tool_run_tests
            test_result = await asyncio.to_thread(
                tool_run_tests, files=[file_path], workspace=workspace, timeout=60
            )
            if test_result:
                # Only include if tests were actually found and run
                if "0/0 passed" not in test_result and "no tests" not in test_result.lower():
                    short = test_result.strip()
                    if len(short) > 600:
                        short = short[:600] + "..."
                    feedback_parts.append(f"[Tests: {short}]")
        except Exception:
            pass

        return "\n".join(feedback_parts) if feedback_parts else ""

    def _record_metrics(self, func_name: str, duration_ms: float, result: str | dict) -> None:
        """Record tool execution metrics."""
        if not self.metrics:
            return
        ok = (
            (isinstance(result, str) and '"status": "ok"' in result)
            or (isinstance(result, dict) and result.get("status") == "ok")
        )
        if hasattr(self.metrics, "record_tool"):
            self.metrics.record_tool(func_name, duration_ms, success=ok)
