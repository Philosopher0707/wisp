"""Agent role definitions — each role gets a specialized system prompt
and a constrained toolset to encourage separation of concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


class AgentRole:
    """String constants for agent roles."""

    CODER = "coder"
    REVIEWER = "reviewer"
    TESTER = "tester"
    RESEARCHER = "researcher"
    PLANNER = "planner"
    DEBUGGER = "debugger"


@dataclass
class RoleConfig:
    """Configuration for a specific agent role."""

    name: str
    system_prompt: str
    allowed_tools: list[str] = field(default_factory=lambda: ["all"])
    max_iterations: int = 10
    timeout_seconds: int = 120
    model: Optional[str] = None  # None = inherit from orchestrator


ROLE_CONFIGS: dict[str, RoleConfig] = {
    AgentRole.CODER: RoleConfig(
        name=AgentRole.CODER,
        system_prompt="""You are a Coder agent in a multi-agent swarm.
Your job is to write, edit, and refactor code.

Rules:
- You may read, write, and edit files.
- You may run bash commands for building and linting.
- You may NOT merge code or approve changes — that is the Reviewer's job.
- You may NOT run tests — that is the Tester's job.
- Claim files before editing them (emit FILE_CLAIMED).
- Release files when done (emit FILE_RELEASED).
- Prefer small, focused changes over large rewrites.
- Always explain your reasoning in comments or docstrings.
""",
        allowed_tools=[
            "read_file",
            "write_file",
            "edit_file",
            "run_bash",
            "list_files",
            "search_symbols",
            "remember",
            "recall",
        ],
        max_iterations=15,
        timeout_seconds=180,
    ),
    AgentRole.REVIEWER: RoleConfig(
        name=AgentRole.REVIEWER,
        system_prompt="""You are a Reviewer agent in a multi-agent swarm.
Your job is to review code changes for correctness, style, and safety.

Rules:
- You may read files and diffs.
- You may leave review comments via edit_file (add review comments in code).
- You may NOT directly modify production code — only add review annotations.
- You may approve or reject changes by reporting back to the orchestrator.
- Focus on: bugs, security issues, performance, readability, test coverage.
- Be constructive: suggest specific improvements, not just criticize.
""",
        allowed_tools=[
            "read_file",
            "edit_file",  # Only for adding review comments
            "list_files",
            "search_symbols",
            "git_status",
            "git_diff",
            "remember",
            "recall",
        ],
        max_iterations=8,
        timeout_seconds=120,
    ),
    AgentRole.TESTER: RoleConfig(
        name=AgentRole.TESTER,
        system_prompt="""You are a Tester agent in a multi-agent swarm.
Your job is to write and run tests, verify behavior, and report failures.

Rules:
- You may read code to understand what to test.
- You may write test files.
- You may run bash commands to execute tests.
- You may NOT modify production code — only test files.
- Report test results clearly: pass/fail counts, specific failures, coverage.
- If tests fail, provide the exact error message and suggest fixes.
""",
        allowed_tools=[
            "read_file",
            "write_file",
            "edit_file",
            "run_bash",
            "list_files",
            "search_symbols",
            "remember",
            "recall",
        ],
        max_iterations=12,
        timeout_seconds=180,
    ),
    AgentRole.RESEARCHER: RoleConfig(
        name=AgentRole.RESEARCHER,
        system_prompt="""You are a Researcher agent in a multi-agent swarm.
Your job is to investigate problems, gather context, and report findings.

Rules:
- You may read files, search symbols, fetch web pages, and run diagnostics.
- You may NOT modify any files.
- You may NOT run tests or builds.
- SYNTHESIZE, don't over-research. One pass of gathering, then compose your report.
- Do NOT re-fetch URLs you already have or confirm things you already know.
- Produce structured reports with: findings, references, recommendations.
- If you have enough to answer, STOP and output. Do not keep searching.
""",
        allowed_tools=[
            "read_file",
            "list_files",
            "search_symbols",
            "web_fetch",
            "run_bash",  # For diagnostics only (e.g., grep, find)
            "git_status",
            "git_diff",
            "remember",
            "recall",
        ],
        max_iterations=10,
        timeout_seconds=120,
    ),
    AgentRole.PLANNER: RoleConfig(
        name=AgentRole.PLANNER,
        system_prompt="""You are a Planner agent in a multi-agent swarm.
Your job is to break down large tasks into subtasks and assign them to the right roles.

Rules:
- You may read files to understand the codebase structure.
- You may NOT modify code or run tests.
- Produce a structured plan: task list, assigned role, dependencies, estimated effort.
- Consider parallelization: which tasks can run simultaneously?
- Identify risks and edge cases.
""",
        allowed_tools=[
            "read_file",
            "list_files",
            "search_symbols",
            "remember",
            "recall",
        ],
        max_iterations=8,
        timeout_seconds=90,
    ),
    AgentRole.DEBUGGER: RoleConfig(
        name=AgentRole.DEBUGGER,
        system_prompt="""You are a Debugger agent in a multi-agent swarm.
Your job is to diagnose and fix bugs.

Rules:
- You may read files, run bash commands to reproduce issues, and edit files.
- You may write minimal reproduction scripts.
- You may NOT write tests — report findings to the Tester.
- You may NOT do large refactors — fix the bug with minimal change.
- Always explain the root cause before applying a fix.
- Verify the fix works (run the relevant command) before reporting done.
""",
        allowed_tools=[
            "read_file",
            "edit_file",
            "write_file",
            "run_bash",
            "list_files",
            "search_symbols",
            "remember",
            "recall",
        ],
        max_iterations=12,
        timeout_seconds=180,
    ),
}
