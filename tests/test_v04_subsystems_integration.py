"""V04 subsystems integration audit — GH#13 through GH#18.

Boundary handshake tests for the five newly landed subsystems:

  A. Setup wizard + boot fallback   (GH#18 — wisp/cli/setup.py)
  B. Bash sandbox routing + server-auth fail-fast (GH#13/GH#14)
  C. Checkpoint / rewind state machine (GH#15)
  D. Pre/post hook interception through ToolExecutor (GH#16)
  E. SWE-bench patch serialization (GH#17)

Deliberate deviations from the audit brief (pinned, not silent):
  - Hooks live in ``<workspace>/.wisp/hooks/*.json`` (see docs/hooks.md),
    not ``.agent/hooks/`` — the tests use the real path.
  - Predictions JSONL keys are ``{instance_id, model_patch, model_name}``
    (wisp/benchmark/cli.py); the brief names ``model_name_or_path``,
    which the official SWE-bench harness accepts as an alias but this
    repo never writes. Pinned as-is, flagged in the audit report.
"""

from __future__ import annotations

import asyncio
import json
import logging
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from wisp.config import WispConfig


# ── Shared fixtures ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_global_state(monkeypatch: pytest.MonkeyPatch):
    """Isolate process-global singletons between scenarios."""
    from wisp import sandbox as sandbox_mod
    from wisp.tools.checkpoints import reset_checkpoint_stores

    sandbox_mod.reset_sandbox()
    reset_checkpoint_stores()
    for var in ("WISP_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
                "NVIDIA_API_KEY", "WISP_SANDBOX"):
        monkeypatch.delenv(var, raising=False)
    yield
    sandbox_mod.reset_sandbox()
    reset_checkpoint_stores()


class Script:
    """Canned terminal inputs + hidden-password stub + captured output."""

    def __init__(self, inputs: list[str], passwords: list[str] | None = None) -> None:
        self._inputs: list[str] = list(inputs)
        self._passwords: list[str] = list(passwords or [])
        self.out: list[str] = []

    def input(self, prompt: str = "") -> str:
        self.out.append(prompt)
        assert self._inputs, f"out of inputs at prompt: {prompt!r}"
        return self._inputs.pop(0)

    def password(self, prompt: str = "") -> str:
        self.out.append(prompt)
        assert self._passwords, f"out of passwords at prompt: {prompt!r}"
        return self._passwords.pop(0)

    def write(self, text: str) -> None:
        self.out.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self.out)


def _ok_probe(provider: str, model: str, key: str, base: str,
              timeout_s: float = 8.0) -> tuple[bool, str]:
    return True, "handshake ok"


# ═══════════════════════════════════════════════════════════════════════
# Scenario A: Setup wizard & boot fallback handshake (GH#18)
# ═══════════════════════════════════════════════════════════════════════

