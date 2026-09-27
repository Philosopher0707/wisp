"""Load `~/.config/wisp/.env` into the process environment — the reader F57 found missing.

`provider_select.store_key()` persists keys to this file through `_upsert_env_file`, and until
this module nothing read it back: a key placed there had no effect on any process (F57).

**Called once, as the first statement of the console entry point** (`wisp.__main__:main`), so it
runs before any `WispConfig` or provider reads the environment. A library importing `wisp`
(the SDK) does not load it, and never did — the fix is additive for every caller.

**Semantics — ADR-0058's rule for a file one component writes and another reads:**

* **Absent** → a no-op, silent. That is the default state, and the environment is untouched.
* **Unreadable** (permissions, a directory, not UTF-8) → one warning, no exception. The file is an
  operator convenience; a problem with it must not break a process that would otherwise work.
* **A malformed line** → skipped with a warning naming its line number; the rest still loads. The
  writer is best-effort, so a partial write is a state the reader must tolerate.
* **The environment wins.** A key already in the environment — even set to an empty string, which
  is the operator's explicit choice — is never overwritten. The file is a fallback, not an override.

**The format is the writer's, and no more:** `KEY=VALUE`, one per line, `#` comments and blank lines
allowed. No quotes, no `export`, no interpolation — a richer format is its own decision.

**A value is never logged.** The file holds API keys; warnings name a line number, never content.
"""
from __future__ import annotations

import logging
import os
import pathlib
import re

logger = logging.getLogger(__name__)

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def user_env_path() -> pathlib.Path:
    """The file `provider_select._persist_env` writes (resolved at call time, so `HOME` counts)."""
    return pathlib.Path.home() / ".config" / "wisp" / ".env"


def load_user_env(path: pathlib.Path | None = None) -> list[str]:
    """Set each `KEY=VALUE` from the file that the environment does not already have.

    Returns the names it set (never the values). Never raises.
    """
    target = pathlib.Path(path) if path is not None else user_env_path()
    try:
        if not target.exists():
            return []
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("could not read %s (%s); its keys are not loaded", target,
                       type(exc).__name__)
        return []

    loaded: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not _KEY.fullmatch(key):
            logger.warning("%s line %d is not KEY=VALUE; skipped", target, number)
            continue
        if key in os.environ:
            continue
        os.environ[key] = value.strip()
        loaded.append(key)
    return loaded
