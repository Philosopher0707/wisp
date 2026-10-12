"""A private HOME for the whole test process, and a tripwire on the real one.

Why (2026-10-12): the suite ran against the developer's REAL home. Tests that drive the provider wizard, `/provider`, `/model` or `store_key` (several through a pty-spawned
REPL) wrote fixture values (`ollama`, `nemotron-3-ultra:cloud`, `WISP_OLLAMA_URL=2`) into the real `~/.config/wisp/.env`, so a green test run left a working setup broken. The
same reads made seven tests fail on one machine and pass on another (they read the real config).

`enter` points `HOME` at a fresh temporary directory BEFORE anything imports `wisp` (so module-level `Path.home()` constants are private too) and every subprocess a test starts
inherits it. `changed` is the tripwire: the real files are fingerprinted at the start and compared at the end, and a difference fails the run, because a test that writes the real
config is a bug even when it passes. `WISP_TEST_REAL_HOME=1` opts out (a test run that must see the real home, such as a manual check of the installed setup).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

#: The operator files a test must never change, relative to HOME.
WATCHED = (".config/wisp/.env", ".config/wisp/config.json", ".config/wisp/mcp.json", ".config/wisp/memory.json")


def snapshot(home: str | os.PathLike[str] | None) -> dict[str, tuple[int, int] | None]:
    """Fingerprint (mtime in ns, size) of each watched file under `home`; None for a file that does not exist."""
    out: dict[str, tuple[int, int] | None] = {}
    for rel in WATCHED:
        path = Path(home or "") / rel
        try:
            stat = path.stat()
            out[rel] = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            out[rel] = None
    return out


def changed(before: dict[str, tuple[int, int] | None], after: dict[str, tuple[int, int] | None]) -> list[str]:
    return sorted(rel for rel in before if before[rel] != after.get(rel))


def enter(real_home: str | None) -> tuple[str | None, dict[str, tuple[int, int] | None]]:
    """Make HOME private. Returns (the private directory or None when opted out, the fingerprint of the real files)."""
    before = snapshot(real_home) if real_home else {}
    if os.environ.get("WISP_TEST_REAL_HOME") == "1":
        return None, before
    private = tempfile.mkdtemp(prefix="wisp-test-home-")
    os.environ["HOME"] = private
    return private, before


def leave(private: str | None) -> None:
    if private:
        shutil.rmtree(private, ignore_errors=True)
