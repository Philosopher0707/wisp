"""`wisp converge` — the CLI face of the objective-level loop.

Kept out of `wisp/autonomous.py` so the library module has no `argparse` or
`sys.exit` in it: the loop must stay callable from the benchmark, the server
and a test without dragging a CLI along.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wisp converge",
        description=("Run an objective to convergence: measure acceptance "
                     "evidence after each attempt and re-attempt with a "
                     "strategy chosen by the recovery ladder."),
    )
    parser.add_argument("objective", help="The objective, in plain language")
    # NOTE: `--model/-m` and `--workspace/-w` are NOT declared here. They are
    # GLOBAL flags, stripped from argv by `extract_global_flags` before a
    # subcommand handler ever sees its arguments — so declaring them locally
    # would silently do nothing and the run would fall back to the config's
    # model and the process CWD. `main` passes the extracted values in.
    parser.add_argument("--max-attempts", type=int, default=3,
                        help="Attempt budget (default 3)")
    parser.add_argument("--permission-mode", default=None,
                        help=("Tool permission mode (full | auto_edit | ask_all | "
                              "read_only). The default is auto_edit, in which "
                              "run_bash is BLOCKED — so the agent cannot run the "
                              "project's own tests. Acceptance is unaffected (the "
                              "harness measures, not the agent), but pass 'full' if "
                              "you want the agent to verify its own work."))
    parser.add_argument("--journal", default=None,
                        help="Append-only JSONL journal of attempts")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from --journal without re-running attempts")
    parser.add_argument("--allow-rollback", action="store_true",
                        help=("Permit the ROLLBACK rung. It restores the "
                              "workspace from a snapshot taken before the "
                              "first attempt, discarding later changes."))
    parser.add_argument("--json", action="store_true",
                        help="Emit the machine-readable result instead of prose")
    return parser


def run_converge(argv: list[str], *, model: str | None = None,
                 workspace: str | None = None) -> int:
    """`wisp converge`. *model* and *workspace* come from the global flags."""
    args = _build_parser().parse_args(argv)

    from wisp.autonomous import converge_on_objective

    def _on_attempt(request, observation, duration):
        state = "succeeded" if observation.turn_succeeded else "did not succeed"
        print(f"  attempt {request.attempt + 1} [{request.rung}]: turn {state} "
              f"in {duration}s, {observation.tool_calls} tool call(s)",
              flush=True)
        if observation.failure_message:
            print(f"    failure: {observation.failure_message[:160]}", flush=True)

    result = asyncio.run(converge_on_objective(
        args.objective,
        workspace or ".",
        model=model,
        permission_mode=args.permission_mode,
        max_attempts=args.max_attempts,
        allow_rollback=args.allow_rollback,
        journal_path=args.journal,
        resume=args.resume,
        on_attempt=_on_attempt,
    ))

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print()
        print(f"goal state: {result.goal_state}")
        print(f"converged:  {'yes' if result.converged else 'NO'}")
        print(f"reason:     {result.reason}")
        for record in result.attempts:
            unmet = ", ".join(record.unmet) or "-"
            print(f"  [{record.index}] {record.rung:<14} verdict={record.verdict:<12} "
                  f"failure={record.failure_class or '-':<16} unmet={unmet}")
            print(f"       session={record.session_id or '-'} "
                  f"changed={', '.join(record.observation.changed_files) or '-'} "
                  f"tools={record.observation.tool_calls}")
            if record.evidence_lines:
                print("       shown:")
                for line in record.evidence_lines:
                    print(f"         - {line[:160]}")
        if result.escalation is not None:
            print(f"escalated:  {result.escalation.reason}")

    return 0 if result.converged else 1


__all__ = ["run_converge"]
