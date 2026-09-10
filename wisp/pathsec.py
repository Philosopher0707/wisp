"""Canonical filesystem containment primitive (12.5C).

ONE implementation of "prove this path is inside this root or reject",
used by tools, graph artifacts, and workspace isolation. Dependency-light
(stdlib only); raises ValueError — callers map to their own error types.
"""

from __future__ import annotations

import os


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
