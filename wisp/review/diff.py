"""A unified-diff parser with the line numbers a review can trust.

Every location the review reports comes from here, so a post-image line's `new_no` is its real number in the file after the change (asserted against real
`git diff` output in tests/review/test_diff.py). The parser never raises: what it could not understand is counted as residue and a hunk whose body ended
early is flagged `truncated`, so a caller cannot call a partial parse complete.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_QUOTED_HEADER = re.compile(r'^"a/(.*)" "b/(.*)"$')


@dataclass
class DiffLine:
    kind: str  # "+", "-" or " "
    text: str
    old_no: int | None
    new_no: int | None


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    path: str
    old_path: str = ""
    status: str = "modified"  # added | deleted | renamed | modified
    binary: bool = False
    truncated: bool = False
    hunks: list[Hunk] = field(default_factory=list)

    def added_lines(self) -> Iterator[tuple[int, str]]:
        for hunk in self.hunks:
            for line in hunk.lines:
                if line.kind == "+" and line.new_no is not None:
                    yield line.new_no, line.text

    def removed_lines(self) -> Iterator[tuple[int, str]]:
        for hunk in self.hunks:
            for line in hunk.lines:
                if line.kind == "-" and line.old_no is not None:
                    yield line.old_no, line.text

    def post_image_lines(self) -> Iterator[tuple[int, str]]:
        """The lines that exist after the change, as far as the hunks show them: added and context lines with their real line numbers."""
        for hunk in self.hunks:
            for line in hunk.lines:
                if line.kind != "-" and line.new_no is not None:
                    yield line.new_no, line.text

    @property
    def added(self) -> int:
        return sum(1 for _ in self.added_lines())

    @property
    def removed(self) -> int:
        return sum(1 for _ in self.removed_lines())


def parse_unified_diff(text: str) -> list[FileDiff]:
    return parse_with_residue(text)[0]


def parse_with_residue(text: str) -> tuple[list[FileDiff], int]:
    """The files in `text` and the number of lines (or short hunks) that could not be understood. Residue 0 means the whole text was read."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    files: list[FileDiff] = []
    current: FileDiff | None = None
    residue = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            current = _file_from_header(line[len("diff --git "):])
            files.append(current)
            i += 1
            continue
        if current is None:
            residue += 1
            i += 1
            continue
        if line.startswith("@@"):
            i, short = _read_hunk(lines, i, current)
            residue += short
            continue
        if not _read_header_line(line, current):
            residue += 1
        i += 1
    return files, residue


def _file_from_header(rest: str) -> FileDiff:
    quoted = _QUOTED_HEADER.match(rest)
    if quoted:
        return FileDiff(path=quoted.group(2))
    half = (len(rest) - 5) // 2
    if half > 0 and rest == f"a/{rest[2:2 + half]} b/{rest[2:2 + half]}":
        return FileDiff(path=rest[2:2 + half])
    left, sep, right = rest.partition(" b/")
    return FileDiff(path=right if sep else rest)


def _read_header_line(line: str, current: FileDiff) -> bool:
    """Consume one metadata line of a file section. False when the line is not one."""
    if line.startswith("--- "):
        name = line[4:].rstrip("\t")
        if name == "/dev/null":
            current.status = "added"
        else:
            current.old_path = name[2:] if name.startswith("a/") else name
        return True
    if line.startswith("+++ "):
        name = line[4:].rstrip("\t")
        if name == "/dev/null":
            current.status = "deleted"
            if current.old_path:
                current.path = current.old_path
        else:
            current.path = name[2:] if name.startswith("b/") else name
        return True
    if line.startswith("new file mode"):
        current.status = "added"
        return True
    if line.startswith("deleted file mode"):
        current.status = "deleted"
        return True
    if line.startswith("rename from "):
        current.status = "renamed"
        current.old_path = line[len("rename from "):]
        return True
    if line.startswith("rename to "):
        current.status = "renamed"
        current.path = line[len("rename to "):]
        return True
    if line.startswith(("Binary files ", "GIT binary patch")):
        current.binary = True
        return True
    return line.startswith(("index ", "similarity index", "dissimilarity index", "old mode", "new mode", "copy from", "copy to", "literal ", "delta "))


def _read_hunk(lines: list[str], start: int, current: FileDiff) -> tuple[int, int]:
    """Read one hunk body by its declared line counts, so a body line that looks like a file header is content. Returns the next index and 1 if the body was short."""
    match = _HUNK.match(lines[start])
    if match is None:
        return start + 1, 1
    old_start, new_start = int(match.group(1)), int(match.group(3))
    old_count = int(match.group(2)) if match.group(2) is not None else 1
    new_count = int(match.group(4)) if match.group(4) is not None else 1
    hunk = Hunk(old_start, old_count, new_start, new_count)
    current.hunks.append(hunk)
    old_left, new_left = old_count, new_count
    old_no, new_no = old_start, new_start
    i = start + 1
    while (old_left > 0 or new_left > 0) and i < len(lines):
        raw = lines[i]
        if raw.startswith("\\"):
            i += 1
            continue
        tag = raw[:1]
        if tag == "+":
            hunk.lines.append(DiffLine("+", raw[1:], None, new_no))
            new_no += 1
            new_left -= 1
        elif tag == "-":
            hunk.lines.append(DiffLine("-", raw[1:], old_no, None))
            old_no += 1
            old_left -= 1
        elif tag == " " or raw == "":
            hunk.lines.append(DiffLine(" ", raw[1:], old_no, new_no))
            old_no += 1
            new_no += 1
            old_left -= 1
            new_left -= 1
        else:
            break
        i += 1
    while i < len(lines) and lines[i].startswith("\\"):
        i += 1
    if old_left > 0 or new_left > 0:
        current.truncated = True
        return i, 1
    return i, 0
