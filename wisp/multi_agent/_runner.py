"""SubagentRunner — execute a single subagent in the parent's event loop.

No nested event loops. No threads. Direct async execution with
``asyncio.timeout`` for cancellation.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from pathlib import Path
from typing import Any

from wisp.config import WispConfig
from wisp.session import Session
from wisp.session_store import get_store

from .task import EventKind, OrchestratorEvent, SubagentContract, SubagentResult

logger = logging.getLogger(__name__)


class SubagentRunner:
    """Execute a single subagent contract and return a ``SubagentResult``.

    Responsibilities:
    - Build child config from parent config + contract overrides
    - Create a Session for the subagent
    - Build system prompt (role-based or default)
    - Run ``WispAgentCore.run_task()`` directly in the same event loop
    - Collect tool calls, files changed, token estimates
    - Return a fully populated ``SubagentResult``

    This class does **not** handle:
    - Caching (see ``ResultCache``)
    - Token budgets (see ``BudgetTracker``)
    - Worktrees (see ``WorktreeManager``)
    - Persistence (see ``Persistence``)
    - Patterns (map-reduce, vote, chain)
    """

    def __init__(
        self,
        parent_config: WispConfig,
        workspace: Path,
    ):
        self.parent_config = parent_config
        self.workspace = workspace
        self._session_mgr = get_store()

    async def run(
        self,
        contract: SubagentContract,
        agent_workspace: str,
        system_prompt: str,
        progress_callback: Any = None,
    ) -> SubagentResult:
        """Run a single subagent and return its result.

        Uses ``asyncio.timeout`` for cancellation — no threads, no nested loops.
        """
        start = time.monotonic()
        tool_calls_log: list[dict] = []

        # Build child config
        child_cfg = self._build_child_config(contract, agent_workspace)

        # Create session
        session = Session.create(
            model=child_cfg.model,
            workspace=agent_workspace,
            first_prompt=contract.task,
        )
        session.title = f"[sub] {contract.name}"

        # Emit start event
        if progress_callback:
            await self._emit(
                progress_callback,
                contract.name,
                EventKind.TASK_STARTED,
                {"role": contract.role, "description": contract.task},
            )

        try:
            async with asyncio.timeout(contract.timeout_seconds):
                result_dict = await self._run_agent(
                    contract,
                    child_cfg,
                    session,
                    system_prompt,
                    agent_workspace,
                    tool_calls_log,
                )

            session.touch()
            self._session_mgr.save(session)

            duration = time.monotonic() - start
            subagent_result = SubagentResult(
                task_id=contract.name,
                success=result_dict["success"],
                output=result_dict["output"],
                tool_calls=list(tool_calls_log),
                elapsed_seconds=duration,
                error=result_dict.get("error"),
                session_id=session.id,
                files_changed=result_dict.get("files_changed", []),
                iterations_used=result_dict.get("iterations_used", 0),
            )

            # Token estimation
            messages = result_dict.get("messages", [])
            if messages:
                in_tok, out_tok, total_tok = self._estimate_tokens(messages)
                subagent_result.input_tokens = in_tok
                subagent_result.output_tokens = out_tok
                subagent_result.tokens_used = total_tok

                # Enforce per-contract output token limit
                if contract.max_output_tokens and out_tok > contract.max_output_tokens:
                    logger.warning(
                        "Subagent %s output tokens %d exceed limit %d",
                        contract.name, out_tok, contract.max_output_tokens,
                    )
                    subagent_result.output = (
                        subagent_result.output[:contract.max_output_chars]
                        + f"\n\n[OUTPUT TRUNCATED: exceeded {contract.max_output_tokens} output tokens]"
                    )

            # Enforce per-contract output char limit
            if len(subagent_result.output) > contract.max_output_chars:
                subagent_result.output = (
                    subagent_result.output[:contract.max_output_chars]
                    + f"\n\n[OUTPUT TRUNCATED: exceeded {contract.max_output_chars} characters]"
                )

            # Emit completion event
            if progress_callback:
                await self._emit(
                    progress_callback,
                    contract.name,
                    EventKind.TASK_COMPLETED,
                    {
                        "files_changed": subagent_result.files_changed,
                        "elapsed": duration,
                        "output": subagent_result.output[:200],
                    },
                )

            return subagent_result

        except asyncio.TimeoutError:
            duration = time.monotonic() - start
            logger.warning("Subagent %s timed out after %.1fs", contract.name, duration)
            session.touch()
            self._session_mgr.save(session)
            if progress_callback:
                await self._emit(
                    progress_callback,
                    contract.name,
                    EventKind.TASK_FAILED,
                    {"error": f"Timeout after {contract.timeout_seconds}s"},
                )
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output=f"[TIMED OUT after {duration:.1f}s]",
                tool_calls=list(tool_calls_log),
                elapsed_seconds=duration,
                error=f"Timeout after {contract.timeout_seconds}s",
                session_id=session.id,
                timed_out=True,
            )

        except Exception as exc:
            duration = time.monotonic() - start
            logger.error("Subagent %s crashed: %s", contract.name, exc, exc_info=True)
            if progress_callback:
                await self._emit(
                    progress_callback,
                    contract.name,
                    EventKind.TASK_FAILED,
                    {"error": str(exc)},
                )
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output="",
                tool_calls=list(tool_calls_log),
                elapsed_seconds=duration,
                error=str(exc),
                session_id=session.id,
            )

    async def _run_agent(
        self,
        contract: SubagentContract,
        config: WispConfig,
        session: Session,
        system_prompt: str,
        workspace_path: str,
        tool_calls_log: list[dict],
    ) -> dict:
        """Run a WispAgentCore instance and return its result dict.

        Direct async execution — no threads, no nested loops.
        """
        from wisp.core.agent import WispAgentCore

        agent = WispAgentCore(
            config=config,
            session=session,
            role=f"subagent:{contract.name}",
        )

        try:
            agent.config.workspace = workspace_path

            if contract.tools != ["all"]:
                agent._allowed_tools = set(contract.tools)

            task_result = await agent.run_task(
                task_description=contract.task,
                workspace=workspace_path,
                max_iterations=contract.max_iterations,
                timeout_seconds=contract.timeout_seconds,
                system_prompt=system_prompt,
            )

            # Collect tool calls
            for msg in agent.messages:
                tcs = msg.get("tool_calls", []) or []
                for tc in tcs:
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    args = func.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                    if not isinstance(args, dict):
                        args = {}
                    arg_preview = self._compact_args(args)
                    tool_calls_log.append({"name": name, "args_preview": arg_preview})

            # Extract files changed
            files_changed: list[str] = []
            output_text = task_result.get("output", "") or ""
            if output_text:
                files_changed = self._extract_files_changed(output_text)

            return {
                "success": task_result.get("success", False),
                "output": output_text,
                "error": None if task_result.get("success") else task_result.get("output"),
                "files_changed": files_changed,
                "iterations_used": len([m for m in agent.messages if m.get("role") == "assistant"]),
                "messages": agent.messages,
            }
        finally:
            agent.close()

    def _build_child_config(self, contract: SubagentContract, workspace: str) -> WispConfig:
        """Clone the parent config with optional per-subagent overrides."""
        child = copy.deepcopy(self.parent_config)
        child.model = contract.model or self.parent_config.model
        child.workspace = workspace
        child.auto_approve = contract.auto_approve
        child.max_context_tokens = contract.max_tokens or self.parent_config.max_context_tokens
        return child

    def _estimate_tokens(self, messages: list[dict]) -> tuple[int, int, int]:
        """Estimate token count from message history.

        Returns (input_tokens, output_tokens, total_tokens).
        """
        chars_per_token = getattr(self.parent_config, "chars_per_token", 4)
        input_chars = 0
        output_chars = 0

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            if isinstance(content, str):
                text = content
            else:
                text = str(content)

            if role in ("user", "system", "tool"):
                input_chars += len(text)
            elif role == "assistant":
                output_chars += len(text)
                for tc in msg.get("tool_calls", []) or []:
                    func = tc.get("function", {})
                    args = func.get("arguments", "")
                    if isinstance(args, str):
                        output_chars += len(args)
                    else:
                        output_chars += len(str(args))

        input_tokens = input_chars // chars_per_token
        output_tokens = output_chars // chars_per_token
        return input_tokens, output_tokens, input_tokens + output_tokens

    @staticmethod
    async def _emit(
        callback: Any,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit a progress event via the callback."""
        event = OrchestratorEvent(
            task_id=task_id,
            event_type=event_type,
            payload=payload,
        )
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(event)
            else:
                callback(event)
        except Exception as e:
            logger.warning("Progress callback failed for %s: %s", task_id, e)

    @staticmethod
    def _compact_args(args: dict) -> str:
        """One-line preview of tool arguments."""
        key = next(iter(args), None)
        if key is None:
            return "..."
        val = args[key]
        s = str(val)
        if len(s) > 60:
            s = s[:60] + "..."
        return f"{key}={s}"

    @staticmethod
    def _extract_files_changed(text: str) -> list[str]:
        """Best-effort extraction of file paths mentioned in output text."""
        import re

        patterns = [
            r"`([a-zA-Z0-9_\-./]+\.(?:py|ts|js|rs|go|java|rb|sh))`",
            r"(?:changed|modified|touched|files written|created files?)[:\-]\s*\n?\s*[-*]\s+([^\s,]+)",
            r"\b([a-zA-Z0-9_\-/]+\.(?:py|ts|js|rs|go|java|rb|sh))\b",
        ]
        found: list[str] = []
        seen: set[str] = set()
        for pat in patterns:
            for m in re.finditer(pat, text, re.IGNORECASE):
                path = m.group(1).strip()
                if path not in seen and len(path) > 2:
                    seen.add(path)
                    found.append(path)
        return found[:20]
