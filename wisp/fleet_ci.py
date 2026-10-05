"""CI status across the fleet: open PRs and the default branch of every GitHub repo in wisp.fleet.toml.

Read-only. It asks `gh` what GitHub reports and never merges, re-runs, comments or pushes. Two things it is
careful about, because both have misled people here:

* a red default branch is reported on its own line, so it cannot hide behind an empty PR list (a red `main` makes
  every PR's CI red, which is how a one-line fix looked like six unrelated failures);
* "no checks" is its own state and never reads as passing, and anything that cannot be read is an error, not a pass.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from wisp.fleet import FleetManifestError, Manifest, find_manifest, load_manifest

_GH_TIMEOUT_S = 60
_PR_FIELDS = "number,title,headRefName,mergeable,mergeStateStatus,statusCheckRollup"
_PASSING = frozenset({"SUCCESS", "SKIPPED", "NEUTRAL"})
_PENDING_STATES = frozenset({"PENDING", "EXPECTED"})
_FAILING_STATES = frozenset({"FAILURE", "ERROR"})
_SLUG = re.compile(r"(?:https?://|ssh://git@|git@)github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?")


@dataclass(frozen=True)
class Checks:
    state: str  # pass | fail | pending | none
    failing: list[str] = field(default_factory=list)
    pending: int = 0
    passing: int = 0


@dataclass
class PrCi:
    number: int
    title: str
    head: str
    mergeable: str
    merge_state: str
    checks: Checks


@dataclass
class RepoCi:
    name: str
    slug: str | None = None
    skipped: str | None = None
    error: str | None = None
    branch: str = ""
    branch_checks: Checks = field(default_factory=lambda: Checks("none"))
    prs: list[PrCi] = field(default_factory=list)

    def states(self) -> list[str]:
        return [p.checks.state for p in self.prs] + [self.branch_checks.state]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "slug": self.slug,
            "skipped": self.skipped,
            "error": self.error,
            "branch": self.branch,
            "branch_state": self.branch_checks.state,
            "branch_failing": self.branch_checks.failing,
            "prs": [
                {
                    "number": p.number, "title": p.title, "head": p.head, "state": p.checks.state,
                    "failing": p.checks.failing, "pending": p.checks.pending,
                    "mergeable": p.mergeable, "merge_state": p.merge_state,
                }
                for p in self.prs
            ],
        }


def github_slug(url: str) -> str | None:
    m = _SLUG.fullmatch(url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def classify_checks(rollup: list[dict[str, object]]) -> Checks:
    """Fold a `statusCheckRollup` into one state. Anything it cannot read counts as pending, never as passing."""
    failing: list[str] = []
    pending = passing = 0
    for item in rollup:
        name = str(item.get("name") or item.get("context") or "?")
        if "state" in item and "status" not in item:  # a commit status context
            state = str(item.get("state") or "").upper()
            if state in _FAILING_STATES:
                failing.append(name)
            elif state in _PENDING_STATES or not state:
                pending += 1
            else:
                passing += 1
            continue
        if str(item.get("status") or "").upper() != "COMPLETED":
            pending += 1
            continue
        conclusion = str(item.get("conclusion") or "").upper()
        if conclusion in _PASSING:
            passing += 1
        else:
            failing.append(name)
    if failing:
        state = "fail"
    elif pending:
        state = "pending"
    elif passing:
        state = "pass"
    else:
        state = "none"
    return Checks(state, failing, pending, passing)


def _gh(*args: str) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=_GH_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr.strip()


def _git(path: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _default_branch(path: Path) -> str:
    head = _git(path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    return head.split("/", 1)[1] if "/" in head else "main"


def _branch_checks(runs: list[dict[str, object]]) -> Checks:
    """Latest run per workflow on the default branch, folded like a rollup."""
    latest: dict[str, dict[str, object]] = {}
    for run in runs:  # gh lists newest first
        latest.setdefault(str(run.get("workflowName") or "?"), run)
    return classify_checks([
        {"name": wf, "status": str(r.get("status") or "").upper(), "conclusion": str(r.get("conclusion") or "").upper()}
        for wf, r in latest.items()
    ])


def collect_repo(name: str, path: Path) -> RepoCi:
    repo = RepoCi(name)
    if not path.exists():
        repo.skipped = f"{path} does not exist"
        return repo
    url = _git(path, "remote", "get-url", "origin")
    if not url:
        repo.skipped = "no remote"
        return repo
    repo.slug = github_slug(url)
    if repo.slug is None:
        repo.skipped = f"not a GitHub repo ({url})"
        return repo

    code, out, err = _gh("pr", "list", "--repo", repo.slug, "--state", "open", "--limit", "50", "--json", _PR_FIELDS)
    if code != 0:
        repo.error = f"gh pr list failed: {err or out.strip()}"
        return repo
    try:
        for pr in json.loads(out or "[]"):
            repo.prs.append(PrCi(
                int(pr["number"]), str(pr.get("title") or ""), str(pr.get("headRefName") or ""),
                str(pr.get("mergeable") or ""), str(pr.get("mergeStateStatus") or ""),
                classify_checks(pr.get("statusCheckRollup") or []),
            ))
    except (ValueError, KeyError, TypeError) as exc:
        repo.error = f"unreadable gh pr list output: {exc}"
        return repo

    repo.branch = _default_branch(path)
    code, out, err = _gh(
        "run", "list", "--repo", repo.slug, "--branch", repo.branch, "--limit", "10",
        "--json", "workflowName,status,conclusion",
    )
    if code != 0:
        repo.error = f"gh run list failed: {err or out.strip()}"
        return repo
    try:
        repo.branch_checks = _branch_checks(json.loads(out or "[]"))
    except (ValueError, TypeError) as exc:
        repo.error = f"unreadable gh run list output: {exc}"
    return repo


def collect(manifest: Manifest) -> list[RepoCi]:
    """One thread per repo (the work is waiting on `gh`); the result keeps the manifest's order."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(lambda r: collect_repo(r.name, r.path), manifest.repos))


