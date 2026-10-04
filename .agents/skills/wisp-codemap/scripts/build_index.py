#!/usr/bin/env python3
"""Regenerate the wisp-codemap skill's ground-truth layer index.

The index is DERIVED from the tree, never hand-edited — same rule as
CURRENT_AUTHORITIES.md. Run after any structural change:

    python .agents/skills/wisp-codemap/scripts/build_index.py

Writes INDEX.md next to the skill, and prints a diff-shaped summary so a
reviewer can see what moved.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

# Directories that are not part of the shipped agent. Keep this list small and
# justified — every entry is a place where a reader would otherwise waste a read.
EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".ruff_cache", ".pytest_cache", "dist", "build", ".eggs", "*.egg-info",
    "graphify-out", ".agent", "qa-results", "docs/archive",
}

# One line each. This is the layer taxonomy the skill teaches.
LAYERS: dict[str, str] = {
    "wisp/core": "turn engine, convergence, context, events, gates",
    "wisp/transport": "Transport ABC + CLI/WS/headless/file transports",
    "wisp/tui": "terminal UI widgets",
    "wisp/repl": "REPL loop",
    "wisp/cli": "slash-command dispatcher, CLI subcommands",
    "wisp/ui": "UI primitives",
    "wisp/server": "FastAPI HTTP routes + REST approval bridge",
    "wisp/auth": "principal, consent, secrets, workspace trust",
    "wisp/policy": "policy engine + capability decisions",
    "wisp/tools": "tool registry and implementations",
    "wisp/providers": "model provider adapters",
    "wisp/contracts": "dataclass/protocol contracts + manifests",
    "wisp/multi_agent": "subagent orchestration (fanout/chain/dag)",
    "wisp/runtime": "runtime wiring",
    "wisp/infra": "audit, policy engine, tracing, telemetry",
    "wisp/trace": "trace recording",
    "wisp/runs": "durable run records",
    "wisp/graph": "graph engine: DSL, executor, verifier, store",
    "wisp/task": "task model",
    "wisp/eval": "evaluation scenarios",
    "wisp/benchmark": "benchmark runners",
    "wisp/plugins": "plugin loading",
    "wisp/extensions": "extension/hook loading",
    "wisp/lsp": "language-server client",
    "wisp/mcp": "MCP client manager",
    "wisp/mcp_servers": "MCP server impls",
    "wisp/sandbox": "sandbox isolation",
    "wisp/release": "packaging/release",
    "wisp/configs": "bundled configs",
    "wisp_net": "standalone network agent",
    "agent": "secondary agent package",
    "tests": "pytest suite",
    "scripts": "dev/derivation scripts",
    # Package root: the 66 modules that sit beside the subpackages. Named here
    # so the top row of the layer table is never blank.
    "wisp": "package root — entry, config, memory, and the root modules below",
    "examples": "example scripts",
    "wisp-desktop": "desktop shell",
    ".kimi": "webbridge helper scripts",
    ".agents": "agent skills + harness config",
}

# Root modules that are load-bearing enough to name individually.
ROOT_MODULES: dict[str, str] = {
    "wisp/entry.py": "process entry point",
    "wisp/commands.py": "legacy command registry",
    "wisp/completion.py": "shell completion",
    "wisp/skills.py": "SKILL.md discovery + parsing",
    "wisp/context_assembler.py": "builds the prompt context",
    "wisp/markdown_parser.py": "streamed markdown rendering",
    "wisp/diff_renderer.py": "diff rendering",
    "wisp/change_tracker.py": "tracks changed files per turn",
    "wisp/capability_filter.py": "tool capability filtering",
    "wisp/pathsec.py": "path traversal defence",
    "wisp/net_security.py": "network egress policy",
    "wisp/approval_state.py": "approval decision state",
    "wisp/agent_memory.py": "cross-session memory",
    "wisp/memory.py": "memory store",
    "wisp/config.py": "configuration",
    "wisp/errors.py": "error taxonomy",
    "wisp/exceptions.py": "exception types",
    "wisp/environment.py": "environment detection",
    "wisp/colors.py": "colour helpers",
    "wisp/diagnostics.py": "diagnostics",
}


def role_for(key: str) -> str:
    """Longest-prefix lookup so nested sublayers inherit their parent's role.

    LAYERS declares top-level layers ("wisp/tui") but keys are full parent
    paths ("wisp/tui/widgets/chat"). An exact-match lookup left 40 of 72 rows
    blank; walking up the path attributes each sublayer to its nearest
    declared ancestor instead.
    """
    probe = key
    while probe and probe not in LAYERS:
        parent, _, _name = probe.rpartition("/")
        if not parent:
            break
        probe = parent
    role = LAYERS.get(probe, "")
    if not role:
        return ""
    depth = key.count("/") - probe.count("/")
    return role if depth == 0 else f"{role} (subpackage)"


def find_root(start: Path) -> Path:
    """Walk up to the directory holding pyproject.toml. Never guess by depth."""
    for cand in [start, *start.parents]:
        if (cand / "pyproject.toml").exists():
            return cand
    return start


def tracked_files(root: Path) -> list[Path]:
    """Prefer git's own view of what ships; fall back to a filesystem walk.

    Git is the authority here: a hardcoded exclusion list goes stale the first
    time someone adds a build dir, and .gitignore already encodes that answer.
    """
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "*.py"],
            capture_output=True, timeout=60, check=True,
        )
        names = res.stdout.decode().split("\0")
        got = [root / n for n in names if n]
        if got:
            return sorted(got)
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(p for p in root.rglob("*.py") if not is_excluded(p, root))


def is_excluded(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(p in EXCLUDED_DIRS or p.endswith(".egg-info") for p in parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None, help="repo root (default: infer)")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if INDEX.md is stale instead of rewriting")
    args = ap.parse_args()

    root = find_root(Path(args.root).resolve()) if args.root else find_root(Path.cwd())
    skill_dir = Path(__file__).resolve().parent.parent
    index_path = skill_dir / "INDEX.md"

    py: dict[str, list[str]] = {}
    for p in tracked_files(root):
        rel = p.relative_to(root).as_posix()
        top = rel.split("/")[0]
        bucket = top if top in {"wisp", "wisp_net", "agent", "tests", "scripts"} else None
        if bucket is None:
            key = top
        elif "/" in rel:
            key = rel.rsplit("/", 1)[0]
        else:
            key = f"{top} (root modules)"
        py.setdefault(key, []).append(rel)

    lines: list[str] = [
        "# wisp-codemap INDEX (derived)",
        "",
        "<!-- GENERATED by build_index.py — DO NOT EDIT. Regenerate: -->",
        "<!--   python .agents/skills/wisp-codemap/scripts/build_index.py -->",
        "",
        f"Repo root: `{root.name}` · {sum(len(v) for v in py.values())} Python files",
        "",
        "## Layers",
        "",
        "| Path | Files | Role |",
        "|---|---|---|",
    ]
    for key in sorted(py, key=lambda k: (-len(py[k]), k)):
        role = role_for(key)
        lines.append(f"| `{key}/` | {len(py[key])} | {role} |")

    lines += ["", "## Modules by layer", ""]
    for key in sorted(py, key=lambda k: (-len(py[k]), k)):
        lines.append(f"### {key}/ ({len(py[key])} files)")
        lines.append("")
        lines.append("```")
        for m in py[key]:
            lines.append(f"  {m}")
        lines.append("```")
        lines.append("")

    out = "\n".join(lines)
    if args.check:
        if not index_path.exists() or index_path.read_text() != out:
            print(f"STALE: {index_path}", file=sys.stderr)
            return 1
        print(f"OK: {index_path} is current")
        return 0

    index_path.write_text(out)
    counts = Counter({k: len(v) for k, v in py.items()})
    print(f"wrote {index_path} ({len(py)} groups, {sum(counts.values())} files)")
    for k, n in counts.most_common(8):
        print(f"  {k:32s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())