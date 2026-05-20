"""WispAgentCore — stateless turn engine.

Replaces: the stateful WispAgentCore in wisp/core/agent.py.
All state is injected or passed as parameters.

Design:
  - Receives session dict, prompt, and dependencies
  - Builds system prompt from context (rules.md, skills, repo map, etc.)
  - Streams events from provider
  - Parses tool calls, checks security, executes via extensions
  - Yields flat dict events for backward compatibility
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from wisp.core.events import (
    AgentEvent,
    thinking as thinking_event,
    content as content_event,
    tool_call as tool_call_event,
    tool_result as tool_result_event,
    error as error_event,
    done as done_event,
)

logger = logging.getLogger(__name__)


def _flatten_event(ev: AgentEvent) -> dict:
    """Convert canonical AgentEvent to flat dict for backward compatibility."""
    flat = dict(ev.data)
    flat["type"] = str(ev.type)
    flat["timestamp"] = ev.timestamp
    return flat


@dataclass
class WispAgentCore:
    """Stateless turn engine."""

    provider: Any
    security: Any
    extensions: Any
    telemetry: Any
    config: Any = None

    # Caches for expensive context building
    _assembler_cache: Any = field(default=None, repr=False)
    _static_prompt_cache: dict = field(default_factory=dict, repr=False)

    async def turn(self, session: dict, prompt: str) -> AsyncIterator[dict]:
        """Run one turn, yielding events.

        Security is checked BEFORE yielding tool_call events.
        Provider exceptions are caught and yielded as error events.
        """
        # Build messages list
        messages = list(session.get("messages", []))
        messages.append({"role": "user", "content": prompt})

        # Build system prompt with full context awareness
        system_prompt = self._build_system_prompt(session, query=prompt)

        # Get tools — built-in + extensions
        tools = self._get_tool_schemas()

        # Stream events from provider
        pending_tool_calls: list[dict] = []
        provider_events: list[dict] = []
        partial_content: list[str] = []

        try:
            for event in self.provider.generate_stream_events(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools if tools else None,
            ):
                # Normalize event
                normalized = self._normalize_event(event)
                provider_events.append(normalized)

                # Accumulate partial content for error recovery
                if normalized.get("type") == "content":
                    partial_content.append(normalized.get("text", ""))

                # Security + extension checks for tool calls
                if normalized.get("type") == "tool_call":
                    # Check security BEFORE yielding
                    if self.security is not None:
                        action = self._make_action(normalized)
                        context = self._make_context(session)
                        try:
                            decision = self.security.check(action, context)
                            if not decision.allowed:
                                yield _flatten_event(error_event(
                                    f"Blocked ({decision.reason}): READ_ONLY mode",
                                    recoverable=True,
                                ))
                                continue
                        except Exception as e:
                            logger.warning("Security check failed: %s", e)

                    # Check extensions
                    if self.extensions is not None:
                        try:
                            ext_result = self.extensions.intercept(normalized)
                            if ext_result.get("action") == "block":
                                yield _flatten_event(error_event(
                                    f"Blocked: {ext_result.get('reason', 'by extension')}",
                                    recoverable=True,
                                ))
                                continue
                        except Exception as e:
                            logger.warning("Extension intercept failed: %s", e)

                    pending_tool_calls.append(normalized)

                # Yield the event
                yield normalized

        except Exception as exc:
            logger.exception("Provider stream failed")
            # Yield partial content so user sees something
            if partial_content:
                yield _flatten_event(content_event("".join(partial_content)))
            yield _flatten_event(error_event(
                f"Stream error: {exc}",
                recoverable=True,
            ))
            return

        # Execute pending tool calls that didn't get a tool_result from provider
        has_tool_results = any(e.get("type") == "tool_result" for e in provider_events)
        if pending_tool_calls and not has_tool_results:
            for tc in pending_tool_calls:
                async for result_event in self._execute_tool(tc, session):
                    yield result_event

    def _build_system_prompt(self, session: dict, query: str | None = None) -> str:
        """Build rich system prompt from session context."""
        from wisp.context_assembler import ContextAssembler, PromptContext

        ws = session.get("workspace", ".")
        ws_path = Path(ws).resolve()

        # Lazy-init assembler
        if self._assembler_cache is None:
            self._assembler_cache = ContextAssembler()
        assembler = self._assembler_cache

        # Check cache for static prompt
        cache_key = (ws,)
        static_prompt = self._static_prompt_cache.get(cache_key)

        if static_prompt is None:
            skills_block = self._build_skills_block(ws)
            project_ctx = self._detect_project_context(ws)
            memory_block = self._build_memory_block(ws)
            git_ctx = self._build_git_context(ws)
            repo_map = self._build_repo_map(ws)

            # Load rules.md if present
            rules_path = ws_path / ".wisp" / "rules.md"
            role_extra = ""
            if rules_path.exists():
                try:
                    role_extra = rules_path.read_text(encoding="utf-8")
                except Exception:
                    pass

            ctx = PromptContext.from_legacy(
                workspace=ws,
                default_system=assembler.default_system,
                role_extra=role_extra or None,
                skills_block=skills_block or None,
                project_context=project_ctx or None,
                memory_block=memory_block or None,
                git_context=git_ctx or None,
                repo_map=repo_map or None,
            )
            static_prompt = assembler.build(ctx)

            tools_block = self._build_tools_block()
            if tools_block:
                static_prompt += "\n\n" + tools_block

            self._static_prompt_cache[cache_key] = static_prompt

        # Add query-specific context
        if query:
            relevant = self._get_relevant_files(ws, query)
            if relevant:
                static_prompt += f"\n\n## Files Relevant to Query\n{relevant}\n"

        # Add compaction notice
        if session.get("compaction_history"):
            count = len(session["compaction_history"])
            static_prompt += f"\n[Session compacted {count} times.]\n"

        return static_prompt

    def invalidate_caches(self) -> None:
        """Invalidate all caches — call when workspace context changes."""
        self._static_prompt_cache.clear()
        logger.debug("Engine caches invalidated")

    def _build_skills_block(self, workspace: str) -> str:
        """Discover and format skills for the system prompt."""
        try:
            from wisp.skills import discover_skills
            skills = discover_skills(workspace)
            if not skills:
                return ""
            lines = ["## Skills"]
            for skill in skills:
                lines.append(f"- {skill.name}: {skill.description}")
                if skill.instructions:
                    lines.append(f"  Instructions: {skill.instructions[:200]}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug("Failed to build skills block: %s", e)
            return ""

    def _detect_project_context(self, workspace: str) -> str:
        """Detect project type and format context."""
        try:
            from wisp.project_context import detect_project_context, format_context
            ctx = detect_project_context(workspace)
            return format_context(ctx)
        except Exception as e:
            logger.debug("Failed to detect project context: %s", e)
            return ""

    def _build_memory_block(self, workspace: str) -> str:
        """Build memory block from agent memory."""
        try:
            from wisp.agent_memory import get_agent_memory
            memory = get_agent_memory()
            return memory.format_for_prompt([])
        except Exception as e:
            logger.debug("Failed to build memory block: %s", e)
            return ""

    def _build_git_context(self, workspace: str) -> str:
        """Build git context string."""
        try:
            from wisp.git_context import format_git_context
            return format_git_context(workspace)
        except Exception as e:
            logger.debug("Failed to build git context: %s", e)
            return ""

    def _build_repo_map(self, workspace: str) -> str:
        """Build repo map for the workspace."""
        try:
            from wisp.repo_map import RepoMap
            ws_path = Path(workspace).resolve()
            rm = RepoMap(ws_path)
            entries = rm.build(use_cache=True, fast_mode=True)
            if entries:
                map_text = rm.format_for_llm(max_tokens=1200)
                return f"## Codebase Map\n{map_text}\n"
        except ImportError:
            pass
        except Exception as e:
            logger.debug("Failed to build repo map: %s", e)
        return ""

    def _get_relevant_files(self, workspace: str, query: str) -> str:
        """Get files relevant to the query from repo map."""
        try:
            from wisp.repo_map import RepoMap
            ws_path = Path(workspace).resolve()
            rm = RepoMap(ws_path)
            rm.build(use_cache=True, fast_mode=True)
            relevant = rm.get_relevant_files(query, top_k=5)
            if relevant:
                return "\n".join(f"- {f}" for f in relevant)
        except Exception as e:
            logger.debug("Failed to get relevant files: %s", e)
        return ""

    def _build_tools_block(self) -> str:
        """Build a human-readable tools description for the system prompt."""
        lines = ["## Tools available"]
        descriptions = {
            "read_file": "Read file contents (supports offset/limit for large files)",
            "write_file": "Create or overwrite a file",
            "edit_file": "Targeted text replacement (surgical edits, with fuzzy fallback)",
            "edit_file_multi": "Make multiple precise edits in a single file in one call",
            "run_bash": "Execute shell commands",
            "list_files": "Explore directory structure",
            "web_fetch": "Fetch content from URLs (web pages, APIs, documentation)",
            "web_search": "Search the web for current information, docs, error messages",
            "search_symbols": "Search code for functions, classes, structs by name (regex-based)",
            "search_codebase": "Semantic search over the codebase using vector similarity",
            "remember": "Store a fact in cross-session memory",
            "recall": "Search cross-session memory and past summaries for relevant facts",
            "spawn_subagent": "Delegate a scoped task to a child agent",
            "git_status": "Show git status (branch, uncommitted files, recent commits)",
            "git_diff": "Show git diff for files or entire workspace",
            "git_branch": "List/create/switch git branches",
            "git_commit": "Stage files and commit with a message",
            "git_push": "Push current branch to remote",
            "gh_pr_create": "Create a GitHub pull request (requires gh CLI)",
            "lsp_diagnostics": "Run language server diagnostics on a file",
            "lsp_definition": "Go to definition of a symbol",
            "lsp_references": "Find all references to a symbol",
            "lsp_hover": "Get type info and docstring for a symbol",
            "lsp_symbols": "List all symbols in a file as an outline tree",
            "diagnose": "Diagnose errors from test output, tracebacks, or command failures",
            "run_tests": "Run tests for changed files or the full test suite",
            "plan_task": "Create a structured plan with subtasks and dependencies",
            "mark_step_done": "Mark a plan task as completed",
            "update_plan": "Update a plan task's status",
        }
        for name, desc in descriptions.items():
            lines.append(f"- {name}: {desc}")
        return "\n".join(lines)

    def _get_tool_schemas(self) -> list[dict]:
        """Get all tool schemas — built-in + extensions."""
        from wisp.tools import TOOL_SCHEMAS

        schemas = list(TOOL_SCHEMAS)

        if self.extensions is not None:
            try:
                ext_tools = self.extensions.tools()
                if ext_tools:
                    schemas.extend(ext_tools)
            except Exception as e:
                logger.warning("Failed to get extension tools: %s", e)

        return schemas

    async def _execute_tool(self, event: dict, session: dict) -> AsyncIterator[dict]:
        """Execute a tool call and yield the result event.

        Security is checked again here as a defense-in-depth measure.
        """
        from wisp.tools import execute_tool

        name = event.get("name", "")
        args = event.get("arguments", {})
        workspace = session.get("workspace", ".")

        # Defense-in-depth: re-check security before execution
        if self.security is not None:
            action = self._make_action(event)
            context = self._make_context(session)
            try:
                decision = self.security.check(action, context)
                if not decision.allowed:
                    yield _flatten_event(tool_result_event(
                        name,
                        {"status": "error", "data": f"Security blocked: {decision.reason}"},
                        duration_ms=0,
                    ))
                    return
            except Exception as e:
                logger.warning("Security re-check failed: %s", e)

        start = time.time()
        try:
            result = execute_tool(name, args, workspace=workspace)
        except Exception as e:
            logger.exception("Tool execution failed: %s", name)
            result = {"status": "error", "data": str(e)}

        duration_ms = (time.time() - start) * 1000

        yield _flatten_event(tool_result_event(name, result, duration_ms=duration_ms))

    def _normalize_event(self, event: Any) -> dict:
        """Normalize provider event to standard format.

        Whitelist known fields instead of copying __dict__ to avoid
        leaking internal state or circular references.
        """
        if isinstance(event, dict):
            return dict(event)

        result: dict[str, Any] = {}

        # Extract type/phase
        if hasattr(event, "type"):
            result["type"] = event.type
        elif hasattr(event, "phase"):
            result["type"] = event.phase
        else:
            result["type"] = "unknown"

        # Whitelist known safe fields
        safe_fields = {
            "text", "name", "arguments", "result", "message",
            "duration_ms", "turns", "session_id", "summary", "reason",
            "level", "recoverable", "tool_call_id", "id",
        }
        for field_name in safe_fields:
            if hasattr(event, field_name):
                result[field_name] = getattr(event, field_name)

        return result

    def _make_action(self, event: dict) -> Any:
        """Create Action from tool_call event."""
        from wisp.infra.security import Action
        return Action(
            name=event.get("name", ""),
            args=event.get("arguments", {}),
        )

    def _make_context(self, session: dict) -> Any:
        """Create Context from session."""
        from pathlib import Path
        from wisp.infra.security import Context
        return Context(workspace=Path(session.get("workspace", ".")))
