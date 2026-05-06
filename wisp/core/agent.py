"""WispAgentCore — event-driven agent engine with zero I/O.

This is the SDK-facing core of Wisp. It contains all agent logic but never
prints, reads input, or touches global state. All output is yielded as
AgentEvent instances; all user interaction is delegated to the transport
layer via callbacks.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

from wisp.config import WispConfig
from wisp.ollama_client import OllamaClient, OllamaError
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
)

logger = logging.getLogger(__name__)


DEFAULT_SYSTEM = """You are Wisp, a helpful coding agent.

You have access to tools that let you read, write, and edit files, run bash commands, and list directories.

## Guidelines
1. Always think step by step. Analyze the problem before writing code.
2. Prefer targeted edits (edit_file) over rewriting entire files.
3. Run tests after making changes to verify correctness.
4. For git operations, use run_bash with appropriate git commands.
5. If a command fails, diagnose the error and try a different approach.
6. Keep explanations concise but clear. Show the user what you're doing.
7. When you're done, summarize what was accomplished.

## Tools available
- read_file: Read file contents (supports offset/limit for large files)
- write_file: Create or overwrite a file
- edit_file: Targeted text replacement (surgical edits, with fuzzy fallback)
- run_bash: Execute shell commands
- list_files: Explore directory structure
- web_fetch: Fetch content from URLs (web pages, APIs, documentation)
- search_symbols: Search code for functions, classes, structs by name
- remember: Store a fact in cross-session memory (preferences, decisions)
- recall: Search cross-session memory and past summaries for relevant facts
- spawn_subagent: Delegate a scoped task to a child agent
- git_status: Show git status (branch, uncommitted files, recent commits)
- git_diff: Show git diff for files or entire workspace
- diagnose: Diagnose errors from test output, tracebacks, or command failures
- plan_task: Create a structured plan with subtasks and dependencies
- mark_step_done: Mark a plan task as completed
- update_plan: Update a plan task's status (pending, in_progress, done, skipped)
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
        self.client = OllamaClient(self.config)
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
        self._system_prompt = ""
        self._active_skill: Optional[str] = None
        self.agent_id = agent_id or _generate_agent_id()
        self.role = role or "agent"
        self._role_system_extra = ""
        self._allowed_tools: Optional[set[str]] = None
        self.mcp = MCPManager(self.config.workspace or ".")
        self._mcp_initialized = False
        from wisp.agent_memory import AgentMemory
        self.agent_memory = AgentMemory()
        self._recent_summaries = self.agent_memory.load_recent(
            workspace=self.config.workspace or ".",
            limit=3,
        )
        from wisp.file_lock import FileLock
        from wisp.change_tracker import ChangeTracker
        self.file_lock = FileLock(self.config.workspace or ".", self.agent_id)
        self.change_tracker = ChangeTracker(self.config.workspace or ".", self.file_lock.agent_id)
        from wisp import tools as tools_module
        tools_module.set_collaboration_tools(self.file_lock, self.change_tracker)

    # ── Message helpers ──────────────────────────────────────────────

    def _add_message(self, role: str, content: str, thinking: str = ""):
        msg = {"role": role, "content": content}
        if thinking:
            msg["thinking"] = thinking
        self.messages.append(msg)

    # ── Continuation expansion ───────────────────────────────────────

    _CONTINUATION_TRIGGERS = frozenset({
        "continue", "go on", "more", "and?", "keep going", "next", "proceed",
        "finish", "tell me more", "expand on that", "elaborate", "what else",
    })

    def _expand_continuation(self, user_text: str) -> str:
        lowered = user_text.strip().lower().rstrip("?.!")
        if lowered not in self._CONTINUATION_TRIGGERS:
            return user_text
        parts: list[str] = [user_text]
        last_assistant = ""
        for m in reversed(self.messages):
            if m.get("role") == "assistant":
                last_assistant = m.get("content", "") or ""
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
                compacted_summary = self.messages[0].get("content", "")
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

    def _build_system_prompt(self, skill_name: Optional[str] = None, workspace: Optional[str] = None) -> str:
        ws = workspace or self.config.workspace or "."
        effective_skill = skill_name or self._active_skill
        cache_key = (effective_skill, ws)
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
            summary_block = AgentMemory().format_for_prompt(self._recent_summaries)
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

    async def _arun(self, prompt: str, system: Optional[str] = None) -> AsyncIterator[AgentEvent]:
        """Execute one user turn and yield all events (internal async implementation)."""
        self._add_message("user", self._expand_continuation(prompt))
        if system is None:
            system = self._build_system_prompt()
        self._trim_context_if_needed(system)

        # Session bookkeeping
        if self.session is None:
            self.session = Session.create(
                model=self.config.model,
                workspace=self.config.workspace or ".",
                first_prompt=prompt,
            )

        # Auto-compact
        compact_event = self._maybe_compact_session()
        if compact_event:
            yield compact_event

        for iteration in range(1, self.max_iterations + 1):
            if self._interrupted:
                break

            # Forward streaming token events
            streamed_content = False
            for event in self._run_turn_streaming_events(system):
                if self._interrupted:
                    break
                yield event
                if event.type == TYPE_CONTENT:
                    streamed_content = True

            response = getattr(self.client, "stream_response", None) or {}
            if not response:
                yield error_event("No response from model", recoverable=False)
                break

            if not isinstance(response, dict):
                yield error_event(f"Unexpected response type: {type(response).__name__}", recoverable=False)
                break

            msg = response.get("message", {})
            content = msg.get("content", "") or "" if isinstance(msg, dict) else ""
            thinking_text = msg.get("thinking", "") or "" if isinstance(msg, dict) else ""
            tool_calls = self._parse_tool_call(response)

            if not tool_calls:
                if content:
                    self._add_message("assistant", content, thinking_text)
                self._save_session()
                if not streamed_content:
                    yield content_event(content)
                yield done_event(
                    session_id=self.session.id if self.session else "",
                    turns=iteration,
                )
                break

            self._add_message("assistant", content or "", thinking_text)
            if tool_calls:
                self.messages[-1]["tool_calls"] = tool_calls

            for tc in tool_calls:
                func = tc.get("function", {})
                if isinstance(func, dict):
                    yield tool_call_event(func.get("name", ""), func.get("arguments", {}))

            async for event in WispAgentCore._run_tool_calls(self, tool_calls, self.config.workspace or "."):
                yield event

        self._save_session()

    async def run(self, prompt: str) -> AsyncIterator[AgentEvent]:
        """Execute one user turn and yield all events.

        This is the primary SDK entry point. Transports consume the yielded
        events and decide how to present them (print, WebSocket, etc.).
        """
        async for event in self._arun(prompt):
            yield event

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
                    logger.error("Stream error (%s): %s", event.error_type, event.message)
                    return

        except OllamaError as e:
            logger.error("Ollama error: %s", e)
        except Exception as e:
            logger.error("Unexpected error in streaming turn: %s", e, exc_info=True)

    def _run_turn_streaming(self, system: str) -> dict:
        """Backward compat: accumulate silently and return response dict."""
        for _ in self._run_turn_streaming_events(system):
            pass
        return getattr(self.client, "stream_response", None) or {}

    def _parse_tool_call(self, response: dict) -> Optional[list[dict]]:
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
    ) -> AsyncIterator[AgentEvent]:
        """Execute tool calls and yield result events.

        Dangerous commands yield approval_request events; the transport
        layer must call the returned future to approve/deny.
        """
        for tc in tool_calls:
            if self._interrupted:
                break

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

            # Dangerous command guard
            danger_reason = None
            if func_name == "run_bash":
                from wisp.tools import check_dangerous_command
                danger_reason = check_dangerous_command(func_args.get("command", ""))

            if danger_reason:
                # In SDK mode, dangerous commands are blocked unless explicitly approved
                # by the transport layer. We yield an approval_request and skip.
                yield approval_request(func_name, func_args, reason=danger_reason)
                blocked_result = f"[Blocked: dangerous command — {danger_reason}]"
                yield tool_result_event(
                    func_name,
                    blocked_result,
                )
                self.messages.append({
                    "role": "tool",
                    "content": blocked_result,
                    "name": func_name,
                    **({"tool_call_id": tc.get("id")} if tc.get("id") else {}),
                })
                continue

            # Execute tool
            start = time.monotonic()
            if func_name == "spawn_subagent":
                result = self._spawn_subagent(func_args, workspace)
            elif self._is_mcp_tool(func_name):
                try:
                    result = self.mcp.call_tool(func_name, func_args)
                    if len(result) > 8000:
                        result = result[:8000] + f"\n... [truncated {len(result)} total chars]"
                except Exception as e:
                    result = f"MCP error: {e}"
            else:
                try:
                    result = execute_tool(func_name, func_args, workspace, max_data_chars=8000, file_lock=self.file_lock)
                except ToolError as e:
                    result = f"Error: {e}"
                except Exception as e:
                    result = f"Unexpected error: {e}"

            duration_ms = (time.monotonic() - start) * 1000

            if func_name == "remember":
                self._invalidate_system_prompt_cache()

            yield tool_result_event(func_name, result, duration_ms=duration_ms)
            self.messages.append({
                "role": "tool",
                "content": str(result),
                "name": func_name,
                **({"tool_call_id": tc.get("id")} if tc.get("id") else {}),
            })

    def _spawn_subagent(self, args: dict, workspace: str) -> str:
        from wisp.subagent import SubagentRunner, SubagentContract
        depth = getattr(self, "_subagent_depth", 0)
        if depth >= 1:
            return "[Error: subagents cannot spawn subagents (max depth = 1)]"
        contract = SubagentContract(
            task=args.get("task", ""),
            tools=args.get("tools", ["all"]),
            max_iterations=int(args.get("max_iterations", 15)),
            timeout_seconds=int(args.get("timeout_seconds", 120)),
            output_format=args.get("output_format", "text"),
            workspace=workspace,
            auto_approve=self.config.auto_approve,
        )
        runner = SubagentRunner(self)
        result = runner.spawn(contract)
        return result.output

    # ── Non-interactive task runner ──────────────────────────────────

    async def run_task(
        self,
        task_description: str,
        workspace: str = ".",
        max_iterations: int = 10,
        timeout_seconds: float = 120.0,
    ) -> dict:
        """Run a full agent loop for a single task, non-interactively.

        Returns a dict with ``success`` (bool) and ``output`` (str) keys.
        """
        self._add_message("user", task_description)
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

            msg = response.get("message", {})
            content = msg.get("content", "") or "" if isinstance(msg, dict) else ""
            thinking = msg.get("thinking", "") or "" if isinstance(msg, dict) else ""
            tool_calls = self._parse_tool_call(response)

            self._add_message("assistant", content or "", thinking)
            if tool_calls:
                self.messages[-1]["tool_calls"] = tool_calls
                async for _event in WispAgentCore._run_tool_calls(self, tool_calls, workspace):
                    pass  # consume but don't yield in non-interactive mode
                iteration += 1
                continue

            final_content = content
            break
        else:
            final_content = f"[Task reached max iterations ({max_iterations}) without completion]"
            return {"success": False, "output": final_content}

        return {"success": True, "output": final_content}
