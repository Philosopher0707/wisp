"""WispAgentCore — event-driven agent engine with zero I/O.

This is the SDK-facing core of Wisp. It contains all agent logic but never
prints, reads input, or touches global state. All output is yielded as
AgentEvent instances; all user interaction is delegated to the transport
layer via callbacks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Optional

# (tool_name, args, danger_reason) -> (approved, modified_args_or_none)
ApprovalHandler = Callable[[str, dict, str], Awaitable[tuple[bool, Optional[dict]]]]

from wisp.config import WispConfig
from wisp.ollama_client import OllamaClient, OllamaError
from wisp.providers import get_provider
from wisp.stream_events import (
    TokenBatch,
    ToolCallBatch,
    Checkpoint,
    StreamComplete,
    StreamError,
)
from wisp.tools import TOOL_SCHEMAS, execute_tool, ToolError
from wisp.skills import discover_skills
from wisp.adapters import Session, get_store
from wisp.project_context import detect_project_context, format_context
from wisp.code_index import build_index as build_regex_index, format_index_summary
from wisp.tree_sitter_index import build_index as build_ts_index, is_tree_sitter_available
from wisp.mcp import MCPManager
from wisp.memory import format_memory_block
from wisp.core.message_format import extract_text
from wisp.tool_executor import ToolExecutor
from wisp.context_assembler import ContextAssembler
from wisp.core.events import (
    AgentEvent,
    TYPE_CONTENT,
    thinking,
    tool_call as tool_call_event,
    tool_result as tool_result_event,
    content as content_event,
    error as error_event,
    done as done_event,
    system as system_event,
    approval_request,
    steering_feedback,
)

logger = logging.getLogger(__name__)


def _coerce_tool_data(value: object) -> object:
    """If value is a dict, JSON-serialize it so string ops don't crash."""
    if isinstance(value, dict):
        return json.dumps(value)
    return value


def _maybe_to_thread(sync_callable: Callable[..., Any], *args: Any) -> Any:
    """Run *sync_callable* in a thread if an event loop is running,
    otherwise call it synchronously.

    This is the minimal correct fix for running ``requests``-based code
    inside ``async def WispAgentCore.__init__`` without blocking the
    event loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return sync_callable(*args)
    # We're in a running event loop but this is a *synchronous* call-site
    # (e.g. ``__init__`` or a sync method).  ``asyncio.to_thread``
    # returns a coroutine which can't be awaited here, so we call
    # the function directly.  In production there's no running loop
    # during ``__init__``, so this path only affects tests where the
    # provider is already a fast mock anyway.
    return sync_callable(*args)


DEFAULT_SYSTEM = """You are Wisp, a helpful coding agent.

You have access to tools that let you read, write, and edit files, run bash commands, and list directories.

## Guidelines
1. Think step by step, BUT if the user says "do it", "write it", "go ahead", "now", or any other direct action command, SKIP the analysis and EXECUTE immediately based on what was already decided.
2. Prefer targeted edits (edit_file) over rewriting entire files.
3. Run tests after making changes to verify correctness.
4. For git operations, use run_bash with appropriate git commands.
5. If a command fails, diagnose the error and try a different approach.
6. Keep explanations concise but clear. Show the user what you're doing.
7. When you're done, summarize what was accomplished.
8. Before declaring a task done, run lsp_diagnostics on changed files to catch errors.
9. For git workflow: check status → branch → commit → push → create PR. Always verify each step.

