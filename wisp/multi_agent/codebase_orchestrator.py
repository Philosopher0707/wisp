"""High-level orchestrator for parallel codebase analysis and module writing.

Builds on ``SubagentOrchestrator`` to provide:

1. **Discover** — scan workspace via ``RepoMap`` to find modules/packages
2. **Analyze** — spawn researcher subagents in parallel, one per module
3. **Synthesize** — reducer aggregates findings into a coherent architecture report
4. **Plan** — planner breaks writing tasks into dependency-ordered batches
5. **Write** — coder subagents write modules in isolated worktrees, parallel where safe
6. **Review** — reviewer subagents vet each module, optionally with voting
7. **Integrate** — parent applies approved changes to the real workspace

Usage::

    from wisp.multi_agent import SubagentOrchestrator
    from wisp.multi_agent.codebase_orchestrator import CodebaseOrchestrator

    base = SubagentOrchestrator(parent_agent=agent)
    cbo = CodebaseOrchestrator(base)

    # Full pipeline: analyze + write + review + integrate
    report = await cbo.run_pipeline(
        goal="Add OAuth2 support to the auth system",
        auto_integrate=True,
    )

    # Or step-by-step:
    modules = await cbo.discover_modules()
    analysis = await cbo.analyze_modules(modules)
    written = await cbo.write_modules(analysis.write_plan)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .orchestrator import SubagentOrchestrator
from .task import SubagentContract, SubagentResult, OrchestratorEvent, EventKind
from .roles import AgentRole

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────

_DEFAULT_MODULE_TIMEOUT = 180.0          # seconds per module analysis/write
_DEFAULT_REVIEW_TIMEOUT = 120.0
_DEFAULT_MAX_MODULE_LINES = 2000         # split modules larger than this
_DEFAULT_PARALLEL_MODULES = 4
_DEFAULT_ANALYSIS_TOKEN_RATIO = 0.25     # % of global budget for analysis phase
_DEFAULT_MAX_SUBAGENT_DEPTH = 2          # safety guard


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class ModuleInfo:
    """A discovered module / package in the workspace."""

    path: str                          # relative path from workspace root
    name: str                          # module/package name
    kind: str = "module"               # "module" | "package" | "entry_point"
    language: str = "python"
    line_count: int = 0
    importance: float = 0.0            # PageRank-style score (0-1)
    dependencies: list[str] = field(default_factory=list)   # modules this imports
    dependents: list[str] = field(default_factory=list)     # modules importing this
    entry_points: list[str] = field(default_factory=list)   # public APIs / classes
    summary: str = ""


@dataclass
class ModuleAnalysis:
    """Result of analyzing a single module."""

    module: ModuleInfo
    findings: str = ""                 # free-form researcher report
    issues: list[dict] = field(default_factory=list)   # {severity, line, description}
    suggested_changes: list[dict] = field(default_factory=list)  # {type, description}
    tokens_used: int = 0
    elapsed_seconds: float = 0.0
    success: bool = False


@dataclass
class WriteTask:
    """A single writing assignment for a coder subagent."""

    module: ModuleInfo
    instruction: str = ""              # what to write / modify
    role: str = AgentRole.CODER
    dependencies_satisfied: bool = True   # False if upstream modules not yet written
    worktree_isolated: bool = True
    timeout_seconds: float = _DEFAULT_MODULE_TIMEOUT
    max_iterations: int = 15
    context_files: list[str] = field(default_factory=list)
    output_schema: Optional[dict] = None


@dataclass
class WriteResult:
    """Result of a write task."""

    task: WriteTask
    code_output: str = ""              # the generated / modified code
    files_changed: list[str] = field(default_factory=list)
    review_passed: bool = False
    review_feedback: str = ""
    tokens_used: int = 0
    elapsed_seconds: float = 0.0
    success: bool = False
    worktree_path: Optional[str] = None   # for manual inspection


@dataclass
class CodebaseReport:
    """Final aggregated report from a full pipeline run."""

    goal: str = ""
    modules_discovered: list[ModuleInfo] = field(default_factory=list)
    modules_analyzed: list[ModuleAnalysis] = field(default_factory=list)
    modules_written: list[WriteResult] = field(default_factory=list)
    integration_log: list[str] = field(default_factory=list)
    total_tokens_used: int = 0
    total_elapsed_seconds: float = 0.0
    success: bool = False
    errors: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render as a human-readable Markdown summary."""
        lines = [
            f"# Codebase Pipeline Report: {self.goal}",
            "",
            f"- **Modules discovered:** {len(self.modules_discovered)}",
            f"- **Modules analyzed:** {len(self.modules_analyzed)}",
            f"- **Modules written:** {len(self.modules_written)}",
            f"- **Total tokens:** {self.total_tokens_used:,}",
            f"- **Total time:** {self.total_elapsed_seconds:.1f}s",
            f"- **Success:** {'✅' if self.success else '❌'}",
            "",
            "## Analysis Findings",
            "",
        ]
        for ma in self.modules_analyzed:
            status = "✅" if ma.success else "❌"
            lines.append(f"### {status} `{ma.module.path}`")
            lines.append(ma.findings[:800] + ("…" if len(ma.findings) > 800 else ""))
            lines.append("")
        lines.append("## Written Modules")
        lines.append("")
        for wr in self.modules_written:
            status = "✅" if wr.success and wr.review_passed else "❌"
            lines.append(
                f"- {status} `{wr.task.module.path}` — "
                f"{len(wr.files_changed)} files, {wr.tokens_used:,} tokens"
            )
        if self.errors:
            lines.append("")
            lines.append("## Errors")
            lines.append("")
            for e in self.errors:
                lines.append(f"- ❌ {e}")
        return "\n".join(lines)


