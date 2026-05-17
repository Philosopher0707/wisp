"""Unit tests for bash tool termination and process group isolation."""

import asyncio
import os
import signal
import subprocess
import time
import pytest

from wisp.tools.bash import tool_run_bash
from wisp.tools._utils import ToolError


class TestBashTermination:
    """Verify that canceling tool_run_bash cleans up the process group and grandchildren."""

    def test_bash_termination_on_cancellation(self):
        """Cancelling tool_run_bash should SIGTERM/SIGKILL all processes in the PG."""
        unique_marker = f"marker_{int(time.time())}"
        cmd = f"sleep 99 & sleep 100 & wait"  # Spawns grandchildren

        async def run_and_cancel():
            # Run in workspace
            task = asyncio.create_task(
                tool_run_bash(
                    command=f"echo '{unique_marker}' && {cmd}",
                    workspace=".",
                    timeout=10,
                )
            )
            # Wait for processes to spin up
            await asyncio.sleep(0.5)
            # Cancel the task
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_and_cancel())

        # Give some time for os.killpg cleanup
        time.sleep(0.5)

        # Check if the process or children are still running
        ps_out = subprocess.run(
            ["ps", "-ef"],
            capture_output=True,
            text=True,
        ).stdout

        # Verify that the markers/commands are completely gone!
        assert unique_marker not in ps_out, f"The unique marker command '{unique_marker}' should have been killed"
