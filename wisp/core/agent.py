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
from wisp.session import Session, SessionManager
from wisp.project_context import detect_project_context, format_context
from wisp.code_index import build_index as build_regex_index, format_index_summary
from wisp.tree_sitter_index import build_index as build_ts_index, is_tree_sitter_available
from wisp.mcp import MCPManager
from wisp.memory import format_memory_block
from wisp.core.message_format import extract_text
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
        msg = getattr(r, 'message', '') or str(r)
        if msg:
            msgs.append(msg)
    return "; ".join(msgs)


def _get_modified_args(hook_results: list) -> Optional[dict]:
    """Return modified tool args from hook results, if any hook modified them."""
    for r in (hook_results or []):
        modified = getattr(r, 'modified_args', None)
        if modified is not None:
            return modified
    return None


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
        if not self.config._context_tokens_explicit:
            try:
                detected = self.client.get_context_length()
                if detected != self.config.max_context_tokens:
                    logger.info(
                        "Auto-detected context window for %s: %d tokens",
                        self.config.model, detected,
                    )
                    self.config.max_context_tokens = detected
            except OllamaError:
                pass
        self.session_mgr = SessionManager()
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
        self.mcp = MCPManager(self.config.workspace or ".")
        self._mcp_initialized = False
        from wisp.lsp.manager import LSPManager
        self.lsp = LSPManager(self.config.workspace or ".")
        from wisp.agent_memory import AgentMemory
        self.agent_memory = AgentMemory()
        self._recent_summaries = self.agent_memory.load_recent(
            workspace=self.config.workspace or ".",
            limit=7,
        )
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
        return total // self.config.chars_per_token

    def _trim_context_if_needed(self, system_prompt: str = ""):
        budget = self.config.max_context_tokens
        overhead = self._estimate_tokens([{"content": system_prompt}])
        user_count = sum(1 for m in self.messages if m.get("role") == "user")
        while user_count > 1 and self._estimate_tokens(self.messages) + overhead > budget:
            _remove_oldest_turn(self.messages)
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
        loaded = self.session_mgr.load(session_id)
        if loaded is not None:
            return loaded
        resolved = self.session_mgr.get_session_id_from_fragment(session_id)
        if resolved:
            return self.session_mgr.load(resolved)
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

    # ── System prompt ────────────────────────────────────────────────

    def _build_system_prompt(self, skill_name: Optional[str] = None, workspace: Optional[str] = None, query: Optional[str] = None) -> str:
        ws = workspace or self.config.workspace or "."
        effective_skill = skill_name or self._active_skill
        cache_key = (effective_skill, ws, query)
        if not hasattr(self, "_system_prompt_cache"):
            self._system_prompt_cache = {}
        cached = self._system_prompt_cache.get(cache_key)
        if cached is not None:
            return cached

        ws_abs = Path(ws).resolve()
        system = DEFAULT_SYSTEM
        system += f"\n\n## Workspace\nYou are working in: {ws_abs}"
        if hasattr(self, "_role_system_extra") and self._role_system_extra:
            system += f"\n\n{self._role_system_extra}"

        skills = discover_skills(ws)
        system += self._build_skills_block_from_skills(skills)

        # ── OntoSkills: inject deterministic skill context ──
        from wisp.skills import has_ontology, match_skill_via_ontology
        if has_ontology():
            # Try user prompt first (most relevant), then workspace name
            ontology_result = None
            if hasattr(self, "_last_user_prompt") and self._last_user_prompt:
                ontology_result = match_skill_via_ontology(self._last_user_prompt)
            if ontology_result is None:
                ontology_result = match_skill_via_ontology(str(ws_abs))
            if ontology_result:
                system += f"\n\n## {ontology_result['name']}\n{ontology_result['context']}"

        project_ctx = detect_project_context(ws)
        ctx_block = format_context(project_ctx)
        if ctx_block:
            system += f"\n\n{ctx_block}"

        if not hasattr(self, "_code_index_cache"):
            self._code_index_cache = {}
        if ws not in self._code_index_cache:
            if is_tree_sitter_available():
                self._code_index_cache[ws] = build_ts_index(ws)
            else:
                self._code_index_cache[ws] = build_regex_index(ws)
        code_index = self._code_index_cache[ws]
        index_summary = format_index_summary(code_index)
        if index_summary:
            system += f"\n\n{index_summary}"
        self._code_index = code_index

        memory_block = format_memory_block(ws)
        if memory_block:
            system += f"\n\n{memory_block}"

        if hasattr(self, "_recent_summaries") and self._recent_summaries:
            from wisp.agent_memory import AgentMemory
            last_msgs = []
            if self.session and self.session.messages:
                # Include up to 8 most recent messages from current session for continuity
                last_msgs = self.session.messages[-8:]
            summary_block = AgentMemory().format_for_prompt(
                self._recent_summaries, last_messages=last_msgs
            )
            if summary_block:
                system += f"\n\n{summary_block}"

        from wisp.git_context import format_git_context
        git_block = format_git_context(ws)
        if git_block:
            system += f"\n\n{git_block}"

        from wisp.planner import PlanStore
        plan_store = PlanStore()
        active_plan = plan_store.load_active(ws)
        if active_plan:
            system += f"\n\n{active_plan.format_for_prompt()}"

        if effective_skill:
            skill = next((s for s in skills if s.name == effective_skill), None)
            if skill:
                system += f"\n\n## Active Skill: {skill.name}\n{skill.description}\n\n{skill.instructions}"
            else:
                logger.warning("Skill '%s' not found in discovered skills", effective_skill)

        if self.config.plan_mode:
            system += (
                "\n\n## PLAN MODE ACTIVE\n"
                "You are in plan mode. Your job is to produce a detailed implementation plan.\n"
                "- Use read-only tools (read_file, list_files, search_symbols, lsp_*) to understand the codebase.\n"
                "- Do NOT modify any files, run bash commands, or make git changes.\n"
                "- Output a structured plan in markdown with: summary, files to touch, step-by-step approach, edge cases.\n"
                "- End with '## Plan Complete' when finished."
            )

        if self.config.plan_context:
            system += f"\n\n## Approved Plan\n{self.config.plan_context}\n\nFollow the approved plan above. Execute each step."

        # ── Repo Map: inject structural overview of the codebase ──
        try:
            from wisp.repo_map import RepoMap
            rm = RepoMap(ws_abs)
            # Build full map (cached).  Skeleton caches are auto-upgraded.
            entries = rm.build(use_cache=True, fast_mode=False)
            if entries:
                map_text = rm.format_for_llm(max_tokens=1200)
                system += f"\n\n## Codebase Map\n{map_text}\n"
                # Inject files relevant to the user's query for dynamic context
                if query:
                    relevant = rm.get_relevant_files(query, top_k=5)
                    if relevant:
                        system += "\n## Files Relevant to Query\n"
                        for f in relevant:
                            system += f"- {f}\n"
                        deps_extra = []
                        for f in relevant:
                            deps = rm.get_dependents(f)[:3]
                            if deps:
                                deps_extra.extend(deps)
                        if deps_extra:
                            system += "\n## Dependents of Relevant Files\n"
                            for d in sorted(set(deps_extra))[:5]:
                                system += f"- {d}\n"
        except ImportError:
            pass
        except Exception as e:
            logger.warning("Failed to build repo map: %s", e)

        # ── Context files (CLAUDE.md, AGENTS.md, etc.) ──
        if hasattr(self.config, 'load_context_files'):
            try:
                context = self.config.load_context_files()
                if context:
                    system = context + "\n\n" + system
            except Exception as e:
                logger.warning("Failed to load context files: %s", e)

        self._system_prompt_cache[cache_key] = system
        return system

    def _build_skills_block_from_skills(self, skills: list) -> str:
        if not skills:
            return ""
        lines = ["\n## Available Skills", "You can invoke any of these skills when relevant:"]
        for s in skills:
            lines.append(f"- {s.name}: {s.description}")
        lines.append("To invoke a skill, mention its name and follow its instructions.")
        return "\n".join(lines)

    def _invalidate_system_prompt_cache(self):
        if hasattr(self, "_system_prompt_cache"):
            self._system_prompt_cache.clear()
        if hasattr(self, "_code_index_cache"):
            self._code_index_cache.clear()

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
        compact_event = self._maybe_compact_session()
        if compact_event:
            yield compact_event

        # Hard trim only if compaction wasn't sufficient
        self._trim_context_if_needed(system)

        # ── Auto parallel research for complex queries ─────────────────
        research_results = await self._auto_parallel_research(prompt)
        if research_results:
            for event in research_results:
                yield event

        # ── Auto-delegation for capability mismatch ────────────────────
        if getattr(self.config, "auto_delegate", True):
            delegation = await self._check_delegation(prompt)
            if delegation:
                for event in delegation:
                    yield event

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

            # Forward streaming token events
            streamed_content = False
            for event in self._run_turn_streaming_events(system):
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
                    break

                if isinstance(event, TokenBatch):
                    if event.phase == "thinking":
                        if not _in_thinking:
                            _in_thinking = True
                        yield thinking(event.text)
                    else:
                        if _in_thinking:
                            _in_thinking = False
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
                    partial = event.partial_content or ""
                    partial_thinking = event.partial_thinking or ""
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

    async def _run_tool_calls(
        self,
        tool_calls: list,
        workspace: str,
        approval_handler: Optional[ApprovalHandler] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Execute tool calls and yield result events.

        Dangerous commands yield approval_request events; the transport
        layer must call the returned future to approve/deny.
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

            # ── Pre-tool hooks ──
            if self.hook_manager:
                try:
                    from wisp.hooks import HookEvent
                    context = {
                        "tool_name": func_name,
                        "tool_args": func_args,
                        "workspace": self.config.workspace,
                        "session_id": self.session.id if self.session else "",
                        "cwd": str(Path(self.config.workspace)),
                    }
                    hook_results = await self.hook_manager.run_hooks(HookEvent.PRE_TOOL_USE, context)
                    if _should_block_hook(hook_results):
                        blocked_msg = f"[Blocked by hook: {_collect_hook_messages(hook_results)}]"
                        yield tool_result_event(func_name, blocked_msg)
                        self.messages.append({
                            "role": "tool", "content": blocked_msg, "name": func_name,
                            **({"tool_call_id": tc.get("id")} if tc.get("id") is not None else {}),
                        })
                        continue
                    modified = _get_modified_args(hook_results)
                    if modified is not None:
                        func_args = modified
                except ImportError:
                    pass
                except Exception as e:
                    logger.warning("Pre-tool hook failed for %s: %s", func_name, e)

            # ── Plan mode guard (plan mode blocks all writes) ──
            write_tools = {
                "write_file", "edit_file", "edit_file_multi", "run_bash",
                "git_branch", "git_commit", "git_push", "gh_pr_create",
                "plan_task", "mark_step_done", "update_plan",
            }
            if self.config.plan_mode and func_name in write_tools:
                blocked = f"[Blocked: plan mode — {func_name} requires write access]"
                yield tool_result_event(func_name, blocked)
                self.messages.append({
                    "role": "tool", "content": blocked, "name": func_name,
                    **({"tool_call_id": tc.get("id")} if tc.get("id") is not None else {}),
                })
                continue

            # Dangerous command auto-block (no prompt — just block silently)
            if func_name == "run_bash":
                from wisp.tools import check_dangerous_command
                danger_reason = check_dangerous_command(func_args.get("command", ""))
                if danger_reason:
                    blocked = f"[Blocked: dangerous command — {danger_reason}]"
                    yield tool_result_event(func_name, blocked)
                    self.messages.append({
                        "role": "tool", "content": blocked, "name": func_name,
                        **({"tool_call_id": tc.get("id")} if tc.get("id") is not None else {}),
                    })
                    continue

            # ── Circuit breaker ──
            if hasattr(self, "circuit_breaker"):
                if self.circuit_breaker.is_open(func_name):
                    blocked_msg = (
                        f"[Circuit breaker open for {func_name}: "
                        f"{self.circuit_breaker.status(func_name)}]"
                    )
                    self.metrics.record_tool_block()
                    yield tool_result_event(func_name, blocked_msg)
                    continue

            # ── Approval gating ──
            needs_approval = func_name in {
                "write_file", "edit_file", "edit_file_multi", "run_bash",
                "git_branch", "git_commit", "git_push", "gh_pr_create",
            }
            if needs_approval and approval_handler and not getattr(self.config, "auto_approve", False):
                reason = f"{func_name} modifies workspace state"
                yield approval_request(func_name, func_args, reason)
                approved, modified = await approval_handler(func_name, func_args, reason)
                if modified is not None:
                    func_args = modified
                if not approved:
                    blocked = f"[Blocked: user declined {func_name}]"
                    yield tool_result_event(func_name, blocked)
                    self.messages.append({
                        "role": "tool", "content": blocked, "name": func_name,
                        **({"tool_call_id": tc.get("id")} if tc.get("id") is not None else {}),
                    })
                    continue

            # Execute tool
            start = time.monotonic()
            if func_name == "spawn_subagent":
                try:
                    result = await self._spawn_subagent(func_args, workspace)
                except Exception as e:
                    result = f"Subagent spawn failed: {e}"
                    logger.error("Subagent spawn failed: %s", e, exc_info=True)
            elif self._is_mcp_tool(func_name):
                try:
                    result = self.mcp.call_tool(func_name, func_args)
                    if len(result) > 8000:
                        result = result[:8000] + f"\n... [truncated {len(result)} total chars]"
                except Exception as e:
                    result = f"MCP error: {e}"
            else:
                try:
                    result = execute_tool(func_name, func_args, workspace, max_data_chars=8000, file_lock=self.file_lock, lsp_manager=self.lsp)
                except ToolError as e:
                    result = f"Error: {e}"
                except Exception as e:
                    result = f"Unexpected error: {e}"

            duration_ms = (time.monotonic() - start) * 1000

            if func_name == "remember":
                self._invalidate_system_prompt_cache()

            yield tool_result_event(func_name, result, duration_ms=duration_ms)
            # Extract human-readable summary for the LLM from structured dict results
            if isinstance(result, dict) and "data" in result:
                msg_content = str(result["data"])
            else:
                msg_content = str(result)
            self.messages.append({
                "role": "tool",
                "content": msg_content,
                "name": func_name,
                **({"tool_call_id": tc.get("id")} if tc.get("id") is not None else {}),
            })

            # ── Post-tool hooks ──
            if hasattr(self, "metrics"):
                ok = (isinstance(result, str) and '"status": "ok"' in result) or (isinstance(result, str) and not result.startswith("["))
                self.metrics.record_tool(func_name, duration_ms, success=ok)
                if hasattr(self, "circuit_breaker"):
                    self.circuit_breaker.record(func_name, success=ok)

            if self.hook_manager:
                try:
                    from wisp.hooks import HookEvent
                    post_context = {
                        "tool_name": func_name,
                        "tool_args": func_args,
                        "tool_result": str(result),
                        "duration_ms": duration_ms,
                        "workspace": self.config.workspace,
                        "session_id": self.session.id if self.session else "",
                    }
                    await self.hook_manager.run_hooks(HookEvent.POST_TOOL_USE, post_context)
                except ImportError:
                    pass
                except Exception as e:
                    logger.warning("Post-tool hook failed for %s: %s", func_name, e)

    async def _spawn_subagent(self, args: dict, workspace: str) -> str:
        """Spawn a single subagent and return its output.

        Async-native: delegates directly to SubagentOrchestrator.run()
        without creating threads.  Progress events are streamed back
        via the parent agent's event loop.
        """
        from wisp.multi_agent import SubagentOrchestrator, SubagentContract
        from wisp.multi_agent.task import EventKind, OrchestratorEvent

        depth = getattr(self, "_subagent_depth", 0)
        if depth >= 1:
            return "[Error: subagents cannot spawn subagents (max depth = 1)]"

        # ── Build contract from tool args ──────────────────────────────
        contract = SubagentContract(
            task=args.get("task", ""),
            tools=args.get("tools", ["all"]),
            max_iterations=int(args.get("max_iterations", 15)),
            timeout_seconds=float(args.get("timeout_seconds", 120)),
            output_format=args.get("output_format", "text"),
            workspace=workspace,
            auto_approve=self.config.auto_approve,
            worktree_isolated=args.get("worktree_isolated", False),
            max_tokens=args.get("max_tokens"),
            output_schema=args.get("output_schema"),
        )

        # ── Check cache ────────────────────────────────────────────────
        cache_key = self._subagent_cache_key(contract)
        cache = getattr(self, "_subagent_cache", {})
        cached = cache.get(cache_key)
        if cached:
            age = time.monotonic() - cached["ts"]
            ttl = 300 if contract.output_format == "json" else 60  # 5min for structured, 1min for text
            if age < ttl:
                logger.info("[sub] Cache hit for %s (age=%.0fs)", contract.name, age)
                return cached["output"]

        # ── Local model fallback for simple tasks ────────────────────
        local_model = self._pick_local_model_for_subagent(contract.task)
        if local_model:
            contract.model = local_model
            logger.info("Using local model %s for subagent %s", local_model, contract.name)

        # ── Adaptive timeout based on task complexity + model latency ──
        timeout = self._adaptive_subagent_timeout(
            contract.task, contract.timeout_seconds
        )
        contract.timeout_seconds = timeout

        # ── Progress callback: stream subagent events to parent ──────
        async def _progress(event: OrchestratorEvent) -> None:
            if event.event_type == EventKind.TASK_STARTED:
                logger.info("[sub] %s started", event.task_id)
            elif event.event_type == EventKind.TASK_COMPLETED:
                logger.info("[sub] %s completed", event.task_id)
            elif event.event_type == EventKind.TASK_FAILED:
                logger.warning("[sub] %s failed: %s", event.task_id, event.payload.get("error", ""))

        contract.progress_callback = _progress

        # ── Run subagent with retry ────────────────────────────────────
        orch = SubagentOrchestrator(parent_agent=self)
        max_retries = int(args.get("auto_retry", True)) * 2  # 0 or 2 retries
        last_error = ""
        result = None
        for attempt in range(max_retries + 1):
            try:
                result = await orch.run(contract)
                if result.success:
                    # ── Schema validation ──────────────────────────────
                    if contract.output_schema:
                        from wisp.multi_agent.schema_validator import validate_subagent_output
                        is_valid, validated_data, errors = validate_subagent_output(
                            result.output, contract.output_schema, auto_retry=True
                        )
                        if is_valid and validated_data is not None:
                            result.validated_output = validated_data
                            logger.info("Subagent %s output validated against schema", contract.name)
                        else:
                            logger.warning("Subagent %s output failed schema validation: %s",
                                          contract.name, "; ".join(errors))
                            if contract.auto_retry_parse and attempt < max_retries:
                                from wisp.multi_agent.schema_validator import build_retry_prompt
                                contract.task = build_retry_prompt(
                                    contract.task, contract.output_schema, result.output, errors
                                )
                                contract.retry_count = getattr(contract, 'retry_count', 0) + 1
                                logger.info("Retrying subagent %s with schema feedback", contract.name)
                                last_error = f"Schema validation failed: {'; '.join(errors)}"
                                continue
                    break
                last_error = result.error or "subagent failed"
                # Don't retry on timeout — it's a waste of time and tokens
                if result.timed_out or "timeout" in last_error.lower():
                    logger.warning("Subagent %s timed out — not retrying", contract.name)
                    break
                if attempt < max_retries:
                    backoff = 2 ** attempt
                    logger.warning("Subagent %s failed (attempt %d/%d), retrying in %ds: %s",
                                   contract.name, attempt + 1, max_retries + 1, backoff, last_error)
                    await asyncio.sleep(backoff)
            except Exception as exc:
                last_error = str(exc)
                if attempt < max_retries:
                    backoff = 2 ** attempt
                    logger.warning("Subagent %s crashed (attempt %d/%d), retrying in %ds: %s",
                                   contract.name, attempt + 1, max_retries + 1, backoff, last_error)
                    await asyncio.sleep(backoff)
                else:
                    logger.error("Subagent %s crashed: %s", contract.name, exc, exc_info=True)
                    return f"[Error: subagent crashed after {max_retries + 1} attempts: {exc}]"
        else:
            # All retries exhausted
            return f"[Error: subagent failed after {max_retries + 1} attempts: {last_error}]"

        if result is None:
            return f"[Error: subagent failed after {max_retries + 1} attempts: {last_error}]"

        # ── Return output (with size guard) ────────────────────────────
        output = result.output
        if len(output) > 12000:
            output = output[:12000] + f"\n... [truncated: {len(result.output)} total chars]"

        # ── Store in cache ─────────────────────────────────────────────
        if result.success and len(output) < 50000:
            cache[cache_key] = {"ts": time.monotonic(), "output": output}
            self._subagent_cache = cache

        return output

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
        from wisp.multi_agent import SubagentOrchestrator, SubagentContract
        try:
            orch = SubagentOrchestrator(parent_agent=self)
            contracts = []
            for spec in specs:
                if isinstance(spec, dict):
                    contract = SubagentContract(**spec)
                else:
                    contract = spec
                # Apply production optimizations
                # Respect explicit user timeout — don't override with adaptive
                if contract.timeout_seconds < 30.0:
                    contract.timeout_seconds = self._adaptive_subagent_timeout(
                        contract.task, contract.timeout_seconds
                    )
                local_model = self._pick_local_model_for_subagent(contract.task)
                if local_model:
                    contract.model = local_model
                contracts.append(contract)
            results = await orch.run_parallel(contracts)
            for r in results:
                logger.info(
                    "Subagent %s: success=%s, duration=%.1fs, files=%s, tokens=%d",
                    r.task_id, r.success, r.elapsed_seconds, r.files_changed, r.tokens_used,
                )
            return results
        except Exception as e:
            logger.error("Failed to spawn subagents: %s", e)
        return []

    # ── Auto parallel research ───────────────────────────────────────

    async def _auto_parallel_research(self, prompt: str) -> list:
        """Automatically spawn parallel subagents for complex research queries.

        Detects research-oriented prompts and breaks them into parallel
        sub-tasks. Returns AgentEvent list for yielding.
        """
        # Only trigger for research-like queries
        research_keywords = [
            "research", "compare", "analyze", "survey", "overview",
            "explain", "what is", "how does", "pros and cons",
            "differences between", "similarities between",
        ]
        prompt_lower = prompt.lower()
        is_research = any(kw in prompt_lower for kw in research_keywords)

        # Don't trigger for simple queries
        if not is_research or len(prompt) < 40:
            return []

        # Don't trigger if already in a subagent
        if getattr(self, "_subagent_depth", 0) > 0:
            return []

        # Check if auto-research is enabled (default: True)
        if not getattr(self.config, "auto_parallel_research", True):
            return []

        logger.info("Auto-parallel research triggered for: %s", prompt[:60])

        # Break into parallel research angles
        angles = self._research_angles(prompt)
        if len(angles) < 2:
            return []

        from wisp.multi_agent import SubagentContract
        from wisp.core.events import content as content_event

        contracts = [
            SubagentContract(
                name=f"research-{i+1}",
                task=angle,
                timeout_seconds=60,
                isolation="process",
                max_iterations=5,
                output_format="markdown",
                max_tokens=4000,
                workspace=self.config.workspace,
                auto_approve=self.config.auto_approve,
            )
            for i, angle in enumerate(angles[:4])  # Max 4 parallel
        ]

        events = []
        events.append(content_event(f"🔍 Researching: {prompt[:80]}...\n"))
        events.append(content_event(f"  Spawning {len(contracts)} parallel subagents...\n"))

        results = await self.spawn_subagents(contracts)

        # Build synthesized context
        synthesis = "\n## Research Results\n\n"
        for r in results:
            if r.success:
                synthesis += f"### {r.task_id}\n{r.output[:2000]}\n\n"
            elif r.timed_out:
                synthesis += f"### {r.task_id}\n[Timed out after {r.elapsed_seconds:.0f}s]\n\n"
            else:
                synthesis += f"### {r.task_id}\n[Error: {r.error or 'unknown'}]\n\n"

        # Inject as assistant context (not a real assistant message)
        self.messages.append({
            "role": "system",
            "content": f"[Parallel research completed]\n{synthesis}",
        })

        events.append(content_event(f"  ✓ Research complete ({len([r for r in results if r.success])}/{len(results)} succeeded)\n"))
        return events

    def _research_angles(self, prompt: str) -> list[str]:
        """Break a research prompt into parallel investigation angles."""
        prompt_lower = prompt.lower()

        # KV caching research
        if "kv cache" in prompt_lower or "kv caching" in prompt_lower:
            return [
                "Research the foundational problem: why KV caching is needed in transformers, memory complexity of attention",
                "Research architectural improvements: Multi-Query Attention, Grouped-Query Attention, FlashAttention optimizations",
                "Research compression methods: quantization, eviction policies, H2O, SnapKV, dynamic compression",
                "Research system-level optimizations: vLLM PagedAttention, continuous batching, memory paging",
            ]

        # Generic research breakdown
        return [
            f"Research the core concepts and fundamentals: {prompt}",
            f"Research recent advances and state-of-the-art: {prompt}",
            f"Research practical implementations and tools: {prompt}",
            f"Research limitations, challenges, and future directions: {prompt}",
        ]

    async def _check_delegation(self, prompt: str) -> list:
        """Check if the task should be auto-delegated to subagents.

        Uses DelegationAnalyzer and CapabilityMatcher to detect capability
        mismatch and automatically spawn appropriate subagents.
        """
        from wisp.multi_agent import get_delegation_analyzer, SubagentContract
        from wisp.multi_agent.capability_matcher import CapabilityMatcher
        from wisp.core.events import content as content_event

        events = []
        contracts = []

        # ── Check 1: DelegationAnalyzer (complexity/research/scope triggers) ──
        analyzer = get_delegation_analyzer()
        signal = analyzer.analyze(prompt, current_iteration=0,
                                  max_iterations=self.max_iterations)

        if signal.should_delegate:
            logger.info("Auto-delegation triggered: %s (confidence=%.2f)",
                        signal.reason, signal.confidence)
            events.append(content_event(
                f"🔄 Auto-delegating: {signal.reason} (confidence: {signal.confidence:.0%})\n"
            ))
            for spec in signal.suggested_contracts:
                contracts.append(SubagentContract(
                    name=spec.get("name", "subagent"),
                    task=spec.get("task", ""),
                    role=spec.get("role", "generalist"),
                    timeout_seconds=spec.get("timeout_seconds", 60),
                    max_iterations=spec.get("max_iterations", 5),
                    isolation="process",
                    output_format="markdown",
                    workspace=self.config.workspace,
                    auto_approve=self.config.auto_approve,
                ))

        # ── Check 2: CapabilityMatcher (role/tool mismatch) ──
        available_tools = list(self._allowed_tools) if self._allowed_tools else None
        matcher = CapabilityMatcher()
        mismatch = matcher.detect_mismatch(
            current_role=self.role or "agent",
            task=prompt,
            available_tools=available_tools,
        )
        if mismatch and mismatch.should_delegate():
            logger.info("Capability mismatch detected: %s (confidence=%.2f)",
                        mismatch.reason, mismatch.confidence)
            events.append(content_event(
                f"🔄 Capability mismatch: {mismatch.reason} (confidence: {mismatch.confidence:.0%})\n"
            ))
            contract = matcher.build_delegation_contract(
                mismatch, prompt, parent_context=self._system_prompt[:500]
            )
            contract.workspace = self.config.workspace
            contract.auto_approve = self.config.auto_approve
            contracts.append(contract)

        if not contracts:
            return []

        events.append(content_event(f"  Spawning {len(contracts)} subagent(s)...\n"))

        # Use context partitioning to pass only relevant context
        from wisp.multi_agent import partition_context
        for contract in contracts:
            contract.context_files = partition_context(
                self.messages, contract.task, max_messages=5
            )

        results = await self.spawn_subagents(contracts)

        # Build synthesized context
        synthesis = "\n## Delegated Task Results\n\n"
        for r in results:
            if r.success:
                synthesis += f"### {r.task_id}\n{r.output[:1500]}\n\n"
            elif r.timed_out:
                synthesis += f"### {r.task_id}\n[Timed out after {r.elapsed_seconds:.0f}s]\n\n"
            else:
                synthesis += f"### {r.task_id}\n[Error: {r.error or 'unknown'}]\n\n"

        # Inject as system context
        self.messages.append({
            "role": "system",
            "content": f"[Auto-delegation completed]\n{synthesis}",
        })

        events.append(content_event(
            f"  ✓ Delegation complete ({len([r for r in results if r.success])}/{len(results)} succeeded)\n"
        ))
        return events

    def _subagent_cache_key(self, contract: SubagentContract) -> str:
        """Build a cache key from contract fields that affect output."""
        import hashlib
        parts = [
            contract.task,
            ",".join(sorted(contract.tools)),
            str(contract.model or ""),
            str(contract.workspace or ""),
            contract.output_format,
            str(contract.output_schema or ""),
        ]
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    # ── Local model fallback helper ──────────────────────────────────

    def _pick_local_model_for_subagent(self, task: str) -> Optional[str]:
        """Return a fast local model name if the task is simple enough.

        Simple tasks: read_file, list_files, short summaries.
        Complex tasks: analysis, synthesis, multi-file edits.
        """
        # Only fall back if parent is using a cloud model
        parent_model = getattr(self.config, "model", "")
        if not parent_model or ":cloud" not in parent_model:
            return None  # already local

        # Check if any fast local model is available
        fast_locals = ["llama3.2", "llama3.1", "qwen2.5", "phi4", "gemma2"]
        available = self._list_local_models()
        for candidate in fast_locals:
            for name in available:
                if candidate in name.lower():
                    return name
        return None

    def _list_local_models(self) -> list[str]:
        """Query Ollama for locally available models. Cached for 60s."""
        now = time.monotonic()
        cache = getattr(self, "_local_model_cache", None)
        if cache and now - cache["ts"] < 60:
            return cache["models"]
        try:
            import requests
            url = getattr(self.config, "ollama_url", "http://localhost:11434")
            resp = requests.get(f"{url}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                self._local_model_cache = {"ts": now, "models": models}
                return models
        except Exception:
            pass
        return []

    # ── Adaptive timeout helper ──────────────────────────────────────

    def _adaptive_subagent_timeout(self, task: str, requested: float) -> float:
        """Compute adaptive timeout based on task complexity and model latency.

        Cloud models (e.g. deepseek-v4-pro:cloud) need longer timeouts than
        local models.  Task length and tool count also affect duration.
        """
        model = getattr(self.config, "model", "")
        is_cloud = ":cloud" in model or "https://" in getattr(self.config, "ollama_url", "")

        # Base latency: local ~5s/turn, cloud ~15-30s/turn
        base_per_turn = 25.0 if is_cloud else 8.0

        # Estimate iterations needed from task complexity
        estimated_iterations = 3  # minimum
        if len(task) > 200:
            estimated_iterations += 1
        if len(task) > 500:
            estimated_iterations += 1
        # Tool-heavy tasks need more iterations
        tool_keywords = ["read", "write", "edit", "list", "search", "run"]
        tool_mentions = sum(1 for kw in tool_keywords if kw in task.lower())
        estimated_iterations += min(tool_mentions, 3)

        estimated_seconds = estimated_iterations * base_per_turn + 10  # overhead

        # Clamp: never less than 30s, never more than 300s
        adaptive = max(30.0, min(estimated_seconds, 300.0))

        # Respect explicit user request — don't override with larger adaptive timeout
        # User knows their constraints better than our heuristic
        if requested >= 30.0:
            return requested
        return max(adaptive, requested)

    # ── Non-interactive task runner ──────────────────────────────────

    async def run_task(
        self,
        task_description: str,
        workspace: str = ".",
        max_iterations: int = 10,
        timeout_seconds: float = 120.0,
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
                response = self._run_turn_streaming(system)
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
