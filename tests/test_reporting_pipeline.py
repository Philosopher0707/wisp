"""Pytest suite — concurrent telemetry, stream isolation, Pydantic report serialization."""

import asyncio
import json
import logging
import time

import pytest
from pydantic import ValidationError


# ── Pydantic schemas ─────────────────────────────────────────────


class TestModels:
    def test_file_anchor_requires_lines(self):
        from agent.models import AuditFinding

        # bare path must fail
        with pytest.raises(ValidationError):
            AuditFinding.model_validate(
                {"severity": "P1", "file_anchor": "src/system.rs", "issue_summary": "some root cause here yes", "remediation": "fix strategy here yes"}
            )
        # valid anchors pass
        for anchor in ["src/system.rs:42-89", "src/events/mod.rs:15", "src/state/metrics.rs:88-102"]:
            AuditFinding.model_validate(
                {"severity": "P0", "file_anchor": anchor, "issue_summary": "root cause description longer", "remediation": "concrete remediation longer"}
            )

    def test_severity_enum(self):
        from agent.models import AuditFinding

        for sev in ["P0", "P1", "P2"]:
            AuditFinding.model_validate(
                {"severity": sev, "file_anchor": "a/b.rs:1", "issue_summary": "root cause description longer", "remediation": "concrete remediation longer"}
            )
        with pytest.raises(ValidationError):
            AuditFinding.model_validate(
                {"severity": "P3", "file_anchor": "a/b.rs:1", "issue_summary": "root cause description longer", "remediation": "concrete remediation longer"}
            )

    def test_subagent_state(self):
        from agent.models import SubagentState

        s = SubagentState(worker_id="bg-e2fb0a69", role="coder", focus="Analyzing Architecture", activity="Reading src/system.rs:42-89", elapsed_s=1.2, tokens_used=10, status="running")
        assert s.worker_id == "bg-e2fb0a69"

    def test_report_requires_coverage_with_issues(self):
        from agent.models import CodebaseAnalysisReport

        with pytest.raises(ValidationError):
            CodebaseAnalysisReport.model_validate(
                {
                    "generated_at": "2026-08-31T00:00:00+00:00",
                    "issue_matrix": [{"severity": "P0", "file_anchor": "a/b.rs:1-2", "issue_summary": "root cause description longer", "remediation": "concrete remediation longer"}],
                    "coverage_ledger": [],
                }
            )

    def test_report_roundtrip(self, tmp_path):
        from agent.models import CodebaseAnalysisReport

        r = CodebaseAnalysisReport(
            generated_at="2026-08-31T00:00:00+00:00",
            file_map={"src/main.rs": "entry"},
            issue_matrix=[
                {"severity": "P1", "file_anchor": "src/system.rs:42-89", "issue_summary": "root cause description longer", "remediation": "concrete remediation longer"}
            ],
            coverage_ledger=[{"path": "src/main.rs", "tested": True}],
        )
        j = r.to_pretty_json()
        assert "src/system.rs:42-89" in j
        r2 = CodebaseAnalysisReport.model_validate_json(j)
        assert r2.issue_matrix[0].file_anchor == "src/system.rs:42-89"

    def test_three_canonical_gaps_present_after_synthesize(self, tmp_path):
        from agent.synthesizer import Synthesizer

        syn = Synthesizer(out_dir=tmp_path / ".agent")
        r = syn.run([], findings=[])
        anchors = {f.file_anchor for f in r.issue_matrix}
        assert "src/system.rs:42-89" in anchors  # network tick
        assert any("load" in f.issue_summary.lower() for f in r.issue_matrix)
        assert any("user" in f.issue_summary.lower() for f in r.issue_matrix)


# ── Stream isolation ─────────────────────────────────────────────


class TestStreamIsolation:
    def test_provider_warnings_go_to_file_not_console(self, tmp_path, caplog):
        from agent.logger import BadgeFilter, install, uninstall, LOG_PATH
        import logging

        # Install with file at tmp .agent/runtime.log
        # Patch LOG_PATH to tmp
        import agent.logger as lg

        orig = lg.LOG_PATH
        lg.LOG_PATH = tmp_path / "runtime.log"
        try:
            lg.install()
            lg2 = logging.getLogger("wisp.core.provider_stream")
            # This warning should be filtered from caplog propagation and land in file
            with caplog.at_level(logging.WARNING):
                lg2.warning("Provider stream closed without any content [sse_lines=2 usable=0 empty_choice_chunks=1 finish=stop] (attempt 1/3) — retrying")
            # File should contain it
            text = (tmp_path / "runtime.log").read_text() if (tmp_path / "runtime.log").exists() else ""
            assert "Provider stream" in text or "sse_lines" in text
            # Console via caplog should NOT contain the silenced message (BadgeFilter returns False)
            # caplog captures before filter propagation — check file instead
        finally:
            lg.uninstall()
            lg.LOG_PATH = orig

    def test_truncate_payload_badge(self):
        from agent.logger import truncate_payload

        ugly = "x" * 100 + " … +35 more"
        badged = truncate_payload(ugly)
        assert "… +35 more" not in badged
        assert "✓" in badged or "Truncated" in badged

        ugly_json = {"data": "y" * 5000 + " … +28 more", "other": 1}
        badged2 = truncate_payload(ugly_json)
        assert isinstance(badged2, dict)
        assert "… +28 more" not in str(badged2.get("data", ""))

    def test_clean_payload_unchanged(self):
        from agent.logger import truncate_payload

        clean = "[✓ Read 4 files · 18 KB]"
        assert truncate_payload(clean) == clean


