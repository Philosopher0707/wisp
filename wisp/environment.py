"""Dynamic environment grounding for the system prompt.

Collects the live execution environment — working directory, OS, Python,
shell, git branch + commit hash, package managers, and suggested
verification commands — and formats it as a ``## Environment`` prompt
section. This grounds the model in facts it would otherwise hallucinate
or have to probe with tool calls.

All collection is best-effort: any failure (missing git, unreadable
manifests) degrades to an omitted line, never an exception.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "EnvironmentSnapshot",
    "collect_environment",
    "format_environment_block",
]

# Lockfile / manifest → package-manager detection. First matching marker
# wins per manager, so pip's two markers don't produce duplicate entries.
_PACKAGE_MANAGERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("uv", ("uv.lock",)),
    ("poetry", ("poetry.lock",)),
    ("pip", ("requirements.txt", "pyproject.toml", "setup.py")),
    ("conda", ("environment.yml",)),
    ("npm", ("package-lock.json",)),
    ("yarn", ("yarn.lock",)),
    ("pnpm", ("pnpm-lock.yaml",)),
    ("bun", ("bun.lockb", "bun.lock")),
    ("cargo", ("Cargo.toml",)),
    ("go", ("go.mod",)),
    ("bundler", ("Gemfile.lock",)),
    ("maven", ("pom.xml",)),
    ("gradle", ("build.gradle", "build.gradle.kts")),
    ("composer", ("composer.json",)),
)


@dataclass(frozen=True, slots=True)
class EnvironmentSnapshot:
    """Facts about the machine and workspace the agent is running in."""

    cwd: str
    os_name: str = ""
    os_version: str = ""
    machine: str = ""
    python_version: str = ""
    shell: str = ""
    git_branch: str = ""
    git_commit: str = ""
    package_managers: tuple[str, ...] = ()
    verification_commands: tuple[str, ...] = ()


def _run(args: list[str], cwd: str, timeout: int = 5) -> str:
    """Run a command, returning stripped stdout on success, else ''."""
    try:
        result = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _detect_package_managers(ws: Path) -> tuple[str, ...]:
    """Detect package managers from lockfiles/manifests in the workspace."""
    found: list[str] = []
    for manager, markers in _PACKAGE_MANAGERS:
        if any((ws / marker).exists() for marker in markers):
            found.append(manager)
    return tuple(found)


def _detect_verification_commands(ws: Path) -> tuple[str, ...]:
    """Suggest verification commands based on the detected toolchain."""
    commands: list[str] = []
    test_markers = (
        (ws / "pytest.ini").exists()
        or (ws / "pyproject.toml").exists()
        or (ws / "setup.cfg").exists()
        or any((ws / "tests").glob("test_*.py"))
    )
    if test_markers:
        commands.append("python -m pytest tests/ -x -q")
    if (ws / "package.json").exists():
        pkg = None
        try:
            pkg = json.loads((ws / "package.json").read_text(encoding="utf-8"))
        except Exception:
            pkg = None
        scripts = (pkg or {}).get("scripts", {}) if isinstance(pkg, dict) else {}
        if "test" in scripts:
            commands.append("npm test")
        if "lint" in scripts:
            commands.append("npm run lint")
    # Linters for common configs
    if (ws / ".eslintrc.js").exists() or (ws / "eslint.config.js").exists():
        commands.append("npx eslint .")
    if (ws / ".ruff.toml").exists() or (ws / "ruff.toml").exists():
        commands.append("ruff check .")
    if (ws / ".flake8").exists():
        commands.append("flake8 .")
    return tuple(dict.fromkeys(commands))  # dedupe, preserve order


def collect_environment(workspace: str) -> EnvironmentSnapshot:
    """Collect an EnvironmentSnapshot for *workspace* (all best-effort)."""
    ws = Path(workspace).resolve()
    is_mac = sys.platform == "darwin"
    is_win = os.name == "nt"

    return EnvironmentSnapshot(
        cwd=str(ws),
        os_name="Windows" if is_win else ("macOS" if is_mac else platform.system()),
        os_version=platform.release(),
        machine=platform.machine(),
        python_version=platform.python_version(),
        shell=os.environ.get("SHELL", "").rsplit("/", 1)[-1],
        git_branch=_run(["git", "rev-parse", "--abbrev-ref", "HEAD"], str(ws)),
        git_commit=_run(["git", "rev-parse", "--short", "HEAD"], str(ws)),
        package_managers=_detect_package_managers(ws),
        verification_commands=_detect_verification_commands(ws),
    )


def format_environment_block(snap: EnvironmentSnapshot) -> str:
    """Format a snapshot as the '## Environment' system-prompt section."""
    lines = [
        "## Environment",
        "",
        f"- working directory: {snap.cwd}",
    ]
    if snap.os_name:
        version = f" {snap.os_version}" if snap.os_version else ""
        machine = f" ({snap.machine})" if snap.machine else ""
        lines.append(f"- OS: {snap.os_name}{version}{machine}")
    if snap.python_version:
        lines.append(f"- Python: {snap.python_version}")
    if snap.shell:
        lines.append(f"- shell: {snap.shell}")
    if snap.git_branch:
        commit = f" @ {snap.git_commit}" if snap.git_commit else ""
        lines.append(f"- git: {snap.git_branch}{commit}")
    if snap.package_managers:
        lines.append(f"- package managers: {', '.join(snap.package_managers)}")
    if snap.verification_commands:
        lines.append("- suggested verification commands:")
        for cmd in snap.verification_commands:
            lines.append(f"  - `{cmd}`")
    return "\n".join(lines)
