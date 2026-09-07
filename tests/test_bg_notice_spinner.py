"""GH#8: background lifecycle notices must not tear the spinner live row.

``CLITransport._watch_background`` writes settle/started notices to stdout.
While a spinner row is active those writes must be wrapped in the same
pause/write/resume protocol the SUBAGENT branch uses — otherwise a notice
landing mid-frame smears the row.
"""

from __future__ import annotations

import asyncio
import io
import types

import pytest

from wisp.terminal_width import OutputMode
from wisp.transport.cli import CLITransport
from wisp.transport.spinner import Spinner


class _OrderLog(io.StringIO):
    """StringIO that records every write into a shared order log."""

    def __init__(self, log: list, tag: str) -> None:
        super().__init__()
        self._log = log
        self._tag = tag

    def write(self, s: str) -> int:  # type: ignore[override]
        self._log.append((self._tag, s))
        return super().write(s)


def _drive(event: dict) -> tuple[list, str]:
    """Run _watch_background until one notice lands; return (order, text)."""
    order: list = []
    out = _OrderLog(order, "notice")
    transport = CLITransport(runtime=object())
    transport._stdout = out

    spinner = Spinner(_OrderLog(order, "frame"), mode=OutputMode.UNICODE)
    spinner.start("parent tool running")
    transport._spinner = spinner
    orig_pause, orig_resume = spinner.pause, spinner.resume

    def pause() -> None:
        order.append(("spinner-ctl", "pause"))
        orig_pause()

    def resume() -> None:
        order.append(("spinner-ctl", "resume"))
        orig_resume()

    spinner.pause = pause  # type: ignore[method-assign]
    spinner.resume = resume  # type: ignore[method-assign]

    async def run() -> str:
        queue: asyncio.Queue = asyncio.Queue()
        transport.background_agents = types.SimpleNamespace(subscribe=lambda: queue)
        task = asyncio.create_task(transport._watch_background())
        try:
            await queue.put(event)
            for _ in range(200):
                if "settled" in out.getvalue() or "started" in out.getvalue():
                    break
                await asyncio.sleep(0.01)
            return out.getvalue()
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            spinner.stop()

    text = asyncio.run(run())
    return order, text


def _ctl_positions(order: list) -> tuple[int | None, int | None, int | None]:
    pause_at = next((i for i, e in enumerate(order) if e == ("spinner-ctl", "pause")), None)
    resume_at = next((i for i, e in enumerate(order) if e == ("spinner-ctl", "resume")), None)
    write_at = next(
        (i for i, e in enumerate(order) if e[0] == "notice" and ("settled" in e[1] or "started" in e[1])),
        None,
    )
    return pause_at, write_at, resume_at


@pytest.mark.parametrize(
    "event",
    [
        {"type": "agent_settled", "agent_id": "a1", "label": "worker-1",
         "ok": True, "elapsed_seconds": 3, "summary": "done"},
        {"type": "agent_started", "agent_id": "a2", "label": "worker-2"},
    ],
)
def test_bg_notice_pauses_and_resumes_active_spinner(event: dict) -> None:
    order, text = _drive(event)
    assert "worker-" in text  # notice actually rendered
    pause_at, write_at, resume_at = _ctl_positions(order)
    assert pause_at is not None, "spinner.pause() never called around bg notice"
    assert write_at is not None, "notice write not observed"
    assert resume_at is not None, "spinner.resume() never called around bg notice"
    assert pause_at < write_at < resume_at, (
        f"expected pause < write < resume, got {pause_at} {write_at} {resume_at}"
    )