# ── Orchestrator ──────────────────────────────────────────────────────────


class CodebaseOrchestrator:
    """High-level orchestrator for parallel codebase work.

    Wraps a ``SubagentOrchestrator`` and adds domain-specific helpers for
    discovering modules, analyzing them in parallel, planning writes with
    dependency ordering, and integrating results back into the workspace.
    """

    def __init__(
        self,
        orchestrator: SubagentOrchestrator,
        *,
        max_parallel: int = _DEFAULT_PARALLEL_MODULES,
        analysis_token_ratio: float = _DEFAULT_ANALYSIS_TOKEN_RATIO,
        auto_review: bool = True,
        auto_test: bool = False,
        integration_strategy: str = "git_apply",  # "git_apply" | "copy" | "none"
    ):
        self.orch = orchestrator
        self.max_parallel = max_parallel
        self.analysis_token_ratio = analysis_token_ratio
        self.auto_review = auto_review
        self.auto_test = auto_test
        self.integration_strategy = integration_strategy
        self._workspace = orchestrator.workspace or Path.cwd().resolve()

    # ── Discovery ───────────────────────────────────────────────────────

    async def discover_modules(
        self,
        paths: Optional[list[str]] = None,
        languages: Optional[list[str]] = None,
        max_files: int = 500,
    ) -> list[ModuleInfo]:
        """Discover modules/packages in the workspace.

        Uses ``RepoMap`` for dependency-aware indexing, then filters to
        the requested languages and paths.

        Args:
            paths: Specific relative paths to limit discovery. None = all.
            languages: e.g. ["python", "rust"]. None = all supported.
            max_files: Cap on files to scan (RepoMap default).

        Returns:
            List of ``ModuleInfo`` sorted by importance (descending).
        """
        from wisp.repo_map import RepoMap

        rm = RepoMap(self._workspace, max_entries=max_files)
        entries = rm.build(use_cache=True, fast_mode=True)

        modules: list[ModuleInfo] = []
        seen_paths: set[str] = set()

        for e in entries:
            if e.path in seen_paths:
                continue
            seen_paths.add(e.path)

            # Filter by explicit paths
            if paths and not any(e.path.startswith(p) for p in paths):
                continue

            # Language detection from extension
            ext = Path(e.path).suffix.lower()
            lang_map = {
                ".py": "python", ".pyi": "python",
                ".rs": "rust",
                ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
                ".go": "go",
                ".java": "java", ".kt": "kotlin",
            }
            lang = lang_map.get(ext, "unknown")
            if languages and lang not in languages:
                continue

            # Build dependency/dependent lists from repo map
            deps = sorted(rm.get_dependencies(e.path)) if hasattr(rm, "get_dependencies") else []
            rev_deps = sorted(rm.get_dependents(e.path)) if hasattr(rm, "get_dependents") else []

            mi = ModuleInfo(
                path=e.path,
                name=Path(e.path).stem,
                kind=e.kind if e.kind in ("module", "package", "entry_point") else "module",
                language=lang,
                line_count=e.line,
                importance=e.importance,
                dependencies=deps,
                dependents=rev_deps,
                entry_points=[e.signature] if e.signature else [],
                summary=e.summary,
            )
            modules.append(mi)

        # Sort by importance descending
        modules.sort(key=lambda m: m.importance, reverse=True)
        logger.info("Discovered %d modules in workspace", len(modules))
        return modules

    # ── Analysis ────────────────────────────────────────────────────────

    async def analyze_modules(
        self,
        modules: list[ModuleInfo],
        analysis_prompt: Optional[str] = None,
        progress_callback: Optional[Callable[[OrchestratorEvent], None]] = None,
    ) -> list[ModuleAnalysis]:
        """Analyze modules in parallel with researcher subagents.

        Each module gets a researcher subagent that investigates its
        structure, issues, and needed changes. Results are aggregated
        and also stored in shared context for downstream coders.

        Args:
            modules: Modules to analyze (usually from ``discover_modules``).
            analysis_prompt: Extra instructions for the researcher.
            progress_callback: Optional real-time progress hook.

        Returns:
            List of ``ModuleAnalysis`` (same order as input).
        """
        if not modules:
            return []

        token_budget = self.orch.get_token_budget_remaining()
        if token_budget is not None:
            analysis_budget = int(token_budget * self.analysis_token_ratio)
            self.orch.set_global_token_budget(analysis_budget)

        default_prompt = (
            "Analyze this module thoroughly. Report:\n"
            "1. Current structure and responsibilities\n"
            "2. Bugs, security issues, or code smells\n"
            "3. Missing features or incomplete implementations\n"
            "4. Recommended changes with specific line references\n"
            "Be concise but specific. Use the tool calls to inspect imports, "
            "read the full file if needed, and search for related symbols."
        )
        prompt = analysis_prompt or default_prompt

        # Build contracts — one researcher per module
        contracts: list[SubagentContract] = []
        for mi in modules:
            full_task = (
                f"Analyze module: `{mi.path}`\n\n"
                f"Language: {mi.language}\n"
                f"Estimated lines: {mi.line_count}\n"
                f"Importance score: {mi.importance:.2f}\n"
                f"Dependencies: {', '.join(mi.dependencies[:10]) or 'none'}\n"
                f"Dependents: {', '.join(mi.dependents[:10]) or 'none'}\n\n"
                f"{prompt}"
            )
            contracts.append(
                SubagentContract(
                    name=f"research-{mi.name}",
                    role=AgentRole.RESEARCHER,
                    task=full_task,
                    context_files=[mi.path],
                    timeout_seconds=_DEFAULT_MODULE_TIMEOUT,
                    max_iterations=10,
                    output_format="json" if self.auto_review else "text",
                    worktree_isolated=False,   # researchers only read
                    progress_callback=progress_callback,
                )
            )

        results: list[SubagentResult] = await self.orch.run_parallel(
            contracts, max_concurrent=self.max_parallel, adaptive=True
        )

        analyses: list[ModuleAnalysis] = []
        for mi, res in zip(modules, results):
            ma = ModuleAnalysis(
                module=mi,
                findings=res.output,
                success=res.success,
                tokens_used=res.tokens_used,
                elapsed_seconds=res.elapsed_seconds,
            )
            # Try to parse structured issues from JSON output
            if res.validated_output and isinstance(res.validated_output, dict):
                ma.issues = res.validated_output.get("issues", [])
                ma.suggested_changes = res.validated_output.get("suggested_changes", [])
            analyses.append(ma)

            # Publish to shared context so coders can read it
            await self.orch.set_shared(f"analysis:{mi.path}", {
                "findings": ma.findings,
                "issues": ma.issues,
                "suggested_changes": ma.suggested_changes,
                "success": ma.success,
            })

        logger.info(
            "Analysis complete: %d/%d modules succeeded",
            sum(1 for a in analyses if a.success),
            len(analyses),
        )
        return analyses

    # ── Planning ──────────────────────────────────────────────────────────

    def plan_writes(
        self,
        analyses: list[ModuleAnalysis],
        goal: str,
        respect_dependencies: bool = True,
    ) -> list[list[WriteTask]]:
        """Build dependency-ordered batches of write tasks.

        Uses a simple topological sort: modules with no unsatisfied
        dependencies go in batch 0, then batch 1, etc. This lets safe
        modules be written in parallel while preserving import order.

        Args:
            analyses: Analyzed modules (only successful ones are considered).
            goal: The high-level instruction driving what to write.
            respect_dependencies: If False, all modules go in batch 0.

        Returns:
            List of batches, each batch is a list of ``WriteTask`` objects
            that can be executed in parallel.
        """
        successful = [a for a in analyses if a.success]
        if not successful:
            return []

        # Build a planner prompt to get structured task breakdown
        # We do this synchronously via a single planner subagent call
        # For simplicity, we'll do a local topological sort + heuristic

        tasks: list[WriteTask] = []
        for a in successful:
            # Build instruction from suggested changes + goal
            changes = "\n".join(
                f"- [{c.get('type', 'change')}] {c.get('description', '')}"
                for c in a.suggested_changes[:5]
            ) or "Implement the required changes based on the goal."

            instruction = (
                f"Goal: {goal}\n\n"
                f"Module: `{a.module.path}`\n"
                f"{changes}\n\n"
                f"Write or modify this module accordingly. "
                f"Preserve existing behavior unless explicitly asked to change it."
            )

            tasks.append(
                WriteTask(
                    module=a.module,
                    instruction=instruction,
                    context_files=[a.module.path] + a.module.dependencies[:5],
                    timeout_seconds=_DEFAULT_MODULE_TIMEOUT,
                )
            )

        if not respect_dependencies:
            return [tasks]

        # Topological sort by dependency name (path)
        task_by_path: dict[str, WriteTask] = {t.module.path: t for t in tasks}
        written: set[str] = set()
        batches: list[list[WriteTask]] = []
        remaining: list[WriteTask] = list(tasks)

        while remaining:
            batch = [
                t for t in remaining
                if all(d in written or d not in task_by_path for d in t.module.dependencies)
            ]
            if not batch:
                # Cycle detected — break it by forcing the first remaining
                batch = [remaining.pop(0)]
            else:
                for t in batch:
                    remaining.remove(t)

            for t in batch:
                t.dependencies_satisfied = True
                written.add(t.module.path)
            batches.append(batch)

        logger.info(
            "Planned %d write tasks in %d dependency-ordered batches",
            len(tasks), len(batches),
        )
        return batches

    # ── Writing ─────────────────────────────────────────────────────────

    async def write_modules(
        self,
        batches: list[list[WriteTask]],
        progress_callback: Optional[Callable[[OrchestratorEvent], None]] = None,
    ) -> list[WriteResult]:
        """Execute write tasks batch-by-batch.

        Each batch runs in parallel; batches wait for all prior batches.
        After each module is written, optionally runs review (and test).

        Args:
            batches: Dependency-ordered batches from ``plan_writes``.
            progress_callback: Optional real-time progress hook.

        Returns:
            Flat list of ``WriteResult`` (order follows batch order).
        """
        all_results: list[WriteResult] = []

        for batch_idx, batch in enumerate(batches):
            logger.info("Write batch %d/%d: %d modules", batch_idx + 1, len(batches), len(batch))

            # Build coder contracts
            contracts: list[SubagentContract] = []
            for wt in batch:
                # Pull shared analysis context into task
                shared_key = f"analysis:{wt.module.path}"
                analysis_ctx = await self.orch.get_shared(shared_key, default={})
                extra = ""
                if analysis_ctx:
                    extra = (
                        f"\n\n## Prior Analysis\n"
                        f"{analysis_ctx.get('findings', '')[:600]}"
                    )

                contracts.append(
                    SubagentContract(
                        name=f"write-{wt.module.name}",
                        role=wt.role,
                        task=wt.instruction + extra,
                        context_files=wt.context_files,
                        timeout_seconds=wt.timeout_seconds,
                        max_iterations=wt.max_iterations,
                        worktree_isolated=wt.worktree_isolated,
                        output_format="text",
                        progress_callback=progress_callback,
                    )
                )

            # Run coders in parallel
            coder_results: list[SubagentResult] = await self.orch.run_parallel(
                contracts, max_concurrent=self.max_parallel, adaptive=True
            )

            # Optional review phase
            if self.auto_review:
                review_contracts: list[SubagentContract] = []
                review_map: list[int] = []  # index into coder_results
                for idx, (wt, cr) in enumerate(zip(batch, coder_results)):
                    if not cr.success:
                        continue
                    review_task = (
                        f"Review the code written for `{wt.module.path}`.\n\n"
                        f"Original goal: {wt.instruction[:300]}…\n\n"
                        f"Code output:\n```\n{cr.output[:2000]}\n```\n\n"
                        "Check: correctness, style, security, test coverage, "
                        "and whether it fulfills the original goal. "
                        "Respond with PASS or FAIL and concise reasoning."
                    )
                    review_contracts.append(
                        SubagentContract(
                            name=f"review-{wt.module.name}",
                            role=AgentRole.REVIEWER,
                            task=review_task,
                            timeout_seconds=_DEFAULT_REVIEW_TIMEOUT,
                            max_iterations=6,
                            worktree_isolated=False,
                        )
                    )
                    review_map.append(idx)

                if review_contracts:
                    review_results = await self.orch.run_parallel(
                        review_contracts, max_concurrent=self.max_parallel, adaptive=True
                    )
                    # Store review outcomes
                    for ridx, batch_idx_inner in enumerate(review_map):
                        batch[batch_idx_inner]._review_result = review_results[ridx]

            # Build WriteResult objects
            for wt, cr in zip(batch, coder_results):
                wr = WriteResult(
                    task=wt,
                    code_output=cr.output,
                    files_changed=cr.files_changed,
                    tokens_used=cr.tokens_used,
                    elapsed_seconds=cr.elapsed_seconds,
                    success=cr.success,
                )
                # Attach review if present
                review_res = getattr(wt, "_review_result", None)
                if review_res:
                    rr: SubagentResult = review_res
                    wr.review_passed = rr.success and "PASS" in rr.output.upper()
                    wr.review_feedback = rr.output
                all_results.append(wr)

                # Publish to shared context for downstream modules
                await self.orch.set_shared(f"written:{wt.module.path}", {
                    "code": wr.code_output,
                    "files_changed": wr.files_changed,
                    "review_passed": wr.review_passed,
                    "success": wr.success,
                })

        return all_results

    # ── Integration ─────────────────────────────────────────────────────

    async def integrate_results(
        self,
        results: list[WriteResult],
        strategy: Optional[str] = None,
    ) -> list[str]:
        """Apply approved write results back to the real workspace.

        Strategies:
        - ``git_apply`` — create a patch from worktree diff and ``git apply``
        - ``copy`` — copy changed files directly from worktree
        - ``none`` — skip integration (user must apply manually)

        Args:
            results: Write results (optionally filtered to review-passed).
            strategy: Override the default integration strategy.

        Returns:
            List of log messages describing what was done.
        """
        strategy = strategy or self.integration_strategy
        log: list[str] = []

        if strategy == "none":
            log.append("Integration skipped (strategy='none').")
            return log

        # Filter to successful + review-passed (or all if review disabled)
        to_integrate = [
            r for r in results
            if r.success and (r.review_passed or not self.auto_review)
        ]

        if not to_integrate:
            log.append("No results passed review; nothing to integrate.")
            return log

        for wr in to_integrate:
            if strategy == "copy":
                log.extend(self._integrate_copy(wr))
            elif strategy == "git_apply":
                log.extend(self._integrate_git_apply(wr))
            else:
                log.append(f"Unknown strategy '{strategy}' for {wr.task.module.path}")

        return log

    def _integrate_copy(self, wr: WriteResult) -> list[str]:
        """Copy files from worktree back to real workspace."""
        log: list[str] = []
        if not wr.worktree_path:
            log.append(f"No worktree path for {wr.task.module.path}; skipped.")
            return log

        wt = Path(wr.worktree_path)
        ws = self._workspace
        for rel_path in wr.files_changed:
            src = wt / rel_path
            dst = ws / rel_path
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
                log.append(f"Copied {rel_path}")
            else:
                log.append(f"Missing {rel_path} in worktree")
        return log

    def _integrate_git_apply(self, wr: WriteResult) -> list[str]:
        """Create and apply a git patch from the worktree."""
        log: list[str] = []
        if not wr.worktree_path:
            log.append(f"No worktree path for {wr.task.module.path}; skipped.")
            return log

        wt = Path(wr.worktree_path)
        try:
            # Generate diff against original workspace
            diff = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=str(wt),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if diff.returncode != 0 or not diff.stdout.strip():
                log.append(f"No diff for {wr.task.module.path}")
                return log

            # Apply to real workspace
            apply = subprocess.run(
                ["git", "apply", "--check"],
                cwd=str(self._workspace),
                input=diff.stdout,
                capture_output=True,
                text=True,
            )
            if apply.returncode != 0:
                log.append(f"Patch dry-run failed for {wr.task.module.path}: {apply.stderr}")
                return log

            apply = subprocess.run(
                ["git", "apply"],
                cwd=str(self._workspace),
                input=diff.stdout,
                capture_output=True,
                text=True,
            )
            if apply.returncode == 0:
                log.append(f"Applied patch for {wr.task.module.path}")
            else:
                log.append(f"git apply failed: {apply.stderr}")
        except Exception as exc:
            log.append(f"Integration error for {wr.task.module.path}: {exc}")
        return log

    # ── Full pipeline ───────────────────────────────────────────────────

    async def run_pipeline(
        self,
        goal: str,
        *,
        paths: Optional[list[str]] = None,
        languages: Optional[list[str]] = None,
        analysis_prompt: Optional[str] = None,
        auto_integrate: bool = True,
        respect_dependencies: bool = True,
        progress_callback: Optional[Callable[[OrchestratorEvent], None]] = None,
    ) -> CodebaseReport:
        """Run the full discover → analyze → plan → write → review → integrate pipeline.

        This is the primary entry point for high-level codebase work.

        Args:
            goal: High-level instruction, e.g. "Add OAuth2 to auth system".
            paths: Limit discovery to these relative paths.
            languages: Limit to these languages.
            analysis_prompt: Custom prompt for the researcher phase.
            auto_integrate: If True, apply approved changes to workspace.
            respect_dependencies: If True, write modules in dependency order.
            progress_callback: Real-time progress events.

        Returns:
            ``CodebaseReport`` with full audit trail.
        """
        start = time.monotonic()
        report = CodebaseReport(goal=goal)

        try:
            # Phase 1: Discover
            modules = await self.discover_modules(paths=paths, languages=languages)
            report.modules_discovered = modules
            if progress_callback:
                progress_callback(OrchestratorEvent(
                    task_id="pipeline",
                    event_type=EventKind.TASK_PROGRESS,
                    payload={"phase": "discover", "modules_found": len(modules)},
                ))

            # Phase 2: Analyze
            analyses = await self.analyze_modules(
                modules, analysis_prompt=analysis_prompt, progress_callback=progress_callback
            )
            report.modules_analyzed = analyses
            if progress_callback:
                progress_callback(OrchestratorEvent(
                    task_id="pipeline",
                    event_type=EventKind.TASK_PROGRESS,
                    payload={"phase": "analyze", "modules_analyzed": len(analyses)},
                ))

            # Phase 3: Plan
            batches = self.plan_writes(analyses, goal, respect_dependencies=respect_dependencies)

            # Phase 4: Write (+ optional review)
            written = await self.write_modules(batches, progress_callback=progress_callback)
            report.modules_written = written

            # Phase 5: Integrate
            if auto_integrate:
                log = await self.integrate_results(written)
                report.integration_log = log

            report.success = all(
                wr.success and (wr.review_passed or not self.auto_review)
                for wr in written
            ) if written else True

        except Exception as exc:
            report.success = False
            report.errors.append(str(exc))
            logger.exception("Pipeline failed")

        report.total_elapsed_seconds = time.monotonic() - start
        report.total_tokens_used = self.orch.get_tokens_consumed()
        return report

    # ── Utility ─────────────────────────────────────────────────────────

    def get_report(self) -> Optional[CodebaseReport]:
        """Return the most recent pipeline report, if any."""
        # Future: store last report as instance state
        return None  # type: ignore[return-value]
