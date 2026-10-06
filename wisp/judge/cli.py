"""CLI glue for ``wisp judge``."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from wisp.judge import core
from wisp.judge.improve import log_to, run_loop, wisp_proposer
from wisp.judge.tasks import HELD_OUT

STATE_DIR = Path(".wisp") / "judge"


def run_judge(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="wisp judge",
        description="Mechanical judge over wisp's task results: SOLVED, GAMED, FAILED, NO-OP, INFRA, BROKEN.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run wisp on tasks in fresh workspaces and judge the result")
    r.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--json-out", default="")
    r.add_argument("--addendum-file", default="", help="text prepended to each task prompt")
    core.add_run_flags(r)

    i = sub.add_parser("improve", help="tune a prompt addendum; adopt a change only if the judge confirms it")
    i.add_argument("--iterations", type=int, default=3)
    i.add_argument("--repeat", type=int, default=2)
    i.add_argument("--state-dir", default=str(STATE_DIR), help="holds addendum.md and learnings.md")
    core.add_run_flags(i)
    i.set_defaults(timeout=300)

    j = sub.add_parser("judge", help="judge an existing run: TASK_ID BEFORE_DIR AFTER_DIR")
    j.add_argument("task_id")
    j.add_argument("before")
    j.add_argument("after")
    j.add_argument("--result", default="", help="wisp --print JSON output file, for the claim")

    sub.add_parser("list", help="list the tasks")
    args = ap.parse_args(argv)

    tasks = core.load_tasks()
    if args.cmd == "list":
        for t in tasks.values():
            print(f"{t.id}{' (held-out)' if t.id in HELD_OUT else ''}: {t.prompt}")
        return 0
    if args.cmd == "judge":
        if args.task_id not in tasks:
            print(f"unknown task {args.task_id!r}; known {sorted(tasks)}", file=sys.stderr)
            return 2
        before = core.snapshot(Path(args.before))
        claim = core.parse_claim(Path(args.result).read_text()) if args.result else None
        v = core.judge(tasks[args.task_id], before, Path(args.after), claim=claim)
        print(json.dumps(asdict(v), indent=2))
        return 0 if v.verdict == "SOLVED" else 1

    cfg = core.run_config(args)
    try:
        core.child_env(cfg.api_base, cfg.key_from)  # fail early on a missing key
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.cmd == "run":
        wanted = {t for t in args.tasks.split(",") if t}
        unknown = wanted - set(tasks)
        if unknown:
            print(f"unknown tasks {sorted(unknown)}; known {sorted(tasks)}", file=sys.stderr)
            return 2
        add = Path(args.addendum_file).read_text(encoding="utf-8") if args.addendum_file else ""
        results = []
        for _ in range(args.repeat):
            for t in tasks.values():
                if not wanted or t.id in wanted:
                    print(f"running {t.id} ...", file=sys.stderr, flush=True)
                    results.append(core.run_one(t, cfg, add))
        if args.json_out:
            Path(args.json_out).write_text(json.dumps([asdict(x) for x in results], indent=2))
        return core.report(results)

    # improve
    state = Path(args.state_dir)
    addendum_path, learnings = state / "addendum.md", state / "learnings.md"
    current = addendum_path.read_text(encoding="utf-8") if addendum_path.exists() else ""
    held = [t for t in tasks.values() if t.id in HELD_OUT]
    tune = [t for t in tasks.values() if t.id not in HELD_OUT]
    final = run_loop(
        tune, held, tasks, repeat=args.repeat, iterations=args.iterations,
        runner=lambda t, add: core.run_one(t, cfg, add), proposer=wisp_proposer(cfg),
        current=current, log=lambda e: log_to(learnings, e))
    if final.strip() != current.strip():
        state.mkdir(parents=True, exist_ok=True)
        addendum_path.write_text(final, encoding="utf-8")
        print(f"adopted a new addendum -> {addendum_path}")
    else:
        print("no change adopted")
    print(f"details in {learnings}")
    return 0
