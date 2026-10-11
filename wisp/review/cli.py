"""`wisp review` and `wisp triage`: the command-line and REPL face of wisp/review.

Both functions return an exit code and write to the streams they are given (the REPL passes its own), so they are safe to call in-process: argument errors never
exit the interpreter. Exit codes: 0 fine, 1 the verdict met the `--fail-on` threshold, 2 a usage, configuration or source error.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, TextIO

from wisp.review import triage as triage_mod
from wisp.review.checks import CheckContext
from wisp.review.diff import parse_with_residue
from wisp.review.engine import ModelRunner, ReviewOptions, review
from wisp.review.lens import LENSES
from wisp.review.report import exit_code, render_json, render_markdown
from wisp.review.rules import RulesError, default_path, load_rules
from wisp.review.source import DiffSource, SourceError, post_image_reader, read_diff, symbol_searcher


class UsageError(Exception):
    pass


class _Exit(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


class _Parser(argparse.ArgumentParser):
    """argparse that raises instead of exiting, so the REPL survives a typo."""

    def __init__(self, *args: Any, out: TextIO, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._out = out

    def error(self, message: str) -> Any:
        raise UsageError(message)

    def exit(self, status: int = 0, message: str | None = None) -> Any:
        raise _Exit(status)

    def print_help(self, file: TextIO | None = None) -> None:
        super().print_help(file or self._out)


def _review_parser(out: TextIO) -> _Parser:
    p = _Parser(prog="wisp review", out=out, description="Review a change: deterministic checks, repo rules and optional model lenses; the verdict is the harness's.")
    p.add_argument("--staged", action="store_true", help="review what is staged")
    p.add_argument("--base", help="review BASE...HEAD (the branch's own work); HEAD defaults to the current commit")
    p.add_argument("--head", help="the head of the range (needs --base)")
    p.add_argument("--commit", help="review one commit")
    p.add_argument("--pr", type=int, help="review a pull request (read through gh)")
    p.add_argument("--rules", help="a rules file (default: .wisp/review-rules.toml in the repository)")
    p.add_argument("--no-model", action="store_true", help="deterministic checks only: no model, no cost, same answer every time")
    p.add_argument("--lens", action="append", choices=list(LENSES), help="run only this lens (repeatable); default: all three")
    p.add_argument("--max-findings", type=int, default=30, help="cap on listed findings; blockers are never dropped")
    p.add_argument("--run-tests", action="store_true", help="also run the tests the change affects, in the local checkout (refused with --pr)")
    p.add_argument("--json", action="store_true", help="print one JSON object instead of text")
    p.add_argument("--fail-on", default="blocked", help="exit 1 when the verdict is this or worse: blocked (default), incomplete, attention")
    return p


def _source(args: argparse.Namespace) -> DiffSource:
    chosen = [name for name, value in (("--staged", args.staged), ("--base", args.base), ("--commit", args.commit), ("--pr", args.pr)) if value]
    if len(chosen) > 1:
        raise UsageError(f"{' and '.join(chosen)} cannot be combined")
    if args.head and not args.base:
        raise UsageError("--head needs --base")
    if args.pr is not None:
        return DiffSource("pr", pr=args.pr)
    if args.commit:
        return DiffSource("commit", commit=args.commit)
    if args.base:
        return DiffSource("range", base=args.base, head=args.head or "HEAD")
    return DiffSource("staged" if args.staged else "uncommitted")


def default_model_runner(workspace: str, model: str | None = None, provider: str | None = None) -> ModelRunner:
    """A runner backed by a headless, read-only agent so a lens can look at the repository around the diff but cannot change it. Each call gets its own session so one
    lens (or one hostile diff) cannot leave instructions in another's context."""

    async def run(prompt: str) -> str:
        from wisp.headless import run_headless

        result = await run_headless(prompt=prompt, model=model, workspace=workspace, session_id=f"review-{uuid.uuid4().hex[:12]}", permission_mode="read_only", provider=provider)
        if not result.get("ok", True):
            raise RuntimeError("; ".join(str(e.get("message", "")) for e in result.get("errors", []))[:300] or "the model run failed")
        return str(result.get("content", ""))

    return run


_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_LOCK = threading.Lock()


def _shared_loop() -> asyncio.AbstractEventLoop:
    """One event loop for every review in this process, on a daemon thread. The headless agent the lenses use caches its composition root and that root is bound to the
    loop it started on, so a fresh loop per `/review` would hand the second review a root whose loop is closed. A daemon thread cannot hold the interpreter at exit."""
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None or _LOOP.is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="wisp-review-loop", daemon=True).start()
            _LOOP = loop
        return _LOOP


def _run_coroutine(coro: Any) -> Any:
    future = asyncio.run_coroutine_threadsafe(coro, _shared_loop())
    try:
        return future.result()
    except KeyboardInterrupt:
        future.cancel()
        raise


