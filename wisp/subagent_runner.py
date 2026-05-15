"""Parallel multi-agent orchestration system for Wisp.

Provides SubagentRunner — an async orchestrator that spawns multiple WispAgentCore
instances in parallel, each optionally running in an isolated git worktree, with
concurrency control, timeouts, and structured result aggregation.

Usage:
    from wisp.subagent_runner import SubagentRunner, SubagentSpec
    from wisp.config import WispConfig

    config = WispConfig()
    runner = SubagentRunner(config, Path.cwd())

    specs = [
        runner.security_auditor(["src/auth.py", "src/api.py"]),
        runner.test_writer(["src/auth.py"]),
    ]

    results = await runner.run_parallel(specs, max_concurrent=2)
    summary = runner.format_results_for_llm(results)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from wisp.config import WispConfig
from wisp.session import Session, SessionManager

logger = logging.getLogger(__name__)

# ── Environment-driven defaults ────────────────────────────────────────

_WISP_SUBAGENT_MAX_CONCURRENT = int(
    os.environ.get("WISP_SUBAGENT_MAX_CONCURRENT", "4")
)
_WISP_SUBAGENT_TIMEOUT = float(
    os.environ.get("WISP_SUBAGENT_TIMEOUT", "300")
)
_WISP_KEEP_WORKTREES = (
    os.environ.get("WISP_KEEP_WORKTREES", "false").lower() == "true"
)

_WORKTREES_DIR_NAME = ".wisp/worktrees"


# ── Data types ─────────────────────────────────────────────────────────


@dataclass
class SubagentSpec:
    """Specification for a single subagent to be spawned."""

    name: str
    """Agent name, e.g.  "security-auditor", "test-writer"."""

    prompt: str
    """Task description / instruction for the subagent."""

    model: str | None = None
    """Model override.  None = inherit from parent config."""

    tools: list[str] = field(default_factory=list)
    """Allowed tool names.  Empty list = all tools."""

    system_prompt: str = ""
    """Specific system prompt for this subagent.  If empty, a short default
    is built from the name and prompt."""

    worktree_isolated: bool = True
    """Run in an isolated git worktree.  When False the subagent shares the
    workspace but with its own session isolation."""

    context_files: list[str] = field(default_factory=list)
    """Specific file paths to mention in the subagent's context."""


# Unified type available at wisp.multi_agent.task.SubagentResult
# This local definition kept for backward compatibility — prefer the unified type for new code.

@dataclass
class SubagentResult:
    """Structured output from a subagent run.

    For new code, prefer wisp.multi_agent.task.SubagentResult which
    is the unified type shared across all multi-agent systems.
    """

    spec: SubagentSpec
    """The spec that produced this result."""

    success: bool
    """True if the subagent completed within budget and timeout."""

    output: str
    """Final message content returned by the subagent."""

    tool_calls: list[dict] = field(default_factory=list)
    """Summary of tool calls made (name + arg preview per call)."""

    duration_seconds: float = 0.0
    """Wall-clock time consumed."""

    error: str | None = None
    """Exception message if the subagent crashed."""

    session_id: str = ""
    """Session ID persisted to the session store for audit."""

    files_changed: list[str] = field(default_factory=list)
    """File paths the subagent reported modifying."""


# ── Runner ─────────────────────────────────────────────────────────────