def overall(repos: list[RepoCi]) -> str:
    """fail beats pending beats pass; an unreadable repo is a fail, because unknown is not green."""
    if any(r.error for r in repos):
        return "fail"
    states = [s for r in repos for s in r.states()]
    if "fail" in states:
        return "fail"
    return "pending" if "pending" in states else "pass"


def _detail(c: Checks) -> str:
    parts = list(c.failing)
    if c.pending:
        parts.append(f"{c.pending} pending")
    return ", ".join(parts)


def render(repos: list[RepoCi]) -> str:
    width = max((len(r.name) for r in repos), default=4)
    lines = [f"{'REPO':<{width}}  {'WHAT':<34} {'CI':<8} DETAIL"]
    for r in repos:
        if r.skipped:
            lines.append(f"{r.name:<{width}}  {'-':<34} {'skipped':<8} {r.skipped}")
            continue
        if r.error:
            lines.append(f"{r.name:<{width}}  {'-':<34} {'error':<8} {r.error}")
            continue
        for p in r.prs:
            what = f"#{p.number} {p.head}"[:33]
            extra = _detail(p.checks)
            if p.mergeable == "CONFLICTING":
                extra = (extra + ", " if extra else "") + "merge conflict"
            lines.append(f"{r.name:<{width}}  {what:<34} {p.checks.state:<8} {extra}")
        lines.append(f"{r.name:<{width}}  {(r.branch or 'default') + ' (branch)':<34} {r.branch_checks.state:<8} {_detail(r.branch_checks)}")
    lines.append("")
    lines.append(f"overall: {overall(repos)}")
    return "\n".join(lines)


def run_ci(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="wisp fleet ci", description="Read-only CI status for the repos in wisp.fleet.toml.")
    parser.add_argument("--manifest", help="path to wisp.fleet.toml")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--strict", action="store_true", help="exit 1 when any PR or default branch is failing, or a repo cannot be read")
    parser.add_argument("--watch", type=float, metavar="SECONDS", help="re-check every SECONDS until nothing is pending")
    parser.add_argument("--timeout", type=float, default=1800.0, help="with --watch: give up after this many seconds (exit 4)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    try:
        manifest = load_manifest(find_manifest(args.manifest))
    except FleetManifestError as exc:
        print(f"wisp fleet: {exc}", file=sys.stderr)
        return 2
    if shutil.which("gh") is None:
        print("wisp fleet ci: the GitHub CLI `gh` is not on PATH, so CI status cannot be read", file=sys.stderr)
        return 3

    deadline = time.monotonic() + args.timeout
    while True:
        repos = collect(manifest)
        state = overall(repos)
        if args.watch is None or state != "pending":
            timed_out = False
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        print(f"wisp fleet ci: still pending, checking again in {args.watch:g}s", file=sys.stderr)
        time.sleep(args.watch)

    if args.json:
        print(json.dumps({"overall": state, "repos": [r.to_dict() for r in repos]}, indent=2))
    else:
        print(render(repos))

    if args.watch is not None:
        if state == "fail":
            return 1
        return 4 if timed_out else 0
    return 1 if args.strict and state == "fail" else 0
