"""WispAgentCore — stateless turn engine.

Replaces: the stateful WispAgentCore in wisp/core/agent.py.
All state is injected or passed as parameters.

Design:
  - Receives session dict, prompt, and dependencies
  - Builds system prompt from context (rules.md, skills, repo map, etc.)
  - Streams events from provider
  - Parses tool calls, checks security, executes via extensions
  - Yields events for the transport to consume
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


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
        """Run one turn, yielding events."""
        # Build messages list
        messages = list(session.get("messages", []))
        messages.append({"role": "user", "content": prompt})

        # Build system prompt with full context awareness
        system_prompt = self._build_system_prompt(session, query=prompt)

        # Get tools from extensions
        tools = []
        if self.extensions is not None:
            try:
                tools = self.extensions.tools()
            except Exception as e:
                logger.warning("Failed to get tools from extensions: %s", e)

        # Stream events from provider
        for event in self.provider.generate_stream_events(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools if tools else None,
        ):
            # Normalize event
            normalized = self._normalize_event(event)

            # Check security for tool calls
            if normalized.get("type") == "tool_call" and self.security is not None:
                action = self._make_action(normalized)
                context = self._make_context(session)
                try:
                    decision = self.security.check(action, context)
                    if not decision.allowed:
                        yield {
                            "type": "error",
                            "message": f"Blocked ({decision.reason}): READ_ONLY mode",
                            "recoverable": True,
                        }
                        continue
                except Exception as e:
                    logger.warning("Security check failed: %s", e)

            # Check extensions for tool calls
            if normalized.get("type") == "tool_call" and self.extensions is not None:
                try:
                    ext_result = self.extensions.intercept(normalized)
                    if ext_result.get("action") == "block":
                        yield {
                            "type": "error",
                            "message": f"Blocked: {ext_result.get('reason', 'by extension')}",
                            "recoverable": True,
                        }
                        continue
                except Exception as e:
                    logger.warning("Extension intercept failed: %s", e)

            yield normalized

    def _build_system_prompt(self, session: dict, query: str | None = None) -> str:
        """Build rich system prompt from session context.

        Loads .wisp/rules.md, discovers skills, builds repo map,
        and assembles everything via ContextAssembler — just like
        the legacy stateful WispAgentCore.
        """
        from wisp.context_assembler import ContextAssembler, PromptContext

        ws = session.get("workspace", ".")
        ws_path = Path(ws).resolve()

        # Lazy-init assembler
        if self._assembler_cache is None:
            self._assembler_cache = ContextAssembler()
        assembler = self._assembler_cache

        # Check cache for static prompt (workspace + skills + project context)
        cache_key = (ws,)
        static_prompt = self._static_prompt_cache.get(cache_key)

        if static_prompt is None:
            # Gather context sections
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

            # Build static prompt via assembler
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
            self._static_prompt_cache[cache_key] = static_prompt

        # Add query-specific context
        if query:
            # Add relevant files hint if repo map is available
            relevant = self._get_relevant_files(ws, query)
            if relevant:
                static_prompt += f"\n\n## Files Relevant to Query\n{relevant}\n"

        # Add compaction notice
        if session.get("compaction_history"):
            count = len(session["compaction_history"])
            static_prompt += f"\n[Session compacted {count} times.]\n"

        return static_prompt

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

    def _normalize_event(self, event: Any) -> dict:
        """Normalize provider event to standard format."""
        if isinstance(event, dict):
            return dict(event)
        # Handle StreamEvent objects (TokenBatch, Checkpoint, StreamComplete, etc.)
        # which use 'phase' instead of 'type'
        result: dict[str, Any] = {}
        if hasattr(event, "type"):
            result["type"] = event.type
        elif hasattr(event, "phase"):
            result["type"] = event.phase
        else:
            result["type"] = "unknown"
        if hasattr(event, "__dict__"):
            result.update(event.__dict__)
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
