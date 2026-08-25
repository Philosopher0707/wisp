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
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from wisp.config import WispConfig
from wisp.infra.security import PermissionMode
from wisp.core.events import (
    AgentEvent,
    tool_result as _tool_result_event,
    approval_request as _approval_request_event,
    subagent as _subagent_event,
    system as system_event,
)
from wisp.tools.errors import ToolError
from wisp.tools._utils import check_dangerous_command
from wisp.tools.registry import execute_tool
from wisp.tools.registry import TOOL_IMPLS
from wisp.tools.audit import AuditLog

logger = logging.getLogger(__name__)

# Heartbeat cadence while blocking subagent tools run (patchable in tests).
_HEARTBEAT_FIRST_S = 5.0
_HEARTBEAT_EVERY_S = 20.0

# (tool_name, args, danger_reason) -> (approved, modified_args_or_none)
ApprovalHandler = Callable[[str, dict, str], Awaitable[tuple[bool, Optional[dict]]]]

_MCP_SHADOW_WARNED: set[str] = set()


def _warn_mcp_shadowed_builtin(name: str) -> None:
    """Warn once when an MCP server advertises a name owned by a builtin.

    Bare-name MCP calls lose to builtins at dispatch; operators need to know
    their server's tool is being shadowed instead of silently never running.
    """
    if name in _MCP_SHADOW_WARNED:
        return
    _MCP_SHADOW_WARNED.add(name)
    logger.warning(
        "MCP server exposes tool '%s' which collides with a builtin — the "
        "builtin wins. Call it as mcp:<server>/%s to reach the MCP tool.",
        name, name,
    )


def orchestrator_event_to_agent_event(orch_ev: Any) -> AgentEvent:
    """Convert an OrchestratorEvent to a canonical subagent AgentEvent.

    The mapping keeps the EventKind vocabulary intact and squeezes the
    payload into one short detail fragment per kind — enough for live
    status lines without dumping raw payloads into the event stream.
    """
    p = getattr(orch_ev, "payload", None) or {}
    if not isinstance(p, dict):
        p = {}
    kind = str(getattr(orch_ev, "event_type", "") or "")
    name = str(getattr(orch_ev, "task_id", "") or "")
    role = str(p.get("role", ""))

    if kind == "task_started":
        detail = str(p.get("description", ""))[:80]
    elif kind == "task_completed":
        try:
            detail = f"{float(p.get('elapsed', 0)):.1f}s"
        except (TypeError, ValueError):
            detail = ""
        files = p.get("files_changed") or []
        if files:
            detail += f" · {len(files)} file{'s' if len(files) != 1 else ''}"
    elif kind == "task_failed":
        detail = str(p.get("error", ""))[:120]
    elif kind == "task_retry":
        attempt = p.get("retry", p.get("attempt", "?"))
        backoff = p.get("backoff_seconds")
        detail = f"retry #{attempt}"
        if isinstance(backoff, (int, float)) and backoff > 0:
            detail += f" in {backoff:.0f}s"
    else:
        detail = ""

    extras: dict[str, Any] = {}
    if isinstance(p.get("elapsed"), (int, float)):
        extras["elapsed"] = p["elapsed"]
    if p.get("error"):
        extras["error"] = str(p["error"])
    return _subagent_event(kind=kind, name=name, role=role, detail=detail, **extras)

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
    "spawn_background",
    "subagent_send",
    "orchestrate_vote",
    "orchestrate_map_reduce",
    "orchestrate_chain",
    "orchestrate_dag",
    "capture_skill",
}

# Pure, network-bound tools worth memoizing against model loops.
_REPEAT_GUARD_TOOLS: frozenset[str] = frozenset({"web_fetch", "web_search"})
_REPEAT_TTL_SECONDS = 600.0
_REPEAT_NUDGE_AFTER = 1   # first repeat serves the cached copy with a warning
_REPEAT_BLOCK_AFTER = 2   # further repeats get an instruction to synthesize

