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
MANAGED_BY = "wisp-fleet"
_WORKER_ROLES = frozenset({"agent", "tool", "harness", "runtime", "app"})
_RISK_LEVELS = frozenset({"read", "write", "exec", "network", "privileged"})
_SKIP_DIRS = frozenset({"node_modules", "__pycache__", "build", "dist", "site-packages"})
_GIT_TIMEOUT_S = 30


class FleetManifestError(ValueError):
    """The manifest cannot be trusted: say so rather than guessing a default."""


@dataclass(frozen=True)
class WorkerSpec:
    """How wisp reaches a repo as an MCP stdio server (ADR 2026-10-04-fleet-worker-contract)."""

    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 30
    tool_risk: dict[str, str] = field(default_factory=dict)
    disabled_tools: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RepoSpec:
    name: str
    path: Path
    role: str
    remote_required: bool
    worker: WorkerSpec | None = None


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
        worker = _parse_worker(name, role, raw["worker"]) if "worker" in raw else None
        repos.append(RepoSpec(name, _resolve(raw["path"], base), role, remote_required, worker))

    orchestrators = [r.name for r in repos if r.role == "orchestrator"]
    if len(orchestrators) != 1:
        raise FleetManifestError(f"exactly one repo must have role 'orchestrator', found {orchestrators or 'none'}")

    scan = [_resolve(s, base) for s in data.get("fleet", {}).get("scan", [])]
    return Manifest(repos, scan)


def _parse_worker(name: str, role: str, raw: object) -> WorkerSpec:
    if role not in _WORKER_ROLES:
        raise FleetManifestError(f"repo {name!r}: a worker table is not allowed on role {role!r}")
    if not isinstance(raw, dict):
        raise FleetManifestError(f"repo {name!r}: worker must be a table")
    command = raw.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(c, str) and c for c in command):
        raise FleetManifestError(f"repo {name!r}: worker.command must be a non-empty list of strings")
    env = raw.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise FleetManifestError(f"repo {name!r}: worker.env must be a table of strings")
    timeout = raw.get("timeout_seconds", 30)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise FleetManifestError(f"repo {name!r}: worker.timeout_seconds must be a positive integer")
    risk = raw.get("tool_risk", {})
    if not isinstance(risk, dict) or any(v not in _RISK_LEVELS for v in risk.values()):
        raise FleetManifestError(f"repo {name!r}: worker.tool_risk values must be one of {sorted(_RISK_LEVELS)}; unknown risk level")
    disabled = raw.get("disabled_tools", [])
    if not isinstance(disabled, list) or not all(isinstance(t, str) for t in disabled):
        raise FleetManifestError(f"repo {name!r}: worker.disabled_tools must be a list of strings")
    return WorkerSpec(list(command), dict(env), timeout, dict(risk), list(disabled))


def _expand(token: str) -> str:
    return os.path.expanduser(token) if token.startswith("~") else token


def render_mcp_servers(manifest: Manifest) -> list[dict[str, object]]:
    """The MCP entries wisp's `mcp.json` should hold for the manifest's workers.

    Rendering only describes a server. Wisp's MCP trust model (origin pinning, first-use consent, scopes)
    still decides whether it may run, and the first use still asks the human.
    """
    entries: list[dict[str, object]] = []
    for repo in manifest.repos:
        w = repo.worker
        if w is None:
            continue
        entry: dict[str, object] = {
            "name": repo.name,
            "transport": "stdio",
            "command": _expand(w.command[0]),
            "args": [_expand(a) for a in w.command[1:]],
            "env": {k: _expand(v) for k, v in w.env.items()},
            "timeout_seconds": w.timeout_seconds,
            "tool_risk": dict(w.tool_risk),
            "managedBy": MANAGED_BY,
        }
        if w.disabled_tools:
            entry["disabled_tools"] = list(w.disabled_tools)
        entries.append(entry)
    return entries


def merge_mcp_config(existing: object, managed: list[dict[str, object]]) -> object:
    """Fold the fleet's entries into an existing config without touching anyone else's servers."""
    if existing is None:
        servers: list[object] = []
        container: dict[str, object] | None = None
    elif isinstance(existing, list):
        servers, container = list(existing), None
    elif isinstance(existing, dict) and isinstance(existing.get("mcpServers"), list):
        servers, container = list(existing["mcpServers"]), existing
    else:
        raise FleetManifestError("existing MCP config has an unrecognised shape; refusing to overwrite it")

    kept = [s for s in servers if not (isinstance(s, dict) and s.get("managedBy") == MANAGED_BY)]
    taken = {s.get("name") for s in kept if isinstance(s, dict)}
    for entry in managed:
        if entry["name"] in taken:
            raise FleetManifestError(f"MCP server {entry['name']!r} is already defined by hand; rename one of them")
    merged = [*kept, *managed]
    if container is None:
        return merged
    return {**container, "mcpServers": merged}


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
    parser.add_argument("action", choices=["status", "doctor", "workers"])
    parser.add_argument("--manifest", help=f"path to {MANIFEST_NAME}")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--strict", action="store_true", help="exit 1 when any repo has a problem")
    parser.add_argument("--fetch", action="store_true", help="git fetch each repo first (updates remote refs only)")
    parser.add_argument("--write", metavar="MCP_JSON", help="workers: merge the rendered servers into this MCP config")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    try:
        manifest = load_manifest(find_manifest(args.manifest))
    except FleetManifestError as exc:
        print(f"wisp fleet: {exc}", file=sys.stderr)
        return 2

    if args.action == "workers":
        return _run_workers(manifest, args.write)

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


def _run_workers(manifest: Manifest, write: str | None) -> int:
    managed = render_mcp_servers(manifest)
    if write is None:
        print(json.dumps(managed, indent=2))
        return 0

    target = Path(write).expanduser()
    existing: object = None
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"wisp fleet: {target}: cannot read existing MCP config ({exc}); left untouched", file=sys.stderr)
            return 2
    try:
        merged = merge_mcp_config(existing, managed)
    except FleetManifestError as exc:
        print(f"wisp fleet: {target}: {exc}", file=sys.stderr)
        return 2

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    print(f"wrote {len(managed)} worker server(s) to {target}; wisp still asks for consent on first use")
    return 0