class TestScenarioASetupBootHandshake:
    def _no_creds_config(self) -> WispConfig:
        # Empty provider + no keys anywhere = the boot-fallback trigger.
        return WispConfig().replace(provider="")

    def test_boot_with_no_creds_flags_setup_needed(self) -> None:
        from wisp.cli.setup import _needs_setup

        assert _needs_setup(self._no_creds_config()) is True

    def test_boot_off_tty_never_blocks_or_throws(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`wisp repl` piped / non-interactive must degrade, not crash."""
        from wisp.cli.setup import maybe_offer_setup

        class DeadStdin:
            def isatty(self) -> bool:
                return False

        monkeypatch.setattr(sys, "stdin", DeadStdin())
        # Must return (False) rather than raise a transport exception.
        assert maybe_offer_setup(self._no_creds_config()) is False

    def test_boot_decline_returns_to_repl(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from wisp.cli.setup import maybe_offer_setup

        class FakeTty:
            def isatty(self) -> bool:
                return True

        monkeypatch.setattr(sys, "stdin", FakeTty())
        monkeypatch.setattr("builtins.input", lambda _p="": "n")
        assert maybe_offer_setup(self._no_creds_config()) is False

    def test_boot_accept_runs_wizard_and_signals_restart(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A completed setup returns True — caller restarts on fresh config."""
        import wisp.cli.setup as setup_mod

        class FakeTty:
            def isatty(self) -> bool:
                return True

        monkeypatch.setattr(sys, "stdin", FakeTty())
        monkeypatch.setattr("builtins.input", lambda _p="": "y")
        monkeypatch.setattr(setup_mod, "run_setup",
                            lambda: {"provider": "ollama", "saved": True})
        assert setup_mod.maybe_offer_setup(self._no_creds_config()) is True

    def test_wizard_typed_key_never_echoed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import wisp.cli.setup as setup_mod
        import wisp.provider_select as ps

        persisted: list[dict[str, str]] = []
        stored: list[tuple[str, str]] = []
        monkeypatch.setattr(ps, "persist",
                            lambda update: persisted.append(update) or True)
        monkeypatch.setattr(ps, "store_key",
                            lambda p, k: stored.append((p, k)))
        secret = "sk-live-typed-SECRET-999"
        # openai (option 2) → default model → empty base → typed key.
        s = Script(inputs=["2", "", ""], passwords=[secret])
        result = setup_mod.run_setup(s.input, s.password, s.write,
                                     probe_fn=_ok_probe)
        assert result is not None and result["saved"] is True
        assert stored == [("openai", secret)]
        assert secret not in s.text, "API key echoed to stdout!"

    def test_wizard_env_key_never_written_to_disk(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import wisp.provider_select as ps
        from wisp.cli.setup import run_setup

        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-123")
        stored: list[tuple[str, str]] = []
        monkeypatch.setattr(ps, "persist", lambda update: True)
        monkeypatch.setattr(ps, "store_key",
                            lambda p, k: stored.append((p, k)))
        s = Script(inputs=["2", "", "y"])
        result = run_setup(s.input, s.password, s.write, probe_fn=_ok_probe)
        assert result is not None and result["provider"] == "openai"
        assert stored == [], "env-sourced key must never hit disk"

    def test_written_config_is_owner_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import wisp.config as cfg_mod

        # WISP_CONFIG_DIR binds at import — patch the constant, not HOME.
        monkeypatch.setattr(cfg_mod, "WISP_CONFIG_DIR",
                            tmp_path / ".config" / "wisp")
        cfg_mod.save_config({"provider": "ollama", "model": "m"})
        mode = stat.S_IMODE(
            (tmp_path / ".config" / "wisp" / "config.json").stat().st_mode)
        assert mode == 0o600

    def test_written_env_file_is_owner_only(self, tmp_path: Path) -> None:
        from wisp.provider_select import _upsert_env_file

        target = tmp_path / ".env"
        _upsert_env_file(target, {"WISP_API_KEY": "sk-x"})
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


# ═══════════════════════════════════════════════════════════════════════
# Scenario B: Bash sandbox fallback & server auth enforcement (GH#13/14)
# ═══════════════════════════════════════════════════════════════════════

class _RecordingSandbox:
    """Stand-in confinement boundary: records, never touches the host."""

    name = "fake-docker"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    def is_available(self) -> bool:
        return True

    async def run(self, command: str, cwd: str = "",
                  timeout: int = 60) -> tuple[int, str, str]:
        self.calls.append((command, cwd, timeout))
        return (0, "canned-out", "canned-err")


class TestScenarioBSandboxAndServerAuth:
    @pytest.mark.asyncio
    async def test_dangerous_command_blocked_before_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Elevated commands raise ToolError and never reach any provider."""
        from wisp.tools import bash as bash_mod
        from wisp.tools._utils import ToolError

        fake = _RecordingSandbox()
        monkeypatch.setattr(bash_mod, "get_sandbox", lambda ws: fake)
        with pytest.raises(ToolError, match="[Pp]rivilege|Dangerous|blocked"):
            await bash_mod.async_tool_run_bash("sudo rm -rf /", str(tmp_path))
        assert fake.calls == [], "dangerous command reached the provider!"

    @pytest.mark.asyncio
    async def test_benign_command_routes_strictly_through_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from wisp.tools import bash as bash_mod

        fake = _RecordingSandbox()
        monkeypatch.setattr(bash_mod, "get_sandbox", lambda ws: fake)
        marker = tmp_path / "host-touched.txt"
        out = await bash_mod.async_tool_run_bash(
            f"touch {marker} && echo hi", str(tmp_path))
        assert fake.calls, "sandbox provider was never consulted"
        assert not marker.exists(), "command escaped confinement to host"
        assert out == "canned-out\n--- stderr ---\ncanned-err"

    @pytest.mark.asyncio
    async def test_traversal_shaped_command_still_confined(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from wisp.tools import bash as bash_mod

        fake = _RecordingSandbox()
        monkeypatch.setattr(bash_mod, "get_sandbox", lambda ws: fake)
        await bash_mod.async_tool_run_bash("cat ../../etc/hostname",
                                           str(tmp_path))
        assert len(fake.calls) == 1, "command bypassed the sandbox provider"

    @pytest.mark.asyncio
    async def test_unconfined_fallback_is_loud(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No Docker here → host runs, but a WARNING must say so."""
        from wisp.tools import bash as bash_mod

        with caplog.at_level(logging.WARNING, logger="wisp.tools.bash"):
            out = await bash_mod.async_tool_run_bash(
                "echo fallback-hi", str(tmp_path))
        assert "fallback-hi" in out
        assert any("UNCONFINED" in r.message or
                   ("host" in r.message.lower()
                    and "sandbox" in r.message.lower())
                   for r in caplog.records), \
            "unconfined execution ran silently!"

    def test_server_without_token_fails_fast(self, capsys: Any) -> None:
        from wisp.server.main import _check_bind_auth

        with pytest.raises(SystemExit) as exc:
            _check_bind_auth("127.0.0.1", False, False)
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "WISP_API_KEY" in err and "--no-auth" in err

    def test_server_no_auth_flag_warns_not_fails(self) -> None:
        from wisp.server.main import _check_bind_auth

        warning = _check_bind_auth("127.0.0.1", True, False)
        assert warning is not None and "DISABLED" in warning

    def test_server_main_entry_fails_fast_without_token(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """main() must refuse before uvicorn ever binds."""
        from unittest.mock import patch

        from wisp.server.main import main

        monkeypatch.delenv("WISP_API_KEY", raising=False)
        with patch("uvicorn.run") as mock_run:
            with pytest.raises(SystemExit) as exc:
                main(host="127.0.0.1", port=8123, no_auth=False)
        assert exc.value.code == 2
        mock_run.assert_not_called()

    def test_server_non_loopback_never_allows_no_auth(self) -> None:
        from wisp.server.main import _check_bind_auth

        with pytest.raises(SystemExit):
            _check_bind_auth("0.0.0.0", True, False)


# ═══════════════════════════════════════════════════════════════════════
# Scenario C: File checkpoint & rewind state machine (GH#15)
# ═══════════════════════════════════════════════════════════════════════

class TestScenarioCCheckpointRewind:
    def _three_turn_fixture(self, ws: Path) -> dict[str, str]:
        """Three successive edit_file turns across a multi-file workspace."""
        from wisp.tools.filesystem import tool_edit_file

        wss = str(ws)
        (ws / "a.txt").write_text("a-v1\n", encoding="utf-8")
        (ws / "b.txt").write_text("b-v1\n", encoding="utf-8")
        (ws / "c.txt").write_text("c-v1\n", encoding="utf-8")
        tool_edit_file(path="a.txt", workspace=wss,
                       old_text="a-v1", new_text="a-v2")
        tool_edit_file(path="b.txt", workspace=wss,
                       old_text="b-v1", new_text="b-v2")
        tool_edit_file(path="c.txt", workspace=wss,
                       old_text="c-v1", new_text="c-v2")
        return {"a": "a-v1\n", "b": "b-v1\n", "c": "c-v1\n"}

    def test_checkpoints_recorded_per_turn(self, tmp_path: Path) -> None:
        from wisp.tools.checkpoints import get_checkpoint_store

        originals = self._three_turn_fixture(tmp_path)
        store = get_checkpoint_store(str(tmp_path))
        assert len(store.list()) == 3, "one checkpoint per mutation turn"
        by_path = {c.path: c for c in store.list()}
        assert set(by_path) == {"a.txt", "b.txt", "c.txt"}
        assert all(c.kind == "edit" for c in store.list())
        assert by_path["a.txt"].content == originals["a"]

    def test_rewind_reverts_target_leaves_siblings(
        self, tmp_path: Path,
    ) -> None:
        from wisp.tools.checkpoints import tool_rewind

        self._three_turn_fixture(tmp_path)
        out = tool_rewind(workspace=str(tmp_path), path="a.txt")
        assert out["status"] == "ok"
        assert (tmp_path / "a.txt").read_text() == "a-v1\n"
        # Sibling turns are untouched — rewind is per-file, not a wipe.
        assert (tmp_path / "b.txt").read_text() == "b-v2\n"
        assert (tmp_path / "c.txt").read_text() == "c-v2\n"

    def test_rewind_is_itself_rewindable(self, tmp_path: Path) -> None:
        from wisp.tools.checkpoints import tool_rewind

        self._three_turn_fixture(tmp_path)
        tool_rewind(workspace=str(tmp_path), path="a.txt")
        assert (tmp_path / "a.txt").read_text() == "a-v1\n"
        tool_rewind(workspace=str(tmp_path), path="a.txt")  # forward again
        assert (tmp_path / "a.txt").read_text() == "a-v2\n"

    def test_failed_mutation_purges_its_checkpoint(
        self, tmp_path: Path,
    ) -> None:
        """Invalid intermediate diffs must not linger in agent state."""
        from wisp.tools.checkpoints import get_checkpoint_store
        from wisp.tools.errors import ToolError
        from wisp.tools.filesystem import tool_edit_file

        self._three_turn_fixture(tmp_path)
        before = len(get_checkpoint_store(str(tmp_path)).list())
        with pytest.raises(ToolError):
            tool_edit_file(path="a.txt", workspace=str(tmp_path),
                           old_text="no-such-needle", new_text="x")
        store = get_checkpoint_store(str(tmp_path))
        assert len(store.list()) == before, "failed edit leaked a checkpoint"
        assert (tmp_path / "a.txt").read_text() == "a-v2\n"

    def test_rewind_list_contract(self, tmp_path: Path) -> None:
        from wisp.tools.checkpoints import tool_rewind

        self._three_turn_fixture(tmp_path)
        listed = tool_rewind(workspace=str(tmp_path), list_only=True)
        assert listed["status"] == "ok"
        assert listed["metadata"]["count"] == 3
        assert all(f"#{i}" in listed["data"] for i in (1, 2, 3))


# ═══════════════════════════════════════════════════════════════════════
# Scenario D: Hook interception & pre/post tracing (GH#16)
# ═══════════════════════════════════════════════════════════════════════

def _hook_mgr(ws: Path, *hooks: dict[str, Any]):
    """Write JSON hook files to the canonical .wisp/hooks/ dir and load."""
    from wisp.infra.hook_types import ToolHookManager

    d = ws / ".wisp" / "hooks"
    d.mkdir(parents=True, exist_ok=True)
    for i, h in enumerate(hooks):
        (d / f"hook-{i}.json").write_text(json.dumps(h), encoding="utf-8")
    mgr = ToolHookManager(workspace=str(ws))
    mgr.load_hooks()
    return mgr


def _hook_cfg(ws: Path) -> WispConfig:
    from wisp.config import PermissionMode

    return WispConfig().replace(workspace=str(ws),
                                permission_mode=PermissionMode.FULL)


def _run_tool(te: Any, name: str, args: dict[str, Any], ws: Path) -> list[Any]:
    async def _go() -> list[Any]:
        return [ev async for ev in te.execute(name, args, str(ws))]

    # asyncio.run = deadlock canary: a hook that blocks the loop hangs here.
    return asyncio.run(_go())


def _result_text(events: list[Any]) -> str:
    for ev in reversed(events):
        data = getattr(ev, "data", {}) or {}
        if isinstance(data, dict) and "result" in data:
            return str(data["result"])
    return ""


class TestScenarioDHookInterception:
    def test_pre_hook_block_stops_tool(self, tmp_path: Path) -> None:
        from wisp.tool_executor import ToolExecutor

        mgr = _hook_mgr(tmp_path, {
            "name": "no-bash", "event": "pre_tool_use", "matcher": "run_bash",
            "command": "echo blocked-by-user-hook >&2; exit 2"})
        te = ToolExecutor(config=_hook_cfg(tmp_path), hook_manager=mgr)
        marker = tmp_path / "should-not-exist.txt"
        text = _result_text(
            _run_tool(te, "run_bash", {"command": f"touch {marker}"}, tmp_path))
        assert "Blocked by hook" in text
        assert "blocked-by-user-hook" in text
        assert not marker.exists(), "blocked command still executed!"

    def test_pre_hook_warn_passes_tool_output_through(
        self, tmp_path: Path,
    ) -> None:
        from wisp.tool_executor import ToolExecutor

        mgr = _hook_mgr(tmp_path, {
            "name": "warn-bash", "event": "pre_tool_use", "matcher": "run_bash",
            "command": "echo careful >&2; exit 1"})
        te = ToolExecutor(config=_hook_cfg(tmp_path), hook_manager=mgr)
        marker = tmp_path / "warn-ran.txt"
        events = _run_tool(te, "run_bash",
                           {"command": f"touch {marker} && echo tool-says-hi"},
                           tmp_path)
        assert marker.exists(), "warn hook wrongly blocked execution"
        assert "tool-says-hi" in _result_text(events), \
            "hook swallowed the tool output"

    def test_post_hook_traces_completion_synchronously(
        self, tmp_path: Path,
    ) -> None:
        """post_tool_use fires awaited (not fire-and-forget-threaded)."""
        from wisp.tool_executor import ToolExecutor

        mgr = _hook_mgr(tmp_path, {
            "name": "trace-post", "event": "post_tool_use",
            "matcher": "run_bash",
            "command": "touch post-trace-marker.txt; exit 0"})
        assert len(mgr.list_hooks()) == 1
        te = ToolExecutor(config=_hook_cfg(tmp_path), hook_manager=mgr)
        events = _run_tool(te, "run_bash", {"command": "echo ok"}, tmp_path)
        assert "ok" in _result_text(events)
        assert (tmp_path / "post-trace-marker.txt").exists(), \
            "post-tool hook did not execute synchronously"

    def test_rewrite_hook_replaces_args_without_deadlock(
        self, tmp_path: Path,
    ) -> None:
        from wisp.tool_executor import ToolExecutor

        rewritten = {"tool_args": {"command": "echo REWRITTEN-ARGS"}}
        mgr = _hook_mgr(tmp_path, {
            "name": "rewrite-bash", "event": "pre_tool_use",
            "matcher": "run_bash",
            "command": f"echo '{json.dumps(rewritten)}'; exit 0"})
        te = ToolExecutor(config=_hook_cfg(tmp_path), hook_manager=mgr)
        text = _result_text(
            _run_tool(te, "run_bash", {"command": "echo ORIGINAL"}, tmp_path))
        assert "REWRITTEN-ARGS" in text
        assert "ORIGINAL" not in text


# ═══════════════════════════════════════════════════════════════════════
# Scenario E: SWE-bench patch serialization (GH#17)
# ═══════════════════════════════════════════════════════════════════════

def _mock_core_factory(
    solve: Callable[[Path], None],
) -> Callable[[str], Any]:
    class _Core:
        def __init__(self, model: str) -> None:
            self.model = model

        async def turn(self, session: dict[str, Any], prompt: str,
                       approval_handler: Any = None):  # type: ignore[no-untyped-def]
            solve(Path(session["workspace"]))
            yield {"type": "content", "text": "done"}

    return lambda model: _Core(model)


def _probe_task() -> Any:
    from wisp.benchmark.tasks import BenchmarkTask

    return BenchmarkTask(
        id="v04-probe",
        title="probe",
        prompt="write answer",
        instance_id="v04-probe-instance-1",
        setup=lambda ws: (ws / "base.py").write_text(
            "BASE = 1\n", encoding="utf-8"),
        verify=lambda ws: (True, ""),
    )


def _solve(ws: Path) -> None:
    (ws / "answer.py").write_text("VALUE = 42\n", encoding="utf-8")


class TestScenarioEBenchSerialization:
    def test_run_task_captures_patch(self, tmp_path: Path) -> None:
        from wisp.benchmark.runner import run_task

        res = asyncio.run(run_task(
            _probe_task(), "mock-model",
            _mock_core_factory(_solve), workdir=tmp_path))
        assert res.passed is True
        assert res.instance_id == "v04-probe-instance-1"
        assert "answer.py" in res.model_patch
        assert "+VALUE = 42" in res.model_patch
        assert res.model_patch.startswith("diff --git")

    def test_patch_parses_with_git_apply_check(
        self, tmp_path: Path,
    ) -> None:
        """The captured diff must re-apply onto the pristine baseline."""
        from wisp.benchmark.runner import run_task

        workdir = tmp_path / "ws"
        workdir.mkdir()
        res = asyncio.run(run_task(
            _probe_task(), "mock-model",
            _mock_core_factory(_solve), workdir=workdir))
        assert res.model_patch, "empty patch — nothing to validate"
        (ws_dir,) = [d for d in workdir.iterdir() if d.is_dir()]
        patch_file = tmp_path / "model.patch"
        patch_file.write_text(res.model_patch, encoding="utf-8")
        # Prove the patch re-applies onto the pristine baseline: export
        # HEAD to a clean dir (no stash — intent-to-add entries make
        # `git stash` refuse) and run git apply --check there.
        pristine = tmp_path / "pristine"
        pristine.mkdir()
        archive = subprocess.run(["git", "archive", "HEAD"], cwd=ws_dir,
                                 capture_output=True, check=True, timeout=30)
        subprocess.run(["tar", "-x", "-C", str(pristine)], input=archive.stdout,
                       check=True, timeout=30)
        proc = subprocess.run(
            ["git", "apply", "--check", str(patch_file)],
            cwd=pristine, capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, \
            f"patch rejected by git apply --check: {proc.stderr}"

    def test_predictions_jsonl_schema(self, tmp_path: Path) -> None:
        import asyncio as _asyncio

        from wisp.benchmark.cli import write_predictions_jsonl
        from wisp.benchmark.runner import run_task

        res = _asyncio.run(run_task(
            _probe_task(), "mock-model",
            _mock_core_factory(_solve), workdir=tmp_path))
        out = write_predictions_jsonl(
            tmp_path / "predictions.jsonl", ["mock-model"], [res])
        lines = out.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        body = json.loads(lines[0])
        # NOTE: repo writes `model_name`; the brief's `model_name_or_path`
        # is the alias the official SWE-bench harness also accepts.
        assert set(body.keys()) == {"instance_id", "model_patch", "model_name"}
        assert body["instance_id"] == "v04-probe-instance-1"
        assert body["model_name"] == "mock-model"
        assert "answer.py" in body["model_patch"]

    def test_run_bench_predictions_flag_end_to_end(
        self, tmp_path: Path,
    ) -> None:
        from wisp.benchmark.cli import run_bench

        rc = run_bench(
            ["--models", "mock-model", "--tasks", "json-edit",
             "--workdir", str(tmp_path / "ws"),
             "--predictions", str(tmp_path / "predictions.jsonl")],
            core_factory=_mock_core_factory(
                lambda ws: (ws / "out.json").write_text(
                    "{}", encoding="utf-8")),
        )
        assert rc in (0, 1)  # pass/fail is task business; wiring is ours
        lines = (tmp_path / "predictions.jsonl").read_text(
            encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert set(json.loads(lines[0]).keys()) == {
            "instance_id", "model_patch", "model_name"}
