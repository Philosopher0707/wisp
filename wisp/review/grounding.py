"""Grounding: turning a model's claim into a finding only when the code it quotes is in the diff.

The model proposes (file, quote, message). The harness looks the quote up in the post-image of that file, takes the line number from the diff, writes the
quote itself (so what is shown is the repository's text, scrubbed, not the model's), caps the severity (a model can warn, never block: only established evidence
blocks) and treats the message as data: control characters and ANSI escapes stripped, bounded, secrets redacted. A quote that is not there is dropped and
counted by the caller; a quote that is only on an unchanged context line is kept as a note.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from wisp.core.gates import secrets as secret_gate
from wisp.review.checks import safe_quote
from wisp.review.diff import FileDiff
from wisp.review.rules import Rules
from wisp.review.types import Finding, Severity

MAX_MESSAGE_CHARS = 500
MAX_SUGGESTION_CHARS = 300
MIN_QUOTE_CHARS = 6

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SEVERITY = {"warn": Severity.WARN, "warning": Severity.WARN, "block": Severity.WARN, "info": Severity.INFO}
_LENS_IN_EVIDENCE = re.compile(r"model claim \((\w+)\)")


@dataclass(frozen=True)
class Claim:
    file: str
    quote: str
    severity: str
    message: str
    suggestion: str
    rule: str
    lens: str


def clean_text(text: str, limit: int) -> str:
    """Model text as data for a human: no escapes or control characters, one line, no secrets, bounded."""
    text = _CONTROL.sub("", _ANSI.sub("", text))
    return secret_gate.scrub(" ".join(text.split())).text[:limit]


def _normalize(text: str) -> str:
    """The characters of `text` without any whitespace: a quote only has to name the same code, not reproduce its spacing."""
    return "".join(text.split())


def _candidates(quote: str) -> list[list[str]]:
    """The quote as whitespace-free lines, and again with a diff marker (+ or -) taken off each line, for a model that copied the marker."""
    raw = [line for line in quote.strip("\n").split("\n") if line.strip()]
    plain = [_normalize(line) for line in raw]
    stripped = [_normalize(line[1:]) if line[:1] in "+-" else _normalize(line) for line in raw]
    out = [plain]
    if stripped != plain:
        out.append(stripped)
    return out


def _lookup(files: Mapping[str, FileDiff], name: str) -> FileDiff | None:
    name = name.strip()
    for candidate in (name, name[2:] if name.startswith(("a/", "b/", "./")) else name):
        if candidate in files:
            return files[candidate]
    return None


def _locate(fd: FileDiff, lines: list[str]) -> tuple[int, bool] | None:
    """(line number, on an added line) of the first match of `lines` in the post-image, preferring a changed line over a context line."""
    entries: list[tuple[int, str, bool]] = []
    for hunk in fd.hunks:
        for line in hunk.lines:
            if line.kind != "-" and line.new_no is not None:
                entries.append((line.new_no, _normalize(line.text), line.kind == "+"))
    best: tuple[int, bool] | None = None
    for i in range(len(entries) - len(lines) + 1):
        window = entries[i:i + len(lines)]
        if any(window[j][0] != window[0][0] + j for j in range(len(window))):
            continue
        if all(lines[j] in window[j][1] for j in range(len(lines))):
            added = any(w[2] for w in window)
            if added:
                return window[0][0], True
            if best is None:
                best = (window[0][0], False)
    return best


def ground(claim: Claim, files: Mapping[str, FileDiff], rules: Rules | None = None) -> Finding | None:
    fd = _lookup(files, claim.file)
    if fd is None:
        return None
    hit: tuple[int, bool] | None = None
    for lines in _candidates(claim.quote):
        if sum(len(line) for line in lines) < MIN_QUOTE_CHARS:
            continue
        hit = _locate(fd, lines)
        if hit is not None:
            break
    if hit is None:
        return None
    number, changed = hit
    text = next(t for n, t in fd.post_image_lines() if n == number)
    severity = _SEVERITY.get(claim.severity.strip().lower(), Severity.INFO) if changed else Severity.INFO
    cited = claim.rule if rules is not None and claim.rule in {r.id for r in rules.rules} else ""
    where = f"{fd.path}:{number}"
    evidence = f"model claim ({claim.lens}) grounded in the diff at {where}" + ("" if changed else ", on an unchanged context line")
    return Finding(
        rule=f"rule:{cited}" if cited else f"model:{claim.lens}", severity=severity, file=fd.path, line=number, quote=safe_quote(text),
        message=clean_text(claim.message, MAX_MESSAGE_CHARS), evidence=evidence, suggestion=clean_text(claim.suggestion, MAX_SUGGESTION_CHARS))


def merge_duplicates(findings: list[Finding]) -> list[Finding]:
    """One finding per piece of code: lenses that flagged the same text are merged, the strongest severity kept, every lens named in the evidence."""
    groups: dict[tuple[str, str], list[Finding]] = {}
    for finding in findings:
        groups.setdefault((finding.file, _normalize(finding.quote)), []).append(finding)
    merged: list[Finding] = []
    for group in groups.values():
        group.sort(key=lambda f: f.severity.rank)
        primary = group[0]
        others = sorted({_lens_of(f) for f in group[1:]} - {_lens_of(primary)} - {""})
        if others:
            primary = Finding(primary.rule, primary.severity, primary.file, primary.line, primary.quote, primary.message,
                              f"{primary.evidence}; also flagged by: {', '.join(others)}", primary.suggestion)
        merged.append(primary)
    return merged


def _lens_of(finding: Finding) -> str:
    match = _LENS_IN_EVIDENCE.search(finding.evidence)
    return match.group(1) if match else ""
