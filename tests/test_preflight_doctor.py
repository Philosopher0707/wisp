"""Tests for wisp.core.doctor — 7/7 pre-flight checks must pass.

Exercises the doctor end-to-end via `run_preflight_sync`, plus targeted
coverage of each subsystem check, the report aggregator, and the
banner/detailed formatters.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

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

# A sandbox image whose repository name is not in `_BARE_OS_IMAGE_REPOS`, so the build-sequence
# check does not short-circuit to WARN on a project image.
_PROJECT_IMAGE = "wisp-sandbox:py311"


@pytest.fixture(scope="module")
def hermetic_ws() -> str:
    """A workspace that satisfies `build_sequence` without depending on the real repo.

    The check resolves `python3`/`pytest` through `credential_free_env(workspace=ws)`, which
    prepends ``<ws>/.venv/bin`` to PATH. A developer running the suite from a repo whose `.venv`
    exists sees OK; CI checks out a tree with no `.venv` at all (`actions/setup-python` installs
    to a tool cache, not the workspace) and saw FAIL — "no .venv in the workspace". The tests
    below assert on the *contract* of the check, so they must construct that contract instead
    of inheriting whichever machine they run on.

    Executables are empty shell stubs: the check only runs `shutil.which`, never the binary.
    """
    ws = Path(tempfile.mkdtemp(prefix="wisp-doctor-"))
    bindir = ws / ".venv" / "bin"
    bindir.mkdir(parents=True)
    for name in ("python3", "python", "pytest"):
        stub = bindir / name
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    return str(ws)


@pytest.fixture
def project_image(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin a non-bare-OS sandbox image so the check reaches its OK branch."""
    monkeypatch.setenv("WISP_SANDBOX_IMAGE", _PROJECT_IMAGE)
    return _PROJECT_IMAGE


# ── Top-level: 6/6 must be healthy ──────────────────────────────────


class TestPreflightAllChecksPass:
    def test_all_five_checks_pass(self, hermetic_ws, project_image):
        report = run_preflight_sync(workspace=hermetic_ws, timeout_s=20.0)
        statuses = {c.name: c.status for c in report.checks}
        assert set(statuses) == set(CHECK_NAMES)
        for name, status in statuses.items():
            assert status == CheckStatus.OK, (
                f"{name} did not pass: "
                f"{next(c.message for c in report.checks if c.name == name)}"
            )
        assert report.passed == 7
        assert report.failed == 0
        assert report.warnings == 0
        assert report.healthy is True

    def test_check_names_contract(self):
        assert CHECK_NAMES == (
            "path_environment",
            "build_sequence",
            "stream_hygiene",
            "tool_cache",
            "autonomous_policy",
            "graph_integrity",
            "boot_context",
        )

    def test_banner_healthy(self, hermetic_ws, project_image):
        report = run_preflight_sync(workspace=hermetic_ws, timeout_s=20.0)
        assert report.banner == format_banner(report)
        assert "7/7" in report.banner
        assert report.banner.startswith("✓")

    def test_detailed_contains_all_sections(self):
        report = run_preflight_sync(timeout_s=2.0)
        text = format_detailed(report)
        for name in CHECK_NAMES:
            assert name in text

    def test_report_to_dict_is_jsonable(self, hermetic_ws, project_image):
        report = run_preflight_sync(workspace=hermetic_ws, timeout_s=20.0)
        d = report.to_dict()
        assert d["passed"] == 7
        assert d["total"] == 7
        assert d["healthy"] is True
        assert len(d["checks"]) == 7

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
        assert result.details["nodes"].endswith("/7")
        assert result.details["retry_in_source"] is True
        assert result.details["circuit_breaker"] is True

    def test_initial_state_queued(self):
        result = asyncio.run(_check_graph_integrity())
        assert "queued" in result.details["initial_status"]

    def test_policy_bounds_configured(self):
        result = asyncio.run(_check_graph_integrity())
        assert result.details["max_depth"] > 0
        assert result.details["max_nodes"] > 0


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
        assert report.total == 7
        # Even with a 1 ms budget the structure survives; some checks may
        # legitimately complete that fast, but we never get fewer than 7.
        assert len(report.checks) == 7

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
                    "shutil", "subprocess", "sys",
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

