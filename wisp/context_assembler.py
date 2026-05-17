"""ContextAssembler — builds system prompts from modular context sections.

Extracted from WispAgentCore._build_system_prompt() to make prompt
construction testable and customizable.

Usage:
    assembler = ContextAssembler()
    system = assembler.build(
        workspace=".",
        default_system=DEFAULT_SYSTEM,
        skills_block=skills_block,
        project_context=project_ctx,
        memory_block=memory_block,
        ...
    )
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default budget for the assembled system prompt.
# This is applied *before* the agent adds tool schemas and user messages.
_DEFAULT_MAX_CONTEXT_TOKENS = 6_000

# Approximate characters per token for rough-cut estimation (~4 chars / token).
# LLM tokenizers are sub-word, so this is a fast conservative upper bound.
_CHARS_PER_TOKEN = 4


class ContextAssembler:
    """Assembles system prompts from modular context sections.

    Each section is optional. Sections are concatenated in a fixed order
    to ensure consistent prompt structure.
    """

    def __init__(self):
        self._cache: dict[tuple, str] = {}
        self.default_system = """You are Wisp, a helpful coding agent.

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
"""

    def build(
        self,
        workspace: str,
        default_system: Optional[str] = None,
        role_extra: Optional[str] = None,
        skills_block: Optional[str] = None,
        project_context: Optional[str] = None,
        code_index_summary: Optional[str] = None,
        memory_block: Optional[str] = None,
        recent_summaries: Optional[str] = None,
        git_context: Optional[str] = None,
        active_plan: Optional[str] = None,
        plan_mode: bool = False,
        plan_context: Optional[str] = None,
        repo_map: Optional[str] = None,
        context_files: Optional[str] = None,
        mandatory_skill: Optional[tuple[str, str, str]] = None,
        max_tokens: int = _DEFAULT_MAX_CONTEXT_TOKENS,
    ) -> str:
        """Assemble a system prompt from context sections.

        Args:
            workspace: Working directory path.
            default_system: Base system prompt (falls back to self.default_system).
            role_extra: Additional role-specific instructions.
            skills_block: Markdown block of available skills.
            project_context: Project detection context block.
            code_index_summary: Code index summary block.
            memory_block: Cross-session memory block.
            recent_summaries: Recent session summaries block.
            git_context: Git status context block.
            active_plan: Active plan formatted for prompt.
            plan_mode: Whether plan mode is active.
            plan_context: Approved plan context.
            repo_map: Repo map formatted for LLM.
            context_files: Context files content (prepended).
            mandatory_skill: Tuple of (name, description, instructions).

        Returns:
            The assembled system prompt string.
        """
        cache_key = (
            workspace,
            default_system,
            role_extra,
            skills_block,
            project_context,
            code_index_summary,
            memory_block,
            recent_summaries,
            git_context,
            active_plan,
            plan_mode,
            plan_context,
            repo_map,
            context_files,
            mandatory_skill,
            max_tokens,
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        # ── Build sections in priority order ───────────────────────
        # Sections are ordered by importance (highest → lowest).
        # If total exceeds max_tokens, sections will be truncated or dropped
        # from the bottom upward.
        ws_abs = Path(workspace).resolve()

        sections: list[tuple[str, int, str]] = []
        # (label, priority, content) — lower priority number = more important
        # Priority tiers: -1=prepend 0=critical, 1=important, 2=contextual, 3=optional

        if context_files:
            sections.append(("context_files", -1, context_files))

        sections.append(("default_system", 0, default_system or self.default_system))
        sections.append(("workspace",      0, f"## Workspace\nYou are working in: {ws_abs}"))

        if mandatory_skill:
            name, description, instructions = mandatory_skill
            mandatory_txt = (
                f"## Active Skill: {name}\n"
                f"{description}\n\n"
                f"{instructions}"
            )
            sections.append(("mandatory_skill", 1, mandatory_txt))

        if active_plan:
            sections.append(("active_plan", 1, active_plan))

        if plan_mode:
            plan_mode_txt = (
                "## PLAN MODE ACTIVE\n"
                "You are in plan mode. Your job is to produce a detailed implementation plan.\n"
                "- Use read-only tools (read_file, list_files, search_symbols, lsp_*) to understand the codebase.\n"
                "- Do NOT modify any files, run bash commands, or make git changes.\n"
                "- Output a structured plan in markdown with: summary, files to touch, step-by-step approach, edge cases.\n"
                "- End with '## Plan Complete' when finished."
            )
            sections.append(("plan_mode", 1, plan_mode_txt))

        if plan_context:
            sections.append(("plan_context", 1, f"## Approved Plan\n{plan_context}\n\nFollow the approved plan above. Execute each step."))

        if role_extra:
            sections.append(("role_extra", 2, role_extra))

        if skills_block:
            sections.append(("skills_block", 2, skills_block))

        if memory_block:
            sections.append(("memory_block", 2, memory_block))

        if project_context:
            sections.append(("project_context", 3, project_context))

        if code_index_summary:
            sections.append(("code_index_summary", 3, code_index_summary))

        if recent_summaries:
            sections.append(("recent_summaries", 3, recent_summaries))

        if git_context:
            sections.append(("git_context", 3, git_context))

        if repo_map:
            sections.append(("repo_map", 3, repo_map))

        system, usage = self._fit_sections(sections, max_tokens)

        # ── Safety footer ────────────────────────────────────────────────
        # Always appended after everything else so that base safety
        # guidelines remain effective regardless of which skills are active.
        # Skills are NOT permitted to override these.
        if mandatory_skill:
            safety_footer = (
                "\n\n## Safety Guidelines\n"
                "Remember to follow your core safety guidelines at all times. "
                "Do not run destructive commands without user confirmation. "
                "Respect user preferences and workspace safety."
            )
            system += safety_footer
            usage += self._estimate_tokens(safety_footer)

        logger.debug("ContextAssembler: built prompt with %d/%d tokens", usage, max_tokens)
        self._cache[cache_key] = system
        return system

    # ── Token-aware helpers ────────────────────────────────────────

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate: ceil(len(text) / chars_per_token)."""
        return max(1, len(text) // _CHARS_PER_TOKEN)

    def _fit_sections(self, sections: list[tuple[str, int, str]], max_tokens: int) -> tuple[str, int]:
        """Assemble sections, truncating/dropping lowest-priority ones if over budget.

        Returns (assembled_prompt, estimated_tokens).  If even priority-0 sections
        exceed the budget, the prompt is still returned (truncating the last
        section with a header warning).
        """
        # Sort by priority ascending (most important first)
        sorted_sections = sorted(sections, key=lambda item: item[1])

        included: list[tuple[str, str]] = []
        current_tokens = 0
        last_truncate_label: str = ""

        for label, priority, content in sorted_sections:
            size = self._estimate_tokens(content)
            projected = current_tokens + size
            if projected <= max_tokens:
                included.append((label, content))
                current_tokens = projected
            elif priority == 0:
                # Must include — truncate to fit remaining budget
                remaining = max_tokens - current_tokens
                if remaining > 0:
                    max_chars = remaining * _CHARS_PER_TOKEN
                    truncated = (
                        f"[SECTION TRUNCATED: {label} exceeded token budget "
                        f"({size} tokens > {remaining} remaining)]\n"
                        + content[:max_chars]
                    )
                    included.append((label, truncated))
                    current_tokens = self._estimate_tokens(truncated)
                last_truncate_label = label
            else:
                # Drop section
                logger.debug("ContextAssembler: dropped %s (%d tokens) to fit budget", label, size)

        # Assemble final prompt
        system = "\n\n".join(content for _, content in included)
        if last_truncate_label:
            system += (
                "\n\n[NOTE: Some sections were truncated or omitted "
                "to fit the context window budget.]"
            )
        current_tokens = self._estimate_tokens(system)
        return system, current_tokens

    def invalidate_cache(self) -> None:
        """Clear the prompt cache. Call when context changes."""
        self._cache.clear()