def run_review(
    argv: list[str], *, workspace: str | None = None, model: str | None = None, provider: str | None = None, runner: ModelRunner | None = None,
    out: TextIO | None = None, err: TextIO | None = None,
) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    parser = _review_parser(out)
    try:
        args = parser.parse_args(argv)
        source = _source(args)
        if args.fail_on not in ("blocked", "incomplete", "attention"):
            raise UsageError("--fail-on must be blocked, incomplete or attention")
        if args.max_findings < 1:
            raise UsageError("--max-findings must be at least 1")
        if args.run_tests and source.kind == "pr":
            raise UsageError("--run-tests runs the tests of the local checkout; with --pr that would run the pull request's code, so it is refused")
    except _Exit as done:
        return done.status
    except UsageError as exc:
        print(f"wisp review: {exc}", file=err)
        return 2

    root = os.path.abspath(workspace or ".")
    try:
        rules_path = default_path(root) if not args.rules else _explicit_rules(args.rules)
        rules = load_rules(rules_path)
        text = read_diff(root, source)
    except (RulesError, SourceError, UsageError) as exc:
        print(f"wisp review: {exc}", file=err)
        return 2

    files, residue = parse_with_residue(text)
    gaps: list[str] = []
    ctx = CheckContext(read_post_image=post_image_reader(root, source), symbol_tested=symbol_searcher(root, gaps), gaps=gaps)
    if args.run_tests:
        from wisp.test_runner import run_affected_tests

        ctx.run_tests = lambda changed: run_affected_tests(changed, root)
    options = ReviewOptions(lenses=tuple(args.lens) if args.lens else tuple(LENSES), use_model=not args.no_model, max_findings=args.max_findings)
    model_runner = None if args.no_model else (runner or default_model_runner(root, model, provider))
    try:
        report = _run_coroutine(review(files, residue, ctx, rules, options, model_runner, source=source.label()))
    except KeyboardInterrupt:
        print("wisp review: stopped", file=err)
        return 2
    print(render_json(report) if args.json else render_markdown(report), file=out)
    return exit_code(report, args.fail_on)


def _explicit_rules(path: str):
    from pathlib import Path

    candidate = Path(path)
    if not candidate.exists():
        raise UsageError(f"rules file not found: {path}")
    return candidate


def _triage_parser(out: TextIO) -> _Parser:
    p = _Parser(prog="wisp triage", out=out, description="Read-only triage of open pull requests (through gh). It never merges, closes, comments, labels or approves.")
    p.add_argument("--limit", type=int, default=50, help=f"how many open pull requests to read (1 to {triage_mod.MAX_LIMIT})")
    p.add_argument("--repo", help="OWNER/NAME (default: the repository of the current directory)")
    p.add_argument("--stale-days", type=int, default=triage_mod.STALE_DAYS, help="days without activity before a pull request is flagged stale")
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    return p


def run_triage(
    argv: list[str], *, workspace: str | None = None, client: Any = None, now: datetime | None = None, out: TextIO | None = None, err: TextIO | None = None,
) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    try:
        args = _triage_parser(out).parse_args(argv)
        if not 1 <= args.limit <= triage_mod.MAX_LIMIT:
            raise UsageError(f"--limit must be between 1 and {triage_mod.MAX_LIMIT}")
        if args.stale_days < 1:
            raise UsageError("--stale-days must be at least 1")
    except _Exit as done:
        return done.status
    except UsageError as exc:
        print(f"wisp triage: {exc}", file=err)
        return 2
    root = os.path.abspath(workspace or ".")
    gh = client or triage_mod.GhClient(root, args.repo)
    try:
        prs = gh.list_open_prs(args.limit)
        default_branch = gh.default_branch()
    except triage_mod.TriageError as exc:
        print(f"wisp triage: {exc}", file=err)
        return 2
    rows = triage_mod.classify(prs, now=now or datetime.now(timezone.utc), default_branch=default_branch, stale_days=args.stale_days)
    print(triage_mod.render_json(rows) if args.json else triage_mod.render_markdown(rows, repo=args.repo or ""), file=out)
    return 0


def run_slash(command: str, args: str, config: Any) -> str:
    """The text a REPL slash command (`review` or `triage`) shows: the report on success, `wisp <command>: ...` lines on error. Never raises and never exits."""
    import io
    import shlex

    try:
        argv = shlex.split(args) if args else []
    except ValueError as exc:
        return f"wisp {command}: bad quoting: {exc}"
    workspace = (config.get("workspace") if isinstance(config, dict) else getattr(config, "workspace", None)) or os.getcwd()
    out, err = io.StringIO(), io.StringIO()
    if command == "review":
        model = config.get("model") if isinstance(config, dict) else getattr(config, "model", None)
        provider = config.get("provider") if isinstance(config, dict) else getattr(config, "provider", None)
        run_review(argv, workspace=workspace, model=model or None, provider=provider or None, out=out, err=err)
    else:
        run_triage(argv, workspace=workspace, out=out, err=err)
    return (out.getvalue() + err.getvalue()).strip()
