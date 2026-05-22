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
from typing import Any, AsyncIterator, Optional

from wisp.core.events import (
    AgentEvent,
    thinking as thinking_event,
    content as content_event,
    tool_call as tool_call_event,
    tool_result as tool_result_event,
    error as error_event,
    done as done_event,
)
from wisp.core.approval_gate import ApprovalGate

logger = logging.getLogger(__name__)


def _flatten_event(ev: AgentEvent | dict) -> dict:
    """Convert canonical AgentEvent to flat dict for backward compatibility."""
    if isinstance(ev, dict):
        return dict(ev)
    flat = dict(ev.data)
    flat["type"] = str(ev.type)
    flat["timestamp"] = ev.timestamp
    return flat


@dataclass
class WispAgentCore:
    """Stateless turn engine."""

    provider: Any = None
    security: Any = None
    extensions: Any = None
    telemetry: Any = None
    config: Any = None
    tool_executor: Any = None

    # Caches for expensive context building
    _assembler_cache: Any = field(default=None, repr=False)
    _static_prompt_cache: dict = field(default_factory=dict, repr=False)
    _approval_gate: Any = field(default=None, repr=False)

    async def turn(self, session: dict, prompt: str, approval_handler=None) -> AsyncIterator[dict]:
        """Run one turn, yielding events.

        Loops internally: provider → tool_calls → execute → append → provider
        until the model returns content (no tool calls) or max iterations.
        """
        # Build messages list
        messages = list(session.get("messages", []))
        # Avoid duplicating the user message if runtime already added it
        if (
            not messages
            or messages[-1].get("role") != "user"
            or messages[-1].get("content") != prompt
        ):
            messages.append({"role": "user", "content": prompt})

        # Build system prompt with full context awareness
        system_prompt = self._build_system_prompt(session, query=prompt)

        # Get tools — built-in + extensions
        tools = self._get_tool_schemas()

        max_iterations = getattr(self.config, "max_iterations", 30)

        for iteration in range(max_iterations):
            pending_tool_calls: list[dict] = []
            provider_events: list[dict] = []
            partial_content: list[str] = []
            has_tool_calls = False

            try:
                async for event in self._stream_events_async(
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
                    if normalized.get("type") in ("tool_call", "tool_calls"):
                        has_tool_calls = True
                        # Normalize type to singular for downstream consistency
                        normalized["type"] = "tool_call"
                        # Extract calls from ToolCallBatch if present
                        if "calls" in normalized and "name" not in normalized:
                            calls = normalized.pop("calls", [])
                            if calls:
                                # Yield individual tool_call events for each call
                                for call in calls:
                                    func = call.get("function", {})
                                    single = {
                                        "type": "tool_call",
                                        "name": func.get("name", ""),
                                        "arguments": func.get("arguments", {}),
                                    }
                                    if "id" in call:
                                        single["id"] = call["id"]
                                    # Process each individually
                                    tc_event = dict(single)
                                    # Check security BEFORE yielding
                                    gate = self._get_approval_gate()
                                    allowed, reason = await gate.check(
                                        tc_event, session, approval_handler=approval_handler
                                    )
                                    if not allowed:
                                        yield _flatten_event(
                                            error_event(
                                                f"Blocked: {reason}",
                                                recoverable=True,
                                            )
                                        )
                                        continue

                                    # Check extensions
                                    if self.extensions is not None:
                                        try:
                                            ext_result = self.extensions.intercept(tc_event)
                                            if ext_result.get("action") == "block":
                                                yield _flatten_event(
                                                    error_event(
                                                        f"Blocked: {ext_result.get('reason', 'by extension')}",
                                                        recoverable=True,
                                                    )
                                                )
                                                continue
                                        except Exception as e:
                                            logger.exception(
                                                "Extension intercept failed — treating as deny: %s",
                                                e,
                                            )
                                            yield _flatten_event(
                                                error_event(
                                                    f"Extension intercept failed: {e}. Tool call denied.",
                                                    recoverable=True,
                                                )
                                            )
                                            continue

                                    pending_tool_calls.append(tc_event)
                                    yield _flatten_event(tc_event)
                                continue  # Skip the default yield below since we already yielded
                            continue

                        # Check security BEFORE yielding
                        gate = self._get_approval_gate()
                        allowed, reason = await gate.check(normalized, session, approval_handler=approval_handler)
                        if not allowed:
                            yield _flatten_event(
                                error_event(
                                    f"Blocked: {reason}",
                                    recoverable=True,
                                )
                            )
                            continue

                        # Check extensions
                        if self.extensions is not None:
                            try:
                                ext_result = self.extensions.intercept(normalized)
                                if ext_result.get("action") == "block":
                                    yield _flatten_event(
                                        error_event(
                                            f"Blocked: {ext_result.get('reason', 'by extension')}",
                                            recoverable=True,
                                        )
                                    )
                                    continue
                            except Exception as e:
                                logger.exception(
                                    "Extension intercept failed — treating as deny: %s", e
                                )
                                yield _flatten_event(
                                    error_event(
                                        f"Extension intercept failed: {e}. Tool call denied.",
                                        recoverable=True,
                                    )
                                )
                                continue

                        pending_tool_calls.append(normalized)

                    # Yield the event (skip complete events)
                    if normalized.get("type") in ("complete", "done"):
                        continue
                    yield normalized

            except Exception as exc:
                logger.exception("Provider stream failed")
                if partial_content:
                    yield _flatten_event(content_event("".join(partial_content)))
                yield _flatten_event(
                    error_event(
                        f"Stream error: {exc}",
                        recoverable=True,
                    )
                )
                return

            # ── If no tool calls, the model produced final content ──
            if not has_tool_calls:
                yield _flatten_event(done_event(session.get("id", "")))
                return

            # ── Execute tools and feed results back to messages ──
            tool_results_events: list[dict] = []
            has_tool_results = any(e.get("type") == "tool_result" for e in provider_events)
            if pending_tool_calls and not has_tool_results:
                for tc in pending_tool_calls:
                    async for result_event in self._execute_tool(
                        tc, session, approval_handler=approval_handler
                    ):
                        tool_results_events.append(result_event)
                        yield result_event

            # Append assistant + tool messages to continue the conversation
            assistant_msg = {"role": "assistant", "content": "".join(partial_content)}
            if pending_tool_calls:
                import json
                import uuid as _uuid
                tc_blocks = []
                for tc in pending_tool_calls:
                    args = tc.get("arguments", {})
                    tc_blocks.append({
                        "id": tc.get("id", f"call_{_uuid.uuid4().hex[:8]}"),
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                        },
                    })
                assistant_msg["tool_calls"] = tc_blocks
            messages.append(assistant_msg)

            for tr in tool_results_events:
                content = tr.get("result", tr.get("data", ""))
                if isinstance(content, dict):
                    content = json.dumps(content)
                tc_id = tr.get("tool_call_id", "")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": str(content),
                })

        # Max iterations reached
        yield _flatten_event(error_event("Max iterations reached", recoverable=False))
        yield _flatten_event(done_event(session.get("id", "")))

    async def _stream_events_async(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict] | None,
    ):
        """Wrap a synchronous provider generator in an async iterator.

        Runs the blocking I/O in a thread to avoid blocking the event loop.
        This allows concurrent requests in FastAPI and responsive REPL.
        """
        import asyncio

        # Check if the provider already has an async version
        provider = self.provider
        if hasattr(provider, "generate_stream_events_async"):
            async for event in provider.generate_stream_events_async(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
            ):
                yield event
            return

        # Fallback: run sync generator in a thread via queue.
        # We spawn a thread that pushes events into an asyncio.Queue,
        # then yield from the queue. This preserves streaming without
        # blocking the event loop.
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        done = object()  # sentinel

        def _sync_producer():
            try:
                for event in provider.generate_stream_events(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools,
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
                loop.call_soon_threadsafe(queue.put_nowait, done)
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, done)
                raise

        # Start producer thread
        import threading

        thread = threading.Thread(target=_sync_producer, daemon=True)
        thread.start()

        while True:
            event = await queue.get()
            if event is done:
                break
            yield event

        thread.join(timeout=5.0)

    def _build_system_prompt(self, session: dict, query: str | None = None) -> str:
        """Build rich system prompt from session context."""
        from wisp.context_assembler import ContextAssembler, PromptContext

        ws = session.get("workspace", ".")
        ws_path = Path(ws).resolve()

        # Lazy-init assembler
        if self._assembler_cache is None:
            self._assembler_cache = ContextAssembler()
        assembler = self._assembler_cache

        # Check cache for static prompt — include mtimes of key context files
        # so that edits to rules.md, skills, etc. invalidate the cache.
        context_mt = 0.0
        for candidate in (
            ws_path / ".wisp" / "rules.md",
            ws_path / ".wisp" / "conventions.md",
        ):
            try:
                context_mt = max(context_mt, candidate.stat().st_mtime)
            except OSError:
                pass
        cache_key = (ws, context_mt)
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
                # Configurable max tokens for repo map (default 1200)
                max_tokens = 1200
                if self.config is not None:
                    max_tokens = getattr(self.config, "repo_map_max_tokens", 1200)
                map_text = rm.format_for_llm(max_tokens=max_tokens)
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

    async def _execute_tool(self, event: dict, session: dict, approval_handler=None) -> AsyncIterator[dict]:
        """Execute a tool call via ToolExecutor, yielding flattened events.

        Schema validation is done here as defense-in-depth.
        ToolExecutor handles permission checks, hooks, and dispatch.
        """
        name = event.get("name", "")
        args = event.get("arguments", {})
        workspace = session.get("workspace", ".")

        # ── Schema validation (defense-in-depth) ─────────────────
        schema_error = self._validate_tool_args(name, args)
        if schema_error:
            yield _flatten_event(
                tool_result_event(
                    name,
                    self._normalize_tool_result(
                        {"status": "error", "data": schema_error}
                    ),
                    duration_ms=0,
                    tool_call_id=event.get("id"),
                )
            )
            return

        if self.tool_executor is not None:
            # Wrap simple handler (event_dict -> bool) to ToolExecutor's protocol
            # (name, args, reason) -> (approved, modified_args_or_none)
            wrapped_handler = None
            if approval_handler is not None:
                async def _wrap_approval(name, args, reason):
                    approved = await approval_handler({"name": name, "arguments": args})
                    return approved, None
                wrapped_handler = _wrap_approval

            async for agent_event in self.tool_executor.execute(
                name, args, workspace,
                tool_call_id=event.get("id"),
                approval_handler=wrapped_handler,
            ):
                yield _flatten_event(agent_event)
        else:
            # Fallback: direct execution when no ToolExecutor wired
            import json
            from wisp.tools import execute_tool
            start = time.time()
            try:
                raw_result = execute_tool(name, args, workspace=workspace)
            except Exception as e:
                logger.exception("Tool execution failed: %s", name)
                raw_result = {"status": "error", "data": str(e)}
            duration_ms = (time.time() - start) * 1000
            normalized = self._normalize_tool_result(raw_result)
            yield _flatten_event(
                tool_result_event(
                    name, normalized, duration_ms=duration_ms, tool_call_id=event.get("id")
                )
            )

    def _normalize_tool_result(self, result: Any) -> dict:
        """Normalize any tool result to a standard JSON-serializable schema.

        Schema:
            {
                "status": "ok" | "error",
                "data": str | dict | list,     # human-readable or structured result
                "metadata": {                   # optional metadata
                    "tool": str,
                    "args": dict,
                    "result_length": int,
                    ...
                }
            }
        """
        import json
        from pathlib import Path

        # Already in standard schema
        if isinstance(result, dict) and "status" in result:
            # Ensure data is serializable
            data = result.get("data", "")
            return {
                "status": result["status"],
                "data": self._serialize_value(data),
                "metadata": self._serialize_value(result.get("metadata", {})),
            }

        # JSON string that contains a structured result — parse it
        if isinstance(result, str) and result.startswith("{"):
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict) and "status" in parsed:
                    return {
                        "status": parsed["status"],
                        "data": self._serialize_value(parsed.get("data", "")),
                        "metadata": self._serialize_value(parsed.get("metadata", {})),
                    }
            except json.JSONDecodeError:
                pass

        # Error tuple/list (must have exactly 2 elements, first is "error")
        if (
            isinstance(result, (list, tuple))
            and len(result) == 2
            and result[0] == "error"
        ):
            return {
                "status": "error",
                "data": str(result[1]),
                "metadata": {"raw": str(result)},
            }

        # Exception
        if isinstance(result, BaseException):
            return {
                "status": "error",
                "data": str(result),
                "metadata": {"exception_type": type(result).__name__},
            }

        # None
        if result is None:
            return {"status": "ok", "data": "", "metadata": {}}

        # Path
        if isinstance(result, Path):
            return {"status": "ok", "data": str(result), "metadata": {"is_path": True}}

        # Bytes
        if isinstance(result, bytes):
            try:
                text = result.decode("utf-8")
            except UnicodeDecodeError:
                text = result.decode("utf-8", errors="replace")
            return {"status": "ok", "data": text, "metadata": {"was_bytes": True}}

        # String
        if isinstance(result, str):
            return {"status": "ok", "data": result, "metadata": {}}

        # Dict
        if isinstance(result, dict):
            return {"status": "ok", "data": result, "metadata": {}}

        # List
        if isinstance(result, list):
            return {"status": "ok", "data": result, "metadata": {}}

        # Anything else — coerce to string
        return {
            "status": "ok",
            "data": str(result),
            "metadata": {"original_type": type(result).__name__},
        }

    def _serialize_value(self, value: Any) -> Any:
        """Serialize a value to JSON-compatible types."""
        import json
        from pathlib import Path

        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return value.decode("utf-8", errors="replace")
        if isinstance(value, dict):
            return {k: self._serialize_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._serialize_value(v) for v in value]

        # Fallback: JSON round-trip
        try:
            return json.loads(json.dumps(value, default=str))
        except (TypeError, ValueError):
            return str(value)

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
            "text",
            "name",
            "arguments",
            "result",
            "message",
            "duration_ms",
            "turns",
            "session_id",
            "summary",
            "reason",
            "level",
            "recoverable",
            "tool_call_id",
            "id",
            "calls",
        }
        for field_name in safe_fields:
            if hasattr(event, field_name):
                result[field_name] = getattr(event, field_name)

        # Map provider-specific event types to canonical types
        # (ToolCallBatch uses 'tool_calls' which we handle in turn())

        return result

    def _validate_tool_args(self, name: str, args: dict) -> Optional[str]:
        """Validate tool arguments against the registered JSON schema.

        Returns an error message string if validation fails, or None
        if the tool is not found or validation succeeds.
        """
        from wisp.tools import TOOL_SCHEMAS

        # Find the schema for this tool
        schema = None
        for ts in TOOL_SCHEMAS:
            if ts.get("function", {}).get("name") == name:
                schema = ts.get("function", {}).get("parameters", {})
                break

        if schema is None:
            return None  # Unknown tool — let security layer handle it

        try:
            import jsonschema
            jsonschema.validate(instance=args, schema=schema)
            return None
        except Exception as exc:
            return f"Schema validation failed for tool '{name}': {exc}"

    def _get_approval_gate(self) -> ApprovalGate:
        """Lazily create the approval gate from current security policy."""
        if self._approval_gate is None:
            self._approval_gate = ApprovalGate(self.security)
        return self._approval_gate

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