class TestTheBuildSequenceCheck:
    """The chain that must hold before the agent can run the command its own prompt suggests.

    Every link was broken on 2026-10-01 and each produced the same symptom — an agent that could not
    verify its own work: no virtualenv; a `python` that resolved to a host interpreter with no pytest;
    a sandbox image (`ubuntu:22.04`) with no interpreter at all, while `run_bash` executes *inside*
    that container.
    """

    from pathlib import Path as _Path

    REPO = _Path(__file__).resolve().parents[1]

    @staticmethod
    def _run(workspace, image=None):
        from wisp.core.doctor import _check_build_sequence

        had = "WISP_SANDBOX_IMAGE" in os.environ
        previous = os.environ.get("WISP_SANDBOX_IMAGE")
        if image is not None:
            os.environ["WISP_SANDBOX_IMAGE"] = image
        try:
            return asyncio.run(_check_build_sequence(str(workspace)))
        finally:
            if image is not None:
                if had:
                    os.environ["WISP_SANDBOX_IMAGE"] = previous
                else:
                    os.environ.pop("WISP_SANDBOX_IMAGE", None)

    def test_it_is_registered_and_runs(self):
        assert "build_sequence" in CHECK_NAMES
        from wisp.core.doctor import run_preflight_sync

        report = run_preflight_sync(timeout_s=2.0)
        assert any(c.name == "build_sequence" for c in report.checks)

    def test_ok_for_a_workspace_with_a_venv(self, hermetic_ws):
        result = self._run(hermetic_ws, image="wisp-sandbox:py311")
        assert result.status is CheckStatus.OK
        assert result.details["python_is_project_venv"] is True
        assert result.details["pytest"]

    def test_a_bare_os_image_warns(self, hermetic_ws):
        """`run_bash` executes inside the container, so a bare base means no interpreter at all."""
        result = self._run(hermetic_ws, image="ubuntu:22.04")
        assert result.status is CheckStatus.WARN
        assert result.details["sandbox_image_is_bare_os"] is True

    def test_a_project_image_is_not_flagged_as_bare(self, hermetic_ws):
        """The check matches the *repository* part of the reference, so an image built from a bare
        base under a project name is not flagged — and a name-based "contains python" test would
        have missed `wisp-sandbox:py311` entirely."""
        result = self._run(hermetic_ws, image="wisp-sandbox:py311")
        assert result.details["sandbox_image_is_bare_os"] is False

    def test_no_venv_fails(self, tmp_path):
        result = self._run(tmp_path)
        assert result.status is CheckStatus.FAIL
        assert "no .venv" in result.message

    def test_it_stays_inside_the_preflight_budget(self):
        """Static on purpose: asking Docker costs seconds, and a check over the 100 ms budget is
        reported as *timed out* — a permanent WARN that says nothing about the real state."""
        result = self._run(self.REPO)
        assert result.latency_ms < 100, (
            f"took {result.latency_ms:.0f}ms — the check must not shell out")


class TestTheDeepSandboxCheck:
    """The slow half of `build_sequence`, asked for with `/doctor deep`.

    `build_sequence` judges the sandbox image by *name*, because the pre-flight gives every check
    100 ms and a Docker call costs seconds. A name catches a bare-OS base and nothing else: an image
    called `wisp-sandbox:py312` could exist and still lack Python, lack the runner, or not exist.
    This actually runs it. It is a **separate function**, not a check inside `run_preflight` — a
    check that cannot finish in the budget is reported as "timed out", a permanent WARN that says
    nothing about the real state.
    """

    @staticmethod
    def _has_docker():
        import shutil

        return shutil.which("docker") is not None

    def test_a_missing_image_fails(self):
        """Runs in ~a second and needs no network, so it is a real check rather than a live test."""
        import shutil

        if not shutil.which("docker"):
            pytest.skip("docker not on PATH")
        from wisp.core.doctor import check_sandbox_image_deep

        result = asyncio.run(check_sandbox_image_deep(image="wisp-nonexistent-image:zzz"))
        assert result.status is CheckStatus.FAIL
        assert result.details["returncode"] != 0

    def test_an_image_with_python_but_no_runner_fails(self):
        """The case a *name* cannot catch, and the reason this function exists.

        `python:3.12-slim` is exactly what the code default is: it has an interpreter and no pytest.
        `build_sequence` sees "python" in the name and is content. This runs it and finds the real
        state — the prompt's suggested `python -m pytest` cannot run there.
        """
        import shutil

        if not shutil.which("docker"):
            pytest.skip("docker not on PATH")
        from wisp.core.doctor import check_sandbox_image_deep

        result = asyncio.run(check_sandbox_image_deep(image="python:3.12-slim", timeout_s=120))
        if "docker" in result.message and "not on PATH" in result.message:
            pytest.skip("docker daemon unavailable")
        assert result.details["image"] == "python:3.12-slim"
        assert result.status is CheckStatus.FAIL
        assert "pytest" in result.message

    def test_it_reports_the_image_it_checked(self):
        """The name-based check cannot say *which* image answered; this one must."""
        import shutil

        if not shutil.which("docker"):
            pytest.skip("docker not on PATH")
        from wisp.core.doctor import check_sandbox_image_deep

        result = asyncio.run(check_sandbox_image_deep(image="wisp-nonexistent-image:zzz"))
        assert result.details["image"] == "wisp-nonexistent-image:zzz"
