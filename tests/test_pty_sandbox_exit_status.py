"""The PTY tier reports the command's real exit status.

EOF on the pty master does not reap the child, so `returncode` was still None
when the read loop ended and `None or 0` turned every failing command into a
success — including the verification commands the goal state is derived from.
"""

import os

import pytest

from wisp.sandbox.router import PtySandbox

pytestmark = pytest.mark.skipif(os.name != "posix", reason="pty tier is POSIX-only")


@pytest.mark.asyncio
@pytest.mark.parametrize("command, expected", [
    ("exit 3", 3),
    ("false", 1),
    ("true", 0),
    ("echo hi; exit 7", 7),
])
async def test_the_exit_status_is_the_commands(tmp_path, command, expected):
    rc, _out, _err = await PtySandbox(str(tmp_path)).run(command, timeout=10)
    assert rc == expected


@pytest.mark.asyncio
async def test_output_past_the_cap_still_reports_the_exit_status(tmp_path):
    rc, out, _err = await PtySandbox(str(tmp_path)).run(
        "head -c 400000 /dev/zero | tr '\\0' x; exit 5", timeout=20)
    assert rc == 5
    assert out.endswith("[output truncated]")


@pytest.mark.asyncio
async def test_a_command_past_its_deadline_times_out(tmp_path):
    rc, _out, err = await PtySandbox(str(tmp_path)).run("sleep 5", timeout=1)
    assert rc == -1
    assert "timed out" in err
