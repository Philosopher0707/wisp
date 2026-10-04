"""Fleet status: a read-only view over every repo a manifest declares.

The manifest (`wisp.fleet.toml`) is the single source of truth for which repos belong to the workspace,
what role each plays, and whether it must have a remote. This module only *reports*; it never commits,
pushes, switches branches or deletes anything. A human decides what to do with the report.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROLES = frozenset({"orchestrator", "harness", "runtime", "agent", "tool", "app", "archive"})
MANIFEST_NAME = "wisp.fleet.toml"
_SKIP_DIRS = frozenset({"node_modules", "__pycache__", "build", "dist", "site-packages"})
_GIT_TIMEOUT_S = 30


class FleetManifestError(ValueError):
    """The manifest cannot be trusted: say so rather than guessing a default."""


@dataclass(frozen=True)
class RepoSpec:
    name: str
    path: Path
    role: str
    remote_required: bool


@dataclass(frozen=True)
class Manifest:
    repos: list[RepoSpec]
    scan: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class Problem:
    kind: str
    detail: str


@dataclass
class RepoStatus:
    name: str
    path: Path
    branch: str = ""
    upstream: str | None = None
    dirty: int = 0
    ahead: int = 0
    behind: int = 0
    unpublished: int = 0
    stashes: int = 0
    last_commit: str = ""
    problems: list[Problem] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": str(self.path),
            "branch": self.branch,
            "upstream": self.upstream,
            "dirty": self.dirty,
            "ahead": self.ahead,
            "behind": self.behind,
            "unpublished": self.unpublished,
            "stashes": self.stashes,
            "last_commit": self.last_commit,
            "problems": [{"kind": p.kind, "detail": p.detail} for p in self.problems],
        }


def load_manifest(path: Path) -> Manifest:
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise FleetManifestError(f"{path}: cannot read manifest: {exc}") from exc

    base = Path(path).parent
    repos: list[RepoSpec] = []
    seen: set[str] = set()
    for i, raw in enumerate(data.get("repo", [])):
        for key in ("name", "path", "role"):
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise FleetManifestError(f"repo #{i + 1}: missing or empty field {key!r}")
        name, role = raw["name"], raw["role"]
        if role not in ROLES:
            raise FleetManifestError(f"repo {name!r}: unknown role {role!r}; expected one of {sorted(ROLES)}")
        if name in seen:
            raise FleetManifestError(f"duplicate repo name {name!r}")
        seen.add(name)
        remote_required = raw.get("remote_required", role != "archive")
        if not isinstance(remote_required, bool):
            raise FleetManifestError(f"repo {name!r}: remote_required must be true or false")
        repos.append(RepoSpec(name, _resolve(raw["path"], base), role, remote_required))

    orchestrators = [r.name for r in repos if r.role == "orchestrator"]
    if len(orchestrators) != 1:
        raise FleetManifestError(f"exactly one repo must have role 'orchestrator', found {orchestrators or 'none'}")

    scan = [_resolve(s, base) for s in data.get("fleet", {}).get("scan", [])]
    return Manifest(repos, scan)


def _resolve(raw: str, base: Path) -> Path:
    p = Path(raw).expanduser()
    return p if p.is_absolute() else base / p


def _git(path: Path, *args: str, timeout: int = _GIT_TIMEOUT_S) -> tuple[int, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True, text=True, timeout=timeout, env=env, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, proc.stdout.strip()


def _count(path: Path, *args: str) -> int:
    code, out = _git(path, *args)
    return int(out) if code == 0 and out.isdigit() else 0


def repo_status(name: str, path: Path, *, remote_required: bool = True, fetch: bool = False) -> RepoStatus:
    st = RepoStatus(name=name, path=path)
    if not path.exists():
        st.problems.append(Problem("missing", f"{path} does not exist"))
        return st
    if not (path / ".git").exists():
        st.problems.append(Problem("not-a-repo", f"{path} has no .git"))
        return st

    if fetch:
        _git(path, "fetch", "--quiet", "--all", "--prune", timeout=120)

    _, st.branch = _git(path, "symbolic-ref", "--short", "-q", "HEAD")
    st.branch = st.branch or "(detached)"
    _, porcelain = _git(path, "status", "--porcelain")
    st.dirty = len([ln for ln in porcelain.splitlines() if ln.strip()])
    _, stash = _git(path, "stash", "list")
    st.stashes = len([ln for ln in stash.splitlines() if ln.strip()])
    code, last = _git(path, "log", "-1", "--format=%cs")
    st.last_commit = last if code == 0 else ""

    code, upstream = _git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    st.upstream = upstream if code == 0 and upstream else None
    if st.upstream:
        code, counts = _git(path, "rev-list", "--left-right", "--count", "@{u}...HEAD")
        if code == 0 and len(counts.split()) == 2:
            st.behind, st.ahead = (int(n) for n in counts.split())
    st.unpublished = _count(path, "rev-list", "--count", "HEAD", "--not", "--remotes")

    _, remotes = _git(path, "remote")
    has_remote = bool(remotes.split())

    if remote_required and not has_remote:
        st.problems.append(Problem("no-remote", "no remote configured; commits exist only on this machine"))
    if has_remote and st.upstream is None and st.unpublished:
        st.problems.append(Problem("no-upstream", f"branch {st.branch} has {st.unpublished} commit(s) on no remote"))
    if st.ahead:
        st.problems.append(Problem("unpushed", f"{st.ahead} commit(s) ahead of {st.upstream}"))
    if st.behind:
        st.problems.append(Problem("behind", f"{st.behind} commit(s) behind {st.upstream}"))
    if st.dirty:
        st.problems.append(Problem("dirty", f"{st.dirty} uncommitted path(s)"))
    if st.stashes:
        st.problems.append(Problem("stash", f"{st.stashes} stash(es)"))
    return st


def discover_unmanaged(roots: list[Path], managed: list[Path], depth: int = 3) -> list[Path]:
    """Repos under the scan roots that the manifest does not mention. Never descends into a repo."""
    known = {p.resolve() for p in managed}
    found: list[Path] = []

    def walk(d: Path, left: int) -> None:
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name in _SKIP_DIRS or not entry.is_dir():
                continue
            if (entry / ".git").exists():
                if entry.resolve() not in known:
                    found.append(entry)
            elif left > 1:
                walk(entry, left - 1)

    for root in roots:
        if root.is_dir():
            if (root / ".git").exists():
                if root.resolve() not in known:
                    found.append(root)
            else:
                walk(root, depth)
    return found


def find_manifest(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("WISP_FLEET_MANIFEST")
    if env:
        return Path(env).expanduser()
    here = Path.cwd().resolve()
    for d in (here, *here.parents):
        if (d / MANIFEST_NAME).is_file():
            return d / MANIFEST_NAME
    return Path.home() / "dev" / "wisp" / MANIFEST_NAME


def _render(statuses: list[RepoStatus], roles: dict[str, str], unmanaged: list[Path] | None) -> str:
    width = max((len(s.name) for s in statuses), default=4)
    lines = [f"{'REPO':<{width}}  {'ROLE':<12} {'BRANCH':<34} STATE"]
    for s in statuses:
        state = "ok" if not s.problems else ", ".join(f"{p.kind}" + _suffix(s, p) for p in s.problems)
        lines.append(f"{s.name:<{width}}  {roles.get(s.name, ''):<12} {s.branch[:33]:<34} {state}")
    bad = sum(1 for s in statuses if s.problems)
    lines.append("")
    lines.append(f"{len(statuses)} repo(s), {bad} with problems")
    if unmanaged is not None:
        lines.append("")
        if unmanaged:
            lines.append(f"{len(unmanaged)} git repo(s) under the scan roots are not in the manifest:")
            lines.extend(f"  unmanaged  {p}" for p in unmanaged)
        else:
            lines.append("no unmanaged repos under the scan roots")
    return "\n".join(lines)


def _suffix(s: RepoStatus, p: Problem) -> str:
    counts = {"dirty": s.dirty, "unpushed": s.ahead, "behind": s.behind, "stash": s.stashes, "no-upstream": s.unpublished}
    return f"({counts[p.kind]})" if p.kind in counts else ""


def run_fleet(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="wisp fleet", description="Read-only status over the repos in wisp.fleet.toml.")
    parser.add_argument("action", choices=["status", "doctor"])
    parser.add_argument("--manifest", help=f"path to {MANIFEST_NAME}")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--strict", action="store_true", help="exit 1 when any repo has a problem")
    parser.add_argument("--fetch", action="store_true", help="git fetch each repo first (updates remote refs only)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    try:
        manifest = load_manifest(find_manifest(args.manifest))
    except FleetManifestError as exc:
        print(f"wisp fleet: {exc}", file=sys.stderr)
        return 2

    statuses = [repo_status(r.name, r.path, remote_required=r.remote_required, fetch=args.fetch) for r in manifest.repos]
    unmanaged = discover_unmanaged(manifest.scan, [r.path for r in manifest.repos]) if args.action == "doctor" else None
    roles = {r.name: r.role for r in manifest.repos}

    if args.json:
        payload: dict[str, object] = {"repos": [s.to_dict() for s in statuses]}
        if unmanaged is not None:
            payload["unmanaged"] = [str(p) for p in unmanaged]
        print(json.dumps(payload, indent=2))
    else:
        print(_render(statuses, roles, unmanaged))

    failing = any(s.problems for s in statuses) or bool(unmanaged)
    return 1 if args.strict and failing else 0
