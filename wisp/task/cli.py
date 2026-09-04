"""Task CLI (M6 T4): start/list/inspect/review/approve-plan/pause/resume/
cancel/export-evidence. Output contract: human text by default, --json
emits {"ok","data","error"}. Exit codes: 0 ok, 1 denied/failed/empty,
2 usage. `wisp completion bash|zsh` prints a static script.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO


def _db_path(args: list[str]) -> Path:
    if "--db" in args:
        return Path(args[args.index("--db") + 1])
    env = os.environ.get("WISP_DB")
    if env:
        return Path(env)
    if "--workspace" in args:
        return Path(args[args.index("--workspace") + 1]) / ".wisp" / "wisp.db"
    return Path.cwd() / ".wisp" / "wisp.db"


def _json_mode(args: list[str]) -> bool:
    return "--json" in args


def _clean(args: list[str]) -> list[str]:
    # Strip only --json: --db/--workspace pairs must survive so _db_path
    # and the per-command parsers (goal-first positional order) see them.
    return [a for a in args if a != "--json"]


def _manager(db: Path):
    from wisp.infra.store import UnifiedStore
    from wisp.runs.store import SQLiteRunStore
    from wisp.task.manager import TaskManager
    return TaskManager(SQLiteRunStore(UnifiedStore(db)))


def _emit(out: TextIO, as_json: bool, ok: bool, data: Any, error: str = "") -> int:
    if as_json:
        print(json.dumps({"ok": ok, "data": data, "error": error},
                         sort_keys=True), file=out)
    elif ok and isinstance(data, str):
        print(data, file=out)
    elif not ok:
        print(f"error: {error}", file=out)
    return 0 if ok else 1


def _cmd_start(args: list[str], out: TextIO, as_json: bool) -> int:
    if not args:
        print("usage: wisp task start GOAL [--workspace D]", file=out)
        return 2
    goal = args[0]
    ws = "."
    if "--workspace" in args:
        ws = args[args.index("--workspace") + 1]
    tid = _manager(_db_path(args)).start(goal, workspace=ws)
    if as_json:
        return _emit(out, True, True, {"task_id": tid})
    print(tid, file=out)
    return 0


def _cmd_list(args: list[str], out: TextIO, as_json: bool) -> int:
    tasks = _manager(_db_path(args)).list()
    if as_json:
        return _emit(out, True, True, tasks)
    if not tasks:
        print("no tasks", file=out)
        return 0
    for t in tasks:
        print(f"{t['task_id']} [{t['status']}] {t['goal'][:80]}", file=out)
    return 0


def _cmd_inspect(args: list[str], out: TextIO, as_json: bool) -> int:
    if not args:
        print("usage: wisp task inspect <task-id>", file=out)
        return 2
    try:
        data = _manager(_db_path(args)).inspect(args[0])
    except KeyError as e:
        return _emit(out, as_json, False, None, str(e))
    if as_json:
        return _emit(out, True, True, data)
    print(f"{data['task_id']} [{data['status']}] {data['goal']}", file=out)
    print(f"workspace: {data['workspace']} model: {data['model']}", file=out)
    for t in data["transitions"]:
        print(f"  {t['from']} -> {t['to']} ({t['reason']})", file=out)
    return 0


def _cmd_review(args: list[str], out: TextIO, as_json: bool) -> int:
    if not args:
        print("usage: wisp task review <task-id>", file=out)
        return 2
    from wisp.task.review import render_review
    try:
        data = _manager(_db_path(args)).inspect(args[0])
    except KeyError as e:
        return _emit(out, as_json, False, None, str(e))
    plan = data.get("plan")
    if not plan:
        return _emit(out, as_json, False, None,
                     f"task {args[0]} has no attached plan")
    text = render_review(plan)
    if as_json:
        return _emit(out, True, True, {"review": text})
    print(text, file=out)
    return 0


def _cmd_approve_plan(args: list[str], out: TextIO, as_json: bool) -> int:
    if not args:
        print("usage: wisp task approve-plan <task-id> [--plan FILE] "
              "[--scope all|<class>] [--approver NAME]", file=out)
        return 2
    from wisp.task.review import approve_scope
    mgr = _manager(_db_path(args))
    scope = "all"
    if "--scope" in args:
        scope = args[args.index("--scope") + 1]
    approver = "cli"
    if "--approver" in args:
        approver = args[args.index("--approver") + 1]
    try:
        if "--plan" in args:
            plan = json.loads(Path(args[args.index("--plan") + 1]).read_text())
            mgr.attach_plan(args[0], plan)
        data = mgr.inspect(args[0])
        plan = data.get("plan")
        if not plan:
            return _emit(out, as_json, False, None, "no attached plan")
        decision = approve_scope(plan, scope=scope, approver=approver)
    except (KeyError, ValueError) as e:
        return _emit(out, as_json, False, None, str(e))
    if as_json:
        return _emit(out, True, True, decision)
    print(f"approved scope={decision['scope']} "
          f"classes={','.join(decision['action_classes'])}", file=out)
    if decision["pending"]:
        print(f"pending: {','.join(decision['pending'])}", file=out)
    return 0


def _cmd_pause_resume_cancel(verb: str, args: list[str], out: TextIO,
                             as_json: bool) -> int:
    if not args:
        print(f"usage: wisp task {verb} <task-id>", file=out)
        return 2
    mgr = _manager(_db_path(args))
    try:
        data = {"pause": mgr.pause, "resume": mgr.resume,
                "cancel": mgr.cancel}[verb](args[0])
    except (KeyError, ValueError) as e:
        return _emit(out, as_json, False, None, str(e))
    if as_json:
        return _emit(out, True, True, data)
    print(f"{args[0]}: {data['status']}", file=out)
    return 0


def _cmd_export_evidence(args: list[str], out: TextIO, as_json: bool) -> int:
    # Reuse the M5 implementation (trace ids and task ids share the store).
    # Evidence is already JSON; no envelope wrapping here.
    from wisp.trace.cli import task_main
    return task_main(["export-evidence", *args], out=out)


_COMPLETION_BASH = """# wisp shell completion (bash)
_wisp_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local cmds="run repl tui task trace replay audit policy plan session memory mcp git config check models compact skills completion"
    COMPREPLY=($(compgen -W "$cmds" -- "$cur"))
}
complete -F _wisp_complete wisp
"""

_COMPLETION_ZSH = """#compdef wisp
_wisp() {
    local cmds=(run repl tui task trace replay audit policy plan session memory mcp git config check models compact skills completion)
    _describe 'command' cmds
}
_wisp
"""


def _cmd_completion(args: list[str], out: TextIO) -> int:
    if not args or args[0] not in ("bash", "zsh"):
        print("usage: wisp completion <bash|zsh>", file=out)
        return 2
    print(_COMPLETION_BASH if args[0] == "bash" else _COMPLETION_ZSH, file=out)
    return 0


_COMMANDS = ("start", "list", "inspect", "review", "approve-plan", "pause",
             "resume", "cancel", "export-evidence")


def main(argv: list[str], out: TextIO | None = None) -> int:
    out = out if out is not None else sys.stdout
    as_json = _json_mode(argv)
    args = _clean(argv)
    if not args or args[0] not in _COMMANDS:
        print(f"usage: wisp task <{'|'.join(_COMMANDS)}> [--json]", file=out)
        return 2
    cmd, rest = args[0], args[1:]
    if cmd == "start":
        return _cmd_start(rest, out, as_json)
    if cmd == "list":
        return _cmd_list(rest, out, as_json)
    if cmd == "inspect":
        return _cmd_inspect(rest, out, as_json)
    if cmd == "review":
        return _cmd_review(rest, out, as_json)
    if cmd == "approve-plan":
        return _cmd_approve_plan(rest, out, as_json)
    if cmd in ("pause", "resume", "cancel"):
        return _cmd_pause_resume_cancel(cmd, rest, out, as_json)
    if cmd == "export-evidence":
        return _cmd_export_evidence(rest, out, as_json)
    return 2
