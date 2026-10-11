"""run_bash must not hide a failed pipeline stage behind the last stage's exit code.

`swift build 2>&1 | tail -30` exits 0 when swift fails and tail succeeds, so the agent (and the log's
`# exit: 0`) reported success on a failed build. The exit code keeps its shell meaning, but a failed earlier
stage is now stated on the first line of the result. Real bash, real pipelines; nothing is mocked.
"""

from __future__ import annotations

import asyncio

import pytest

from wisp.sandbox.router import SandboxRouter
from wisp.tools import bash as bash_mod


@pytest.fixture(autouse=True)
def _host_bash(monkeypatch):
    monkeypatch.setenv("WISP_SANDBOX", "off")


def _run(cmd: str, ws) -> bash_mod.BashRun:
    return asyncio.run(bash_mod.run_bash_confined(cmd, str(ws), 20))


def _tool(cmd: str, ws) -> str:
    return asyncio.run(bash_mod.async_tool_run_bash(cmd, str(ws), 20))


# A stage with a fixed exit status: `ls /missing` exits 1 on macOS but 2 on Linux, and CI is Linux.
_FAILING_STAGE = "{ echo boom >&2; exit 4; } 2>&1 | tail -1"


def test_failed_first_stage_behind_a_successful_tail_is_reported(tmp_path):
    out = _tool(_FAILING_STAGE, tmp_path)
    first = out.splitlines()[0]
    assert first.startswith("[pipeline:") and "stage 1" in first and "exited 4" in first
    assert "boom" in out  # the command's own output is still there


def test_exit_code_keeps_its_shell_meaning(tmp_path):
    run = _run("false | true", tmp_path)
    assert run.returncode == 0
    assert run.pipeline_failures == ((1, 1),)
    assert "[exit code" not in _tool("false | true", tmp_path)


def test_command_not_found_is_127(tmp_path):
    assert _run("no_such_tool_xyz 2>&1 | cat", tmp_path).pipeline_failures == ((1, 127),)


def test_clean_pipeline_output_is_untouched(tmp_path):
    assert _tool("echo hi | cat", tmp_path) == "hi\n"
    assert _run("echo hi | cat", tmp_path).pipeline_failures == ()


def test_sigpipe_from_head_is_not_a_failure(tmp_path):
    run = _run("yes | head -1", tmp_path)
    assert run.pipeline_failures == ()
    assert run.stdout == "y\n"


def test_no_pipeline_no_note_and_no_marker_leak(tmp_path):
    run = _run("echo a; echo b >&2", tmp_path)
    assert run.stdout == "a\n" and run.stderr == "b\n"
    assert run.pipeline_failures == ()


def test_a_command_that_already_fails_is_not_double_reported(tmp_path):
    run = _run("set -o pipefail; false | true", tmp_path)
    assert run.returncode == 1
    assert run.pipeline_failures == ()  # the exit code already says it; the note is for the masked case
    assert _tool("set -o pipefail; false | true", tmp_path).startswith("[exit code: 1]")


def test_explicit_exit_status_is_preserved(tmp_path):
    assert _run("echo x | cat; exit 7", tmp_path).returncode == 7


def test_the_danger_gate_still_sees_the_original_command(tmp_path):
    with pytest.raises(Exception, match="Dangerous"):
        _run("rm -rf / | cat", tmp_path)


def test_marker_from_a_merged_stream_provider_is_parsed_and_stripped(tmp_path, monkeypatch):
    """The PTY tier merges stderr into stdout, so the marker can arrive in stdout."""

    class Merged:
        name = "pty"

        def is_available(self):
            return True

        async def run(self, command, cwd="", timeout=60):
            return (0, "visible\r\n\r\n__WISP_PIPESTATUS__ 2 0\r\n", "")

    monkeypatch.delenv("WISP_SANDBOX", raising=False)
    monkeypatch.setattr(bash_mod, "get_router", lambda w: SandboxRouter(w, tiers=[Merged()]))
    run = _run("irrelevant", tmp_path)
    assert run.stdout == "visible\r\n"
    assert run.pipeline_failures == ((1, 2),)


def test_a_provider_that_never_ran_the_epilogue_is_left_alone(tmp_path, monkeypatch):
    class Plain:
        name = "fake"

        def is_available(self):
            return True

        async def run(self, command, cwd="", timeout=60):
            return (0, "canned", "")

    monkeypatch.delenv("WISP_SANDBOX", raising=False)
    monkeypatch.setattr(bash_mod, "get_router", lambda w: SandboxRouter(w, tiers=[Plain()]))
    run = _run("x", tmp_path)
    assert run.stdout == "canned" and run.pipeline_failures == ()


def test_the_real_pty_tier_reports_it_too(tmp_path, monkeypatch):
    from wisp.sandbox.router import PtySandbox

    monkeypatch.delenv("WISP_SANDBOX", raising=False)
    monkeypatch.setattr(bash_mod, "get_router", lambda w: SandboxRouter(w, tiers=[PtySandbox(w)]))
    run = _run(_FAILING_STAGE, tmp_path)
    assert run.provider == "pty" and run.returncode == 0
    assert run.pipeline_failures == ((1, 4),)
    assert "__WISP_PIPESTATUS__" not in run.stdout + run.stderr
