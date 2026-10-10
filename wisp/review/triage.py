"""Read-only triage of open pull requests: what each one is waiting for, from the facts GitHub reports. No model, and nothing is changed on GitHub.

The outcomes follow the capability list the owner pasted: ready for review, changes requested, CI failure to investigate, possible duplicate, missing
requirements, security review required, human decision. Several can apply to one PR; the most urgent is the outcome and every other finding stays in `reasons`
and `flags`. Fail closed: a PR whose CI or mergeability is unknown is never `ready_for_review`, and a malformed entry is reported, not dropped.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from wisp.core.gates import secrets as secret_gate
from wisp.core.gates.deps import is_manifest_path
from wisp.review.checks import _is_ci_or_config, is_test_path

MAX_LIMIT = 200
STALE_DAYS = 30
LARGE_FILES = 40
LARGE_LINES = 1500
DUPLICATE_OVERLAP = 0.6
DUPLICATE_MIN_SHARED = 2
MIN_BODY_CHARS = 20
GH_TIMEOUT_S = 60

OUTCOME_ORDER = ["draft", "ci_failure", "conflicting", "changes_requested", "missing_requirements", "possible_duplicate", "security_review", "waiting_for_ci", "human_decision", "ready_for_review"]

_NEXT_ACTION = {
    "draft": "Wait for the author to mark it ready for review.",
    "ci_failure": "Investigate the failing check(s) before anyone reviews it.",
    "conflicting": "Ask the author to rebase or resolve the conflicts.",
    "changes_requested": "Waiting on the author to address the review comments.",
    "missing_requirements": "Ask for a description or a linked issue.",
    "possible_duplicate": "Compare it with the overlapping pull request(s) and keep one.",
    "security_review": "Assign a reviewer who owns the security-sensitive paths.",
    "waiting_for_ci": "Wait for CI to finish.",
    "human_decision": "A person has to decide; the reasons say what could not be determined.",
    "ready_for_review": "Ready for a human review.",
}
_FAILING = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE", "ERROR"}
_SECURITY = re.compile(r"(?:^|/)(?:auth\w*|security|secrets?|credentials?|permissions?|oauth\w*|crypto\w*)(?:[/_.\-]|$)", re.IGNORECASE)
_DOCS = (".md", ".rst", ".txt")
_CODE = (".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".php", ".c", ".cc", ".cpp", ".h", ".cs", ".kt", ".swift")
FIELDS = ("number,title,url,author,isDraft,headRefName,baseRefName,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,files,additions,deletions,changedFiles,"
          "labels,updatedAt,createdAt,body,closingIssuesReferences")


class TriageError(Exception):
    """The pull requests could not be read."""


@dataclass
class TriageRow:
    number: int
    title: str
    url: str
    author: str
    base: str
    head: str
    outcome: str
    next_action: str
    ci: str
    review: str
    mergeable: str
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    overlaps: list[int] = field(default_factory=list)
    stacked_on: int | None = None
    age_days: int = 0


def _ci_state(rollup: Any) -> tuple[str, list[str]]:
    if rollup is None:
        return "unknown", []
    if not isinstance(rollup, list):
        return "unknown", []
    if not rollup:
        return "none", []
    failing: list[str] = []
    pending = False
    for entry in rollup:
        if not isinstance(entry, dict):
            pending = True
            continue
        name = str(entry.get("name") or entry.get("context") or "check")
        if entry.get("__typename") == "StatusContext" or "state" in entry and "status" not in entry:
            state = str(entry.get("state", "")).upper()
            if state in _FAILING:
                failing.append(name)
            elif state != "SUCCESS":
                pending = True
            continue
        if str(entry.get("status", "")).upper() != "COMPLETED":
            pending = True
        elif str(entry.get("conclusion") or "").upper() in _FAILING:
            failing.append(name)
    if failing:
        return "failing", failing
    return ("pending" if pending else "passing"), []


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalized_title(title: str) -> str:
    return " ".join(title.casefold().split())


def classify(prs: Sequence[Any], *, now: datetime | None = None, default_branch: str = "main", stale_days: int = STALE_DAYS) -> list[TriageRow]:
    now = now or datetime.now(timezone.utc)
    parsed: list[dict[str, Any]] = []
    unreadable: list[TriageRow] = []
    for entry in prs:
        if isinstance(entry, dict) and isinstance(entry.get("number"), int) and entry["number"] > 0:
            parsed.append(entry)
        else:
            number = entry.get("number") if isinstance(entry, dict) and isinstance(entry.get("number"), int) else 0
            unreadable.append(TriageRow(number, "", "", "", "", "", "human_decision", _NEXT_ACTION["human_decision"], "unknown", "", "", ["unreadable pull request entry from gh"]))
    files_by_number = {p["number"]: [f["path"] for f in p.get("files") or [] if isinstance(f, dict) and isinstance(f.get("path"), str)] for p in parsed}
    head_to_number = {str(p.get("headRefName", "")): p["number"] for p in parsed if p.get("headRefName")}
    stacked_of: dict[int, int | None] = {}
    for p in parsed:
        parent = head_to_number.get(str(p.get("baseRefName", ""))) if str(p.get("baseRefName", "")) != default_branch else None
        stacked_of[p["number"]] = None if parent == p["number"] else parent

    def ancestors(number: int) -> set[int]:
        seen: set[int] = set()
        current = stacked_of.get(number)
        while current is not None and current not in seen:
            seen.add(current)
            current = stacked_of.get(current)
        return seen

    rows: list[TriageRow] = []
    for p in parsed:
        number = p["number"]
        files = files_by_number[number]
        reasons: list[str] = []
        flags: list[str] = []
        applies: set[str] = set()

        ci, failing = _ci_state(p.get("statusCheckRollup"))
        if p.get("isDraft"):
            applies.add("draft")
            reasons.append("it is a draft")
        if ci == "failing":
            applies.add("ci_failure")
            reasons.append("failing check(s): " + ", ".join(failing))
        elif ci == "pending":
            applies.add("waiting_for_ci")
            reasons.append("checks are still running")
        elif ci == "unknown":
            applies.add("human_decision")
            reasons.append("no CI result was reported (the field was missing), so it is not treated as passing")
        elif ci == "none":
            flags.append("no-ci")
            reasons.append("the repository reported no checks for this pull request")
        mergeable = str(p.get("mergeable") or "UNKNOWN").upper()
        if mergeable == "CONFLICTING" or str(p.get("mergeStateStatus", "")).upper() == "DIRTY":
            applies.add("conflicting")
            reasons.append("it has merge conflicts with its base branch")
        elif mergeable == "UNKNOWN":
            applies.add("human_decision")
            reasons.append("GitHub has not computed whether it merges cleanly")
        review = str(p.get("reviewDecision") or "")
        if review == "CHANGES_REQUESTED":
            applies.add("changes_requested")
            reasons.append("a reviewer requested changes")
        body = p.get("body") if isinstance(p.get("body"), str) else ""
        if len(body.strip()) < MIN_BODY_CHARS and not p.get("closingIssuesReferences"):
            applies.add("missing_requirements")
            reasons.append("no description and no linked issue")
        sensitive = [f for f in files if not f.lower().endswith(_DOCS) and (_SECURITY.search(f) or _is_ci_or_config(f) or is_manifest_path(f) or os.path.basename(f).startswith((".env", "Dockerfile")))]
        if sensitive:
            applies.add("security_review")
            reasons.append("touches security-sensitive path(s): " + ", ".join(sensitive[:4]))
        code = [f for f in files if f.lower().endswith(_CODE) and not is_test_path(f) and not f.lower().endswith(_DOCS)]
        if code and not any(is_test_path(f) for f in files):
            flags.append("no-tests")
            reasons.append("source files changed and no test file did")
        if int(p.get("additions") or 0) + int(p.get("deletions") or 0) > LARGE_LINES or int(p.get("changedFiles") or len(files)) > LARGE_FILES:
            flags.append("large")
            reasons.append("it is a large change")
        updated = _parse_time(p.get("updatedAt"))
        age = (now - updated).days if updated else 0
        if updated and age > stale_days:
            flags.append("stale")
            reasons.append(f"no activity for {age} days")

        base = str(p.get("baseRefName", ""))
        stacked_on = stacked_of[number]
        if stacked_on is not None:
            flags.append("stacked")
            reasons.append(f"it is stacked on #{stacked_on}")
        elif base and base != default_branch:
            flags.append("non-default-base")
            reasons.append(f"its base is '{base}', not '{default_branch}'")

        overlaps: list[int] = []
        title = _normalized_title(str(p.get("title", "")))
        for other in parsed:
            if other["number"] == number or other["number"] in ancestors(number) or number in ancestors(other["number"]):
                continue  # a stack overlaps by construction: a pull request is not a duplicate of the one it is built on
            shared = set(files) & set(files_by_number[other["number"]])
            union = set(files) | set(files_by_number[other["number"]])
            same_files = len(shared) >= DUPLICATE_MIN_SHARED and len(shared) / len(union) >= DUPLICATE_OVERLAP
            same_title = bool(title) and title == _normalized_title(str(other.get("title", "")))
            if same_files or same_title:
                overlaps.append(other["number"])
        if overlaps:
            applies.add("possible_duplicate")
            reasons.append("it overlaps #" + ", #".join(str(n) for n in sorted(overlaps)))

        outcome = next((name for name in OUTCOME_ORDER if name in applies), "ready_for_review")
        action = _NEXT_ACTION[outcome]
        if outcome == "possible_duplicate":
            action = f"Compare it with #{', #'.join(str(n) for n in sorted(overlaps))} and keep one."
        author = p.get("author", {}).get("login", "") if isinstance(p.get("author"), dict) else ""
        rows.append(TriageRow(number, str(p.get("title", "")), str(p.get("url", "")), str(author), base, str(p.get("headRefName", "")), outcome, action, ci, review, mergeable,
                              reasons, flags, sorted(overlaps), stacked_on, age))
    rows.sort(key=lambda r: (OUTCOME_ORDER.index(r.outcome), r.number))
    return rows + unreadable


def render_markdown(rows: Sequence[TriageRow], repo: str = "") -> str:
    head = f"# Pull request triage{f' for {repo}' if repo else ''}"
    note = "This report never merges, closes, comments, labels or approves; those are separate decisions for a person."
    if not rows:
        return f"{head}\n\nNo open pull requests.\n\n{note}\n"
    out = [head, "", f"{len(rows)} open pull request(s), most urgent first.", "", "| PR | Outcome | CI | Flags | Next action |", "|---|---|---|---|---|"]
    for row in rows:
        out.append(f"| #{row.number} {row.title[:60]} | {row.outcome} | {row.ci} | {', '.join(row.flags) or '-'} | {row.next_action} |")
    out.append("")
    for row in rows:
        if row.reasons:
            out.append(f"### #{row.number} {row.title[:80]}")
            out.extend(f"- {reason}" for reason in row.reasons)
            out.append("")
    out.append(note)
    return "\n".join(out) + "\n"


def render_json(rows: Sequence[TriageRow]) -> str:
    return json.dumps([asdict(row) for row in rows], indent=2, ensure_ascii=False)


class GhClient:
    """Reads pull requests with `gh`. Only `gh pr list` and `gh repo view` are ever run."""

    def __init__(self, workspace: str, repo: str | None = None) -> None:
        self._workspace = workspace
        self._repo = repo

    def _run(self, *args: str) -> str:
        if shutil.which("gh") is None:
            raise TriageError("the GitHub CLI (gh) is not installed or not on PATH; it is needed to read pull requests")
        try:
            proc = subprocess.run(["gh", *args], cwd=self._workspace, capture_output=True, timeout=GH_TIMEOUT_S, check=False, env={**os.environ, "GH_PROMPT_DISABLED": "1"})
        except subprocess.TimeoutExpired as exc:
            raise TriageError(f"gh took longer than {GH_TIMEOUT_S}s") from exc
        if proc.returncode != 0:
            raise TriageError("gh " + " ".join(args[:2]) + " failed: " + secret_gate.scrub(proc.stderr.decode("utf-8", "replace").strip()).text[:300])
        return proc.stdout.decode("utf-8", "replace")

    def list_open_prs(self, limit: int) -> list[Any]:
        args = ["pr", "list", "--state", "open", "--limit", str(max(1, min(int(limit), MAX_LIMIT))), "--json", FIELDS]
        if self._repo:
            args += ["--repo", self._repo]
        try:
            data = json.loads(self._run(*args))
        except ValueError as exc:
            raise TriageError("gh pr list did not return JSON") from exc
        if not isinstance(data, list):
            raise TriageError("gh pr list did not return a list")
        return data

    def default_branch(self) -> str:
        args = ["repo", "view"] + ([self._repo] if self._repo else []) + ["--json", "defaultBranchRef", "-q", ".defaultBranchRef.name"]
        name = self._run(*args).strip()
        return name or "main"
