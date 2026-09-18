"""TUI task ownership: screens must not spawn bare asyncio tasks.

Context (see PHASE_BOUNDARY_FORENSIC.md / F13):

`wisp/tui/task_owner.py` exists precisely because bare `asyncio.create_task`
leaks in three ways — its own module docstring says so. It provides
`OwnedTasks.spawn()` (names the task, logs exceptions via a done-callback) and
`cancel_all()` (called from `on_unmount`).

Two screen sites bypassed it:
  - `workspace.py` `_ws_task` — assigned and **never read**, so a failure in
    `_start_ws` vanished with no log and no cancellation.
  - `workspace.py` `_local_task` — had an explicit cancel path, but no
    exception logging and no unmount coverage.

Both now go through `_owned.spawn()`.

`wisp/tui/data/ws_client.py` is a **deliberate exception**: it is not a
Textual screen, and its task has an explicit lifecycle (awaited and cancelled
in `close()`), so it is already owned.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent

# The one module allowed to call asyncio.create_task directly — it is the
# implementation of the ownership helper.
_OWNER_MODULE = "wisp/tui/task_owner.py"

# Not a screen; task is awaited + cancelled explicitly in close().
_KNOWN_EXCEPTION = "wisp/tui/data/ws_client.py"


def _bare_create_task_calls(path: pathlib.Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_task"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"):
            lines.append(node.lineno)
    return lines


def test_no_bare_create_task_in_the_tui():
    offenders: dict[str, list[int]] = {}
    for py in (REPO / "wisp/tui").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = str(py.relative_to(REPO))
        if rel in (_OWNER_MODULE, _KNOWN_EXCEPTION):
            continue
        lines = _bare_create_task_calls(py)
        if lines:
            offenders[rel] = lines
    assert not offenders, (
        "bare asyncio.create_task in the TUI — use OwnedTasks.spawn() so the "
        f"task is named, its exceptions are logged, and unmount cancels it: {offenders}"
    )


def test_the_known_exception_still_owns_its_task():
    """If ws_client stops cancelling/awaiting, it must move onto OwnedTasks."""
    src = (REPO / _KNOWN_EXCEPTION).read_text(encoding="utf-8")
    assert "_connect_task.cancel()" in src, (
        "ws_client no longer cancels its connect task; it is now a leak and "
        "must use OwnedTasks"
    )
    assert "await self._connect_task" in src


def test_workspace_screen_spawns_through_owned_tasks():
    src = (REPO / "wisp/tui/screens/workspace.py").read_text(encoding="utf-8")
    assert "asyncio.create_task" not in src, (
        "workspace.py reintroduced a bare create_task"
    )
    assert '_owned.spawn(self._start_ws()' in src
    assert "_local_task = self._owned.spawn(" in src


def test_owned_tasks_logs_failures():
    """The done-callback is the whole point — a silent failure is the bug."""
    src = (REPO / "wisp/tui/task_owner.py").read_text(encoding="utf-8")
    assert "add_done_callback" in src
    assert "logger.error" in src
    assert "cancel_all" in src
