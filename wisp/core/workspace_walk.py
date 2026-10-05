"""One bounded, pruned, deterministic walk of a workspace.

Every caller that reads files from a workspace (the affected-test lookup after each write, grep and glob, the repo map,
the code index, ...) used to carry its own `os.walk` / `rglob` loop and its own skip list. A rule one loop had and another
lacked is how a write from ``$HOME`` came to spend 581 s parsing ``Library`` and ``site-packages``. This module is the
mechanism, once:

* **Prune before descending.** A skipped directory is never read (``os.walk`` with ``dirnames`` edited in place), so the
  cost of a rule is the cost of not looking, not of looking and discarding.
* **Never follow symlinks**, for files or directories.
* **Stable order.** A directory's files, then its subdirectories, each sorted: two walks of a tree agree.
* **A budget** on files and wall-clock time, with the caller's choice of policy: ``"stop"`` (yield what was found, the
  budget says it ran out; for a search whose partial result is still useful) or ``"raise"`` (refuse; for an analysis
  where a partial tree would silently give a wrong answer).

The caller still states which directories to skip (``skip_dirs``) and whether to skip hidden ones, so moving a caller onto
this changes nothing until that caller chooses to. ``STANDARD_SKIP_DIRS`` is the set for new callers.

Core primitive: standard library only, so it can be imported from any layer.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

__all__ = ["STANDARD_SKIP_DIRS", "WalkBudget", "WalkBudgetExceeded", "is_home_directory", "walk_files"]

#: Dependency, build-output and per-machine trees that are never the project's own source. Virtualenvs are also
#: recognised by their ``pyvenv.cfg`` whatever they are called (see ``skip_venvs``).
STANDARD_SKIP_DIRS: frozenset[str] = frozenset({
    "__pycache__", "node_modules", "site-packages", "dist-packages", "venv", "env", "virtualenv",
    "build", "dist", "Library",
})


class WalkBudgetExceeded(RuntimeError):
    """A walk with ``on_budget="raise"`` ran past its file or time budget."""


class WalkBudget:
    """A file-count and wall-clock allowance. The clock starts at the first charge, not at construction."""

    def __init__(self, max_files: int | None = None, max_seconds: float | None = None) -> None:
        self.max_files = max_files
        self.max_seconds = max_seconds
        self.files = 0
        self.reason = ""  # "", "files" or "time"
        self._deadline: float | None = None

    @property
    def exceeded(self) -> bool:
        return bool(self.reason)

    def allow_next(self) -> bool:
        """May one more file be yielded? Records why not, and stays refused once refused."""
        if self.reason:
            return False
        if self._deadline is None and self.max_seconds is not None:
            self._deadline = time.monotonic() + self.max_seconds
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self.reason = "time"
            return False
        if self.max_files is not None and self.files >= self.max_files:
            self.reason = "files"
            return False
        self.files += 1
        return True


def is_home_directory(path: str | os.PathLike[str]) -> bool:
    """Is ``path`` the user's home directory (after resolving symlinks)? A home directory is not a project.

    The workspace is whatever directory wisp was launched from; from ``$HOME`` it is the whole machine, so features that
    read the workspace refuse to analyse it and the boot preflight says so once.
    """
    try:
        return Path(path).resolve() == Path.home().resolve()
    except (OSError, RuntimeError):
        return False


def _is_venv(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "pyvenv.cfg"))


def walk_files(
    root: str | os.PathLike[str],
    *,
    skip_dirs: Iterable[str] = (),
    skip_hidden: bool = False,
    skip_venvs: bool = False,
    suffixes: tuple[str, ...] | None = None,
    budget: WalkBudget | None = None,
    on_budget: str = "stop",
) -> Iterator[str]:
    """Yield the path (a ``str``) of every regular, non-symlink file under ``root``, in stable order.

    ``skip_dirs`` are directory *names* never entered; ``skip_hidden`` also skips names starting with ``.``;
    ``skip_venvs`` skips any directory holding a ``pyvenv.cfg``. ``suffixes`` keeps only files ending in one of them
    (and only those are charged to the budget). With a ``budget``, ``on_budget`` says what running out means:
    ``"stop"`` ends the walk quietly (``budget.exceeded`` tells the caller), ``"raise"`` raises
    :class:`WalkBudgetExceeded`. A budget that is exactly enough is not exceeded.
    """
    if on_budget not in ("stop", "raise"):
        raise ValueError(f"on_budget must be 'stop' or 'raise', not {on_budget!r}")
    skip = frozenset(skip_dirs)
    base = os.fspath(root)
    if not os.path.isdir(base):
        return

    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in skip
            and not (skip_hidden and d.startswith("."))
            and not os.path.islink(os.path.join(dirpath, d))
            and not (skip_venvs and _is_venv(os.path.join(dirpath, d)))
        )
        for name in sorted(filenames):
            if suffixes is not None and not name.endswith(suffixes):
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            if budget is not None and not budget.allow_next():
                if on_budget == "raise":
                    raise WalkBudgetExceeded(
                        f"{base}: walk budget exceeded ({budget.reason}; limits: files={budget.max_files}, "
                        f"seconds={budget.max_seconds})")
                return
            yield full