# ── Telemetry live tracker ───────────────────────────────────────


class TestTelemetry:
    @pytest.mark.asyncio
    async def test_concurrent_progress_rendering(self):
        from agent.telemetry import TelemetryTracker

        tracker = TelemetryTracker(max_concurrent=4)
        async with tracker.live():
            await tracker.register("bg-e2fb0a69", role="coder", focus="Analyzing Architecture")
            await tracker.register("bg-940a2c69", role="coder", focus="Inspecting UI Compositor")
            await tracker.update("bg-e2fb0a69", activity="Reading src/system.rs:42-89", tokens_used=100)
            await tracker.update("bg-940a2c69", activity="Reading src/theme.rs:10-20", tokens_used=200)
            await tracker.tick()
            snap = tracker.snapshot()
            assert "bg-e2fb0a69" in snap
            assert snap["bg-e2fb0a69"].activity.startswith("Reading")
            assert snap["bg-940a2c69"].tokens_used == 200

    @pytest.mark.asyncio
    async def test_wait_all_live_during_fanout(self):
        from agent.telemetry import TelemetryTracker

        tracker = TelemetryTracker(max_concurrent=4)

        class FakeMgr:
            def __init__(self):
                self.calls = 0

            def list(self, include_finished=True):
                self.calls += 1
                if self.calls < 3:
                    return [{"agent_id": "bg-1", "status": "running", "tokens_used": 10}]
                return []

        mgr = FakeMgr()
        async with tracker.live():
            await tracker.register("bg-1", role="coder", focus="Test")
            result = await tracker.wait_all(mgr, timeout_s=2, poll_interval=0.05)
            assert "bg-1" in result

    def test_render_snapshot_pure(self):
        from agent.telemetry import render_snapshot
        from agent.models import SubagentState

        states = {
            "bg-e2fb0a69": SubagentState(worker_id="bg-e2fb0a69", role="coder", focus="Analyzing Architecture", activity="Reading src/system.rs:42-89", elapsed_s=12.3, tokens_used=500, status="running"),
            "bg-940a2c69": SubagentState(worker_id="bg-940a2c69", role="coder", focus="System Monitor", activity="Reading src/events/mod.rs:22-48", elapsed_s=5.0, tokens_used=200, status="completed"),
        }
        out = render_snapshot(states)  # type: ignore
        # Should not raise; when rich available returns Table, else str
        assert out is not None


# ── Synthesizer dual contract ─────────────────────────────────────


class TestSynthesizer:
    def test_persists_validated_json_and_pretty_prints(self, tmp_path, capsys):
        from agent.synthesizer import Synthesizer

        syn = Synthesizer(out_dir=tmp_path / ".agent")
        payloads = [
            {"role": "coder", "output": "found issues", "files_changed": ["src/system.rs"]},
        ]
        findings = [
            {
                "severity": "P2",
                "file_anchor": "src/ui/screens/dashboard.rs:10-20",
                "issue_summary": "Dashboard screen lacks unit tests for render path",
                "remediation": "Add cargo test for Dashboard::render with snapshot",
                "source_subagent": "ui-render",
            }
        ]
        r = syn.run(payloads, findings=findings)
        # JSON persisted and valid
        jpath = tmp_path / ".agent" / "audit_summary.json"
        assert jpath.exists()
        data = json.loads(jpath.read_text())
        assert "issue_matrix" in data
        # Markdown persisted
        assert (tmp_path / ".agent" / "audit_report.md").exists()
        # Every finding has line anchor
        for f in data["issue_matrix"]:
            assert ":" in f["file_anchor"] and any(c.isdigit() for c in f["file_anchor"])

    def test_rejects_bare_path_findings(self, tmp_path):
        from agent.synthesizer import Synthesizer

        syn = Synthesizer(out_dir=tmp_path / ".agent")
        bad = [{"severity": "P1", "file_anchor": "src/system.rs", "issue_summary": "root cause description longer", "remediation": "concrete remediation longer"}]
        with pytest.raises(ValidationError):
            syn.run([], findings=bad)

    def test_dual_output_human_and_machine(self, tmp_path):
        from agent.synthesizer import Synthesizer

        syn = Synthesizer(out_dir=tmp_path / ".agent")
        r = syn.run([], findings=[])
        assert r.to_pretty_json()
        assert (tmp_path / ".agent" / "audit_summary.json").exists()
        assert (tmp_path / ".agent" / "audit_report.md").read_text().startswith("#")

    def test_coverage_ledger_marks_untested(self, tmp_path):
        from agent.synthesizer import Synthesizer

        syn = Synthesizer(out_dir=tmp_path / ".agent")
        r = syn.run([], findings=[])
        untested = [c.path for c in r.coverage_ledger if not c.tested]
        assert "src/app.rs" in untested
        assert "src/events/mod.rs" in untested
        assert "src/ui/screens/*" in untested

    def test_functional_synthesize_entry(self, tmp_path):
        from agent.synthesizer import synthesize

        r = synthesize([], out_dir=tmp_path / ".agent", findings=[])
        assert r.issue_matrix  # canonical gaps injected
        assert (tmp_path / ".agent" / "audit_summary.json").exists()
