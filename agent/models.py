"""Pydantic schemas for the reporting pipeline.

Dual contract: validated JSON at .agent/audit_summary.json for downstream
graph nodes + rich markdown/tables for human stdout.

File-anchor rule: every finding must carry `path:line` or `path:start-end`
so downstream patches can `rg` without regex scraping.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── File-anchor pattern ──────────────────────────────────────────
# Requires explicit line numbers: src/system.rs:42  or  src/system.rs:42-89
# Bare paths and struct-only mentions fail validation.
_FILE_ANCHOR_RE = re.compile(
    r"""
    ^                           # start
    [^\s:]+\.[a-zA-Z0-9]+        # path with extension (no spaces, at least one dot)
    :                           # colon separator
    \d+                         # start line
    (?:-\d+)?                   # optional -end
    (?:\:\d+(?:-\d+)?)?          # optional second anchor for multi-file ranges
    $                           # end
    """,
    re.VERBOSE,
)

# Allowlist for severity — matches spec P0/P1/P2 with human labels
Severity = Literal["P0", "P1", "P2"]


class SubagentState(BaseModel):
    """Live telemetry for one fanout worker.

    Mirrors BackgroundAgentEntry wisp/multi_agent/background.py:39
    but enriched for rich.live rendering.
    """

    worker_id: str = Field(..., description="e.g. bg-e2fb0a69")
    role: str = Field(default="coder", description="coder/researcher/planner/etc")
    focus: str = Field(default="", description="Target module, e.g. Analyzing Architecture")
    activity: str = Field(default="", description="Current tool, e.g. Reading src/system.rs:42-89")
    elapsed_s: float = Field(default=0.0, ge=0.0, description="Wall-clock seconds since launch")
    tokens_used: int = Field(default=0, ge=0)
    cost_usd: Optional[float] = Field(default=None, ge=0.0)
    status: Literal["running", "completed", "failed", "cancelled"] = "running"
    progress: Optional[str] = Field(default=None, description="Optional 0-100% or spinner phase")

    @field_validator("worker_id")
    @classmethod
    def _id_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("worker_id must be non-empty")
        return v

    model_config = {"extra": "allow"}


class AuditFinding(BaseModel):
    """Typed engineering issue — replaces unstructured 'Gaps identified'."""

    severity: Severity = Field(..., description="P0 Critical / P1 High / P2 Tech Debt")
    file_anchor: str = Field(
        ...,
        description="Path with explicit line refs, e.g. src/system.rs:42-89",
        examples=["src/system.rs:42-89", "src/events/mod.rs:15", "src/state/metrics.rs:88-102"],
    )
    issue_summary: str = Field(..., min_length=10, description="Root cause, not bare symbol")
    remediation: str = Field(..., min_length=10, description="Concrete snippet or strategy")
    source_subagent: Optional[str] = Field(default=None, description="Originating worker_id")
    tags: List[str] = Field(default_factory=list)

    @field_validator("file_anchor")
    @classmethod
    def _anchor_must_have_lines(cls, v: str) -> str:
        v = v.strip()
        if not _FILE_ANCHOR_RE.match(v):
            raise ValueError(
                f"file_anchor must be 'path:line' or 'path:start-end' (got {v!r}); "
                "bare paths like 'src/system.rs' are rejected — add :42-89"
            )
        return v

    @field_validator("issue_summary", "remediation")
    @classmethod
    def _not_bare_path(cls, v: str) -> str:
        v = v.strip()
        if len(v.split()) < 3:
            raise ValueError("issue_summary/remediation must be descriptive, not a bare path/symbol")
        return v

    model_config = {"extra": "forbid"}


class CoverageEntry(BaseModel):
    path: str
    tested: bool
    note: Optional[str] = None


class CodebaseAnalysisReport(BaseModel):
    """Top-level dual-output contract persisted at .agent/audit_summary.json."""

    title: str = Field(default="Codebase Audit — aether-tui (Rust)")
    generated_at: str = Field(..., description="ISO8601")
    file_map: Dict[str, str] = Field(default_factory=dict, description="path -> role/summary")
    issue_matrix: List[AuditFinding] = Field(default_factory=list)
    coverage_ledger: List[CoverageEntry] = Field(default_factory=list)
    subagent_states: List[SubagentState] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict, description="tokens, elapsed, cost rollup")
    version: str = Field(default="1.0")

    @field_validator("issue_matrix")
    @classmethod
    def _unique_anchors(cls, v: List[AuditFinding]) -> List[AuditFinding]:
        seen = set()
        for f in v:
            key = (f.file_anchor, f.issue_summary[:60])
            if key in seen:
                raise ValueError(f"duplicate finding for {f.file_anchor}")
            seen.add(key)
        return v

    @model_validator(mode="after")
    def _must_have_anchors_if_findings(self) -> "CodebaseAnalysisReport":
        # No bare-findings-without-ledger — ensures debt audit is explicit
        if self.issue_matrix and not self.coverage_ledger:
            raise ValueError("coverage_ledger must be populated when issue_matrix is non-empty")
        return self

    def to_pretty_json(self) -> str:
        return self.model_dump_json(indent=2, exclude_none=True)

    model_config = {"extra": "allow"}


__all__ = ["SubagentState", "AuditFinding", "CoverageEntry", "CodebaseAnalysisReport", "_FILE_ANCHOR_RE"]
