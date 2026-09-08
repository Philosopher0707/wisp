"""Harness scaffolding: thin primitives, verification floor, sandbox
router, auto-skill capture, and their seams in WispAgentCore.

Covers the YC-harness refactor: 3 primitives over reused implementations,
HARNESS REJECTION before exit-0 evidence, Docker→PTY failover, and
RESOLVED-trail persistence to .wisp/skills/auto/.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from wisp.core.verification import HARNESS_REJECTION, VerificationFloorGuard


def _guard(**kw: Any) -> VerificationFloorGuard:
    return VerificationFloorGuard(min_turns=5, max_nudges=2, **kw)


class TestVerificationFloorGuard:
    def test_clean_turn_finishes_freely(self) -> None:
        assert _guard().rejection() is None

    def test_early_finish_attempt_rejected(self) -> None:
        g = _guard()
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        msg = g.rejection()
        assert msg is not None and msg.startswith("[HARNESS REJECTION]")
        assert "must run the tests" in msg

    def test_exact_rejection_contract(self) -> None:
        g = _guard()
        g.note_tool_result("fs_mutate", "ok", {"op": "edit", "path": "a.py"})
        assert g.rejection() == HARNESS_REJECTION

    def test_exit_zero_after_last_edit_resolves(self) -> None:
        g = _guard()
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        assert g.rejection() is not None  # premature first
        g.note_tool_result("exec_sandbox", "all green", {"command": "pytest"})
        assert g.rejection() is None
        assert g.resolved() is True

    def test_failed_suite_keeps_blocking(self) -> None:
        g = _guard()
        g.note_tool_result("edit_file", "ok", {"path": "a.py"})
        g.note_tool_result("run_bash", "[exit code: 1]\nFAILED", {"command": "pytest"})
        assert g.rejection() is not None
        assert g.resolved() is False

    def test_stale_evidence_does_not_count(self) -> None:
        g = _guard()
        g.note_tool_result("run_bash", "ok", {"command": "pytest"})
        g.note_tool_result("write_file", "ok", {"path": "a.py"})  # invalidates
        assert g.rejection() is not None

    def test_read_only_mutate_does_not_arm(self) -> None:
        g = _guard()
        g.note_tool_result("fs_mutate", "content", {"op": "read", "path": "a.py"})
        assert g.rejection() is None

    def test_floor_exhaustion_allows_honest_surrender(self) -> None:
        g = _guard()
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        for _ in range(5):  # grind floor: 5 turns of attempts
            g.note_tool_result("run_bash", "[exit code: 1]", {"command": "pytest"})
        assert g.rejection() is not None  # nudges still left
        assert g.rejection() is not None
        assert g.rejection() is None  # floor + nudges spent: may finish
        assert g.resolved() is False  # surrendered, never RESOLVED

    def test_disabled_guard_never_blocks(self) -> None:
        g = _guard(enabled=False)
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        assert g.rejection() is None

    def test_trail_records_digested_steps(self) -> None:
        g = _guard()
        g.note_tool_result("write_file", "ok", {"path": "a.py", "content": "x" * 500})
        assert g.steps and g.steps[0][0] == "write_file"


class TestPrimitives:
    def test_three_schemas_only(self) -> None:
        from wisp.tools.primitives import PRIMITIVE_IMPLS, PRIMITIVE_SCHEMAS, thin_tool_names

        assert thin_tool_names() == ["exec_sandbox", "fs_mutate", "git_checkpoint"]
        assert {s["function"]["name"] for s in PRIMITIVE_SCHEMAS} == set(PRIMITIVE_IMPLS)

    def test_exec_sandbox_runs_and_shapes_output(self, tmp_path: Path) -> None:
        from wisp.tools.primitives import async_exec_sandbox

        out = asyncio.run(async_exec_sandbox(
            {"command": "echo harness-hi", "timeout": 30}, str(tmp_path)))
        assert "harness-hi" in out

    def test_exec_sandbox_blocks_dangerous(self, tmp_path: Path) -> None:
        from wisp.tools.errors import ToolError
        from wisp.tools.primitives import async_exec_sandbox

        with pytest.raises(ToolError, match="[Dd]angerous|rivilege"):
            asyncio.run(async_exec_sandbox(
                {"command": "sudo rm -rf /"}, str(tmp_path)))

    def test_exec_sandbox_rejects_bad_args(self, tmp_path: Path) -> None:
        from wisp.tools.errors import ToolError
        from wisp.tools.primitives import async_exec_sandbox

        with pytest.raises(ToolError, match="invalid arguments"):
            asyncio.run(async_exec_sandbox({"timeout": 10}, str(tmp_path)))

    def test_fs_mutate_write_read_edit_roundtrip(self, tmp_path: Path) -> None:
        from wisp.tools.checkpoints import reset_checkpoint_stores
        from wisp.tools.primitives import async_fs_mutate

        ws = str(tmp_path)
        try:
            asyncio.run(async_fs_mutate(
                {"op": "write", "path": "f.txt", "content": "v1"}, ws))
            read = asyncio.run(async_fs_mutate({"op": "read", "path": "f.txt"}, ws))
            assert "v1" in str(read)
            asyncio.run(async_fs_mutate(
                {"op": "edit", "path": "f.txt",
                 "old_text": "v1", "new_text": "v2"}, ws))
            assert (tmp_path / "f.txt").read_text() == "v2"
        finally:
            reset_checkpoint_stores()

    def test_fs_mutate_rejects_unknown_op(self, tmp_path: Path) -> None:
        from wisp.tools.errors import ToolError
        from wisp.tools.primitives import async_fs_mutate

        with pytest.raises(ToolError, match="invalid arguments"):
            asyncio.run(async_fs_mutate(
                {"op": "delete", "path": "f.txt"}, str(tmp_path)))

    def test_git_checkpoint_diff_and_list(self, tmp_path: Path) -> None:
        import subprocess

        from wisp.tools.checkpoints import reset_checkpoint_stores
        from wisp.tools.primitives import async_git_checkpoint, async_fs_mutate

        ws = str(tmp_path)
        try:
            (tmp_path / "base.py").write_text("BASE = 1\n")
            subprocess.run(["git", "init", "-q"], cwd=ws, check=True, timeout=30)
            subprocess.run(["git", "add", "-A"], cwd=ws, check=True, timeout=30)
            subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                            "commit", "-qm", "base"], cwd=ws, check=True, timeout=30)
            asyncio.run(async_fs_mutate(
                {"op": "write", "path": "new.py", "content": "NEW = 1\n"}, ws))
            diff = asyncio.run(async_git_checkpoint({"action": "diff"}, ws))
            assert "new.py" in str(diff) and str(diff).startswith("diff --git")
            # Validate against a pristine HEAD export, never the live tree.
            pristine = tmp_path / "pristine"
            pristine.mkdir()
            archive = subprocess.run(["git", "archive", "HEAD"], cwd=ws,
                                     capture_output=True, check=True, timeout=30)
            subprocess.run(["tar", "-x", "-C", str(pristine)],
                           input=archive.stdout, check=True, timeout=30)
            patch_file = tmp_path / "model.patch"
            patch_file.write_text(str(diff))
            proc = subprocess.run(["git", "apply", "--check", str(patch_file)],
                                  cwd=pristine, capture_output=True,
                                  text=True, timeout=30)
            assert proc.returncode == 0, proc.stderr
            listed = asyncio.run(async_git_checkpoint({"action": "list"}, ws))
            assert listed["status"] == "ok" and "new.py" in listed["data"]
        finally:
            reset_checkpoint_stores()

    def test_git_checkpoint_restore_rewinds(self, tmp_path: Path) -> None:
        from wisp.tools.checkpoints import reset_checkpoint_stores
        from wisp.tools.primitives import async_fs_mutate, async_git_checkpoint

        ws = str(tmp_path)
        try:
            (tmp_path / "a.txt").write_text("v1")
            asyncio.run(async_fs_mutate(
                {"op": "edit", "path": "a.txt",
                 "old_text": "v1", "new_text": "v2"}, ws))
            out = asyncio.run(async_git_checkpoint(
                {"action": "restore", "path": "a.txt"}, ws))
            assert out["status"] == "ok"
            assert (tmp_path / "a.txt").read_text() == "v1"
        finally:
            reset_checkpoint_stores()


class TestSandboxRouter:
    def test_failover_to_pty_when_docker_down(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from wisp.sandbox import DockerSandbox
        from wisp.sandbox.router import SandboxRouter, reset_router

        monkeypatch.setattr(DockerSandbox, "is_available", lambda self: False)
        try:
            router = SandboxRouter(str(tmp_path))
            picked = router.route()
            assert picked.name in ("pty", "host")
            rc, out, _ = asyncio.run(router.run("echo failover-ok"))
            assert rc == 0 and "failover-ok" in out
        finally:
            reset_router()

    def test_pty_tier_runs_isolated(self, tmp_path: Path) -> None:
        from wisp.sandbox.router import PtySandbox

        pty = PtySandbox(str(tmp_path))
        if not pty.is_available():
            pytest.skip("no POSIX pty on this platform")
        rc, out, err = asyncio.run(pty.run("echo pty-hi && echo e1 >&2"))
        assert rc == 0 and "pty-hi" in out  # stderr merges on a pty

    def test_pty_tier_blocks_dangerous(self, tmp_path: Path) -> None:
        from wisp.sandbox.router import PtySandbox

        rc, _, err = asyncio.run(
            PtySandbox(str(tmp_path)).run("sudo id"))
        assert rc == -1 and "Dangerous" in err

    def test_router_never_raises_and_never_pollutes_stdout(
        self, tmp_path: Path, capsys: Any,
    ) -> None:
        from wisp.sandbox.router import SandboxRouter, reset_router

        class Dead:
            name = "dead"

            def is_available(self) -> bool:
                raise RuntimeError("daemon gone")

            async def run(self, *a: Any, **k: Any) -> Any:
                raise RuntimeError("daemon gone")

        try:
            router = SandboxRouter(str(tmp_path), tiers=[Dead()])  # type: ignore[list-item]
            rc, _, err = asyncio.run(router.run("echo x"))
            assert rc == -1 and err
        finally:
            reset_router()
        captured = capsys.readouterr()
        assert captured.out == "", "failover polluted stdout"

    def test_route_decision_cached(self, tmp_path: Path) -> None:
        from wisp.sandbox.router import SandboxRouter, reset_router

        calls = {"n": 0}

        class Counting:
            name = "counting"

            def is_available(self) -> bool:
                calls["n"] += 1
                return True

            async def run(self, *a: Any, **k: Any) -> tuple[int, str, str]:
                return (0, "ok", "")

        try:
            router = SandboxRouter(str(tmp_path), tiers=[Counting()])  # type: ignore[list-item]
            router.route()
            router.route()
            assert calls["n"] == 1, "availability re-probed inside TTL"
        finally:
            reset_router()


class TestAutoSkillCapture:
    def test_resolved_trail_persists_skill(self, tmp_path: Path) -> None:
        from wisp.skill_capture import capture_resolved_skill

        skill_dir = capture_resolved_skill(
            "fix off-by-one in totals",
            [("edit_file", {"path": "totals.py"}),
             ("exec_sandbox", {"command": "pytest"})],
            str(tmp_path))
        assert skill_dir is not None
        body = (skill_dir / "SKILL.md").read_text()
        assert "totals.py" in body and "pytest" in body
        assert str(skill_dir).startswith(str(tmp_path / ".wisp" / "skills" / "auto"))

    def test_trivial_trail_captures_nothing(self, tmp_path: Path) -> None:
        from wisp.skill_capture import capture_resolved_skill

        assert capture_resolved_skill(
            "x", [("exec_sandbox", {"command": "pytest"})], str(tmp_path)) is None


class TestCoreSeams:
    def test_thin_mode_serves_three_schemas(self) -> None:
        from wisp.core.stateless import WispAgentCore

        core = WispAgentCore()
        core.config = SimpleNamespace(thin_tools=True)  # type: ignore[assignment]
        core.extensions = None
        names = [s["function"]["name"] for s in core._get_tool_schemas()]
        assert names == ["exec_sandbox", "fs_mutate", "git_checkpoint"]

    def test_default_mode_keeps_full_registry(self) -> None:
        from wisp.core.stateless import WispAgentCore

        core = WispAgentCore()
        core.config = SimpleNamespace()  # type: ignore[assignment]
        core.extensions = None
        names = [s["function"]["name"] for s in core._get_tool_schemas()]
        assert len(names) == 42 and "exec_sandbox" not in names

    def test_thin_prompt_menu_matches_schemas(self, tmp_path: Path) -> None:
        from wisp.core.stateless import WispAgentCore

        core = WispAgentCore()
        core.config = SimpleNamespace(thin_tools=True, workspace=str(tmp_path))  # type: ignore[assignment]
        core.extensions = None
        menu = core._build_tools_block(None)
        assert "exec_sandbox" in menu and "run_bash" not in menu

    def test_primitive_dispatch_end_to_end(self, tmp_path: Path) -> None:
        # GH#25: thin names run through the wired ToolExecutor (gated path),
        # never around it — without an executor they fail closed.
        from wisp.config import WispConfig, PermissionMode
        from wisp.core.stateless import WispAgentCore
        from wisp.tool_executor import ToolExecutor

        core = WispAgentCore()
        core.config = SimpleNamespace(thin_tools=True)  # type: ignore[assignment]
        core.extensions = None
        core.tool_executor = None

        async def _go() -> list[dict[str, Any]]:
            return [e async for e in core._execute_tool(
                {"name": "exec_sandbox", "id": "c1",
                 "arguments": {"command": "echo dispatched"}},
                {"workspace": str(tmp_path)})]

        events = asyncio.run(_go())
        assert events and "requires a wired ToolExecutor" in str(events[-1])

        cfg = WispConfig().replace(
            workspace=str(tmp_path), permission_mode=PermissionMode.FULL,
            auto_approve=True)
        core.tool_executor = ToolExecutor(config=cfg)
        events = asyncio.run(_go())
        assert events and "dispatched" in str(events[-1])