# Executor-dispatched subagent tools — never shadowed by MCP bare names.
_SUBAGENT_TOOLS: frozenset[str] = frozenset({
    "spawn", "fanout", "spawn_background",
    "subagent_list", "subagent_result", "subagent_send", "subagent_cancel",
    "orchestrate_vote", "orchestrate_map_reduce", "orchestrate_chain",
    "orchestrate_dag",
})


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
        background_agents: Any | None = None,
        extensions: Any | None = None,
    ):
        self.config = config
        self.extensions = extensions
        self.hook_manager = hook_manager
        self.metrics = metrics
        self.mcp = mcp
        self.file_lock = file_lock
        self.lsp_manager = lsp_manager
        self.subagent_orchestrator = subagent_orchestrator
        self.audit_trail = audit_trail
        self.background_agents = background_agents
        from wisp.skill_capture import get_capture
        self.skill_capture = get_capture()
        # key -> (monotonic_ts, cached_result_str, hit_count)
        self._repeat_cache: dict[str, tuple[float, str, int]] = {}
        # Set per spawn/fanout execution; carries AgentEvents from the
        # orchestrator's sync progress callbacks into execute()'s stream.
        self._sub_event_queue: Optional[asyncio.Queue] = None

    # ── Public API ───────────────────────────────────────────────────

    def _repeat_key(self, func_name: str, func_args: dict[str, Any]) -> str:
        import json as _json
        try:
            return f"{func_name}:{_json.dumps(func_args, sort_keys=True, default=str)}"
        except (TypeError, ValueError):
            return ""

    def _check_repeat_call(self, func_name: str, func_args: dict[str, Any]) -> str | None:
        """Return a substitute result when the call duplicates recent work.

        None means proceed normally. First repeat: replay the cached result
        tagged with a nudge. Further repeats: refuse with an explicit
        instruction to use what was already fetched.
        """
        if func_name not in _REPEAT_GUARD_TOOLS:
            self._repeat_cache.pop("_last", None)  # keep dict bounded implicitly
            return None
        key = self._repeat_key(func_name, func_args)
        if not key:
            return None
        entry = self._repeat_cache.get(key)
        now = time.monotonic()
        if entry is not None and now - entry[0] <= _REPEAT_TTL_SECONDS:
            _, cached, count = entry
            self._repeat_cache[key] = (now, cached, count + 1)
            if count >= _REPEAT_BLOCK_AFTER:
                return (
                    f"[REPEAT BLOCKED] You have already made this identical "
                    f"{func_name} call {count + 1} times. The earlier results "
                    f"are in this conversation. Do NOT fetch again — "
                    f"synthesize your answer from the data you already have."
                )
            return (
                f"[REPEAT] Identical {func_name} call already made moments ago.\n"
                f"Cached result:\n{cached[:4000]}\n"
                f"(Calling it again with the same arguments will return the same "
                f"data. If you have enough information, answer now.)"
            )
        # Miss or expired: record AFTER execution succeeds (see execute()).
        self._pending_repeat_key = key
        return None

    def _record_repeat_result(self, func_name: str, result_str: str) -> None:
        """Cache a successful guarded-tool result for the repeat guard."""
        key = getattr(self, "_pending_repeat_key", None)
        if key and func_name in _REPEAT_GUARD_TOOLS and result_str:
            self._repeat_cache[key] = (time.monotonic(), result_str, 0)
        self._pending_repeat_key = None

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

        # ── Repeat-call guard for network-bound tools ──
        # Live evidence: a looping model re-fetched one URL for minutes,
        # burning iterations and tripping API rate limits. Identical
        # web_fetch/web_search calls get the cached result (once, with a
        # nudge), then an instruction to synthesize instead of re-fetching.
        repeat_msg = self._check_repeat_call(func_name, func_args)
        if repeat_msg is not None:
            yield _tool_result_event(func_name, repeat_msg)
            return

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
        if func_name not in TOOL_IMPLS and self.extensions is not None:
            # Extension tools (skill__, mcp, plugins) are advertised via the
            # host but live outside TOOL_IMPLS; builtins always win.
            ext_result = self.extensions.call_tool(func_name, func_args, workspace)
            if ext_result is not None:
                yield _tool_result_event(
                    func_name,
                    ext_result,
                    duration_ms=0,
                    tool_call_id=tool_call_id,
                )
                return

        if func_name in ("spawn", "fanout", "orchestrate_vote",
                         "orchestrate_map_reduce", "orchestrate_chain",
                         "orchestrate_dag"):
            # Stream subagent lifecycle events while the orchestrator runs:
            # the progress callback lands on a queue from inside the exec
            # task, and this generator interleaves it with waiting. A
            # wall-clock heartbeat fills the gaps — real researchers emit
            # nothing between started/completed for minutes at a time,
            # which users read as a hang.
            queue: asyncio.Queue = asyncio.Queue()
            self._sub_event_queue = queue
            exec_task = asyncio.create_task(
                self._execute_tool(func_name, func_args, workspace)
            )
            started = time.monotonic()
            next_heartbeat = started + _HEARTBEAT_FIRST_S
            try:
                while not exec_task.done():
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        now = time.monotonic()
                        if now >= next_heartbeat:
                            next_heartbeat = now + _HEARTBEAT_EVERY_S
                            yield system_event(
                                f"⏳ {func_name} running… {int(now - started)}s"
                            )
                        continue
                    yield item
                while not queue.empty():
                    yield queue.get_nowait()
                result, duration_ms = exec_task.result()
            finally:
                self._sub_event_queue = None
                if not exec_task.done():
                    exec_task.cancel()
        else:
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

        # Skill capture: feed the workflow recorder (best-effort).
        try:
            self.skill_capture.record(func_name, func_args)
        except Exception:
            logger.debug("skill capture record failed", exc_info=True)

        # Repeat guard: remember successful guarded-tool results only.
        result_str = result if isinstance(result, str) else json.dumps(result, default=str)
        if not (result_str.startswith("Error") or '"status": "error"' in result_str[:200]
                or result_str.startswith("[WEB_FETCH_FAILED]")):
            try:
                self._record_repeat_result(func_name, str(result))
            except Exception:
                logger.debug("repeat-cache record failed", exc_info=True)

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
        if mode == PermissionMode.READ_ONLY and self._is_external_call(func_name):
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
        # MCP tools = external code = always require explicit approval.
        if self._is_external_call(func_name):
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

        # A colliding bare name routes to the builtin (see _is_external_call);
        # warn once so operators know their server's tool is being shadowed.
        # Subagent tools are dispatched here regardless of TOOL_IMPLS.
        if (func_name not in _SUBAGENT_TOOLS
                and func_name in TOOL_IMPLS and self._is_mcp_tool(func_name)):
            _warn_mcp_shadowed_builtin(func_name)

        if func_name == "spawn":
            result = await self._spawn(func_args, workspace)
        elif func_name == "fanout":
            result = await self._fanout(func_args, workspace)
        elif func_name == "spawn_background":
            result = await self._spawn_background(func_args, workspace)
        elif func_name == "subagent_list":
            result = await self._subagent_list(func_args)
        elif func_name == "subagent_result":
            result = await self._subagent_result(func_args)
        elif func_name == "subagent_send":
            result = await self._subagent_send(func_args)
        elif func_name == "subagent_cancel":
            result = await self._subagent_cancel(func_args)
        elif func_name == "orchestrate_vote":
            result = await self._orchestrate_vote(func_args, workspace)
        elif func_name == "orchestrate_map_reduce":
            result = await self._orchestrate_map_reduce(func_args, workspace)
        elif func_name == "orchestrate_chain":
            result = await self._orchestrate_chain(func_args, workspace)
        elif func_name == "orchestrate_dag":
            result = await self._orchestrate_dag(func_args, workspace)
        elif func_name == "capture_skill":
            result = await self._capture_skill(func_args, workspace)
        elif self._is_external_call(func_name):
            # Builtin names always win over a bare-name MCP match: an MCP
            # server advertising "read_file" must not replace the core tool.
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
            # Per-tool timeout to prevent hanging the agent on stuck tools
            tool_timeout = getattr(self.config, "tool_timeout", 300) if self.config else 300
            try:
                async with asyncio.timeout(tool_timeout):
                    result = await asyncio.to_thread(
                        execute_tool,
                        func_name,
                        func_args,
                        workspace,
                        max_data_chars=8000,
                        file_lock=self.file_lock,
                        lsp_manager=self.lsp_manager,
                    )
            except asyncio.TimeoutError:
                logger.error("Tool %s timed out after %ds", func_name, tool_timeout)
                structured = {
                    "status": "error",
                    "tool": func_name,
                    "data": f"Tool timed out after {tool_timeout}s",
                    "metadata": _build_tool_metadata(func_name, func_args, ""),
                }
                result = json.dumps(structured, ensure_ascii=False)
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

        return result, duration_ms

    def _is_external_call(self, name: str) -> bool:
        """True when this call reaches MCP/external code rather than a builtin.

        Builtin names always win over the bare-name MCP match — dispatch and
        both approval gates share this predicate so they can never disagree
        about which side of the boundary a call lands on.
        """
        if name.startswith("mcp:"):
            return True
        if name.startswith("mcp__") and name.count("__") >= 2:
            return True
        if name in TOOL_IMPLS:
            return False
        return self._is_mcp_tool(name)

    def _is_mcp_tool(self, name: str) -> bool:
        """Check if a tool name belongs to an MCP server.

        Accepts the canonical prefixed form ``mcp:server/tool``, the legacy
        double-underscore form ``mcp__server__tool``, and bare names that
        match a tool on some MCP server.
        """
        if not self.mcp:
            return False
        # Canonical namespace prefix
        if name.startswith("mcp:"):
            return True
        # Legacy double-underscore form (older extension schemas)
        if name.startswith("mcp__") and name.count("__") >= 2:
            return True
        # Bare-name search — must match a tool on some MCP server
        try:
            for tool in self.mcp.get_all_tools():
                if getattr(tool, "name", None) == name:
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def _canonical_mcp_name(name: str) -> str:
        """Normalize legacy MCP name forms to ``mcp:server/tool``."""
        if name.startswith("mcp__") and name.count("__") >= 2:
            _, server, tool = name.split("__", 2)
            return f"mcp:{server}/{tool}"
        return name

    async def _call_mcp_tool(self, func_name: str, func_args: dict) -> str:
        """Call an MCP tool and truncate if needed.  Runs in a thread so stdio doesn't block the loop."""
        if not self.mcp:
            return json.dumps({
                "status": "error",
                "tool": func_name,
                "data": "MCP error: no MCP manager",
            }, ensure_ascii=False)
        try:
            canonical = self._canonical_mcp_name(func_name)
            result = await asyncio.to_thread(self.mcp.call_tool, canonical, func_args)
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

            # Inherit the executing agent's nesting depth — without this every
            # spawned contract resets to 0 and the orchestrator's depth guard
            # can never trip (unbounded recursion).
            contract._subagent_depth = int(getattr(self.config, "_subagent_depth", 0) or 0) + 1
            contract._subagent_branch_count = int(getattr(self.config, "_subagent_branch_count", 0) or 0) + 1

            queue = self._sub_event_queue
            if queue is not None:
                contract.progress_callback = lambda ev: queue.put_nowait(
                    orchestrator_event_to_agent_event(ev)
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

    # ── Background subagent tools ─────────────────────────────────────

    def _get_background_manager(self) -> Any | None:
        """Resolve the background manager, creating one lazily when only an
        orchestrator was wired (direct ToolExecutor users, tests)."""
        if self.background_agents is None:
            if self.subagent_orchestrator is None:
                return None
            from wisp.multi_agent.background import BackgroundAgentManager
            self.background_agents = BackgroundAgentManager(self.subagent_orchestrator)
        return self.background_agents

    def _build_contract(self, func_args: dict, workspace: str, name: str) -> tuple[Any | None, str]:
        """Shared spawn/spawn_background contract builder.

        Returns (contract, error). Exactly one of the two is non-empty.
        """
        role = func_args.get("role", "generalist")
        try:
            from wisp.multi_agent.roles import ROLE_CONFIGS
            role_cfg = ROLE_CONFIGS.get(role)
        except Exception:
            role_cfg = None

        if role_cfg is None:
            valid = ["coder", "reviewer", "tester", "researcher", "planner", "debugger", "generalist"]
            return None, f"Unknown role '{role}'. Valid roles: {', '.join(valid)}"

        timeout = func_args.get("timeout_seconds") or role_cfg.timeout_seconds
        max_iter = func_args.get("max_iterations") or role_cfg.max_iterations

        from wisp.multi_agent.task import SubagentContract
        contract = SubagentContract(
            name=name,
            role=role,
            task=func_args.get("task", ""),
            tools=func_args.get("tools") or role_cfg.allowed_tools,
            max_iterations=int(max_iter),
            timeout_seconds=float(timeout),
            worktree_isolated=func_args.get("worktree_isolated", False),
            model=func_args.get("model") or role_cfg.model,
            workspace=workspace,
            auto_approve=func_args.get("auto_approve", False),
            output_format=func_args.get("output_format", "text"),
            output_schema=func_args.get("output_schema"),
            max_tokens=func_args.get("max_tokens"),
            max_retries=int(func_args.get("auto_retry", True)) * 2,
        )
        # Inherit the executing agent's nesting depth — without this every
        # spawned contract resets to 0 and the orchestrator's depth guard
        # can never trip (unbounded recursion).
        contract._subagent_depth = int(getattr(self.config, "_subagent_depth", 0) or 0) + 1
        contract._subagent_branch_count = int(getattr(self.config, "_subagent_branch_count", 0) or 0) + 1
        return contract, ""

    def _tool_error(self, tool: str, message: str) -> str:
        return json.dumps({
            "status": "error",
            "tool": tool,
            "data": message,
            "metadata": {},
        }, ensure_ascii=False)

    async def _spawn_background(self, func_args: dict, workspace: str) -> str:
        """Execute spawn_background — launch a subagent without blocking.

        Returns an agent id immediately; results are collected later via
        subagent_result / subagent_list.
        """
        manager = self._get_background_manager()
        if manager is None:
            return self._tool_error(
                "spawn_background",
                "Subagent orchestrator not available — wire it via CompositionRoot",
            )

        task = func_args.get("task", func_args.get("prompt", ""))
        if not task:
            return self._tool_error("spawn_background", "spawn_background requires a 'task' argument")

        contract, err = self._build_contract(func_args, workspace, name="spawn-background")
        if err:
            return self._tool_error("spawn_background", err)
        contract.task = task

        launched = await manager.launch(contract, label=func_args.get("label", ""))
        if not launched.get("ok"):
            return json.dumps({
                "status": "error",
                "tool": "spawn_background",
                "data": launched.get("error", "launch failed"),
                "metadata": {},
            }, ensure_ascii=False)

        return json.dumps({
            "status": "ok",
            "tool": "spawn_background",
            "data": {
                "agent_id": launched["agent_id"],
                "label": launched["label"],
                "status": launched["status"],
                "note": "Running in background. Poll with subagent_list / subagent_result; "
                        "continue with subagent_send; stop with subagent_cancel.",
            },
            "metadata": {
                "agent_id": launched["agent_id"],
                "role": contract.role,
            },
        }, ensure_ascii=False)

    async def _subagent_list(self, func_args: dict) -> str:
        manager = self._get_background_manager()
        if manager is None:
            return self._tool_error("subagent_list", "Background agents not available")
        entries = manager.list(include_finished=bool(func_args.get("include_finished", True)))
        return json.dumps({
            "status": "ok",
            "tool": "subagent_list",
            "data": {"agents": entries, "count": len(entries)},
            "metadata": {},
        }, ensure_ascii=False)

    async def _subagent_result(self, func_args: dict) -> str:
        manager = self._get_background_manager()
        if manager is None:
            return self._tool_error("subagent_result", "Background agents not available")
        agent_id = func_args.get("agent_id", "")
        if not agent_id:
            return self._tool_error("subagent_result", "subagent_result requires an 'agent_id' argument")
        snapshot = await manager.result(agent_id, wait_seconds=float(func_args.get("wait_seconds", 0) or 0))
        if not snapshot.get("ok"):
            return self._tool_error("subagent_result", snapshot.get("error", "unknown"))
        return json.dumps({
            "status": "ok",
            "tool": "subagent_result",
            "data": snapshot,
            "metadata": {"agent_id": agent_id, "status": snapshot.get("status")},
        }, ensure_ascii=False)

    async def _subagent_send(self, func_args: dict) -> str:
        manager = self._get_background_manager()
        if manager is None:
            return self._tool_error("subagent_send", "Background agents not available")
        agent_id = func_args.get("agent_id", "")
        message = func_args.get("message", "")
        if not agent_id or not message:
            return self._tool_error("subagent_send", "subagent_send requires 'agent_id' and 'message'")
        outcome = await manager.send(agent_id, message)
        if not outcome.get("ok"):
            return self._tool_error("subagent_send", outcome.get("error", "send failed"))
        return json.dumps({
            "status": "ok",
            "tool": "subagent_send",
            "data": {
                "agent_id": outcome["agent_id"],
                "status": outcome["status"],
                "note": "Continuation running. Collect with subagent_result.",
            },
            "metadata": {"agent_id": agent_id},
        }, ensure_ascii=False)

    async def _subagent_cancel(self, func_args: dict) -> str:
        manager = self._get_background_manager()
        if manager is None:
            return self._tool_error("subagent_cancel", "Background agents not available")
        agent_id = func_args.get("agent_id", "")
        if not agent_id:
            return self._tool_error("subagent_cancel", "subagent_cancel requires an 'agent_id' argument")
        outcome = manager.cancel(agent_id)
        if not outcome.get("ok"):
            return self._tool_error("subagent_cancel", outcome.get("error", "cancel failed"))
        return json.dumps({
            "status": "ok",
            "tool": "subagent_cancel",
            "data": outcome,
            "metadata": {"agent_id": agent_id},
        }, ensure_ascii=False)

    # ── Orchestration patterns (blocking, result returned this turn) ──

    def _pattern_result(self, tool: str, pattern: str, result: Any) -> str:
        """Shared JSON envelope for vote/map-reduce/chain outcomes."""
        ok = bool(getattr(result, "success", False)) if result is not None else False
        output = (getattr(result, "output", "") or "")[:2000] if result is not None else ""
        return json.dumps({
            "status": "ok" if ok else "error",
            "tool": tool,
            "data": {
                "ok": ok,
                "pattern": pattern,
                "summary": output,
                "files": list(getattr(result, "files_changed", []) or []) if result is not None else [],
                "error": getattr(result, "error", None) if result is not None else "no result",
                "elapsed_seconds": round(getattr(result, "elapsed_seconds", 0.0) or 0.0, 1),
            },
            "metadata": {
                "pattern": pattern,
                "task_id": getattr(result, "task_id", "") if result is not None else "",
            },
        }, ensure_ascii=False)

    async def _orchestrate_vote(self, func_args: dict, workspace: str) -> str:
        """Execute orchestrate_vote — N independent voters, majority wins."""
        orch = self.subagent_orchestrator
        if orch is None:
            return self._tool_error("orchestrate_vote",
                                    "Subagent orchestrator not available — wire it via CompositionRoot")
        task = func_args.get("task", "")
        if not task:
            return self._tool_error("orchestrate_vote", "orchestrate_vote requires a 'task' argument")

        try:
            n_voters = max(2, min(6, int(func_args.get("voters", 3) or 3)))
        except (TypeError, ValueError):
            n_voters = 3
        try:
            threshold = float(func_args.get("consensus_threshold", 0.6) or 0.6)
        except (TypeError, ValueError):
            threshold = 0.6
        threshold = min(1.0, max(0.1, threshold))

        base, err = self._build_contract({"task": task, **{
            k: func_args[k] for k in ("role", "timeout_seconds", "model") if k in func_args
        }}, workspace, name="vote-voter")
        if err:
            return self._tool_error("orchestrate_vote", err)

        voters = [
            dc_replace(base, name=f"vote-{i}")
            for i in range(n_voters)
        ]
        result = await orch.run_vote(
            task=task, agents=voters,
            consensus_threshold=threshold,
        )
        return self._pattern_result("orchestrate_vote", "vote", result)

    async def _orchestrate_map_reduce(self, func_args: dict, workspace: str) -> str:
        """Execute orchestrate_map_reduce — parallel mappers + synthesis."""
        orch = self.subagent_orchestrator
        if orch is None:
            return self._tool_error("orchestrate_map_reduce",
                                    "Subagent orchestrator not available — wire it via CompositionRoot")
        task = func_args.get("task", "")
        items = func_args.get("items") or []
        if not task:
            return self._tool_error("orchestrate_map_reduce", "orchestrate_map_reduce requires a 'task' argument")
        if not isinstance(items, list) or not items:
            return self._tool_error("orchestrate_map_reduce",
                                    "orchestrate_map_reduce requires a non-empty 'items' array")
        items = [str(i) for i in items[:20]]

        role = func_args.get("role", "generalist")
        try:
            max_concurrent = max(1, min(8, int(func_args.get("max_concurrent", 4) or 4)))
        except (TypeError, ValueError):
            max_concurrent = 4

        template, err = self._build_contract({"task": task, "role": role}, workspace, name="map")
        if err:
            return self._tool_error("orchestrate_map_reduce", err)

        from wisp.multi_agent.task import SubagentContract

        def mapper(item: str) -> SubagentContract:
            return dc_replace(template, task=f"{task}\n\nItem:\n{item}")

        reducer_task = (
            f"Synthesize the following per-item findings into one coherent answer. "
            f"Overall goal: {task}"
        )
        result = await orch.run_map_reduce(
            task=task, items=items, mapper=mapper,
            reducer=reducer_task, max_concurrent=max_concurrent,
        )
        return self._pattern_result("orchestrate_map_reduce", "map_reduce", result)

    async def _orchestrate_chain(self, func_args: dict, workspace: str) -> str:
        """Execute orchestrate_chain — sequential pipeline with context passing."""
        orch = self.subagent_orchestrator
        if orch is None:
            return self._tool_error("orchestrate_chain",
                                    "Subagent orchestrator not available — wire it via CompositionRoot")
        steps = func_args.get("steps") or []
        if not isinstance(steps, list) or len(steps) < 2:
            return self._tool_error("orchestrate_chain",
                                    "orchestrate_chain requires a 'steps' array with at least 2 steps")

        contracts = []
        for i, step in enumerate(steps[:6]):
            if not isinstance(step, dict) or not step.get("task"):
                return self._tool_error("orchestrate_chain", f"step {i} requires a 'task'")
            contract, err = self._build_contract(
                {"task": step["task"], "role": step.get("role", "generalist")},
                workspace, name=f"chain-{i}",
            )
            if err:
                return self._tool_error("orchestrate_chain", err)
            contracts.append(contract)

        pass_context = bool(func_args.get("pass_context", True))
        result = await orch.run_chain(contracts, pass_context=pass_context)
        return self._pattern_result("orchestrate_chain", "chain", result)

    async def _orchestrate_dag(self, func_args: dict, workspace: str) -> str:
        """Execute orchestrate_dag — dependency-ordered parallel subagents.

        Independent nodes run in parallel per level; upstream outputs are
        injected into dependents by the scheduler (dataflow edges, not
        just ordering).
        """
        orch = self.subagent_orchestrator
        if orch is None:
            return self._tool_error("orchestrate_dag",
                                    "Subagent orchestrator not available — wire it via CompositionRoot")
        nodes_spec = func_args.get("nodes") or []
        if not isinstance(nodes_spec, list) or not nodes_spec:
            return self._tool_error("orchestrate_dag",
                                    "orchestrate_dag requires a non-empty 'nodes' array")

        from wisp.multi_agent.dag import TaskDAG, TaskNode

        dag = TaskDAG()
        seen_names: set[str] = set()
        for i, spec in enumerate(nodes_spec):
            if not isinstance(spec, dict) or not spec.get("name") or not spec.get("task"):
                return self._tool_error(
                    "orchestrate_dag", f"node {i} requires 'name' and 'task'")
            name = str(spec["name"])
            if name in seen_names:
                return self._tool_error("orchestrate_dag", f"duplicate node name '{name}'")
            seen_names.add(name)

        for spec in nodes_spec:
            name = str(spec["name"])
            contract, err = self._build_contract(
                {"task": spec["task"], "role": spec.get("role", "generalist")},
                workspace, name=name,
            )
            if err:
                return self._tool_error("orchestrate_dag", err)
            deps = spec.get("depends_on") or []
            unknown = [d for d in deps if d not in seen_names]
            if unknown:
                return self._tool_error(
                    "orchestrate_dag",
                    f"node '{name}' depends on unknown node(s): {', '.join(unknown)}")
            dag.add_node(TaskNode(name=name, task=contract, dependencies=list(deps)))

        cycle_errors = dag.validate()
        if cycle_errors:
            return self._tool_error("orchestrate_dag",
                                    f"invalid DAG: {'; '.join(cycle_errors[:3])}")

        try:
            max_par = max(1, min(8, int(func_args.get("max_parallelism", 4) or 4)))
        except (TypeError, ValueError):
            max_par = 4

        dag_result = await orch.run_dag(dag, max_parallelism=max_par)
        ok = bool(getattr(dag_result, "success", False))
        node_results = getattr(dag_result, "node_results", {}) or {}
        level_order = getattr(dag_result, "level_order", []) or []
        ordered_names = [n for level in level_order for n in level] or list(node_results)
        summary_lines = []
        for node_name in ordered_names:
            r = node_results.get(node_name)
            out = (getattr(r, "output", "") or "").strip() if r is not None else ""
            status = "ok" if (r is not None and getattr(r, "success", False)) else "FAILED"
            snippet = out[:160].replace("\n", " ")
            summary_lines.append(f"[{status}] {node_name}: {snippet}" if snippet
                                 else f"[{status}] {node_name}")
        all_files = sorted({f for r in node_results.values()
                            for f in (getattr(r, "files_changed", []) or [])})
        return json.dumps({
            "status": "ok" if ok else "error",
            "tool": "orchestrate_dag",
            "data": {
                "ok": ok,
                "pattern": "dag",
                "summary": "\n".join(summary_lines)[:2000],
                "files": all_files,
                "error": "; ".join(getattr(dag_result, "errors", [])[:3]) or None,
                "elapsed_seconds": round(getattr(dag_result, "total_elapsed", 0.0) or 0.0, 1),
                "level_order": level_order,
            },
            "metadata": {
                "pattern": "dag",
                "nodes": len(node_results),
            },
        }, ensure_ascii=False)

    async def _capture_skill(self, func_args: dict, workspace: str) -> str:
        """Execute capture_skill — persist a demonstrated workflow as SKILL.md.

        Prefers an explicit step list; falls back to the recorder's repeated
        tail sequence, then to the raw recent history.
        """
        name = str(func_args.get("name", "") or "").strip()
        description = str(func_args.get("description", "") or "").strip()
        if not name:
            return self._tool_error("capture_skill", "capture_skill requires a 'name' argument")
        if not description:
            return self._tool_error("capture_skill", "capture_skill requires a 'description' argument")

        from wisp.skill_capture import CapturedStep, get_capture
        capture = self.skill_capture or get_capture()

        explicit = func_args.get("steps") or []
        try:
            if isinstance(explicit, list) and explicit:
                steps = [CapturedStep(tool=str(s)[:120]) for s in explicit]
            else:
                suggestion = capture.suggest()
                if suggestion is not None:
                    steps = suggestion.steps
                    description = (
                        f"{description} "
                        f"(observed {suggestion.occurrences}x in this session)"
                    )
                elif len(capture) > 0:
                    steps = capture.recent(8)
                else:
                    return self._tool_error(
                        "capture_skill",
                        "No tool history recorded yet — pass explicit 'steps' "
                        "or run the workflow first.",
                    )

            path, merged = capture.render_skill(name, description, workspace, steps=steps)
        except ValueError as e:
            return self._tool_error("capture_skill", str(e))
        except OSError as e:
            logger.error("capture_skill write failed: %s", e, exc_info=True)
            return self._tool_error("capture_skill", f"could not write skill file: {e}")

        if merged:
            note = ("Merged into the existing skill of this name (capture count "
                    "bumped; a new step sequence is kept as a variant).")
        else:
            note = ("Skill saved. Reload it any time with /skill "
                    f"{path.parent.name} — it is also auto-discovered.")

        return json.dumps({
            "status": "ok",
            "tool": "capture_skill",
            "data": {
                "ok": True,
                "skill_name": path.parent.name,
                "path": str(path),
                "step_count": len(steps),
                "merged": merged,
                "note": note,
            },
            "metadata": {"skill_path": str(path)},
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

            # Ground every child in the parent's filesystem reality. Live
            # evidence (2026-08-25): the model wrote 'autopipe/core' while
            # the actual location was 'active/autopipe/autopipe/core'; all
            # six children list_files'd into the void and failed fast.
            task = (
                f"[Workspace root: {workspace}] Paths are relative to this "
                "root, exactly as they appear in the parent conversation. "
                "If a path is not found, list_files from the workspace root "
                "to locate it before proceeding.\n\n"
            ) + task

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

        queue = self._sub_event_queue
        if queue is not None:
            for c in contracts:
                c.progress_callback = lambda ev: queue.put_nowait(
                    orchestrator_event_to_agent_event(ev)
                )

        # Inherit the executing agent's nesting depth (see _spawn).
        parent_depth = int(getattr(self.config, "_subagent_depth", 0) or 0)
        parent_branch = int(getattr(self.config, "_subagent_branch_count", 0) or 0)
        for i, c in enumerate(contracts):
            c._subagent_depth = parent_depth + 1
            c._subagent_branch_count = parent_branch + 1 + i

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
