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

from pathlib import Path
from typing import Any, Optional


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
        ontology_result: Optional[dict[str, str]] = None,
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
    ) -> str:
        """Assemble a system prompt from context sections.

        Args:
            workspace: Working directory path.
            default_system: Base system prompt (falls back to self.default_system).
            role_extra: Additional role-specific instructions.
            skills_block: Markdown block of available skills.
            ontology_result: Dict with 'name' and 'context' keys.
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
            ontology_result and ontology_result.get("name"),
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
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        system = default_system or self.default_system
        ws_abs = Path(workspace).resolve()

        # ── Base sections ──────────────────────────────────────────
        system += f"\n\n## Workspace\nYou are working in: {ws_abs}"

        if role_extra:
            system += f"\n\n{role_extra}"

        if skills_block:
            system += f"\n\n{skills_block}"

        if ontology_result:
            system += f"\n\n## {ontology_result['name']}\n{ontology_result['context']}"

        if project_context:
            system += f"\n\n{project_context}"

        if code_index_summary:
            system += f"\n\n{code_index_summary}"

        if memory_block:
            system += f"\n\n{memory_block}"

        if recent_summaries:
            system += f"\n\n{recent_summaries}"

        if git_context:
            system += f"\n\n{git_context}"

        if active_plan:
            system += f"\n\n{active_plan}"

        if plan_mode:
            system += (
                "\n\n## PLAN MODE ACTIVE\n"
                "You are in plan mode. Your job is to produce a detailed implementation plan.\n"
                "- Use read-only tools (read_file, list_files, search_symbols, lsp_*) to understand the codebase.\n"
                "- Do NOT modify any files, run bash commands, or make git changes.\n"
                "- Output a structured plan in markdown with: summary, files to touch, step-by-step approach, edge cases.\n"
                "- End with '## Plan Complete' when finished."
            )

        if plan_context:
            system += f"\n\n## Approved Plan\n{plan_context}\n\nFollow the approved plan above. Execute each step."

        if repo_map:
            system += f"\n\n{repo_map}"

        # ── Context files (prepended if provided) ──────────────────
        if context_files:
            system = context_files + "\n\n" + system

        # ── Mandatory skill (appended LAST for recency bias) ─────
        if mandatory_skill:
            name, description, instructions = mandatory_skill
            system += "\n\n"
            system += "==============================\n"
            system += f"MANDATORY Mode: {name}\n"
            system += "==============================\n"
            system += "\n"
            system += "These rules override ALL earlier instructions. You MUST follow them.\n"
            system += "Do NOT ask for confirmation — execute immediately.\n"
            system += "\n"
            system += description + "\n\n"
            system += instructions

        self._cache[cache_key] = system
        return system

    def invalidate_cache(self) -> None:
        """Clear the prompt cache. Call when context changes."""
        self._cache.clear()