## Tools available
- read_file: Read file contents (supports offset/limit for large files)
- write_file: Create or overwrite a file
- edit_file: Targeted text replacement (surgical edits, with fuzzy fallback)
- edit_file_multi: Make multiple precise edits in a single file in one call
- run_bash: Execute shell commands
- list_files: Explore directory structure
- web_fetch: Fetch content from URLs (web pages, APIs, documentation)
- web_search: Search the web for current information, docs, error messages
- search_symbols: Search code for functions, classes, structs by name (regex-based)
- search_codebase: Semantic search over the codebase using vector similarity
- remember: Store a fact in cross-session memory. ONLY use for long-term facts that should persist across multiple separate sessions (user preferences, project conventions, decisions). Do NOT use for information already in the current conversation — the full conversation history is always available to you.
- recall: Search cross-session memory and past summaries for relevant facts. ONLY use for information from earlier sessions that may have been forgotten due to context limits. Do NOT use to recall something the user just said — it's already in the current conversation context.
- spawn_subagent: Delegate a scoped task to a child agent
- git_status: Show git status (branch, uncommitted files, recent commits)
- git_diff: Show git diff for files or entire workspace
- git_branch: List/create/switch git branches
- git_commit: Stage files and commit with a message
- git_push: Push current branch to remote
- gh_pr_create: Create a GitHub pull request (requires gh CLI)
- lsp_diagnostics: Run language server diagnostics on a file
- lsp_definition: Go to definition of a symbol
- lsp_references: Find all references to a symbol
- lsp_hover: Get type info and docstring for a symbol
- lsp_symbols: List all symbols in a file as an outline tree
- diagnose: Diagnose errors from test output, tracebacks, or command failures
- run_tests: Run tests for changed files or the full test suite
- plan_task: Create a structured plan with subtasks and dependencies
- mark_step_done: Mark a plan task as completed
- update_plan: Update a plan task's status
"""


def _remove_oldest_turn(messages: list):
    """Remove the oldest logical turn (user + response + tool results).

    After removal, ensures the list still starts with a user message
    (or is empty) to maintain conversation integrity.

    Safety: never removes the last user message (preserves at least one turn).
    """
    if not messages:
        return

    while messages and messages[0].get("role") != "user":
        del messages[0]
    if not messages:
        return

    start = 0
    end = len(messages)
    for i in range(start + 1, len(messages)):
        if messages[i].get("role") == "user":
            end = i
            break

    remaining_user_count = sum(1 for m in messages if m.get("role") == "user")
    if remaining_user_count <= 1:
        return

    del messages[start:end]


def _generate_agent_id() -> str:
    return f"wisp-{uuid.uuid4().hex[:8]}"


class WispAgentCore:
    """Event-driven agent core — no print, no input, no global state."""

    def __init__(
        self,
        config: Optional[WispConfig] = None,
        session: Optional[Session] = None,
        agent_id: Optional[str] = None,
        role: Optional[str] = None,
    ):
        self.config = config or WispConfig()
        self.provider = get_provider(self.config)
        # Backward-compatible alias while the rest of the codebase migrates.
        self.client = self.provider

        # Health check on startup — use to_thread when called from async context
        # so we do not block the event loop for 5-10 seconds (Issue 20).
        try:
            healthy = _maybe_to_thread(self.provider.check_health)
            if not healthy:
                logger.warning(
                    "Provider '%s' at %s is unreachable. "
                    "Check that the service is running and the model '%s' is available.",
                    self.config.provider,
                    getattr(self.config, "ollama_url", "unknown"),
                    self.config.model,
                )
        except Exception as e:
            logger.warning("Provider health check failed: %s", e)

        if not self.config._context_tokens_explicit:
            try:
                detected = _maybe_to_thread(self.client.get_context_length)
                if detected != self.config.max_context_tokens:
                    logger.info(
                        "Auto-detected context window for %s: %d tokens",
                        self.config.model, detected,
                    )
                    self.config.max_context_tokens = detected
            except OllamaError:
                pass
        self.session_mgr = get_store()
        self.session = session
        self.messages: list[dict] = []
        self.max_iterations = self.config.max_iterations
        self._interrupted = False
        self._paused: asyncio.Event = asyncio.Event()
        self._paused.set()  # starts unpaused
        self._injected_text: Optional[str] = None
        self._system_prompt = ""
        self._active_skill: Optional[str] = None
        self.agent_id = agent_id or _generate_agent_id()
        self.role = role or "agent"
        self._role_system_extra = ""
        self._allowed_tools: Optional[set[str]] = None
        self._circuit_breaker = None
        self._metrics = None
        self._metrics_lock = threading.Lock()
        # Use singleton managers to avoid spawning duplicate child processes
        # (e.g. 5 subagents × 3 MCP servers = 15 orphaned-style processes).
        from wisp.mcp import get_mcp_manager
        self.mcp = get_mcp_manager(self.config.workspace or ".")
        self._mcp_initialized = False
        from wisp.lsp.manager import get_lsp_manager
        self.lsp = get_lsp_manager(self.config.workspace or ".")
        from wisp.agent_memory import get_agent_memory
        self.agent_memory = get_agent_memory()
        self._recent_summaries = self.agent_memory.load_recent_global(limit=7)
        from wisp.file_lock import FileLock
        from wisp.change_tracker import ChangeTracker
        self.file_lock = FileLock(self.config.workspace or ".", self.agent_id)
        self.change_tracker = ChangeTracker(self.config.workspace or ".", self.file_lock.agent_id)
        from wisp import tools as tools_module
        tools_module.set_collaboration_tools(self.file_lock, self.change_tracker)
        tools_module.set_lsp_manager(self.lsp)

        # ── Hooks ──
        self.hook_manager = None
        try:
            from wisp.hooks import HookManager
            self.hook_manager = HookManager(config=self.config, workspace=Path(self.config.workspace))
            self.hook_manager.load_project_hooks()
            logger.info("HookManager initialized")
        except ImportError:
            logger.debug("wisp.hooks module not available — hooks disabled")
        except Exception as e:
            logger.warning("Failed to initialize HookManager: %s", e)

        # ── ToolExecutor ──
        self.tool_executor = ToolExecutor(
            config=self.config,
            hook_manager=self.hook_manager,
            metrics=self.metrics,
            circuit_breaker=self._circuit_breaker,
            mcp=self.mcp,
            file_lock=self.file_lock,
            lsp_manager=self.lsp,
        )
        self.context_assembler = ContextAssembler()

        # ── Incremental token accounting (avoids O(n²) context trimming) ──
        self._cached_token_estimate: int = 0

        # ── SubagentOrchestrator ──
        from wisp.multi_agent import SubagentOrchestrator
        self._orchestrator = SubagentOrchestrator(parent_agent=self)

    def close(self) -> None:
        """Release resources: MCP connections, LSP servers, and save session state.

        Safe to call multiple times — idempotent.
        """
        try:
            self._save_session()
            self._save_session_summary()
        except Exception:
            pass
        try:
            self.mcp.shutdown()
        except Exception:
            pass
        try:
            self.lsp.shutdown_all()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ── Lazy load circuit breaker ──
    @property
    def breaker(self):
        if self._circuit_breaker is None:
            from wisp.circuit_breaker import CircuitBreaker
            self._circuit_breaker = CircuitBreaker(
                failure_threshold=getattr(self.config, "circuit_failure_threshold", 3),
                recovery_timeout=getattr(self.config, "circuit_recovery_timeout", 60),
            )
        return self._circuit_breaker

    # ── Lazy load metrics ──
    @property
    def metrics(self):
        if self._metrics is None:
            with self._metrics_lock:
                if self._metrics is None:
                    from wisp.metrics import AgentMetrics
                    self._metrics = AgentMetrics()
        return self._metrics

    # ── Steering (pause / resume) ────────────────────────────────────

    def pause(self) -> None:
        """Pause agent execution at next steering point."""
        self._paused.clear()

    def resume(self, injected_text: Optional[str] = None) -> None:
        """Resume agent execution, optionally injecting steering feedback."""
        if injected_text:
            self._injected_text = injected_text
        self._paused.set()

    async def _check_steering(self):
        """Wait if paused. Yield inject event if feedback was provided."""
        await self._paused.wait()
        if self._injected_text is not None:
            text = self._injected_text
            self._injected_text = None
            self._add_message("user", text)
            return steering_feedback(text)
        return None

    # ── Message helpers ──────────────────────────────────────────────

    def _add_message(self, role: str, content: str | list[dict], thinking: str = ""):
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        msg = {"role": role, "content": content}
        if thinking:
            msg["thinking"] = thinking
        self.messages.append(msg)
        self._invalidate_token_cache()

    def _inject_system_note(self, note: str) -> None:
        """Insert a system note as an assistant message, never as trailing system.

        Appending a system message mid-conversation is undefined behavior
        for most LLM APIs.  We instead emit it as an assistant "context"
        message so the model sees it as part of the assistant turn rather
        than corrupting the conversation format.
        """
        self.messages.append({
            "role": "assistant",
            "content": [{"type": "text", "text": f"[{note}]"}],
        })
        self._invalidate_token_cache()

    # ── Continuation expansion ───────────────────────────────────────

    # Single-word/single-phrase triggers are safe because we only fire the
    # expansion when the user message is short (<= 25 chars).  Multi-word
    # triggers that could appear inside normal questions are guarded by the
    # length check below.
    # Direct-action triggers: user commands that mean "stop analyzing and execute".
    # Fired when: user message is short (≤40 chars), previous assistant was
    # thinking-only (no tool_calls — just analysis/planning, not after-action).
    _ACTION_TRIGGERS: frozenset[str] = frozenset({
        "do it", "do it now",
        "go ahead",
        "write it", "write that", "write this", "write it now",
        "do that", "do this",
        "make it", "make that",
        "create it", "create that", "create this",
        "build it", "build that",
        "implement it", "implement that",
        "get it done", "finish it", "finish that",
        "go now",
        "execute", "execute it", "execute now",
        "proceed", "proceed now",
    })
    # Multi-word action triggers that can be prefix-matched
    _ACTION_PHRASE_TRIGGERS: frozenset[str] = frozenset({
        "write the file", "create the file", "write it now", "do it now",
        "write the code", "create the code",
    })
    _MAX_ACTION_TRIGGER_LEN = 40

    _MAX_CONTINUATION_LEN = 25

    _CONTINUATION_TRIGGERS = frozenset({
        "continue", "go on", "more", "keep going", "next", "proceed",
        "finish", "expand on that", "elaborate",
    })

    def _expand_continuation(self, user_text: str) -> str:
        """Expand continuation/action triggers so the model knows what to do.

        Two modes:
          1. Continuation triggers ("continue", "go on") — append context tail.
          2. Action triggers ("do it", "write it") — tell model to STOP thinking
             and execute immediately, preserving the context of what was planned.
        """
        lowered = user_text.strip().lower().rstrip("?.!")
        stripped = user_text.strip()

        # ── Mode 2: Action triggers ─────────────────────────────────────
        # When user gives a short direct command after the assistant was analyzing,
        # inject a note telling the model to ACT instead of re-analyzing.
        is_action_trigger = (
            lowered in self._ACTION_TRIGGERS
            or lowered in self._ACTION_PHRASE_TRIGGERS
            or any(stripped.lower().startswith(p) for p in self._ACTION_PHRASE_TRIGGERS)
        )
        if is_action_trigger and len(stripped) <= self._MAX_ACTION_TRIGGER_LEN:
            # Check if previous assistant was thinking-only (no tool_calls, long analysis)
            last_assistant = ""
            last_assistant_role = None
            for m in reversed(self.messages):
                role = m.get("role")
                if role == "assistant":
                    last_assistant = extract_text(m.get("content", "") or "")
                    last_assistant_role = m
                    break
            # Only expand if previous turn was assistant without tool calls
            # (thinking/planning, not after-action synthesis).
            if last_assistant_role and not last_assistant_role.get("tool_calls"):
                prev_text = last_assistant.strip()
                analysis_tail = prev_text[-300:].replace("\n", " ")
                return (
                    f"{stripped}\n"
                    f"[Context: Based on your previous analysis above, "
                    f"EXECUTE the task NOW. Stop thinking or analyzing further. "
                    f"Pick up exactly where you left off in your reasoning and "
                    f"PROCEED TO IMMEDIATE ACTION. "
                    f"Your prior work: ...{analysis_tail}]"
                )

        # ── Mode 1: Continuation triggers ───────────────────────────────
        if lowered not in self._CONTINUATION_TRIGGERS:
            return user_text
        if len(stripped) > self._MAX_CONTINUATION_LEN:
            return user_text
        parts: list[str] = [user_text]
        last_assistant = ""
        for m in reversed(self.messages):
            if m.get("role") == "assistant":
                last_assistant = extract_text(m.get("content", "") or "")
                break
        if last_assistant:
            tail = last_assistant[-200:].replace("\n", " ")
            parts.append(
                f"\n[Context: Continue your previous response. "
                f"Do NOT repeat anything already said. "
                f"Pick up exactly after: {tail}]"
            )
        else:
            compacted_summary = ""
            if (
                self.messages
                and self.messages[0].get("role") == "system"
                and self.messages[0].get("compacted")
            ):
                compacted_summary = extract_text(self.messages[0].get("content", "") or "")
            if compacted_summary:
                m = re.search(
                    r"→ When the user says .continue., resume from: (.+)",
                    compacted_summary,
                )
                if m:
                    topic = m.group(1).strip()
                    parts.append(
                        f"\n[Context: Resume the discussion about: {topic}. "
                        f"Do not repeat prior points. Continue from where you left off.]"
                    )
        return "\n".join(parts)

    # ── Token estimation ─────────────────────────────────────────────

    def _estimate_tokens(self, messages: list[dict]) -> int:
        """Estimate token count for a message list.

        When called with ``self.messages`` the result is cached so that
        repeated calls (e.g. inside the trimming loop) are O(1) instead
        of O(n²).
        """
        if messages is self.messages and getattr(self, "_cached_token_estimate", 0):
            return self._cached_token_estimate

        total = 0
        for msg in messages:
            if msg.get("role") != "tool":
                for key in ("content", "thinking"):
                    val = msg.get(key, "") or ""
                    if isinstance(val, list):
                        val = extract_text(val)
                    total += len(val)
            for tc in msg.get("tool_calls", []) or []:
                func = tc.get("function", {})
                total += len(func.get("name", ""))
                args = func.get("arguments", {})
                if isinstance(args, str):
                    total += len(args)
                elif isinstance(args, dict):
                    total += len(str(args))
            if msg.get("role") == "tool":
                total += len(msg.get("content", "") or "")
        result = total // self.config.chars_per_token
        if messages is self.messages:
            self._cached_token_estimate = result
        return result

    def _invalidate_token_cache(self) -> None:
        """Clear the cached token estimate after any mutation of ``self.messages``."""
        if hasattr(self, "_cached_token_estimate"):
            self._cached_token_estimate = 0

    def _trim_context_if_needed(self, system_prompt: str = ""):
        budget = self.config.max_context_tokens
        overhead = self._estimate_tokens([{"content": system_prompt}])
        user_count = sum(1 for m in self.messages if m.get("role") == "user")
        while user_count > 1 and self._estimate_tokens(self.messages) + overhead > budget:
            _remove_oldest_turn(self.messages)
            self._invalidate_token_cache()
            user_count = sum(1 for m in self.messages if m.get("role") == "user")

    # ── Session management ─────────────────────────────────────────────

    def _save_session(self):
        if self.session is not None:
            self.session.messages = self.messages
            self.session_mgr.save(self.session)

    def _save_session_summary(self) -> None:
        if self.session is None or not self.messages:
            return
        try:
            summary = self.session.summarize()
            if summary:
                self.agent_memory.save(summary)
                logger.info("Saved session summary for %s", self.session.id)
        except Exception as e:
            logger.warning("Failed to save session summary: %s", e)

    def _resolve_session(self, session_id: str):
        loaded = self.session_mgr.load_session(session_id)
        if loaded is not None:
            return loaded
        resolved = self.session_mgr.get_session_id_from_fragment(session_id)
        if resolved:
            return self.session_mgr.load_session(resolved)
        return None

    def _start_new_session(self):
        self._save_session()
        last_user_msg = None
        for m in reversed(self.messages):
            if m.get("role") == "user":
                last_user_msg = dict(m)
                break
        prompt_text = last_user_msg.get("content", "") if last_user_msg else "New session"
        self.session = Session.create(
            model=self.config.model,
            workspace=self.config.workspace or ".",
            first_prompt=prompt_text,
        )
        self.messages = [last_user_msg] if last_user_msg else []
        self._invalidate_token_cache()

    # ── System prompt ────────────────────────────────────────────────

    def _build_system_prompt(self, skill_name: Optional[str] = None, workspace: Optional[str] = None, query: Optional[str] = None) -> str:
        """Build the system prompt for the current turn.

        Delegates assembly to ContextAssembler after gathering context.
        """
        ws = workspace or self.config.workspace or "."
        effective_skill = skill_name or self._active_skill

        # ── Auto-detect skill from user query ──
        auto_detected_skill_name: Optional[str] = None
        if not effective_skill and query:
            from wisp.skills import match_skills
            matched = match_skills(query, ws, min_score=1.5)
            if matched:
                top_skill, score = matched[0]
                effective_skill = top_skill.name
                auto_detected_skill_name = top_skill.name
                logger.info(
                    "Auto-detected skill '%s' (score=%.1f) for query: %s",
                    effective_skill, score, query[:80],
                )

        # Split cache: static parts (workspace, skills, project context,
        # code index, memory, git, base repo map) are keyed by
        # (effective_skill, ws) and shared across turns.  Query-specific
        # additions (relevant files, dependents) are computed fresh each
        # turn and appended to the static base.
        static_key = (effective_skill, ws)
        if not hasattr(self, "_static_system_prompt_cache"):
            self._static_system_prompt_cache = {}
        static_prompt = self._static_system_prompt_cache.get(static_key)

        # Query-specific repo_map additions are cached separately so that
        # repeated identical queries are instant, but different queries
        # don't blow the static cache.
        query_key = (ws, query) if query else (ws, None)
        if not hasattr(self, "_query_repo_map_cache"):
            self._query_repo_map_cache = {}

        ws_abs = Path(ws).resolve()

        # ── Gather context sections ──────────────────────────────
        skills = discover_skills(ws)
        skills_block = self._build_skills_block_from_skills(skills)

        project_ctx = detect_project_context(ws)
        project_context = format_context(project_ctx)

        if not hasattr(self, "_code_index_cache"):
            self._code_index_cache = {}
        if ws not in self._code_index_cache:
            if is_tree_sitter_available():
                self._code_index_cache[ws] = build_ts_index(ws)
            else:
                self._code_index_cache[ws] = build_regex_index(ws)
        code_index = self._code_index_cache[ws]
        self._code_index = code_index
        code_index_summary = format_index_summary(code_index)

        memory_block = format_memory_block(ws)

        recent_summaries = None
        if hasattr(self, "_recent_summaries") and self._recent_summaries:
            from wisp.agent_memory import get_agent_memory
            last_msgs = []
            if self.session and self.session.messages:
                last_msgs = self.session.messages[-8:]
            recent_summaries = get_agent_memory().format_for_prompt(
                self._recent_summaries, last_messages=last_msgs
            )

        from wisp.git_context import format_git_context
        git_context = format_git_context(ws)

        from wisp.planner import PlanStore
        active_plan = PlanStore().load_active(ws)
        active_plan_str = active_plan.format_for_prompt() if active_plan else None

        # ── RepoMap: split static base from query-specific additions ──
        repo_map = None
        query_repo_map = ""
        try:
            from wisp.repo_map import RepoMap
            if not hasattr(self, "_repo_map_instances"):
                self._repo_map_instances = {}
            if ws not in self._repo_map_instances:
                self._repo_map_instances[ws] = RepoMap(ws_abs)
            rm = self._repo_map_instances[ws]
            entries = rm.build(use_cache=True, fast_mode=False)
            if entries:
                # Static base map (no query-specific additions) — cached by ws
                if not hasattr(self, "_repo_map_base_cache"):
                    self._repo_map_base_cache = {}
                base_map = self._repo_map_base_cache.get(ws)
                if base_map is None:
                    map_text = rm.format_for_llm(max_tokens=1200)
                    base_map = f"## Codebase Map\n{map_text}\n"
                    self._repo_map_base_cache[ws] = base_map
                repo_map = base_map

                # Query-specific additions — cached by (ws, query)
                if query:
                    query_repo_map = self._query_repo_map_cache.get(query_key, "")
                    if query_repo_map == "":
                        parts: list[str] = []
                        relevant = rm.get_relevant_files(query, top_k=5)
                        if relevant:
                            parts.append("\n## Files Relevant to Query\n")
                            for f in relevant:
                                parts.append(f"- {f}\n")
                        deps_extra = []
                        for f in relevant:
                            deps = rm.get_dependents(f)[:3]
                            if deps:
                                deps_extra.extend(deps)
                        if deps_extra:
                            parts.append("\n## Dependents of Relevant Files\n")
                            for d in sorted(set(deps_extra))[:5]:
                                parts.append(f"- {d}\n")
                        query_repo_map = "".join(parts)
                        self._query_repo_map_cache[query_key] = query_repo_map
        except ImportError:
            pass
        except Exception as e:
            logger.warning("Failed to build repo map: %s", e)

        context_files = None
        if hasattr(self.config, 'load_context_files'):
            try:
                context_files = self.config.load_context_files()
            except Exception as e:
                logger.warning("Failed to load context files: %s", e)

        active_skill_trio = None
        if effective_skill:
            skill = next((s for s in skills if s.name == effective_skill), None)
            if skill:
                auto_label = " (auto-detected)" if auto_detected_skill_name == skill.name else ""
                active_skill_trio = (
                    f"{skill.name}{auto_label}",
                    skill.description,
                    skill.instructions,
                )
            else:
                logger.warning("Skill '%s' not found in discovered skills", effective_skill)

        # ── Delegate assembly ────────────────────────────────────
        # Cap the system prompt at ~40% of the context window so conversation
        # history still has room (trim_conversation handles message trimming).
        # Guard against MagicMock / None in tests — coerce to int safely.
        try:
            _ctx_tokens = int(self.config.max_context_tokens)
        except (TypeError, ValueError):
            _ctx_tokens = 8192  # sensible default when config is mocked
        sys_budget = max(2000, _ctx_tokens // 3)

        if static_prompt is None:
            static_prompt = self.context_assembler.build(
                workspace=ws,
                default_system=DEFAULT_SYSTEM,
                role_extra=getattr(self, "_role_system_extra", None) or None,
                skills_block=skills_block or None,
                project_context=project_context or None,
                code_index_summary=code_index_summary or None,
                memory_block=memory_block or None,
                recent_summaries=recent_summaries or None,
                git_context=git_context or None,
                active_plan=active_plan_str or None,
                plan_mode=getattr(self.config, "plan_mode", False),
                plan_context=getattr(self.config, "plan_context", None) or None,
                repo_map=repo_map or None,
                context_files=context_files or None,
                mandatory_skill=active_skill_trio,
                max_tokens=sys_budget,
            )
            self._static_system_prompt_cache[static_key] = static_prompt

        # Append query-specific repo_map additions (if any) to the cached
        # static prompt.  This is cheap — just string concatenation.
        system = static_prompt
        if query_repo_map:
            system = static_prompt + query_repo_map

        # Log if truncation actually happened (skip when config is mocked in tests).
        if not isinstance(self.config.max_context_tokens, type(lambda: None)):
            try:
                final_est = self._estimate_tokens([{"content": system}])
                if sys_budget and final_est > sys_budget * 0.9:
                    logger.warning(
                        "System prompt is %d/%d tokens after truncation. "
                        "Consider reducing context sections.",
                        final_est, sys_budget,
                    )
            except (TypeError, ValueError):
                pass  # config was mocked — skip token estimation in tests

        return system

    def _build_skills_block_from_skills(self, skills: list) -> str:
        if not skills:
            return ""
        lines = [
            "",
            "## Available Skills",
            "To activate a skill: mention its name or any trigger phrase in your message.",
        ]
        for s in skills:
            triggers = ", ".join(s.triggers[:4]) if hasattr(s, "triggers") and s.triggers else ""
            if triggers:
                lines.append(f"- {s.name} [{triggers}] - {s.description}")
            else:
                lines.append(f"- {s.name} - {s.description}")
        lines.append("")
        lines.append("To manually activate: use /skill <name>")
        return "\n".join(lines)

    def _invalidate_system_prompt_cache(self):
        if hasattr(self, "_static_system_prompt_cache"):
            self._static_system_prompt_cache.clear()
        if hasattr(self, "_query_repo_map_cache"):
            self._query_repo_map_cache.clear()
        if hasattr(self, "_repo_map_base_cache"):
            self._repo_map_base_cache.clear()
        if hasattr(self, "_code_index_cache"):
            self._code_index_cache.clear()
        # Do NOT clear _repo_map_instances here.  The RepoMap object holds
        # the parsed entries in memory; clearing it would force a full
        # _do_build() on the next turn.  The RepoMap.build(use_cache=True)
        # call checks disk cache / mtimes for staleness, so stale data
        # is harmless.  We only clear instances when the workspace changes.

    # ── Tool schemas ─────────────────────────────────────────────────

    def _get_tool_schemas(self) -> list[dict]:
        if not self._mcp_initialized:
            try:
                self.mcp.initialize()
            except Exception as e:
                logger.warning("MCP initialization failed: %s", e)
            self._mcp_initialized = True

        schemas = list(TOOL_SCHEMAS)
        # Plugin tools (registered at runtime via register_tool())
        try:
            from wisp.plugin_registry import get_plugin_schemas
            schemas.extend(get_plugin_schemas())
        except Exception:
            pass
        try:
            mcp_schemas = self.mcp.get_tool_schemas()
            schemas.extend(mcp_schemas)
        except Exception as e:
            logger.warning("Failed to get MCP tool schemas: %s", e)

        # Deduplicate by tool name — built-ins have absolute precedence.
        # Plugin/MCP schemas that share a name with built-ins are silently
        # dropped so the LLM never sees duplicate tool definitions.
        seen: set[str] = set()
        deduped: list[dict] = []
        for s in schemas:
            name = s.get("function", {}).get("name")
            if name and name not in seen:
                seen.add(name)
                deduped.append(s)
        schemas = deduped

        if hasattr(self, "_allowed_tools") and self._allowed_tools is not None:
            allowed = self._allowed_tools
            schemas = [
                s for s in schemas
                if s.get("function", {}).get("name") in allowed
            ]

        return schemas

    def _is_mcp_tool(self, name: str) -> bool:
        if not self._mcp_initialized:
            return False
        for tool in self.mcp.get_all_tools():
            if tool.name == name:
                return True
        return False

    # ── Compaction ───────────────────────────────────────────────────

    def _maybe_compact_session(self) -> Optional[AgentEvent]:
        if not self.config.auto_compact:
            return None
        if not self.session:
            return None

        # Mid-turn guard
        if self.messages:
            last = self.messages[-1]
            last_role = last.get("role")
            if last_role == "tool":
                return system_event("Tool round in progress; delaying compaction.", "debug")
            if last_role == "assistant" and last.get("tool_calls"):
                return system_event("Assistant emitted tool calls; delaying compaction.", "debug")
            if last_role == "assistant":
                content = last.get("content", "") or ""
                if content and not re.search(r"[.!?```}\])]$", content[-5:]):
                    return system_event("Last assistant turn looks incomplete; delaying compaction.", "debug")

        system = self._build_system_prompt()
        overhead = self._estimate_tokens([{"content": system}])
        msg_tokens = self._estimate_tokens(self.messages)
        budget = self.config.max_context_tokens
        token_pct = (msg_tokens + overhead) / budget * 100 if budget else 0

        if token_pct < self.config.compact_threshold_tokens:
            return None

        compaction_count = len(self.session.compaction_history)
        if compaction_count >= 1:
            # Transport layer should warn user; core just emits event
            pass

        result = self.session.compact(
            keep_recent=self.config.compact_keep_recent,
            chars_per_token=self.config.chars_per_token,
            max_context_tokens=self.config.max_context_tokens,
            client=getattr(self, "client", None),
        )

        if result.get("compacted"):
            self.messages = list(self.session.messages)
            return system_event(
                f"Compacted: {result['before_count']} → {result['after_count']} messages. "
                f"Summary: {result.get('summary', '')[:120]}...",
                "info",
            )
        return None

    # ── Turn execution ───────────────────────────────────────────────

    async def _arun(
        self,
        prompt: str,
        system: Optional[str] = None,
        approval_handler: Optional[ApprovalHandler] = None,
        images: Optional[list[str]] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Execute one user turn and yield all events (internal async implementation)."""
        self._last_user_prompt = prompt  # For ontology skill matching
        content = self._expand_continuation(prompt)
        if images:
            from wisp.core.message_format import merge_content
            content = merge_content(content, images)
        self._add_message("user", content)
        if system is None:
            system = self._build_system_prompt(query=prompt)

        # Session bookkeeping
        if self.session is None:
            self.session = Session.create(
                model=self.config.model,
                workspace=self.config.workspace or ".",
                first_prompt=prompt,
            )

        # Auto-compact FIRST (semantic compression) before hard trim
        # Offload to a thread so the event loop stays responsive.  The
        # synchronous compressor may call the LLM for Tier-3 abstractive
        # summary; we must not block the event loop while waiting.
        compact_event = await asyncio.to_thread(self._maybe_compact_session)
        if compact_event:
            yield compact_event

        # Hard trim only if compaction wasn't sufficient
        self._trim_context_if_needed(system)

        # Steering checkpoint 1: after compact, before iteration loop
        inject = await self._check_steering()
        if inject is not None:
            yield inject

        completion_reason = ""
        last_tool_signature: Optional[str] = None
        reflection_count = 0
        max_reflections = getattr(self.config, "max_reflections", 0)
        for iteration in range(1, self.max_iterations + 1):
            if self._interrupted:
                completion_reason = "interrupted"
                break

            # Steering checkpoint 2: start of each iteration
            inject = await self._check_steering()
            if inject is not None:
                yield inject

            # Forward streaming token events — run the synchronous generator
            # in a background thread so that the asyncio event loop stays
            # free for concurrent WebSocket clients, approvals, and interrupts.
            from wisp.async_utils import sync_gen_iter  # local import avoids top-level cycle
            streamed_content = False
            async for event in sync_gen_iter(lambda: self._run_turn_streaming_events(system)):
                if self._interrupted:
                    completion_reason = "interrupted"
                    break
                # Steering checkpoint 4: during token streaming
                inject = await self._check_steering()
                if inject is not None:
                    yield inject
                yield event
                if event.type == TYPE_CONTENT:
                    streamed_content = True

            response = getattr(self.client, "stream_response", None) or {}
            if not response:
                yield error_event("No response from model", recoverable=False)
                completion_reason = "error"
                break

            # Surface stream errors with actual details instead of vague "No response"
            if response.get("_stream_error"):
                error_type = response.get("_error_type", "Unknown")
                error_msg = response.get("_error_message", "Stream error")
                partial = response.get("_partial_content", "") or ""
                # Q15: Discard partial tool-calling streams that would corrupt
                # the conversation with malformed JSON/arguments.
                if partial and self._is_partial_tool_call(partial):
                    partial = ""
                    error_msg = (
                        f"{error_msg}\n\n"
                        "[Partial tool call discarded — stream cut off "
                        "before arguments were complete]"
                    )
                # Add the assistant's partial content to the conversation so
                # it remains valid (user message must be followed by assistant).
                msg = response.get("message", {})
                thinking = (msg.get("thinking", "")) if isinstance(msg, dict) else ""
                self._add_message("assistant", partial or "", thinking)
                self._save_session()
                if partial:
                    error_msg = f"{error_msg}\n\nPartial output:\n{partial[:500]}"
                yield error_event(
                    f"⏸  Stream error ({error_type}): {error_msg}",
                    recoverable=False
                )
                completion_reason = "error"
                break

            if not isinstance(response, dict):
                yield error_event(f"Unexpected response type: {type(response).__name__}", recoverable=False)
                completion_reason = "error"
                break

            msg = response.get("message", {})
            content = msg.get("content", "") or "" if isinstance(msg, dict) else ""
            thinking_text = msg.get("thinking", "") or "" if isinstance(msg, dict) else ""
            tool_calls = self._parse_tool_call(response)

            if not tool_calls:
                self._add_message("assistant", content or "", thinking_text)
                self._save_session()
                if not streamed_content:
                    yield content_event(content)
                yield done_event(
                    session_id=self.session.id if self.session else "",
                    turns=iteration,
                    reason="natural",
                )
                # natural completion — no post-loop warning needed
                completion_reason = ""
                break

            self._add_message("assistant", content or "", thinking_text)
            if tool_calls:
                self.messages[-1]["tool_calls"] = tool_calls

                # Check for reflective loops (same tool call pattern repeated)
                if max_reflections > 0:
                    parts = []
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        name = func.get("name", "")
                        args = func.get("arguments", {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                args = {}
                        parts.append((name, json.dumps(args, sort_keys=True) if isinstance(args, dict) else str(args)))
                    current_sig = json.dumps(parts, sort_keys=False)
                    if current_sig == last_tool_signature:
                        reflection_count += 1
                        if reflection_count >= max_reflections:
                            completion_reason = "max_reflections"
                            break
                    else:
                        reflection_count = 0
                        last_tool_signature = current_sig

            for tc in tool_calls:
                func = tc.get("function", {})
                if isinstance(func, dict):
                    yield tool_call_event(func.get("name", ""), func.get("arguments", {}))

            async for event in WispAgentCore._run_tool_calls(
                self, tool_calls, self.config.workspace or ".", approval_handler=approval_handler
            ):
                yield event

        else:
            # Hit max_iterations without a final answer
            completion_reason = "max_iterations"

        if completion_reason:
            yield done_event(
                session_id=self.session.id if self.session else "",
                turns=iteration,
                reason=completion_reason,
            )
        if completion_reason == "max_iterations":
            self._save_session()

    async def run(
        self,
        prompt: str,
        approval_handler: Optional[ApprovalHandler] = None,
        images: Optional[list[str]] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Execute one user turn and yield all events.

        This is the primary SDK entry point. Transports consume the yielded
        events and decide how to present them (print, WebSocket, etc.).
        """
        # ── Session start hooks ──
        if self.hook_manager:
            try:
                from wisp.hooks import HookEvent
                await self.hook_manager.run_hooks(HookEvent.SESSION_START, {
                    "prompt": prompt,
                    "workspace": self.config.workspace,
                    "session_id": self.session.id if self.session else "",
                    "agent_id": self.agent_id,
                })
            except Exception as e:
                logger.warning("Session start hook failed: %s", e)

        try:
            async for event in self._arun(prompt, approval_handler=approval_handler, images=images):
                yield event
        finally:
            # ── Session end hooks ──
            if self.hook_manager:
                try:
                    from wisp.hooks import HookEvent
                    await self.hook_manager.run_hooks(HookEvent.SESSION_END, {
                        "workspace": self.config.workspace,
                        "session_id": self.session.id if self.session else "",
                        "agent_id": self.agent_id,
                    })
                except Exception as e:
                    logger.warning("Session end hook failed: %s", e)

    def _run_turn_streaming_events(self, system: str):
        """Yield thinking/content AgentEvent deltas in real-time.

        After the generator finishes, the response dict is available at
        self.client.stream_response.
        """
        self._trim_context_if_needed(system)
        _in_thinking = False
        # Accumulate locally so that an interrupt still produces a partial
        # response rather than leaving stream_response as stale data.
        acc_content: list[str] = []
        acc_thinking: list[str] = []
        # Reset side-channel state at the start of every turn so that a
        # prior turn's result is never accidentally reused.
        self.client.stream_response = None

        try:
            for event in self.client.generate_stream_events(
                system_prompt=system,
                messages=self.messages,
                tools=self._get_tool_schemas(),
                checkpoint_every=50,
            ):
                if self._interrupted:
                    if _in_thinking:
                        _in_thinking = False
                    # Interrupted before completion — still produce the
                    # partial response so that turn 1 isn't invisible.
                    partial = "".join(acc_content)
                    self.client.stream_response = {
                        "message": {
                            "role": "assistant",
                            "content": partial,
                            "thinking": "".join(acc_thinking),
                        },
                        "_interrupted": True,
                    }
                    return

                if isinstance(event, TokenBatch):
                    if event.phase == "thinking":
                        if not _in_thinking:
                            _in_thinking = True
                        acc_thinking.append(event.text)
                        yield thinking(event.text)
                    else:
                        if _in_thinking:
                            _in_thinking = False
                        acc_content.append(event.text)
                        yield content_event(event.text)

                elif isinstance(event, ToolCallBatch):
                    if _in_thinking:
                        _in_thinking = False

                elif isinstance(event, StreamComplete):
                    if _in_thinking:
                        _in_thinking = False
                    break

                elif isinstance(event, StreamError):
                    if _in_thinking:
                        _in_thinking = False
                    # Don't throw away accumulated content — record it so
                    # the conversation state stays valid and the user can
                    # see what the model produced before the stream failed.
                    partial = event.partial_content or "".join(acc_content)
                    partial_thinking = event.partial_thinking or "".join(acc_thinking)
                    self.client.stream_response = {
                        "message": {
                            "role": "assistant",
                            "content": partial,
                            "thinking": partial_thinking,
                        },
                        "_stream_error": True,
                        "_error_type": event.error_type,
                        "_error_message": event.message,
                        "_partial_content": partial,
                    }
                    return

        except OllamaError as e:
            logger.error("Ollama error: %s", e)
            self.client.stream_response = {
                "message": {"role": "assistant", "content": "", "thinking": ""},
                "_stream_error": True,
                "_error_type": "OllamaError",
                "_error_message": str(e),
            }
        except Exception as e:
            logger.error("Unexpected error in streaming turn: %s", e, exc_info=True)
            self.client.stream_response = {
                "message": {"role": "assistant", "content": "", "thinking": ""},
                "_stream_error": True,
                "_error_type": type(e).__name__,
                "_error_message": str(e),
            }

    def _run_turn_streaming(self, system: str) -> dict:
        """Backward compat: accumulate silently and return response dict."""
        for _ in self._run_turn_streaming_events(system):
            pass
        return getattr(self.client, "stream_response", None) or {}

    @staticmethod
    def _parse_tool_call(response: dict) -> Optional[list[dict]]:
        msg = response.get("message", {})
        if not isinstance(msg, dict):
            return None
        tool_calls = msg.get("tool_calls")
        if tool_calls and isinstance(tool_calls, list):
            return tool_calls
        return None

    @staticmethod
    def _is_partial_tool_call(partial: str) -> bool:
        """Detect whether a truncated stream contains a tool-calling payload.

        When a stream fails mid-tool-call, the partial content starts with
        JSON-like fragments that the next turn would try (and fail) to parse.
        Discarding these prevents conversation corruption.

        Heuristics (fast, no regex):
          - Starts with ``{"name"
           - Starts with ``[
           - Starts with ``<
           - Starts with ``"name"`` or ``"function"`` — bare JSON fragment
        """
        if not partial:
            return False
        stripped = partial.lstrip()
        if stripped.startswith("{") and '"name"' in stripped:
            return True
        if stripped.startswith("<"):
            return True
        if stripped.startswith("["):
            return True
        if stripped.startswith('"name"') or stripped.startswith('"function"'):
            return True
        return False

    async def _run_tool_calls(
        self,
        tool_calls: list,
        workspace: str,
        approval_handler: Optional[ApprovalHandler] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Execute tool calls and yield result events.

        Delegates to ToolExecutor for guards, hooks, and execution.
        Handles message appending and cache invalidation here.
        """
        for tc in tool_calls:
            if self._interrupted:
                break

            # Steering checkpoint 3: before each tool execution
            inject = await self._check_steering()
            if inject is not None:
                yield inject

            func = tc.get("function", {})
            if not isinstance(func, dict):
                logger.warning("Malformed tool call: %s", tc)
                continue

            func_name = func.get("name", "")
            func_args = func.get("arguments", {})

            if not func_name:
                continue

            if isinstance(func_args, str):
                try:
                    func_args = json.loads(func_args)
                except json.JSONDecodeError:
                    func_args = {}
            if not isinstance(func_args, dict):
                func_args = {}

            # Delegate to ToolExecutor
            # Must be initialized in __init__; tests that need to bypass should
            # construct ToolExecutor directly and delegate to it.
            assert hasattr(self, "tool_executor"), (
                "tool_executor must be initialized in WispAgentCore.__init__. "
                "Do not bypass __init__ or call _run_tool_calls directly from tests."
            )

            # Intercept spawn_subagent — agent core has the real implementation
            if func_name == "spawn_subagent":
                start = time.monotonic()
                result = await self._spawn_subagent(func_args, workspace)
                duration_ms = (time.monotonic() - start) * 1000
                yield tool_result_event(func_name, result, duration_ms=duration_ms)
                msg = {
                    "role": "tool",
                    "content": str(result),
                    "name": func_name,
                }
                if tc.get("id") is not None:
                    msg["tool_call_id"] = tc.get("id")
                self.messages.append(msg)
                self._invalidate_token_cache()
                continue

            # Run tool execution and capture the result for the conversation
            tool_result: str | dict = ""
            async for event in self.tool_executor.execute(
                tool_name=func_name,
                tool_args=func_args,
                workspace=workspace,
                tool_call_id=tc.get("id"),
                approval_handler=approval_handler,
            ):
                yield event
                # Capture the result from the tool_result event
                if isinstance(event, AgentEvent) and str(event.type) == "tool_result":
                    tool_result = event.data.get("result", "")

            # Build and append the tool message for the conversation,
            # reusing the captured result so tools that mutate the workspace
            # are not executed a second time.
            msg = await self.tool_executor.build_tool_message(
                tool_name=func_name,
                tool_args=func_args,
                workspace=workspace,
                tool_call_id=tc.get("id"),
                result=tool_result,
            )
            self.messages.append(msg)
            self._invalidate_token_cache()

            if func_name == "remember":
                self._invalidate_system_prompt_cache()

    async def _spawn_subagent(self, args: dict, workspace: str) -> str:
        """Spawn a single subagent and return its output.

        Thin delegate to SubagentOrchestrator.spawn_with_guards().
        All guard logic (depth, cache, retry, adaptive timeout) lives
        in the orchestrator.
        """
        return await self._orchestrator.spawn_with_guards(
            task=args.get("task", ""),
            tools=args.get("tools", ["all"]),
            max_iterations=int(args.get("max_iterations", 30)),
            timeout_seconds=float(args.get("timeout_seconds", 300)),
            output_format=args.get("output_format", "text"),
            worktree_isolated=args.get("worktree_isolated", False),
            max_tokens=args.get("max_tokens"),
            output_schema=args.get("output_schema"),
            auto_retry=args.get("auto_retry", True),
            workspace=workspace,
            auto_approve=self.config.auto_approve,
            depth=getattr(self, "_subagent_depth", 0),
            branch_count=getattr(self, "_subagent_branch_count", 0),
        )

    # ── Parallel subagents ───────────────────────────────────────────

    async def spawn_subagents(self, specs: list) -> list:
        """Spawn parallel subagents for independent tasks.

        Optimized batching: applies adaptive timeout and local model
        fallback to each contract, then runs all in parallel.

        Args:
            specs: A list of SubagentContract objects or dicts describing each subagent's task.

        Returns:
            A list of SubagentResult objects, one per spec.
        """
        return await self._orchestrator.spawn_parallel_with_guards(
            specs,
            depth=getattr(self, "_subagent_depth", 0),
            branch_count=getattr(self, "_subagent_branch_count", 0),
        )

    # ── Auto parallel research ───────────────────────────────────────

    # ── Non-interactive task runner ──────────────────────────────────

    async def run_task(
        self,
        task_description: str,
        workspace: str = ".",
        max_iterations: int = 30,
        timeout_seconds: float = 300.0,
        system_prompt: Optional[str] = None,
    ) -> dict:
        """Run a full agent loop for a single task, non-interactively.

        Returns a dict with ``success`` (bool) and ``output`` (str) keys.
        """
        self._add_message("user", task_description)
        if system_prompt is not None:
            system = system_prompt
        else:
            system = self._build_system_prompt(workspace=workspace)
        self._trim_context_if_needed(system)

        start = time.monotonic()
        iteration = 0
        final_content = ""

        while iteration < max_iterations:
            if time.monotonic() - start > timeout_seconds:
                return {"success": False, "output": f"[Task timed out after {timeout_seconds:.0f}s]"}

            try:
                # Offload sync streaming to thread pool to avoid blocking
                # the event loop during long model calls
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None, self._run_turn_streaming, system
                )
            except Exception as e:
                return {"success": False, "output": f"[Error during task execution: {e}]"}

            if not response:
                return {"success": False, "output": "[No response from model]"}

            # Detect stream failures that were swallowed by the generator
            if response.get("_stream_error"):
                err_type = response.get("_error_type", "unknown")
                err_msg = response.get("_error_message", "unknown error")
                return {"success": False, "output": f"[Model stream error ({err_type}): {err_msg}]"}

            msg = response.get("message", {})
            content = msg.get("content", "") or "" if isinstance(msg, dict) else ""
            thinking = msg.get("thinking", "") or "" if isinstance(msg, dict) else ""
            tool_calls = self._parse_tool_call(response)

            self._add_message("assistant", content or "", thinking)
            if tool_calls:
                self.messages[-1]["tool_calls"] = tool_calls
                async for event in self._run_tool_calls(tool_calls, workspace):
                    logger.debug("Swarm agent tool event: %s", event.event_type if hasattr(event, "event_type") else event)
                iteration += 1
                continue

            final_content = content
            break
        else:
            final_content = f"[Task reached max iterations ({max_iterations}) without completion]"
            return {"success": False, "output": final_content}

        return {"success": True, "output": final_content}

