"""Tests for wisp.core.doctor — 5/5 pre-flight checks must pass.

Exercises the doctor end-to-end via `run_preflight_sync`, plus targeted
coverage of each subsystem check, the report aggregator, and the
banner/detailed formatters.
"""

from __future__ import annotations

import asyncio

import pytest

from wisp.core import doctor as doctor_mod
from wisp.core.doctor import (
    CHECK_NAMES,
    CheckResult,
    CheckStatus,
    DoctorReport,
    _check_autonomous_policy,
    _check_graph_integrity,
    _check_path_environment,
    _check_stream_hygiene,
    _check_tool_cache,
    format_banner,
    format_detailed,
    run_preflight_sync,
)


# ── Top-level: 5/5 must be healthy ──────────────────────────────────


class TestPreflightFiveOfFive:
    def test_all_five_checks_pass(self):
        report = run_preflight_sync(timeout_s=2.0)
        statuses = {c.name: c.status for c in report.checks}
        assert set(statuses) == set(CHECK_NAMES)
        for name, status in statuses.items():
            assert status == CheckStatus.OK, (
                f"{name} did not pass: "
                f"{next(c.message for c in report.checks if c.name == name)}"
            )
        assert report.passed == 5
        assert report.failed == 0
        assert report.warnings == 0
        assert report.healthy is True

    def test_check_names_contract(self):
        assert CHECK_NAMES == (
            "path_environment",
            "stream_hygiene",
            "tool_cache",
            "autonomous_policy",
            "graph_integrity",
        )

    def test_banner_healthy(self):
        report = run_preflight_sync(timeout_s=2.0)
        assert report.banner == format_banner(report)
        assert "5/5" in report.banner
        assert report.banner.startswith("✓")

    def test_detailed_contains_all_sections(self):
        report = run_preflight_sync(timeout_s=2.0)
        text = format_detailed(report)
        for name in CHECK_NAMES:
            assert name in text

    def test_report_to_dict_is_jsonable(self):
        report = run_preflight_sync(timeout_s=2.0)
        d = report.to_dict()
        assert d["passed"] == 5
        assert d["total"] == 5
        assert d["healthy"] is True
        assert len(d["checks"]) == 5

    def test_last_report_stored(self):
        report = run_preflight_sync(timeout_s=2.0)
        assert doctor_mod.last_report() is report


# ── Per-check coverage ──────────────────────────────────────────────


class TestPathEnvironment:
    def test_passes(self):
        result = asyncio.run(_check_path_environment())
        assert result.status == CheckStatus.OK
        assert result.name == "path_environment"
        assert "safe_getcwd" in result.details

    def test_detects_writable_workspace(self):
        result = asyncio.run(_check_path_environment())
        assert result.details.get("workspace_writable") is True
        for d in (".agent", ".opencode"):
            assert d in result.details


class TestStreamHygiene:
    def test_passes(self):
        result = asyncio.run(_check_stream_hygiene())
        assert result.status == CheckStatus.OK, result.message
        assert result.details["stream_guard"] == "guarded_provider_stream"
        assert result.details["has_first_token_deadline"] is True
        assert result.details["has_chunk_deadline"] is True
        assert result.details["has_max_attempts"] is True

    def test_renderer_mode_aware(self):
        result = asyncio.run(_check_stream_hygiene())
        assert result.details.get("renderer_uses_box_chars") is True
        assert result.details.get("renderer_uses_display_width") is True
        assert result.details.get("renderer_mode_aware") is True


class TestToolCache:
    def test_passes(self):
        result = asyncio.run(_check_tool_cache())
        assert result.status == CheckStatus.OK, result.message
        assert result.details["tool_count"] > 0
        assert result.details["schema_count"] > 0
        assert result.details["missing_impl"] == []

    def test_registry_consistency(self):
        result = asyncio.run(_check_tool_cache())
        assert result.details["tool_count"] == result.details["schema_count"]


class TestAutonomousPolicy:
    def test_passes(self):
        result = asyncio.run(_check_autonomous_policy())
        assert result.status == CheckStatus.OK, result.message
        assert result.details["dangerous_blocked"].startswith("5/")
        assert result.details["auto_safe"] is True
        assert result.details["auto_danger_blocked"] is True

    def test_security_policy_modes(self):
        result = asyncio.run(_check_autonomous_policy())
        assert result.details["read_allowed"] is True
        assert result.details["write_blocked"] is True


