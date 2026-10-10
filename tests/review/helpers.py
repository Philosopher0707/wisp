"""Build real unified diffs for tests without a git repository: difflib produces genuine hunks and the headers follow git's."""

from __future__ import annotations

import difflib

from wisp.review.diff import FileDiff, parse_unified_diff


def make_diff(path: str, old: list[str] | None, new: list[str] | None) -> str:
    header = f"diff --git a/{path} b/{path}\n"
    if old is None:
        header += "new file mode 100644\n"
    elif new is None:
        header += "deleted file mode 100644\n"
    body = "\n".join(
        difflib.unified_diff(old or [], new or [], fromfile="/dev/null" if old is None else f"a/{path}", tofile="/dev/null" if new is None else f"b/{path}", lineterm="", n=2)
    )
    return header + body + "\n"


def files_of(*diffs: str) -> list[FileDiff]:
    return parse_unified_diff("".join(diffs))


def added_file(path: str, lines: list[str]) -> list[FileDiff]:
    return files_of(make_diff(path, None, lines))


def edited_file(path: str, old: list[str], new: list[str]) -> list[FileDiff]:
    return files_of(make_diff(path, old, new))


def fake_token(kind: str) -> str:
    """Built at run time so no real-looking secret sits in the repository (and push protection has nothing to flag)."""
    parts = {
        "openrouter": ("sk-or-v1-", "a1B2c3D4" * 5),
        "github": ("gh" + "p_", "A1b2C3d4E5f6G7h8I9j0" * 2),
        "stripe": ("sk_" + "live_", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7"),
    }[kind]
    return "".join(parts)
