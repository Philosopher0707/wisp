"""Unit tests for CodebaseOrchestrator.

These tests mock the SubagentOrchestrator to avoid spawning real subagents.
We verify: discovery, analysis batching, topological planning, write batching,
integration strategies, and the full pipeline orchestration.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wisp.multi_agent.codebase_orchestrator import (
    CodebaseOrchestrator,
    ModuleInfo,
    ModuleAnalysis,
    WriteTask,
    WriteResult,
    CodebaseReport,
)
from wisp.multi_agent.task import SubagentContract, SubagentResult


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def mock_orchestrator():
    """A mock SubagentOrchestrator with async methods."""
    mock = MagicMock()
    mock.workspace = Path(tempfile.mkdtemp())
    mock.get_token_budget_remaining = MagicMock(return_value=10_000)
    mock.set_global_token_budget = MagicMock()
    mock.get_tokens_consumed = MagicMock(return_value=0)
    mock.run_parallel = AsyncMock(return_value=[])
    mock.get_shared = AsyncMock(return_value={})
    mock.set_shared = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def sample_modules() -> list[ModuleInfo]:
    return [
        ModuleInfo(
            path="src/auth.py",
            name="auth",
            language="python",
            importance=0.9,
            dependencies=["src/models.py"],
        ),
        ModuleInfo(
            path="src/models.py",
            name="models",
            language="python",
            importance=0.7,
            dependencies=[],
        ),
        ModuleInfo(
            path="src/api.py",
            name="api",
            language="python",
            importance=0.8,
            dependencies=["src/auth.py", "src/models.py"],
        ),
    ]


# ── ModuleInfo / data class tests ───────────────────────────────────────


def test_module_info_dataclass():
    m = ModuleInfo(path="src/x.py", name="x", language="python")
    assert m.path == "src/x.py"
    assert m.dependencies == []


def test_module_analysis_defaults():
    ma = ModuleAnalysis(module=ModuleInfo(path="a.py", name="a"))
    assert ma.success is False
    assert ma.findings == ""


def test_write_result_defaults():
    wr = WriteResult(task=WriteTask(module=ModuleInfo(path="a.py", name="a")))
    assert wr.review_passed is False
    assert wr.success is False


# ── CodebaseOrchestrator construction ───────────────────────────────────


def test_constructor(mock_orchestrator):
    cbo = CodebaseOrchestrator(mock_orchestrator, max_parallel=8)
    assert cbo.max_parallel == 8
    assert cbo.integration_strategy == "git_apply"


# ── Discovery ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_modules_uses_repo_map(mock_orchestrator):
    cbo = CodebaseOrchestrator(mock_orchestrator)

    fake_entries = [
        MagicMock(path="src/a.py", kind="module", importance=0.8, line=50, summary="", signature="def f()"),
        MagicMock(path="src/b.py", kind="module", importance=0.6, line=30, summary="", signature=""),
    ]

    with patch("wisp.repo_map.RepoMap") as MockRM:
        inst = MockRM.return_value
        inst.build.return_value = fake_entries
        inst.get_dependencies = MagicMock(return_value=set())
        inst.get_dependents = MagicMock(return_value=set())

        mods = await cbo.discover_modules()

    assert len(mods) == 2
    assert mods[0].path == "src/a.py"
    assert mods[0].importance == 0.8
    assert mods[0].language == "python"


@pytest.mark.asyncio
async def test_discover_filters_by_language(mock_orchestrator):
    cbo = CodebaseOrchestrator(mock_orchestrator)

    fake_entries = [
        MagicMock(path="src/a.py", kind="module", importance=0.8, line=10, summary="", signature=""),
        MagicMock(path="src/b.rs", kind="module", importance=0.7, line=10, summary="", signature=""),
    ]

    with patch("wisp.repo_map.RepoMap") as MockRM:
        inst = MockRM.return_value
        inst.build.return_value = fake_entries
        inst.get_dependencies = MagicMock(return_value=set())
        inst.get_dependents = MagicMock(return_value=set())

        mods = await cbo.discover_modules(languages=["python"])

    assert len(mods) == 1
    assert mods[0].path == "src/a.py"


@pytest.mark.asyncio
async def test_discover_filters_by_path(mock_orchestrator):
    cbo = CodebaseOrchestrator(mock_orchestrator)

    fake_entries = [
        MagicMock(path="src/a.py", kind="module", importance=0.8, line=10, summary="", signature=""),
        MagicMock(path="tests/test_a.py", kind="module", importance=0.3, line=10, summary="", signature=""),
    ]

    with patch("wisp.repo_map.RepoMap") as MockRM:
        inst = MockRM.return_value
        inst.build.return_value = fake_entries
        inst.get_dependencies = MagicMock(return_value=set())
        inst.get_dependents = MagicMock(return_value=set())

        mods = await cbo.discover_modules(paths=["src/"])

    assert len(mods) == 1
    assert mods[0].path == "src/a.py"


@pytest.mark.asyncio
async def test_discover_empty_workspace(mock_orchestrator):
    cbo = CodebaseOrchestrator(mock_orchestrator)
    with patch("wisp.repo_map.RepoMap") as MockRM:
        inst = MockRM.return_value
        inst.build.return_value = []
        mods = await cbo.discover_modules()
    assert mods == []


# ── Analysis ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_modules_spawns_researchers(mock_orchestrator, sample_modules):
    cbo = CodebaseOrchestrator(mock_orchestrator, max_parallel=4)

    fake_results = [
        SubagentResult(success=True, output="ok", tokens_used=100, elapsed_seconds=1.0),
        SubagentResult(success=True, output="fine", tokens_used=120, elapsed_seconds=1.2),
        SubagentResult(success=False, output="", error="timeout", tokens_used=50),
    ]
    mock_orchestrator.run_parallel = AsyncMock(return_value=fake_results)

    analyses = await cbo.analyze_modules(sample_modules)

    assert len(analyses) == 3
    assert analyses[0].success is True
    assert analyses[2].success is False

    # Verify contracts passed to run_parallel
    contracts = mock_orchestrator.run_parallel.call_args[0][0]
    assert len(contracts) == 3
    assert contracts[0].role == "researcher"
    assert "src/auth.py" in contracts[0].task


@pytest.mark.asyncio
async def test_analyze_modules_publishes_shared_context(mock_orchestrator, sample_modules):
    cbo = CodebaseOrchestrator(mock_orchestrator)

    fake_results = [
        SubagentResult(success=True, output="finding", tokens_used=100),
    ]
    mock_orchestrator.run_parallel = AsyncMock(return_value=fake_results)

    await cbo.analyze_modules(sample_modules[:1])

    mock_orchestrator.set_shared.assert_called_once()
    key = mock_orchestrator.set_shared.call_args[0][0]
    assert key == "analysis:src/auth.py"


@pytest.mark.asyncio
async def test_analyze_modules_empty_input(mock_orchestrator):
    cbo = CodebaseOrchestrator(mock_orchestrator)
    result = await cbo.analyze_modules([])
    assert result == []
    mock_orchestrator.run_parallel.assert_not_called()


# ── Planning ────────────────────────────────────────────────────────────


def test_plan_writes_respects_dependencies(mock_orchestrator, sample_modules):
    cbo = CodebaseOrchestrator(mock_orchestrator)

    analyses = [
        ModuleAnalysis(module=m, success=True, suggested_changes=[{"type": "add", "description": "x"}])
        for m in sample_modules
    ]

    batches = cbo.plan_writes(analyses, goal="Add feature")

    # models.py has no deps → batch 0
    # auth.py depends on models.py → batch 1
    # api.py depends on auth.py and models.py → batch 2
    assert len(batches) == 3
    assert batches[0][0].module.path == "src/models.py"
    assert batches[1][0].module.path == "src/auth.py"
    assert batches[2][0].module.path == "src/api.py"


def test_plan_writes_no_dependencies(mock_orchestrator, sample_modules):
    cbo = CodebaseOrchestrator(mock_orchestrator)

    analyses = [
        ModuleAnalysis(module=m, success=True, suggested_changes=[])
        for m in sample_modules
    ]

    batches = cbo.plan_writes(analyses, goal="Refactor", respect_dependencies=False)

    assert len(batches) == 1
    assert len(batches[0]) == 3


def test_plan_writes_empty_analyses(mock_orchestrator):
    cbo = CodebaseOrchestrator(mock_orchestrator)
    batches = cbo.plan_writes([], goal="X")
    assert batches == []


def test_plan_writes_skips_failed(mock_orchestrator, sample_modules):
    cbo = CodebaseOrchestrator(mock_orchestrator)

    analyses = [
        ModuleAnalysis(module=sample_modules[0], success=False),
        ModuleAnalysis(module=sample_modules[1], success=True, suggested_changes=[]),
    ]

    batches = cbo.plan_writes(analyses, goal="Fix")
    assert len(batches) == 1
    assert len(batches[0]) == 1
    assert batches[0][0].module.path == "src/models.py"


# ── Writing ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_modules_executes_batches(mock_orchestrator, sample_modules):
    cbo = CodebaseOrchestrator(mock_orchestrator, auto_review=False)

    batch = [
        WriteTask(module=sample_modules[0], instruction="write auth"),
        WriteTask(module=sample_modules[1], instruction="write models"),
    ]

    fake_results = [
        SubagentResult(success=True, output="code1", files_changed=["src/auth.py"], tokens_used=200),
        SubagentResult(success=True, output="code2", files_changed=["src/models.py"], tokens_used=150),
    ]
    mock_orchestrator.run_parallel = AsyncMock(return_value=fake_results)

    results = await cbo.write_modules([batch])

    assert len(results) == 2
    assert results[0].success is True
    assert results[0].files_changed == ["src/auth.py"]
    assert results[0].review_passed is False  # auto_review=False


@pytest.mark.asyncio
async def test_write_modules_with_review(mock_orchestrator, sample_modules):
    cbo = CodebaseOrchestrator(mock_orchestrator, auto_review=True)

    batch = [WriteTask(module=sample_modules[0], instruction="write auth")]

    # First call: coder, second call: reviewer
    coder_result = SubagentResult(success=True, output="code1", tokens_used=100)
    review_result = SubagentResult(success=True, output="PASS: looks good", tokens_used=50)

    async def side_effect(contracts, **kwargs):
        # First parallel call = coders, second = reviewers
        if any(c.role == "reviewer" for c in contracts):
            return [review_result]
        return [coder_result]

    mock_orchestrator.run_parallel = AsyncMock(side_effect=side_effect)

    results = await cbo.write_modules([batch])

    assert len(results) == 1
    assert results[0].review_passed is True
    assert "PASS" in results[0].review_feedback


@pytest.mark.asyncio
async def test_write_modules_publishes_shared_context(mock_orchestrator, sample_modules):
    cbo = CodebaseOrchestrator(mock_orchestrator, auto_review=False)

    batch = [WriteTask(module=sample_modules[0], instruction="write auth")]
    mock_orchestrator.run_parallel = AsyncMock(return_value=[
        SubagentResult(success=True, output="code", files_changed=["src/auth.py"]),
    ])

    await cbo.write_modules([batch])

    mock_orchestrator.set_shared.assert_called()
    key = mock_orchestrator.set_shared.call_args[0][0]
    assert key == "written:src/auth.py"


# ── Integration ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_integrate_none_strategy(mock_orchestrator):
    cbo = CodebaseOrchestrator(mock_orchestrator, integration_strategy="none")
    wr = WriteResult(task=WriteTask(module=ModuleInfo(path="a.py", name="a")), success=True)
    log = await cbo.integrate_results([wr])
    assert "Integration skipped" in log[0]


@pytest.mark.asyncio
async def test_integrate_filters_failed(mock_orchestrator):
    cbo = CodebaseOrchestrator(mock_orchestrator, auto_review=True)
    results = [
        WriteResult(
            task=WriteTask(module=ModuleInfo(path="a.py", name="a")),
            success=True,
            review_passed=False,
        ),
    ]
    log = await cbo.integrate_results(results)
    assert "No results passed review" in log[0]


# ── Full pipeline ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_pipeline_end_to_end(mock_orchestrator):
    cbo = CodebaseOrchestrator(mock_orchestrator, max_parallel=4, auto_review=False)

    fake_modules = [
        MagicMock(path="src/a.py", kind="module", importance=0.8, line=10, summary="", signature=""),
    ]

    with patch("wisp.repo_map.RepoMap") as MockRM:
        inst = MockRM.return_value
        inst.build.return_value = fake_modules
        inst.get_dependencies = MagicMock(return_value=set())
        inst.get_dependents = MagicMock(return_value=set())

        fake_analysis = SubagentResult(success=True, output="fine", tokens_used=50)
        fake_write = SubagentResult(success=True, output="code", files_changed=["src/a.py"], tokens_used=100)

        call_count = [0]

        async def pipeline_side_effect(contracts, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [fake_analysis]
            return [fake_write]

        mock_orchestrator.run_parallel = AsyncMock(side_effect=pipeline_side_effect)

        report = await cbo.run_pipeline(goal="Add feature", auto_integrate=False)

    assert isinstance(report, CodebaseReport)
    assert report.goal == "Add feature"
    assert len(report.modules_discovered) == 1
    assert len(report.modules_analyzed) == 1
    assert len(report.modules_written) == 1
    assert report.modules_written[0].success is True


@pytest.mark.asyncio
async def test_run_pipeline_with_integration(mock_orchestrator):
    cbo = CodebaseOrchestrator(
        mock_orchestrator, max_parallel=4, auto_review=False, integration_strategy="none"
    )

    fake_modules = [
        MagicMock(path="src/a.py", kind="module", importance=0.8, line=10, summary="", signature=""),
    ]

    with patch("wisp.repo_map.RepoMap") as MockRM:
        inst = MockRM.return_value
        inst.build.return_value = fake_modules
        inst.get_dependencies = MagicMock(return_value=set())
        inst.get_dependents = MagicMock(return_value=set())

        async def pipeline_side_effect(contracts, **kwargs):
            return [SubagentResult(success=True, output="ok", files_changed=["src/a.py"])]

        mock_orchestrator.run_parallel = AsyncMock(side_effect=pipeline_side_effect)

        report = await cbo.run_pipeline(goal="Refactor", auto_integrate=True)

    assert report.integration_log
    assert "Integration skipped" in report.integration_log[0]


# ── Report formatting ────────────────────────────────────────────────────


def test_codebase_report_markdown():
    report = CodebaseReport(
        goal="Test",
        modules_analyzed=[
            ModuleAnalysis(
                module=ModuleInfo(path="src/a.py", name="a"),
                findings="Found a bug",
                success=True,
            )
        ],
        modules_written=[
            WriteResult(
                task=WriteTask(module=ModuleInfo(path="src/a.py", name="a")),
                success=True,
                review_passed=True,
                files_changed=["src/a.py"],
                tokens_used=150,
            )
        ],
        success=True,
    )
    md = report.to_markdown()
    assert "Test" in md
    assert "src/a.py" in md
    assert "Found a bug" in md


def test_codebase_report_markdown_with_errors():
    report = CodebaseReport(
        goal="Test",
        errors=["Something broke"],
        success=False,
    )
    md = report.to_markdown()
    assert "Something broke" in md
    assert "❌" in md


# ── Topological sort edge cases ─────────────────────────────────────────


def test_plan_writes_cycle_detection(mock_orchestrator):
    cbo = CodebaseOrchestrator(mock_orchestrator)

    # Circular dependency: A -> B -> C -> A
    modules = [
        ModuleInfo(path="a.py", name="a", dependencies=["c.py"]),
        ModuleInfo(path="b.py", name="b", dependencies=["a.py"]),
        ModuleInfo(path="c.py", name="c", dependencies=["b.py"]),
    ]
    analyses = [ModuleAnalysis(module=m, success=True, suggested_changes=[]) for m in modules]

    batches = cbo.plan_writes(analyses, goal="fix", respect_dependencies=True)

    # Should not infinite loop; at least 1 batch per module
    total = sum(len(b) for b in batches)
    assert total == 3


# ── Token budget ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analysis_respects_token_budget(mock_orchestrator):
    mock_orchestrator.get_token_budget_remaining = MagicMock(return_value=1000)
    cbo = CodebaseOrchestrator(mock_orchestrator, analysis_token_ratio=0.3)

    fake_modules = [
        MagicMock(path="src/a.py", kind="module", importance=0.5, line=10, summary="", signature=""),
    ]
    with patch("wisp.repo_map.RepoMap") as MockRM:
        inst = MockRM.return_value
        inst.build.return_value = fake_modules
        inst.get_dependencies = MagicMock(return_value=set())
        inst.get_dependents = MagicMock(return_value=set())

        mock_orchestrator.run_parallel = AsyncMock(return_value=[
            SubagentResult(success=True, output="ok"),
        ])

        await cbo.analyze_modules([ModuleInfo(path="src/a.py", name="a")])

    mock_orchestrator.set_global_token_budget.assert_called_once_with(300)