class TestGraphIntegrity:
    def test_passes(self):
        result = asyncio.run(_check_graph_integrity())
        assert result.status == CheckStatus.OK, result.message
        assert result.details["roundtrip"] is True
        assert result.details["nodes"].endswith("/4")
        assert result.details["breaker_in_source"] is True
        assert result.details["circuit_breaker"] is True

    def test_initial_state_in_progress(self):
        result = asyncio.run(_check_graph_integrity())
        assert "in_progress" in result.details["initial_status"]

    def test_max_iterations_configured(self):
        result = asyncio.run(_check_graph_integrity())
        assert result.details["max_iterations"] > 0


# ── Models & formatters ────────────────────────────────────────────


class TestModels:
    def test_check_status_enum(self):
        assert CheckStatus.OK == "ok"
        assert CheckStatus.WARN == "warn"
        assert CheckStatus.FAIL == "fail"

    def test_check_result_symbol(self):
        for status, sym in [(CheckStatus.OK, "✓"),
                            (CheckStatus.WARN, "⚠"),
                            (CheckStatus.FAIL, "✗")]:
            cr = CheckResult(name="x", commit="c", status=status,
                              message="m", latency_ms=0.0)
            assert cr.symbol == sym

    def test_report_aggregates(self):
        checks = (
            CheckResult("a", "c1", CheckStatus.OK, "ok", 1.0),
            CheckResult("b", "c2", CheckStatus.WARN, "warn", 2.0),
            CheckResult("c", "c3", CheckStatus.FAIL, "fail", 3.0),
        )
        r = DoctorReport(checks=checks, total_duration_ms=6.0)
        assert r.total == 3
        assert r.passed == 1
        assert r.warnings == 1
        assert r.failed == 1
        assert r.healthy is False


class TestBanner:
    def test_healthy_banner(self):
        checks = tuple(CheckResult(f"c{i}", "x", CheckStatus.OK, "ok", 0.0)
                       for i in range(5))
        r = DoctorReport(checks=checks, total_duration_ms=1.0)
        assert format_banner(r).startswith("✓")
        assert "5/5" in format_banner(r)

    def test_degraded_banner(self):
        checks = (
            CheckResult("a", "x", CheckStatus.OK, "ok", 0.0),
            CheckResult("b", "x", CheckStatus.WARN, "w", 0.0),
            CheckResult("c", "x", CheckStatus.OK, "ok", 0.0),
            CheckResult("d", "x", CheckStatus.OK, "ok", 0.0),
            CheckResult("e", "x", CheckStatus.OK, "ok", 0.0),
        )
        r = DoctorReport(checks=checks, total_duration_ms=1.0)
        assert format_banner(r).startswith("⚠")
        assert "1 warning" in format_banner(r)

    def test_failed_banner(self):
        checks = (
            CheckResult("a", "x", CheckStatus.OK, "ok", 0.0),
            CheckResult("b", "x", CheckStatus.FAIL, "f", 0.0),
            CheckResult("c", "x", CheckStatus.OK, "ok", 0.0),
            CheckResult("d", "x", CheckStatus.OK, "ok", 0.0),
            CheckResult("e", "x", CheckStatus.OK, "ok", 0.0),
        )
        r = DoctorReport(checks=checks, total_duration_ms=1.0)
        assert "1 failed" in format_banner(r)


# ── Runner budget / shielding ──────────────────────────────────────


class TestRunnerShielding:
    def test_timeout_budget_does_not_drop_results(self):
        report = run_preflight_sync(timeout_s=0.001)
        assert report.total == 5
        # Even with a 1 ms budget the structure survives; some checks may
        # legitimately complete that fast, but we never get fewer than 5.
        assert len(report.checks) == 5

    def test_total_duration_under_generous_budget(self):
        report = run_preflight_sync(timeout_s=2.0)
        assert report.total_duration_ms < 1000


# ── Namespace alignment regression ────────────────────────────────


class TestNamespaceAlignment:
    """No `agent.*` imports leak into the doctor any more."""

    def test_no_agent_imports_in_doctor_source(self):
        import inspect
        src = inspect.getsource(doctor_mod)
        assert "from agent" not in src
        assert "import agent" not in src

    def test_only_wisp_imports(self):
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(doctor_mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert node.names[0].name.startswith("wisp.") or node.names[
                    0].name in {
                    "asyncio", "concurrent.futures", "importlib.util",
                    "inspect", "logging", "os", "tempfile", "time",
                    "dataclasses", "enum", "pathlib", "typing", "__future__",
                }, f"unexpected top-level import: {node.names[0].name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert mod.startswith("wisp.") or mod in {
                    "__future__", "concurrent.futures", "dataclasses",
                    "enum", "importlib", "pathlib", "typing",
                }, (
                    f"non-wisp import in doctor: {mod}"
                )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))