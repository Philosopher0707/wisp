"""Canonical filesystem containment primitive (12.5C).

ONE implementation of "prove this path is inside this root or reject",
used by tools, graph artifacts, and workspace isolation. Dependency-light
(stdlib only); raises ValueError — callers map to their own error types.

Also owns the **protected-path** predicate (Phase 10): ONE implementation of
"is this target inside a directory that non-read operations must not touch",
used by the agent authorization layer (`auth/decision`), the filesystem tools
(`tools/filesystem`, `tools/checkpoints`), and the REST policy gate
(`server/deps`). See `is_protected_path`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping


#: Path fragments whose contents are executed by Wisp itself. A hook script
#: runs with the full process environment before later tool calls, so writing
#: one is a privilege-escalation primitive — an agent that can write here can
#: arrange arbitrary code execution without a further approval. Reads stay
#: allowed; only mutation is refused.
PROTECTED_PATH_FRAGMENTS: frozenset[str] = frozenset({
    ".wisp/hooks",
    ".wisp\\hooks",  # Windows
})


#: Tool/action argument keys that can name a filesystem target. Every
#: authorization path scans all of them, so a rename *into* a protected
#: directory is refused as surely as a write to it. Defined here, beside the
#: predicate, so a new path-bearing argument is added in one place rather than
#: silently escaping one of the guards.
PATH_BEARING_ARGS: frozenset[str] = frozenset({
    "path", "command", "new_path", "dest", "target",
})


def is_protected_path(candidate: str) -> bool:
    """True when `candidate` names something inside a protected directory.

    Boundary-aware, unlike a bare substring test: `.wisp/hooks/x.json` and
    `.wisp/hooks` match, while `.wisp/hooksfoo/x.json` does **not** — the
    fragment must be followed by a separator or end the path.

    Accepts either a filesystem path or a shell command string, because the
    authorization layers hold whichever the caller supplied. Separators are
    normalised so a Windows-style path is caught on POSIX and vice versa.
    Returns False for empty/None input rather than raising: callers treat
    "cannot tell" as "not protected", and the containment primitive is what
    rejects malformed paths.
    """
    if not candidate or not isinstance(candidate, str):
        return False
    norm = os.path.normpath(candidate).replace("\\", "/")
    for fragment in PROTECTED_PATH_FRAGMENTS:
        marker = fragment.replace("\\", "/")
        idx = norm.find(marker)
        if idx < 0:
            continue
        after = norm[idx + len(marker):]
        if after == "" or after.startswith("/"):
            return True
    return False


def touches_protected_path(args: Mapping[str, object] | None) -> bool:
    """True when any path-bearing argument in `args` names a protected target.

    The **scan** the authorization paths need, kept beside the two things it composes:
    `PATH_BEARING_ARGS` says which keys can name a target, `is_protected_path` says whether one
    does, and this applies the second to the first.

    **Why it lives here.** The scan was written out twice — `auth/decision`'s L4 and the REST
    gate's protected-path guard — as the identical expression over `PATH_BEARING_ARGS`. The
    predicate and the key set were already single-sourced, so both copies agreed on *what* is
    protected and *which keys* to look at; what could still drift was the scan itself, and a
    second copy of a guard is how this module's own divergence started (see
    `tests/test_protected_path_guard.py`). ADR-0059 residual 3 named the duplication and
    deferred the removal; this is that removal.

    Falsy values are skipped, so an absent argument is not tested as the empty path. Callers
    keep their own risk guard and their own refusal message — this decides only the predicate.
    """
    return any(
        is_protected_path(str(value))
        for key, value in (args or {}).items()
        if key in PATH_BEARING_ARGS and value
    )


def resolve_contained(root: str, candidate: str, *, allow_absolute: bool = True) -> str:
    """Resolve `candidate` against `root`; return the real path or raise.

    Guarantees (verified by tests/security/test_path_containment.py):
    - NUL bytes rejected (they break os calls unpredictably);
    - absolute candidates rejected unless allow_absolute AND inside root;
    - `..` traversal, symlink escape, and sibling-prefix tricks
      (e.g. /ws2 vs /ws) all rejected via realpath + separator-anchored
      prefix check; the root itself is allowed.
    """
    if not isinstance(root, str) or not isinstance(candidate, str):
        raise ValueError("root and candidate must be text")
    if "\x00" in root or "\x00" in candidate:
        raise ValueError("NUL byte forbidden in paths")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in candidate):
        # Control characters are legal on POSIX but smuggle terminal/log
        # injection through any path that gets displayed; no caller needs them.
        raise ValueError("control characters forbidden in paths")
    base = os.path.realpath(root)
    if os.path.isabs(candidate):
        if not allow_absolute:
            raise ValueError(f"absolute path forbidden: {candidate!r}")
        target = os.path.realpath(candidate)
    else:
        target = os.path.realpath(os.path.join(base, candidate))
    if target == base:
        return target
    prefix = base if base.endswith(os.sep) else base + os.sep
    if not target.startswith(prefix):
        raise ValueError(f"path escapes root: {candidate!r}")
    return target
