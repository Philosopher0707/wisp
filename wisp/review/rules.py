"""Repo rules for the review: a TOML file the owner writes (`.wisp/review-rules.toml`).

A rule with a `forbid` regex is checked deterministically against added lines in the paths it names, and may block (a pattern match is established
evidence). Its text also goes to the model lenses, as does the text of a rule with no `forbid`, unless `model = false`. A malformed file is an error that names
every problem; it is never half-applied, because a rule that silently did not load is a rule the owner believes is being enforced.
"""

from __future__ import annotations

import posixpath
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from wisp.review.checks import safe_quote
from wisp.review.diff import FileDiff
from wisp.review.types import Finding, Severity

DEFAULT_RELATIVE_PATH = ".wisp/review-rules.toml"
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_KEYS = frozenset({"id", "text", "severity", "paths", "forbid", "model"})
MAX_PATTERN_CHARS = 500
MAX_TEXT_CHARS = 1000


class RulesError(ValueError):
    """The rules file is not valid. The message lists every problem, one per line."""


@dataclass(frozen=True)
class Rule:
    id: str
    text: str
    severity: Severity
    paths: tuple[str, ...]
    forbid: re.Pattern[str] | None
    model: bool


@dataclass(frozen=True)
class Rules:
    rules: tuple[Rule, ...] = ()

    def model_text(self) -> str:
        """The rules the model lenses are told about, one line each, with ids so a violation can cite one."""
        return "\n".join(f"- [{r.id}] ({r.severity.value}) {r.text}" for r in self.rules if r.model)


def default_path(workspace: str) -> Path:
    return Path(workspace) / DEFAULT_RELATIVE_PATH


def load_rules(path: Path | str) -> Rules:
    """The rules in `path`; no file means no rules. Raises RulesError for a file that exists and is not valid."""
    file = Path(path)
    if not file.exists():
        return Rules()
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as exc:
        raise RulesError(f"{file}: not valid TOML: {exc}") from exc
    problems: list[str] = [f"unknown top-level key '{key}'" for key in data if key != "rule"]
    raw = data.get("rule", [])
    if not isinstance(raw, list):
        raise RulesError(f"{file}: 'rule' must be an array of tables ([[rule]])")
    rules: list[Rule] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw, 1):
        rule, found = _build(entry, index, seen)
        problems.extend(found)
        if rule is not None:
            rules.append(rule)
    if problems:
        raise RulesError(f"{file}:\n" + "\n".join(f"  - {p}" for p in problems))
    return Rules(tuple(rules))


def _build(entry: Any, index: int, seen: set[str]) -> tuple[Rule | None, list[str]]:
    where = f"rule #{index}"
    if not isinstance(entry, dict):
        return None, [f"{where}: must be a table"]
    problems = [f"{where}: unknown key '{key}'" for key in entry if key not in _KEYS]
    rule_id = entry.get("id")
    if not isinstance(rule_id, str) or not _ID.match(rule_id):
        problems.append(f"{where}: id must be lowercase letters, digits, '-' or '_' (1 to 64 characters)")
    elif rule_id in seen:
        problems.append(f"{where}: duplicate id '{rule_id}'")
    else:
        seen.add(rule_id)
        where = f"rule '{rule_id}'"
    text = entry.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
        problems.append(f"{where}: text must be a non-empty string of at most {MAX_TEXT_CHARS} characters")
    severity = Severity.WARN
    if "severity" in entry:
        try:
            severity = Severity(entry["severity"])
        except ValueError:
            problems.append(f"{where}: severity must be one of block, warn, info")
    paths: tuple[str, ...] = ("**",)
    if "paths" in entry:
        value = entry["paths"]
        if isinstance(value, list) and value and all(isinstance(p, str) and p for p in value):
            paths = tuple(value)
        else:
            problems.append(f"{where}: paths must be a non-empty list of glob strings")
    forbid: re.Pattern[str] | None = None
    if "forbid" in entry:
        pattern = entry["forbid"]
        if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_CHARS:
            problems.append(f"{where}: forbid must be a regex string of at most {MAX_PATTERN_CHARS} characters")
        else:
            try:
                forbid = re.compile(pattern)
            except re.error as exc:
                problems.append(f"{where}: forbid is not a valid regex: {exc}")
    model = entry.get("model", True)
    if not isinstance(model, bool):
        problems.append(f"{where}: model must be true or false")
    if problems:
        return None, problems
    return Rule(str(rule_id), str(text).strip(), severity, paths, forbid, bool(model)), []


# ── matching ─────────────────────────────────────────────────────────────────

def _glob_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        out.append("[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch))
        i += 1
    return re.compile("".join(out) + r"\Z")


def path_matches(path: str, patterns: Sequence[str]) -> bool:
    """A pattern with no '/' matches the file name anywhere (like .gitignore); one with a '/' matches the whole path. `**` crosses directories, `*` does not."""
    base = posixpath.basename(path)
    for pattern in patterns:
        target = path if "/" in pattern or pattern == "**" else base
        if _glob_regex(pattern).match(target):
            return True
    return False


def check_rules(files: Sequence[FileDiff], rules: Rules) -> list[Finding]:
    found: list[Finding] = []
    for rule in rules.rules:
        if rule.forbid is None:
            continue
        for fd in files:
            if fd.binary or not path_matches(fd.path, rule.paths):
                continue
            for number, text in fd.added_lines():
                if rule.forbid.search(text):
                    found.append(Finding(f"rule:{rule.id}", rule.severity, fd.path, number, safe_quote(text), rule.text, "repo rule (forbid pattern)"))
    return found
