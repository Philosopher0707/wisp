"""Protocol tests — Pydantic IPC schemas for subagent fanout/fanin."""

from __future__ import annotations

import pytest


def test_task_frame_rejects_empty_task():
    from pydantic import ValidationError

    from wisp.core.subagent.protocol import TaskFrame

    with pytest.raises(ValidationError):
        TaskFrame(task_id="t1", task="", role="explorer")


def test_task_frame_enforces_token_budget():
    from pydantic import ValidationError

    from wisp.core.subagent.protocol import TaskFrame

    with pytest.raises(ValidationError):
        TaskFrame(task_id="t1", task="go", role="explorer", token_budget=0)


def test_task_frame_serializes_context_chunks():
    from wisp.core.subagent.protocol import ContextChunk, TaskFrame

    frame = TaskFrame(
        task_id="t1",
        task="audit auth",
        role="auditor",
        allowed_tools=["read_file", "search_codebase"],
        context=[ContextChunk(path="auth.py", content="def login(): ...", line_start=1, line_end=12)],
        token_budget=4000,
    )
    data = frame.model_dump()
    assert data["context"][0]["path"] == "auth.py"
    assert frame.estimated_tokens() > 0


def test_result_schema_status_enum():
    from pydantic import ValidationError

    from wisp.core.subagent.protocol import SubagentResult, TaskStatus

    assert TaskStatus.SUCCESS.value == "SUCCESS"
    with pytest.raises(ValidationError):
        SubagentResult(task_id="t1", status="MAYBE", findings=[], token_usage={"prompt": 1, "completion": 1})


def test_result_rejects_freeform_markdown_without_findings():
    from wisp.core.subagent.protocol import Finding, SubagentResult, TaskStatus, TokenUsage

    ok = SubagentResult(
        task_id="t1", status=TaskStatus.SUCCESS,
        findings=[Finding(kind="note", summary="all clear", path="a.py", line_start=1, line_end=2)],
        token_usage=TokenUsage(prompt=10, completion=5),
    )
    assert ok.findings[0].anchor() == "a.py:1-2"


def test_patch_conflict_detection():
    from wisp.core.subagent.protocol import PatchProposal, patches_conflict

    a = PatchProposal(path="x.py", line_start=10, line_end=20, replacement="aaa")
    b = PatchProposal(path="x.py", line_start=15, line_end=25, replacement="bbb")
    c = PatchProposal(path="x.py", line_start=30, line_end=40, replacement="ccc")
    assert patches_conflict(a, b) is True
    assert patches_conflict(a, c) is False
    assert patches_conflict(a, PatchProposal(path="other.py", line_start=10, line_end=20, replacement="x")) is False


def test_token_usage_totals():
    from wisp.core.subagent.protocol import TokenUsage

    assert TokenUsage(prompt=100, completion=50).total == 150