class SubagentRunner:
    """Async parallel multi-agent orchestrator.

    Spawns WispAgentCore instances concurrently, each with its own session,
    optional git worktree isolation, and a hard timeout.  Results are
    collected and formatted for consumption by a parent agent.
    """

    def __init__(self, config: WispConfig, workspace: Path):
        """Initialise the runner.

        Parameters
        ----------
        config:
            Parent WispConfig (model, provider settings, etc.).
        workspace:
            Path to the repository root.  Worktrees are created underneath
            ``<workspace>/.wisp/worktrees/``.
        """
        self.config = config
        self.workspace = workspace.resolve()
        self._worktrees_root = self.workspace / _WORKTREES_DIR_NAME
        self._session_mgr = SessionManager()

    # ── Public API ─────────────────────────────────────────────────────

    async def run_parallel(
        self,
        specs: list[SubagentSpec],
        max_concurrent: int = _WISP_SUBAGENT_MAX_CONCURRENT,
        parent_session_id: str | None = None,
    ) -> list[SubagentResult]:
        """Run multiple subagent specs concurrently.

        Parameters
        ----------
        specs:
            Subagent specifications to execute.
        max_concurrent:
            Maximum number of subagents running at once (semaphore).
        parent_session_id:
            Optional parent session ID for traceability.

        Returns
        -------
        list[SubagentResult]
            One result per spec, in the same order as ``specs``.
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _guarded(spec: SubagentSpec) -> SubagentResult:
            async with semaphore:
                return await self.run_single(spec)

        tasks = [asyncio.create_task(_guarded(s)) for s in specs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        resolved: list[SubagentResult] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                spec = specs[i]
                resolved.append(
                    SubagentResult(
                        spec=spec,
                        success=False,
                        output="",
                        duration_seconds=0.0,
                        error=f"Unhandled gather exception: {result}",
                        session_id="",
                    )
                )
                logger.error(
                    "Subagent %s crashed during gather: %s", spec.name, result
                )
            else:
                resolved.append(result)

        logger.info(
            "Parallel run complete: %d/%d succeeded",
            sum(1 for r in resolved if r.success),
            len(resolved),
        )
        return resolved

    async def run_single(self, spec: SubagentSpec) -> SubagentResult:
        """Run a single subagent and return its result.

        Handles worktree creation, agent instantiation, timeout enforcement,
        and error capture.  Never raises — failures are returned as
        ``SubagentResult(success=False, error=...)``.
        """
        start = time.monotonic()
        worktree_path: Path | None = None
        session: Session | None = None
        tool_calls_log: list[dict] = []

        # ── Resolve workspace ──────────────────────────────────────────
        if spec.worktree_isolated:
            try:
                worktree_path = await self.create_worktree(spec.name)
            except Exception as exc:
                logger.warning(
                    "Worktree creation failed for %s, falling back to shared "
                    "workspace: %s",
                    spec.name,
                    exc,
                )
                worktree_path = None

        agent_workspace = str(worktree_path or self.workspace)

        # ── Build child config ─────────────────────────────────────────
        child_cfg = self._build_child_config(spec)

        # ── Create session ─────────────────────────────────────────────
        session = Session.create(
            model=child_cfg.model,
            workspace=agent_workspace,
            first_prompt=spec.prompt,
        )
        session.title = f"[sub] {spec.name}"

        # ── Build system prompt ────────────────────────────────────────
        system = spec.system_prompt or self._default_system_prompt(spec)

        # ── Spawn & run with timeout ──────────────────────────────────
        try:
            result = await asyncio.wait_for(
                self._run_agent(
                    spec=spec,
                    config=child_cfg,
                    session=session,
                    system_prompt=system,
                    workspace_path=agent_workspace,
                    tool_calls_log=tool_calls_log,
                ),
                timeout=self._timeout,
            )

            session.touch()
            self._session_mgr.save(session)

            duration = time.monotonic() - start
            return SubagentResult(
                spec=spec,
                success=result["success"],
                output=result["output"],
                tool_calls=list(tool_calls_log),
                duration_seconds=duration,
                error=result.get("error"),
                session_id=session.id,
                files_changed=result.get("files_changed", []),
            )

        except asyncio.TimeoutError:
            duration = time.monotonic() - start
            logger.warning(
                "Subagent %s timed out after %.1fs", spec.name, duration
            )
            session.touch()
            self._session_mgr.save(session)
            return SubagentResult(
                spec=spec,
                success=False,
                output=f"[TIMED OUT after {duration:.1f}s]",
                tool_calls=list(tool_calls_log),
                duration_seconds=duration,
                error=f"Timeout after {self._timeout}s",
                session_id=session.id,
            )

        except Exception as exc:
            duration = time.monotonic() - start
            logger.error(
                "Subagent %s crashed: %s", spec.name, exc, exc_info=True
            )
            if session:
                session.touch()
                self._session_mgr.save(session)
            return SubagentResult(
                spec=spec,
                success=False,
                output="",
                tool_calls=list(tool_calls_log),
                duration_seconds=duration,
                error=str(exc),
                session_id=session.id if session else "",
            )

        finally:
            # ── Cleanup worktree (unless debugging) ──────────────────
            if worktree_path and not _WISP_KEEP_WORKTREES:
                try:
                    await self.cleanup_worktree(worktree_path)
                except Exception as exc:
                    logger.warning(
                        "Failed to clean up worktree %s: %s", worktree_path, exc
                    )

    # ── Worktree management ────────────────────────────────────────────

    async def create_worktree(self, agent_name: str) -> Path:
        """Create an isolated git worktree for a subagent.

        Worktree path: ``.wisp/worktrees/{agent_name}-{uuid[:8]}``
        Branch: ``wisp-subagent/{agent_name}-{timestamp}``

        Returns the resolved path to the new worktree.
        """
        self._worktrees_root.mkdir(parents=True, exist_ok=True)

        short_id = uuid.uuid4().hex[:8]
        ts = int(time.time())
        safe_name = agent_name.replace("/", "-").replace(" ", "-")
        dir_name = f"{safe_name}-{short_id}"
        branch_name = f"wisp-subagent/{safe_name}-{ts}"

        worktree_path = (self._worktrees_root / dir_name).resolve()

        logger.info(
            "Creating worktree: path=%s branch=%s", worktree_path, branch_name
        )

        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "add",
            str(worktree_path),
            "-b",
            branch_name,
            cwd=str(self.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"git worktree add failed (exit {proc.returncode}): {err_text}"
            )

        logger.debug(
            "Worktree created: %s (branch=%s)",
            worktree_path,
            branch_name,
        )
        return worktree_path

    async def cleanup_worktree(self, worktree_path: Path):
        """Remove a worktree and prune the associated branch."""
        logger.info("Cleaning up worktree: %s", worktree_path)

        # Remove the worktree
        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "remove",
            str(worktree_path),
            "--force",
            cwd=str(self.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(
                "git worktree remove failed (exit %d): %s",
                proc.returncode,
                err_text,
            )
            # Fallback: manual directory removal
            if worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)
                logger.debug("Manually removed worktree directory: %s", worktree_path)

        # Prune the worktree metadata
        try:
            prune_proc = await asyncio.create_subprocess_exec(
                "git",
                "worktree",
                "prune",
                cwd=str(self.workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await prune_proc.communicate()
        except Exception as exc:
            logger.debug("Worktree prune failed (non-critical): %s", exc)

        logger.debug("Worktree cleanup complete: %s", worktree_path)

    # ── Result formatting ──────────────────────────────────────────────

    def format_results_for_llm(self, results: list[SubagentResult]) -> str:
        """Format subagent results as a compact summary block for a parent LLM.

        Parameters
        ----------
        results:
            Results from ``run_parallel``.

        Returns
        -------
        str
            A markdown-formatted summary suitable for injecting into the
            parent agent's context.
        """
        if not results:
            return "_(No subagent results.)_"

        lines: list[str] = []
        lines.append("## Subagent Results Summary")
        lines.append("")

        succeeded = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        lines.append(
            f"**{len(succeeded)} succeeded, {len(failed)} failed** "
            f"(out of {len(results)} total)"
        )
        lines.append("")

        for r in results:
            status = "SUCCESS" if r.success else "FAILED"
            lines.append(f"### [{status}] {r.spec.name}")
            lines.append(f"- Duration: {r.duration_seconds:.1f}s")
            if r.error:
                lines.append(f"- Error: {r.error}")
            if r.files_changed:
                files = ", ".join(f"`{f}`" for f in r.files_changed[:10])
                lines.append(f"- Files changed: {files}")
            if r.tool_calls:
                lines.append(
                    f"- Tool calls: {len(r.tool_calls)} "
                    f"({', '.join(tc.get('name', '?') for tc in r.tool_calls[:10])})"
                )

            # Include a concise excerpt of the output
            excerpt = r.output.strip()
            if excerpt:
                if len(excerpt) > 400:
                    excerpt = excerpt[:400] + "..."
                lines.append("")
                lines.append("```")
                lines.append(excerpt)
                lines.append("```")
            lines.append("")

        return "\n".join(lines)

    # ── Built-in template factories ───────────────────────────────────

    @staticmethod
    def security_auditor(target_files: list[str]) -> SubagentSpec:
        """Subagent that audits files for security vulnerabilities.

        Parameters
        ----------
        target_files:
            File paths to audit.
        """
        f_list = "\n".join(f"  - {f}" for f in target_files)
        return SubagentSpec(
            name="security-auditor",
            prompt=(
                "Audit the following files for security vulnerabilities. "
                "Check for:\n"
                "1. Injection risks (SQL, command, path traversal)\n"
                "2. Hardcoded secrets or credentials\n"
                "3. Insecure cryptography or hashing\n"
                "4. Missing input validation\n"
                "5. Unsafe deserialization\n"
                "6. Race conditions (TOCTOU)\n"
                "7. Improper error handling leaking internals\n\n"
                "Files to audit:\n"
                f"{f_list}\n\n"
                "For each finding, report the file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), "
                "and a one-sentence remediation. End with a summary count by severity."
            ),
            system_prompt=(
                "You are a security auditor. Your job is to find vulnerabilities in code. "
                "Be thorough but avoid false positives. Read each file carefully before "
                "reporting. Rank findings by severity. CRITICAL = exploitable remotely, "
                "HIGH = data loss or privilege escalation, MEDIUM = defense-in-depth gaps, "
                "LOW = hardening opportunities."
            ),
            context_files=target_files,
        )

    @staticmethod
    def test_writer(source_files: list[str]) -> SubagentSpec:
        """Subagent that writes tests for given source files.

        Parameters
        ----------
        source_files:
            Source file paths to write tests for.
        """
        f_list = "\n".join(f"  - {f}" for f in source_files)
        return SubagentSpec(
            name="test-writer",
            prompt=(
                "Write comprehensive tests for the following source files. "
                "For each file:\n"
                "1. Read the source to understand the public API\n"
                "2. Write unit tests covering: happy path, edge cases, error conditions, boundary values\n"
                "3. Aim for >80% branch coverage\n"
                "4. Use the project's existing test framework and conventions\n"
                "5. Place tests in the appropriate test directory\n\n"
                "Source files:\n"
                f"{f_list}\n\n"
                "After writing tests, run them to verify they pass. "
                "If tests fail, debug and fix before finishing."
            ),
            system_prompt=(
                "You are a test engineer. Write thorough, maintainable tests. "
                "Read existing tests first to match conventions. "
                "Every public function/class should have at least one test. "
                "Include both positive and negative test cases. "
                "Use fixtures/mocks where appropriate. "
                "Make sure tests can run independently."
            ),
            context_files=source_files,
        )

    @staticmethod
    def code_reviewer(files_or_commits: list[str]) -> SubagentSpec:
        """Subagent that reviews code for bugs and style issues.

        Parameters
        ----------
        files_or_commits:
            File paths or commit SHAs to review.
        """
        items = "\n".join(f"  - {f}" for f in files_or_commits)
        return SubagentSpec(
            name="code-reviewer",
            prompt=(
                "Review the following code for bugs, style issues, and design problems. "
                "For each item, check:\n"
                "1. Logic errors and potential bugs\n"
                "2. Code style and naming consistency\n"
                "3. Design patterns and architecture\n"
                "4. Performance concerns\n"
                "5. Error handling completeness\n"
                "6. Documentation and comments quality\n"
                "7. Test coverage gaps\n\n"
                "Items to review:\n"
                f"{items}\n\n"
                "For each finding, report the location, severity, and suggested fix. "
                "End with an overall assessment (approve / approve with comments / request changes)."
            ),
            system_prompt=(
                "You are a senior code reviewer. Be constructive and specific. "
                "Catch real bugs, not just style nits. "
                "Suggest concrete improvements with code examples where helpful. "
                "Prioritize findings by severity: correctness > security > performance > style."
            ),
            context_files=files_or_commits,
        )

    @staticmethod
    def doc_writer(module_path: str) -> SubagentSpec:
        """Subagent that generates documentation for a module.

        Parameters
        ----------
        module_path:
            Path to the module or package to document.
        """
        return SubagentSpec(
            name="doc-writer",
            prompt=(
                f"Generate comprehensive documentation for the module at `{module_path}`. "
                "Steps:\n"
                "1. Read the module source to understand its API\n"
                "2. Generate or update docstrings for all public functions, classes, and methods\n"
                "3. Create or update a README.md if needed\n"
                "4. Include: module overview, installation, quick-start example, API reference, "
                "and any configuration or environment variables\n"
                "5. Ensure all code examples are runnable and correct\n"
                "6. Check existing docs for consistency\n\n"
                "Module: `{module_path}`"
            ),
            system_prompt=(
                "You are a technical writer. Write clear, accurate documentation. "
                "Use the project's existing documentation style. "
                "Every public API element must be documented. "
                "Include practical code examples. "
                "Document parameters, return types, and raised exceptions. "
                "Keep explanations concise but complete."
            ),
            context_files=[module_path],
        )

    # ── Internal helpers ───────────────────────────────────────────────

    @property
    def _timeout(self) -> float:
        return _WISP_SUBAGENT_TIMEOUT

    def _build_child_config(self, spec: SubagentSpec) -> WispConfig:
        """Clone the parent config with optional per-subagent overrides."""
        child = WispConfig()
        child.model = spec.model or self.config.model
        child.workspace = str(self.workspace)
        child.auto_approve = self.config.auto_approve
        child.show_thinking = self.config.show_thinking
        child.chars_per_token = self.config.chars_per_token
        child.ollama_url = self.config.ollama_url
        child.temperature = self.config.temperature
        child.max_context_tokens = self.config.max_context_tokens
        child._context_tokens_explicit = self.config._context_tokens_explicit
        child.permission_mode = self.config.permission_mode
        child.max_iterations = self.config.max_iterations
        return child

    def _default_system_prompt(self, spec: SubagentSpec) -> str:
        """Build a concise default system prompt when none is provided."""
        parts = [
            f"You are a specialist subagent: **{spec.name}**.",
            "You have tools to read, write, and edit files, run bash commands, "
            "list directories, and fetch URLs.",
            "",
            "## Rules",
            "1. Focus ONLY on your assigned task.",
            "2. Work efficiently — you have a time budget.",
            "3. When done, provide a clear summary of what you did.",
            "4. If you edit files, list the changed paths.",
            "5. If stuck, explain what blocked you and stop.",
        ]

        if spec.tools:
            parts.append("")
            parts.append("## Allowed Tools")
            parts.append(", ".join(spec.tools))

        if spec.context_files:
            parts.append("")
            parts.append("## Context Files")
            for f in spec.context_files:
                parts.append(f"- {f}")

        return "\n".join(parts)

    async def _run_agent(
        self,
        spec: SubagentSpec,
        config: WispConfig,
        session: Session,
        system_prompt: str,
        workspace_path: str,
        tool_calls_log: list[dict],
    ) -> dict:
        """Run a WispAgentCore instance synchronously inside an async task.

        Uses ``run_task`` (the non-interactive API) to drive the agent loop.
        Tool calls are intercepted and logged for the result summary.
        """
        from wisp.core.agent import WispAgentCore

        agent = WispAgentCore(
            config=config,
            session=session,
            role=f"subagent:{spec.name}",
        )

        try:
            # Override the workspace in the config so tool execution resolves
            # paths relative to the worktree (or shared workspace).
            agent.config.workspace = workspace_path

            # Apply tool filtering if specified
            if spec.tools:
                agent._allowed_tools = set(spec.tools)

            # ── Run the task non-interactively ────────────────────────────
            max_iter = config.max_iterations
            timeout_per_task = self._timeout

            task_result = await agent.run_task(
                task_description=spec.prompt,
                workspace=workspace_path,
                max_iterations=max_iter,
                timeout_seconds=timeout_per_task,
                system_prompt=system,
            )

            # Collect tool call summaries from the agent's message history
            for msg in agent.messages:
                tcs = msg.get("tool_calls", []) or []
                for tc in tcs:
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    args = func.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            import json
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                    if not isinstance(args, dict):
                        args = {}
                    # Build a compact arg preview
                    arg_preview = self._compact_args(args)
                    tool_calls_log.append({"name": name, "args_preview": arg_preview})

            # Extract files changed from the final output (best-effort)
            files_changed: list[str] = []
            output_text = task_result.get("output", "") or ""
            if output_text:
                files_changed = self._extract_files_changed(output_text)

            return {
                "success": task_result.get("success", False),
                "output": output_text,
                "error": None if task_result.get("success") else task_result.get("output"),
                "files_changed": files_changed,
            }
        finally:
            agent.close()

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
        """Best-effort extraction of file paths mentioned in output text."""
        import re
        patterns = [
            # Markdown code backtick paths
            r"`([a-zA-Z0-9_\-./]+\.(?:py|ts|js|rs|go|java|rb|sh))`",
            # Changed files / modified files section headers followed by paths
            r"(?:changed|modified|touched|files written|created files?)[:\-]\s*\n?\s*[-*]\s+([^\s,]+)",
            # Bare paths in output
            r"\b([a-zA-Z0-9_\-/]+\.(?:py|ts|js|rs|go|java|rb|sh))\b",
        ]
        found: list[str] = []
        seen: set[str] = set()
        for pat in patterns:
            for m in re.finditer(pat, text, re.IGNORECASE):
                path = m.group(1).strip()
                if path not in seen and len(path) > 2:
                    seen.add(path)
                    found.append(path)
        return found[:20]  # cap to avoid noise
