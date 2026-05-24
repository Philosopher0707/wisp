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
from wisp.infra.store import UnifiedStore

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
        store: UnifiedStore | None = None,
        tool_executor: Any | None = None,
    ):
        self.parent_config = parent_config
        self.workspace = workspace
        default_db = Path(workspace) / ".wisp" / "wisp.db"
        self._store = store or UnifiedStore(default_db)
        self._tool_executor = tool_executor

    async def run(
        self,
        contract: SubagentContract,
        agent_workspace: str,
        system_prompt: str,
        progress_callback: Any = None,
    ) -> SubagentResult:
        """Run a single subagent and return its result.

        Uses a single wall-clock deadline — the contract timeout is the hard
        upper bound regardless of iteration count. Per-iteration timeouts are
        derived from remaining budget so a 300s contract with 30 iterations
        cannot run 9000s.
        """
        start = time.monotonic()
        deadline = start + contract.timeout_seconds
        tool_calls_log: list[dict] = []

        # Build child config
        child_cfg = self._build_child_config(contract, agent_workspace)

        # Create session dict
        import uuid
        from datetime import datetime, timezone
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        session = {
            "id": session_id,
            "model": child_cfg.model,
            "workspace": agent_workspace,
            "messages": [{"role": "user", "content": contract.task}],
            "compaction_history": [],
            "created_at": now,
            "updated_at": now,
            "title": f"[sub] {contract.name}",
        }
        self._store.create_session(session_id, child_cfg.model, agent_workspace, title=f"[sub] {contract.name}")

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
                    deadline,
                )

            from datetime import datetime, timezone
            session["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._store.save_session(session)

            duration = time.monotonic() - start
            subagent_result = SubagentResult(
                task_id=contract.name,
                success=result_dict["success"],
                output=result_dict["output"],
                tool_calls=list(tool_calls_log),
                elapsed_seconds=duration,
                error=result_dict.get("error"),
                session_id=session["id"],
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
            session["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._store.save_session(session)
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
                session_id=session["id"],
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
                session_id=session["id"],
            )

    async def _run_agent(
        self,
        contract: SubagentContract,
        config: WispConfig,
        session: Session,
        system_prompt: str,
        workspace_path: str,
        tool_calls_log: list[dict],
        deadline: float,
    ) -> dict:
        """Run a stateless WispAgentCore instance and return its result dict.

        Uses the new engine (wisp.core.engine) instead of the deprecated
        stateful core (wisp.core.agent).
        """
        from wisp.core.engine import WispAgentCore as StatelessCore
        from wisp.providers.factory import ProviderFactory
        from wisp.infra.security import SecurityPolicy
        from wisp.infra.extensions import ExtensionHost

        # Propagate subagent depth/branch from contract to config so the core
        # can access them (and tests can verify propagation).
        config._subagent_depth = getattr(contract, "_subagent_depth", 0)
        config._subagent_branch_count = getattr(contract, "_subagent_branch_count", 0)

        provider_name = getattr(config, "provider", None)
        if not isinstance(provider_name, str):
            provider_name = "ollama"
        factory = ProviderFactory()
        provider = factory.from_config(config)

        security = SecurityPolicy(
            permission_mode=getattr(config, "permission_mode", "full"),
        )
        extensions = ExtensionHost()

        core = StatelessCore(
            provider=provider,
            security=security,
            extensions=extensions,
            config=config,
            tool_executor=self._tool_executor,
        )

        session_dict = dict(session)
        if system_prompt:
            session_dict["messages"] = [{"role": "system", "content": system_prompt}] + list(session_dict.get("messages", []))

        # Partition context — only pass relevant history to subagent
        raw_messages = list(session_dict.get("messages", []))
        if len(raw_messages) > 10:
            from .context_partition import ContextPartitioner
            partitioner = ContextPartitioner(max_messages=10, max_tokens=4000)
            filtered = partitioner.partition(raw_messages, contract.task, include_system=True)
            # Always ensure the task message is present
            task_msg = {"role": "user", "content": contract.task}
            has_task = any(
                m.get("role") == "user" and m.get("content") == contract.task
                for m in filtered
            )
            if not has_task:
                filtered.append(task_msg)
            session_dict["messages"] = filtered
            logger.debug(
                "Context partitioned for %s: %d → %d messages",
                contract.name, len(raw_messages), len(filtered),
            )

        output_text = ""
        engine_iterations = 0

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError("contract deadline reached")

        async with asyncio.timeout(remaining):
            async for event in core.turn(session_dict, contract.task):
                etype = event.get("type")
                if etype == "content":
                    output_text = event.get("text", "")
                elif etype == "tool_call":
                    engine_iterations += 1
                    name = event.get("name", "")
                    args = event.get("arguments", {})
                    arg_preview = self._compact_args(args)
                    tool_calls_log.append({"name": name, "args_preview": arg_preview})
                elif etype == "error":
                    output_text = event.get("message", "")
                    return {
                        "success": False,
                        "output": output_text,
                        "error": output_text,
                        "files_changed": [],
                        "iterations_used": engine_iterations,
                        "messages": session_dict.get("messages", []),
                    }

        files_changed = self._extract_files_changed(output_text)
        return {
            "success": True,
            "output": output_text,
            "error": None,
            "files_changed": files_changed,
            "iterations_used": engine_iterations,
            "messages": session_dict.get("messages", []),
        }

    def _build_child_config(self, contract: SubagentContract, workspace: str) -> WispConfig:
        """Clone the parent config with optional per-subagent overrides."""
        child = copy.deepcopy(self.parent_config)
        child.model = contract.model or self.parent_config.model
        child.workspace = workspace
        child.auto_approve = contract.auto_approve
        child.max_context_tokens = contract.max_tokens or self.parent_config.max_context_tokens
        child.max_iterations = contract.max_iterations
        return child

    def _estimate_tokens(self, messages: list[dict]) -> tuple[int, int, int]:
        """Estimate token count from message history.

        Returns (input_tokens, output_tokens, total_tokens).
        Uses tiktoken for accurate counting when available, falls back to
        model-specific char ratios (claude=3.5, gpt=4, gemini=3, default=4).
        """
        from wisp.infra.token_counter import TokenCounter

        model = getattr(self.parent_config, "model", "") or ""
        chars_per_token = getattr(self.parent_config, "chars_per_token", 4)
        counter = TokenCounter(chars_per_token=chars_per_token)

        input_chars = 0
        output_chars = 0

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            text = content if isinstance(content, str) else str(content)

            if role in ("user", "system", "tool"):
                input_chars += len(text)
            elif role == "assistant":
                output_chars += len(text)
                for tc in msg.get("tool_calls", []) or []:
                    func = tc.get("function", {})
                    args = func.get("arguments", "")
                    output_chars += len(args) if isinstance(args, str) else len(str(args))

        # Use tiktoken if model is known for total count, char ratio for split.
        # Tool call args already accounted in char computation above.
        input_tokens = counter.estimate_chars(input_chars)
        output_tokens = counter.estimate_chars(output_chars)
        if model:
            parts: list[str] = []
            for m in messages:
                content = m.get("content", "") or ""
                if isinstance(content, str):
                    parts.append(content)
                for tc in m.get("tool_calls", []) or []:
                    func = tc.get("function", {})
                    args = func.get("arguments", "")
                    if isinstance(args, str):
                        parts.append(args)
            full_text = "".join(parts)
            tiktoken_total = counter.count(full_text, model=model)
            if tiktoken_total > 0:
                # Sanity check: tiktoken total should be in same ballpark
                char_total = input_tokens + output_tokens
                if char_total > 0 and abs(tiktoken_total - char_total) / char_total > 0.5:
                    # Large discrepancy — tiktoken disagrees with char ratio.
                    # Use tiktoken total, split proportionally.
                    total_chars = input_chars + output_chars
                    if total_chars > 0:
                        input_tokens = max(0, int(tiktoken_total * input_chars / total_chars))
                        output_tokens = max(0, tiktoken_total - input_tokens)

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
        """Best-effort extraction of file paths mentioned in output text.

        Three-pass strategy:
        1. Backtick-quoted tokens — highest confidence.
        2. Structured multi-line list items after change-verb keywords.
        3. Bare word tokens that look like plausible file paths.

        Markdown decoration (**bold**, *italic*, _italic_) is stripped in a
        pre-processing step so that surrounded paths are still found.
        Paths are accepted if they contain a dot or a slash (covers
        extensionless files like Makefile / Dockerfile).
        """
        import re

        # ── Known-good extensions ────────────────────────────────────────
        _EXT = (
            r"py|ts|js|jsx|tsx|mjs|cjs|"
            r"rs|go|java|rb|sh|bash|zsh|fish|"
            r"c|cpp|cc|h|hpp|"
            r"json|yaml|yml|toml|ini|cfg|conf|env|"
            r"md|rst|txt|csv|"
            r"html|css|scss|sass|"
            r"sql|proto|"
            r"dockerfile|makefile|gemfile|rakefile"
        )
        _EXT_RE = re.compile(rf"\.(?:{_EXT})$", re.IGNORECASE)

        # ── Pre-strip markdown decoration (keep backticks for Pass 1) ───
        clean = text
        clean = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", clean)   # **bold**
        clean = re.sub(r"\*([^*\n]+)\*",     r"\1", clean)   # *italic*
        clean = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", clean)  # _italic_

        # ── Helpers ──────────────────────────────────────────────────────
        def _clean_token(raw: str) -> str:
            s = raw.strip()
            s = re.sub(r"^[`*_\[(<\"']+|[`*_\])>\"']+$", "", s)
            return s.strip()

        def _is_plausible(s: str) -> bool:
            if not s or len(s) < 2 or len(s) > 260:
                return False
            if "." not in s and "/" not in s:
                return False
            if re.search(r'[<>:"|?*\x00-\x1f]', s):
                return False
            if _EXT_RE.search(s):
                return True
            if "/" in s:
                return True
            basename = s.rsplit("/", 1)[-1].lower()
            return basename in {
                "makefile", "dockerfile", "gemfile", "rakefile",
                "procfile", "vagrantfile", "jenkinsfile", "brewfile",
            }

        found: list[str] = []
        seen: set[str] = set()

        def _add(raw: str) -> None:
            path = _clean_token(raw)
            if path and path not in seen and _is_plausible(path):
                seen.add(path)
                found.append(path)

        # ── Pass 1: backtick-quoted tokens (run on original text) ────────
        for m in re.finditer(r"`([^`\n]{2,260})`", text):
            _add(m.group(1))

        # ── Pass 2: multi-line list blocks after change-verb keywords ────
        # Capture everything after the colon/dash up to the next blank line
        # or non-list line, then iterate over every bullet item inside.
        verb_block_re = re.compile(
            r"(?:changed|modified|touched|wrote|created|updated|deleted|removed)"
            r"(?:\s+files?)?"
            r"[:\-]"
            r"((?:\s*\n\s*[-*•]\s+[^\n]+)+)",
            re.IGNORECASE,
        )
        item_re = re.compile(r"[-*•]\s+([^\n]+)")
        for block_m in verb_block_re.finditer(clean):
            for item_m in item_re.finditer(block_m.group(1)):
                _add(item_m.group(1))

        # ── Pass 3: bare tokens with a known extension ───────────────────
        bare_re = re.compile(
            r"(?<![a-zA-Z0-9])"
            r"((?:[a-zA-Z0-9_\-./]+/)?"
            r"[a-zA-Z0-9_\-]+"
            r"\.(?:" + _EXT + r"))"
            r"(?![a-zA-Z0-9])",
            re.IGNORECASE,
        )
        for m in bare_re.finditer(clean):
            _add(m.group(1))

        return found[:20]
