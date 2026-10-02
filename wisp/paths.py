"""Canonical filesystem locations for operator-facing hints.

Single authority for "where is the repo" so operator hints can never
drift to a dead path. The repo moved under iCloud Drive; a hardcoded
`~/Documents/wisp` sent operators to a directory that does not exist.

`default_workspace_hint()` prefers a path that actually exists on this
machine, and falls back to a generic instruction when it cannot find
one — never emitting a literal that is known-false.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["project_root", "default_workspace_hint", "collapse_home"]


def project_root() -> Path:
    """Directory containing the installed `wisp` package's repo root."""
    return Path(__file__).resolve().parent.parent


def collapse_home(path: str | os.PathLike[str]) -> str:
    """Render an absolute path with `$HOME` collapsed to `~` for display."""
    text = str(path)
    home = str(Path.home())
    if home and text.startswith(home):
        return "~" + text[len(home):]
    return text


def default_workspace_hint() -> str:
    """A workspace suggestion for `/workspace` that is true on this machine.

    Order of preference:
      1. `$WISP_WORKSPACE_HINT` — explicit operator override.
      2. The repo root this package was installed from, if it still exists.
      3. The current working directory, if it exists.
      4. `~` — always true, never a fabricated path.

    Returns a display string with `~` collapsed. Never raises.
    """
    override = os.environ.get("WISP_WORKSPACE_HINT")
    if override:
        return override

    # Repo root: exists for a source checkout / editable install.
    try:
        root = project_root()
        if root.is_dir():
            return collapse_home(str(root))
    except OSError:
        pass

    # Otherwise something we know exists: where the operator is standing.
    try:
        cwd = Path.cwd()
        if cwd.is_dir():
            return collapse_home(str(cwd))
    except OSError:
        pass

    return "~"