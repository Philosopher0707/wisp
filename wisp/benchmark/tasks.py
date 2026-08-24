"""Deterministic benchmark tasks with machine-checkable outcomes.

Every task ships a setup() that populates an isolated workspace and a
verify() that decides pass/fail by executing code — never by asking a
model to judge another model.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class BenchmarkTask:
    """One benchmark scenario: a prompt plus deterministic verification."""

    id: str
    title: str
    prompt: str
    difficulty: str = "easy"
    setup: Callable[[Path], None] = lambda ws: None
    verify: Callable[[Path], tuple[bool, str]] = lambda ws: (False, "no verifier")
    # Optional capability gate on the turn's event stream — e.g. prove a
    # subagent was actually spawned rather than the model soloing the task.
    verify_events: Callable[[list[dict]], tuple[bool, str]] | None = None


def _run_python(code: str, workspace: Path) -> tuple[bool, str]:
    """Execute code in the workspace and report success + diagnostic."""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode == 0:
        return True, ""
    detail = (proc.stderr or proc.stdout).strip().splitlines()
    return False, detail[-1][:200] if detail else f"exit {proc.returncode}"


# ── Task 1: create a module with a function ─────────────────────────


def _setup_create_function(ws: Path) -> None:
    (ws / "strings_util.py").write_text(
        '"""String helpers."""\n\n\ndef greet(name):\n    return "hello"\n',
        encoding="utf-8",
    )


_CREATE_FUNCTION_CHECK = """
from strings_util import shout
out = shout("wisp")
assert out == "WISP!", f"got {out!r}"
"""


def _verify_create_function(ws: Path) -> tuple[bool, str]:
    return _run_python(_CREATE_FUNCTION_CHECK, ws)


CREATE_FUNCTION = BenchmarkTask(
    id="create-function",
    title="Add a shout() function that upper-cases its input",
    prompt=(
        "In strings_util.py there is a greet() function. Add a function "
        "shout(name) to the same file that returns the name uppercased "
        "with an exclamation mark appended. Example: shout('wisp') == 'WISP!'. "
        "Edit strings_util.py in place."
    ),
    difficulty="easy",
    setup=_setup_create_function,
    verify=_verify_create_function,
)


# ── Task 2: fix an off-by-one bug ───────────────────────────────────


def _setup_fix_bug(ws: Path) -> None:
    (ws / "totals.py").write_text(
        "def sum_to(n):\n"
        '    """Sum integers 1..n inclusive."""\n'
        "    total = 0\n"
        "    for i in range(1, n):\n"
        "        total += i\n"
        "    return total\n",
        encoding="utf-8",
    )


_FIX_BUG_CHECK = """
from totals import sum_to
assert sum_to(1) == 1, f"sum_to(1) -> {sum_to(1)}"
assert sum_to(5) == 15, f"sum_to(5) -> {sum_to(5)}"
assert sum_to(10) == 55, f"sum_to(10) -> {sum_to(10)}"
"""


def _verify_fix_bug(ws: Path) -> tuple[bool, str]:
    ok, diag = _run_python(_FIX_BUG_CHECK, ws)
    if not ok:
        return False, diag
    text = (ws / "totals.py").read_text(encoding="utf-8")
    if "range(1, n)" in text:
        return False, "loop bound still excludes n"
    return True, ""


FIX_BUG = BenchmarkTask(
    id="fix-off-by-one",
    title="Fix sum_to so it includes n in the range",
    prompt=(
        "totals.py defines sum_to(n) which should sum integers 1..n "
        "inclusive, but it is off by one: sum_to(5) returns 10 instead "
        "of 15. Fix the bug in totals.py."
    ),
    difficulty="easy",
    setup=_setup_fix_bug,
    verify=_verify_fix_bug,
)


# ── Task 3: structured JSON edit ────────────────────────────────────


def _setup_json_edit(ws: Path) -> None:
    (ws / "settings.json").write_text(
        json.dumps({"name": "demo", "retries": 3, "verbose": False}, indent=2),
        encoding="utf-8",
    )


_JSON_EDIT_CHECK = """
import json
cfg = json.load(open("settings.json"))
assert cfg["retries"] == 7, f"retries -> {cfg['retries']}"
assert cfg["name"] == "demo", "must not change other keys"
assert cfg["verbose"] is False, "must not change other keys"
"""


def _verify_json_edit(ws: Path) -> tuple[bool, str]:
    return _run_python(_JSON_EDIT_CHECK, ws)


JSON_EDIT = BenchmarkTask(
    id="json-edit",
    title="Change retries to 7 in settings.json, touching nothing else",
    prompt=(
        "settings.json holds app settings. Change only the retries "
        "value to 7. Keep formatting valid JSON; do not modify any "
        "other key."
    ),
    difficulty="easy",
    setup=_setup_json_edit,
    verify=_verify_json_edit,
)


# ── Task 4: must delegate to a subagent ─────────────────────────────


def _setup_subagent_delegate(ws: Path) -> None:
    lines: list[str] = []
    todo_n = 0
    for i in range(28):
        if i in (2, 5, 9, 13, 17, 21, 25):
            todo_n += 1
            lines.append(f"TODO: item {todo_n} — revisit module {i}")
        else:
            lines.append(f"Line {i}: settled context, no action required.")
    assert todo_n == 7
    d = ws / "data"
    d.mkdir()
    (d / "notes.txt").write_text("\n".join(lines), encoding="utf-8")


_SUBAGENT_DELEGATE_CHECK = """
from pathlib import Path
answer = Path("answer.txt")
assert answer.exists(), "answer.txt missing"
val = answer.read_text().strip()
assert val == "7", f"expected 7 TODOs, got {val!r}"
"""


def _verify_subagent_delegate(ws: Path) -> tuple[bool, str]:
    return _run_python(_SUBAGENT_DELEGATE_CHECK, ws)


def _verify_spawned(events: list[dict]) -> tuple[bool, str]:
    spawned = any(
        e.get("type") == "tool_call" and e.get("name") == "spawn"
        for e in events
    )
    if not spawned:
        tool_names = sorted({
            str(e.get("name")) for e in events
            if e.get("type") == "tool_call"
        })
        return False, (
            "no subagent spawn — model answered solo "
            f"(tools used: {tool_names})"
        )
    return True, ""


SUBAGENT_DELEGATE = BenchmarkTask(
    id="subagent-delegate",
    title=(
        "Delegate counting TODOs in data/notes.txt to a subagent, "
        "write the count to answer.txt"
    ),
    prompt=(
        "Use a subagent (the spawn tool) to read data/notes.txt and count "
        "how many TODO items it contains. Then write just that number to "
        "answer.txt. The file must contain only the number."
    ),
    difficulty="hard",
    setup=_setup_subagent_delegate,
    verify=_verify_subagent_delegate,
    verify_events=_verify_spawned,
)


DEFAULT_TASKS: list[BenchmarkTask] = [CREATE_FUNCTION, FIX_BUG, JSON_EDIT, SUBAGENT_DELEGATE]


def tasks_by_ids(ids: list[str] | None) -> list[BenchmarkTask]:
    """Resolve requested task ids, defaulting to the full suite."""
    if not ids:
        return list(DEFAULT_TASKS)
    known = {t.id: t for t in DEFAULT_TASKS}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise ValueError(f"Unknown task(s): {', '.join(unknown)}")
    return [known[i] for i in ids]
