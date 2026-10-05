"""The commit a generated page says it was generated at: squash-proof.

The derived pages (`register.md`, `CURRENT_FLAGS.md`, `CURRENT_AUTHORITIES.md`) carry "Generated <date> at `<sha>`", and
`tests/reliability/test_current_authorities_pins.py::test_the_header_names_a_real_ancestor_commit` requires the sha to be a real
ancestor of HEAD. They used to stamp `git rev-parse --short HEAD`, which on a PR branch is a branch commit. A squash merge replaces
the branch's commits with a new one, so the stamp named a commit that no longer exists on `main` and `main` CI went red (#85, repaired
by #86).

The stamp is now the **merge-base of HEAD and `origin/main`**: a commit that is already on `main`, so it is an ancestor of everything
`main` becomes whether the PR is merged, squashed or rebased. On `main` itself that merge-base is HEAD, and when there is no
`origin/main` ref (a fresh clone of another branch, a shallow checkout) it falls back to HEAD, as before.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> str | None:
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


def stamp_sha(repo: Path | str) -> str:
    """The short sha to stamp: the merge-base with ``origin/main``, else HEAD."""
    repo = Path(repo)
    base = _git(repo, "merge-base", "HEAD", "origin/main")
    short = _git(repo, "rev-parse", "--short", base) if base else None
    return short or _git(repo, "rev-parse", "--short", "HEAD") or ""


def stamp_date(repo: Path | str, sha: str) -> str:
    """The commit date (``YYYY-MM-DD``) of the stamped commit."""
    return _git(Path(repo), "log", "-1", "--format=%cs", sha) or ""
