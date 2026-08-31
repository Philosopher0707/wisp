"""Reducer node — merges fanout subagent findings into the dual-output report.

Human path: rich markdown tables to stdout (file anchors, severity badges,
coverage ledger).  Machine path: validated Pydantic JSON at .agent/audit_summary.json
so downstream graph nodes can patch without regex scraping.

Explicitly maps the 3 prior telemetry gaps:
  * src/system.rs: Network I/O rate fixed 100ms vs Instant::now()
  * src/events/mod.rs & src/system.rs: missing 1m/5m/15m load averages
  * src/events/mod.rs & src/system.rs: ProcessInfo.user unpopulated
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

try:
    from agent.models import AuditFinding, CodebaseAnalysisReport, CoverageEntry, SubagentState
except Exception:  # allow `python -m agent.synthesizer`
    from models import AuditFinding, CoverageEntry, CodebaseAnalysisReport, SubagentState  # type: ignore

try:
    from rich.console import Console
    from rich.table import Table
    from rich.markdown import Markdown
    from rich.panel import Panel
    _RICH = True
except Exception:
    Console = Table = Markdown = Panel = None  # type: ignore
    _RICH = False

__all__ = ["Synthesizer", "synthesize"]

# ── Canonical gap seeds — must map cleanly to new schema ──────────
# These are the 3 gaps identified in the prior codebase audit. We emit them
# as P0/P1 with precise anchors so CI can fail without scraping.
_CANONICAL_GAPS: List[Dict[str, Any]] = [
    {
        "severity": "P0",
        "file_anchor": "src/system.rs:42-89",
        "issue_summary": "Network I/O rate assumes fixed 100ms tick; on backpressure it under-reports throughput and breaks sparkline scaling",
        "remediation": "Store last Instant::now() and bytes; rate = (bytes_now - bytes_prev) as f64 / elapsed.as_secs_f64(); clamp tick to max 500ms. See aether-tui/src/system.rs:58-76 patch.",
        "source_subagent": "system-monitor",
        "tags": ["telemetry", "metrics"],
    },
    {
        "severity": "P1",
        "file_anchor": "src/system.rs:110-145",
        "issue_summary": "Missing 1m/5m/15m load average collection despite SystemMetricsEvent contract; field stays Default::default()",
        "remediation": "Call sysinfo::System::load_average() or procfs /proc/loadavg; populate metrics.load_avg.{one,five,fifteen} each tick in SystemMonitor::collect()",
        "source_subagent": "system-monitor",
        "tags": ["telemetry", "loadavg"],
    },
    {
        "severity": "P1",
        "file_anchor": "src/events/mod.rs:22-48",
        "issue_summary": "ProcessInfo.user / owner field never populated — events emit empty string, breaking per-user attribution",
        "remediation": "Resolve via users crate or procfs /proc/<pid>/status Uid -> get_user_by_uid; cache uid->name; set process.user = name.unwrap_or(uid.to_string())",
        "source_subagent": "system-monitor",
        "tags": ["telemetry", "process"],
    },
]

# Coverage ledger — explicit, not inferred. Untested modules flagged P2 via issues.
_DEFAULT_COVERAGE: List[Dict[str, Any]] = [
    {"path": "src/main.rs", "tested": True, "note": "entry + event loop covered"},
    {"path": "src/app.rs", "tested": False, "note": "untested — app state transitions"},
    {"path": "src/state/mod.rs", "tested": True, "note": "partial via metrics series"},
    {"path": "src/ui/mod.rs", "tested": False, "note": "Compositor/theming — untested"},
    {"path": "src/theme.rs", "tested": False, "note": "theme system untested"},
    {"path": "src/ui/screens/*", "tested": False, "note": "all screens untested"},
    {"path": "src/system.rs", "tested": True, "note": "monitor covered, I/O rate bug present"},
    {"path": "src/events/mod.rs", "tested": False, "note": "event bus untested"},
    {"path": "src/state/metrics.rs", "tested": True, "note": "series tested"},
    {"path": "src/state/logs.rs", "tested": True, "note": "LogEntry/LogLevel covered"},
    {"path": "src/state/particles.rs", "tested": False, "note": "particle system untested"},
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_findings(raw_findings: List[Dict[str, Any]]) -> List[AuditFinding]:
    """Validate every finding has line-anchored file_anchor; inject canonical gaps if missing."""
    findings: List[AuditFinding] = []
    seen_anchors = set()
    for raw in raw_findings or []:
        try:
            # Normalize legacy bare paths by rejecting them — caller must fix
            f = AuditFinding.model_validate(raw)
            findings.append(f)
            seen_anchors.add(f.file_anchor)
        except ValidationError as e:
            # Re-raise with actionable context — synthesizer is the hard gate
            raise ValidationError.from_exception_data(
                "AuditFinding validation failed — file_anchor must be 'path:line' with remediation",
                line_errors=[{"type": "value_error", "loc": ("file_anchor",), "msg": str(e), "input": raw, "ctx": {"error": str(e)}}],  # type: ignore
            ) from e

    # Ensure the 3 canonical gaps are present (dedupe by anchor)
    for gap in _CANONICAL_GAPS:
        if gap["file_anchor"] not in seen_anchors:
            findings.append(AuditFinding.model_validate(gap))
    return findings


def _coerce_coverage(raw: Optional[List[Dict[str, Any]]]) -> List[CoverageEntry]:
    src = raw if raw is not None else _DEFAULT_COVERAGE
    out: List[CoverageEntry] = []
    for e in src:
        out.append(CoverageEntry.model_validate(e))
    return out


class Synthesizer:
    """Reducer node. Call `run(subagent_payloads)` to get (report, json_path)."""

    def __init__(self, out_dir: str | Path = ".agent", console: Optional[Console] = None):  # type: ignore
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.console = console or (Console() if _RICH else None)  # type: ignore
        self.json_path = self.out_dir / "audit_summary.json"
        self.md_path = self.out_dir / "audit_report.md"

    def run(
        self,
        subagent_payloads: List[Dict[str, Any]],
        *,
        file_map: Optional[Dict[str, str]] = None,
        findings: Optional[List[Dict[str, Any]]] = None,
        coverage: Optional[List[Dict[str, Any]]] = None,
        subagent_states: Optional[List[Dict[str, Any]]] = None,
        title: str = "Codebase Audit — aether-tui (Rust)",
    ) -> CodebaseAnalysisReport:
        # 1. Gather payloads — tolerate both flat and nested wisp event shapes
        merged_findings_raw: List[Dict[str, Any]] = list(findings or [])
        merged_file_map: Dict[str, str] = dict(file_map or {})
        states_raw: List[Dict[str, Any]] = list(subagent_states or [])

        for p in subagent_payloads or []:
            # fanout background payloads are {task, role, output, files_changed}
            if isinstance(p, dict):
                data = p.get("data") if isinstance(p.get("data"), dict) else p
                # File map hints
                for k in ("files", "file_map", "modules"):
                    if isinstance(data.get(k), dict):
                        merged_file_map.update({str(k2): str(v) for k2, v in data[k].items()})
                # Findings may be under findings/issues/gaps
                for k in ("findings", "issues", "gaps", "issue_matrix"):
                    if isinstance(data.get(k), list):
                        merged_findings_raw.extend([x for x in data[k] if isinstance(x, dict)])
                # Single finding shaped dict (has severity)
                if "severity" in data and "file_anchor" in data:
                    merged_findings_raw.append(data)
                # Subagent states
                if isinstance(data.get("subagent_states"), list):
                    states_raw.extend(data["subagent_states"])

        # 2. Validate via Pydantic — hard gate
        issue_matrix = _coerce_findings(merged_findings_raw)
        coverage_ledger = _coerce_coverage(coverage)
        states: List[SubagentState] = []
        for s in states_raw:
            try:
                states.append(SubagentState.model_validate(s))
            except Exception:
                continue

        # Default file_map if empty
        if not merged_file_map:
            merged_file_map = {c.path: ("tested" if c.tested else "untested") for c in coverage_ledger}

        report = CodebaseAnalysisReport(
            title=title,
            generated_at=_now_iso(),
            file_map=merged_file_map,
            issue_matrix=issue_matrix,
            coverage_ledger=coverage_ledger,
            subagent_states=states,
            metrics={"subagents": len(states), "issues": len(issue_matrix), "generated_at": _now_iso()},
        )

        # 3. Persist JSON (validated)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.json_path.write_text(report.to_pretty_json() + "\n", encoding="utf-8")

        # 4. Persist markdown snapshot (human)
        md = self._render_markdown(report)
        self.md_path.write_text(md, encoding="utf-8")

        # 5. Pretty-print to console
        self._print(report)

        return report

    # ── Renderers ────────────────────────────────────────────────

    def _render_markdown(self, r: CodebaseAnalysisReport) -> str:
        lines: List[str] = [f"# {r.title}", f"_Generated {r.generated_at}_", ""]
        lines.append("## Issue Matrix (P0 → P2)")
        lines.append("| Severity | Anchor | Summary | Remediation |")
        lines.append("|---|---|---|---|")
        for f in sorted(r.issue_matrix, key=lambda x: {"P0": 0, "P1": 1, "P2": 2}.get(x.severity, 9)):
            lines.append(f"| {f.severity} | `{f.file_anchor}` | {f.issue_summary} | {f.remediation} |")
        lines.append("")
        lines.append("## Coverage Ledger")
        lines.append("| Path | Tested | Note |")
        lines.append("|---|---|---|")
        for c in r.coverage_ledger:
            badge = "✓" if c.tested else "✗"
            lines.append(f"| {c.path} | {badge} | {c.note or ''} |")
        lines.append("")
        # Also note untested
        untested = [c.path for c in r.coverage_ledger if not c.tested]
        if untested:
            lines.append(f"**Untested modules:** {', '.join(untested)}")
        return "\n".join(lines)

    def _print(self, r: CodebaseAnalysisReport) -> None:
        if not _RICH or self.console is None:
            print(self._render_markdown(r))
            print(f"\n[JSON] {self.json_path}")
            return

        # Issues table
        t = Table(title="Issue Matrix — Prioritized", expand=False)
        t.add_column("Sev", style="bold", no_wrap=True)
        t.add_column("Anchor", style="cyan", no_wrap=False)
        t.add_column("Summary", style="white")
        t.add_column("Remediation", style="green")
        sev_style = {"P0": "red", "P1": "yellow", "P2": "dim"}
        for f in sorted(r.issue_matrix, key=lambda x: {"P0": 0, "P1": 1, "P2": 2}.get(x.severity, 9)):
            t.add_row(f"[{sev_style.get(f.severity,'white')}]{f.severity}[/]", f.file_anchor, f.issue_summary, f.remediation)
        self.console.print(t)

        # Coverage
        c = Table(title="Coverage Ledger", expand=False)
        c.add_column("Path", style="cyan")
        c.add_column("Tested", justify="center")
        c.add_column("Note", style="dim")
        for e in r.coverage_ledger:
            c.add_row(e.path, "[green]✓[/]" if e.tested else "[red]✗[/]", e.note or "")
        self.console.print(c)

        # Footer badges — concise, replaces truncated JSON
        self.console.print(f"[dim]JSON → {self.json_path}  ·  Markdown → {self.md_path}[/dim]")
        # Badge summary (replaces … +35 more)
        total = len(r.issue_matrix)
        p0 = sum(1 for f in r.issue_matrix if f.severity == "P0")
        files = len(r.file_map)
        kb = len(r.to_pretty_json()) // 1024
        self.console.print(f"[bold green][✓ Audit {total} findings ({p0} P0) · {files} files · {kb} KB][/bold green]")


def synthesize(
    subagent_payloads: List[Dict[str, Any]],
    *,
    out_dir: str | Path = ".agent",
    findings: Optional[List[Dict[str, Any]]] = None,
    file_map: Optional[Dict[str, str]] = None,
    coverage: Optional[List[Dict[str, Any]]] = None,
) -> CodebaseAnalysisReport:
    """Functional entry — reducer node. Returns validated report."""
    return Synthesizer(out_dir=out_dir).run(
        subagent_payloads, findings=findings, file_map=file_map, coverage=coverage
    )


if __name__ == "__main__":
    # Demo: synthesize the 3 canonical gaps
    r = synthesize([], findings=[])
    print(r.to_pretty_json())
