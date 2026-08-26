"""OwnedTasks: TUI background tasks must never fail silently.

An approval-forward that dies used to vanish — the server then hit its
60s approval timeout and DENIED while the user believed they had
approved. Every spawn now has a name, exception logging, and teardown
cancellation.
"""

import asyncio
import logging

import pytest

from wisp.tui.task_owner import OwnedTasks


@pytest.mark.asyncio
async def test_exception_is_retrieved_and_logged(caplog):
    owner = OwnedTasks()
    seen = asyncio.Event()

    async def _boom():
        raise RuntimeError("approval forward died")

    def _reap(task):
        OwnedTasks._reap(owner, task)
        seen.set()

    owner._reap = _reap
    with caplog.at_level(logging.ERROR):
        owner.spawn(_boom(), name="approval-forward")
        await asyncio.wait_for(seen.wait(), timeout=2)

    assert any(
        r.exc_info is not None
        and isinstance(r.exc_info[1], RuntimeError)
        and "approval forward died" in str(r.exc_info[1])
        for r in caplog.records
    ), [(r.message, r.exc_info) for r in caplog.records]
    assert len(owner) == 0  # reaped


@pytest.mark.asyncio
async def test_cancel_all_stops_live_work():
    owner = OwnedTasks()

    async def _park():
        await asyncio.Event().wait()

    t1 = owner.spawn(_park(), name="send-prompt")
    t2 = owner.spawn(_park(), name="interrupt")
    await asyncio.sleep(0.02)
    assert len(owner) == 2
    assert owner.cancel_all() == 2
    await asyncio.sleep(0.02)
    assert t1.cancelled() and t2.cancelled()
    assert len(owner) == 0


@pytest.mark.asyncio
async def test_completed_tasks_discard_themselves():
    owner = OwnedTasks()

    async def _quick():
        return 42

    task = owner.spawn(_quick(), name="mount-user")
    assert await asyncio.wait_for(task, timeout=2) == 42
    await asyncio.sleep(0.02)
    assert len(owner) == 0


def test_no_bare_fire_and_forget_left_in_tui_screens():
    """Structural pin: every create_task in TUI screens must be either
    stored in a tracked attribute or spawned through the owner."""
    from pathlib import Path
    import re

    screens = Path("wisp/tui")
    offenders = []
    for path in sorted(screens.rglob("*.py")):
        text = path.read_text()
        for m in re.finditer(r"^\s*asyncio\.create_task\(", text, flags=re.M):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line = text[line_start:text.find("\n", m.start())]
            assigned = re.match(r"\s*(\w+)\s*=\s*asyncio\.create_task", line)
            if not (assigned and assigned.group(1).endswith("_task")):
                offenders.append(f"{path.name}:{text[:m.start()].count(chr(10)) + 1}")
    assert not offenders, f"untracked TUI tasks: {offenders}"
