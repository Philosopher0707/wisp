"""Project context detection — automatically detect language, framework, and dependencies.

Scans the workspace for common project files (pyproject.toml, Cargo.toml,
package.json, etc.) and extracts structured context that gets injected into
the agent's system prompt. This lets the LLM know what it's working with
without the user having to describe their project.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ProjectContext:
    """Detected metadata about the current workspace project."""

    project_name: Optional[str] = None
    language: Optional[str] = None
    language_version: Optional[str] = None
    framework: Optional[str] = None
    build_system: Optional[str] = None
    project_type: Optional[str] = None
    dependencies: list[str] = field(default_factory=list)
    dev_dependencies: list[str] = field(default_factory=list)
    has_tests: bool = False
    test_framework: Optional[str] = None
    has_docker: bool = False
    has_makefile: bool = False


def detect_project_context(workspace: str) -> ProjectContext:
    """Scan workspace for project files and extract context.

    Checks for common project configuration files and builds a
    structured summary of the project's language, framework, and tools.
    """
    ws = Path(workspace).resolve()
    ctx = ProjectContext()

    _check_pyproject_toml(ws, ctx)
    _check_setup_py(ws, ctx)
    _check_requirements_txt(ws, ctx)
    _check_package_json(ws, ctx)
    _check_cargo_toml(ws, ctx)
    _check_go_mod(ws, ctx)
    _check_gemfile(ws, ctx)
    _check_docker(ws, ctx)
    _check_makefile(ws, ctx)
    _check_tests(ws, ctx)

    # Infer project type from build system and files
    _infer_project_type(ctx)

    return ctx


def format_context(ctx: ProjectContext) -> str:
    """Format project context as a human-readable block for the system prompt.

    Returns an empty string if no context was detected.
    """
    lines = []

    if ctx.project_name:
        lines.append(f"Project: {ctx.project_name}")
    if ctx.language:
        version = f" {ctx.language_version}" if ctx.language_version else ""
        lines.append(f"Language: {ctx.language}{version}")
    if ctx.framework:
        lines.append(f"Framework: {ctx.framework}")
    if ctx.build_system:
        lines.append(f"Build: {ctx.build_system}")
    if ctx.project_type:
        lines.append(f"Type: {ctx.project_type}")
    if ctx.dependencies:
        deps = ", ".join(ctx.dependencies[:12])
        if len(ctx.dependencies) > 12:
            deps += f" +{len(ctx.dependencies) - 12} more"
        lines.append(f"Dependencies: {deps}")
    if ctx.has_tests:
        tf = f" ({ctx.test_framework})" if ctx.test_framework else ""
        lines.append(f"Tests: yes{tf}")
    if ctx.has_docker:
        lines.append("Docker: yes")
    if ctx.has_makefile:
        lines.append("Makefile: yes")

    if not lines:
        return ""

    return "\n".join(["## Project Context"] + [f"- {l}" for l in lines])


# ── Detector functions ───────────────────────────────────────────────


def _check_pyproject_toml(ws: Path, ctx: ProjectContext):
    """Detect Python project metadata from pyproject.toml."""
    path = ws / "pyproject.toml"
    if not path.exists():
        return

    ctx.language = "Python"
    ctx.build_system = ctx.build_system or "pyproject.toml"
    content = path.read_text(encoding="utf-8", errors="replace")

    # Project name
    m = re.search(r'^\s*name\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    if m:
        ctx.project_name = ctx.project_name or m.group(1)

    # Python version requirement
    m = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    if m:
        ctx.language_version = m.group(1)

    # Dependencies from [project] dependencies (inline or multi-line)
    in_deps = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("dependencies = ["):
            # Inline: dependencies = ["dep1", "dep2"]
            inner = stripped[len("dependencies = ["):]
            if inner.endswith("]"):
                inner = inner[:-1]
            for dep in re.findall(r'["\']([^"\']+)["\']', inner):
                ctx.dependencies.append(dep)
            continue
        if stripped == "dependencies = [":
            in_deps = True
            continue
        if in_deps:
            if stripped == "]":
                break
            dep = stripped.strip("\",' ")
            if dep and not dep.startswith("#"):
                ctx.dependencies.append(dep)

    # Dependencies from [tool.poetry.dependencies]
    in_poetry_deps = False
    for line in content.splitlines():
        if line.strip().startswith("[tool.poetry.dependencies]"):
            in_poetry_deps = True
            continue
        if in_poetry_deps:
            if line.strip().startswith("[") and not line.strip().startswith("[tool.poetry.dependencies"):
                break
            m = re.match(r'^\s*(\w[\w-]*)\s*=', line)
            if m:
                ctx.dependencies.append(m.group(1))

    # Detect framework from dependencies
    _detect_python_framework(ctx)

    # Test framework
    if re.search(r'tool\.pytest', content):
        ctx.test_framework = "pytest"
    if re.search(r'tool\.unittest', content):
        ctx.test_framework = "unittest"


def _check_setup_py(ws: Path, ctx: ProjectContext):
    """Detect Python project metadata from setup.py."""
    path = ws / "setup.py"
    if not path.exists():
        return

    ctx.language = ctx.language or "Python"
    ctx.build_system = ctx.build_system or "setuptools"
    content = path.read_text(encoding="utf-8", errors="replace")

    m = re.search(r'name\s*=\s*["\']([^"\']+)["\']', content)
    if m:
        ctx.project_name = ctx.project_name or m.group(1)

    m = re.search(r'python_requires\s*=\s*["\']([^"\']+)["\']', content)
    if m:
        ctx.language_version = ctx.language_version or m.group(1)

    # Extract install_requires
    m = re.search(r'install_requires\s*=\s*\[(.*?)\]', content, re.DOTALL)
    if m:
        for dep in re.findall(r'["\']([^"\']+)["\']', m.group(1)):
            if dep not in ctx.dependencies:
                ctx.dependencies.append(dep)

    _detect_python_framework(ctx)


def _check_requirements_txt(ws: Path, ctx: ProjectContext):
    """Detect Python dependencies from requirements.txt."""
    for name in ("requirements.txt", "requirements-dev.txt", "requirements/prod.txt"):
        path = ws / name
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for line in content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("-"):
                dep = re.split(r'[=<>!~]', stripped)[0].strip()
                if dep and dep not in ctx.dependencies:
                    ctx.dependencies.append(dep)


def _check_package_json(ws: Path, ctx: ProjectContext):
    """Detect Node.js project metadata from package.json."""
    path = ws / "package.json"
    if not path.exists():
        return

    ctx.language = ctx.language or "JavaScript"
    ctx.build_system = ctx.build_system or "npm"
    content = path.read_text(encoding="utf-8", errors="replace")

    m = re.search(r'"name"\s*:\s*"([^"]+)"', content)
    if m:
        ctx.project_name = ctx.project_name or m.group(1)

    # Dependencies
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        m = re.search(rf'"{section}"\s*:\s*\{{(.*?)\}}', content, re.DOTALL)
        if m:
            for dep in re.findall(r'"([^"]+)"\s*:', m.group(1)):
                if section == "devDependencies":
                    ctx.dev_dependencies.append(dep)
                elif dep not in ctx.dependencies:
                    ctx.dependencies.append(dep)

    # Detect framework
    all_deps = ctx.dependencies + ctx.dev_dependencies
    deps_str = " ".join(all_deps).lower()
    if "next" in deps_str:
        ctx.framework = ctx.framework or "Next.js"
    elif "react" in deps_str:
        ctx.framework = ctx.framework or "React"
    elif "vue" in deps_str:
        ctx.framework = ctx.framework or "Vue"
    elif "angular" in deps_str:
        ctx.framework = ctx.framework or "Angular"
    elif "express" in deps_str:
        ctx.framework = ctx.framework or "Express"

    # Test framework
    if "jest" in deps_str:
        ctx.test_framework = ctx.test_framework or "Jest"
    elif "vitest" in deps_str:
        ctx.test_framework = ctx.test_framework or "Vitest"
    elif "mocha" in deps_str:
        ctx.test_framework = ctx.test_framework or "Mocha"

    # TypeScript
    if (ws / "tsconfig.json").exists():
        ctx.language = "TypeScript"


def _check_cargo_toml(ws: Path, ctx: ProjectContext):
    """Detect Rust project metadata from Cargo.toml."""
    path = ws / "Cargo.toml"
    if not path.exists():
        return

    ctx.language = ctx.language or "Rust"
    ctx.build_system = ctx.build_system or "Cargo"
    content = path.read_text(encoding="utf-8", errors="replace")

    m = re.search(r'^\s*name\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if m:
        ctx.project_name = ctx.project_name or m.group(1)

    # Edition
    m = re.search(r'edition\s*=\s*"([^"]+)"', content)
    if m:
        ctx.language_version = ctx.language_version or m.group(1)

    # Dependencies from [dependencies] or [workspace.dependencies]
    for section in ("[dependencies]", "[workspace.dependencies]"):
        in_deps = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == section:
                in_deps = True
                continue
            if in_deps:
                if stripped.startswith("[") and stripped != section:
                    break
                m = re.match(r'^(\w[\w-]*)\s*=', stripped)
                if m:
                    dep = m.group(1)
                    if dep not in ctx.dependencies:
                        ctx.dependencies.append(dep)

    # Dev dependencies
    in_dev = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "[dev-dependencies]":
            in_dev = True
            continue
        if in_dev:
            if stripped.startswith("["):
                break
            m = re.match(r'^(\w[\w-]*)\s*=', stripped)
            if m:
                ctx.dev_dependencies.append(m.group(1))


def _check_go_mod(ws: Path, ctx: ProjectContext):
    """Detect Go project metadata from go.mod."""
    path = ws / "go.mod"
    if not path.exists():
        return

    ctx.language = ctx.language or "Go"
    ctx.build_system = ctx.build_system or "go mod"
    content = path.read_text(encoding="utf-8", errors="replace")

    m = re.search(r'^module\s+(\S+)', content, re.MULTILINE)
    if m:
        ctx.project_name = ctx.project_name or m.group(1)

    m = re.search(r'^go\s+(\S+)', content, re.MULTILINE)
    if m:
        ctx.language_version = ctx.language_version or m.group(1)

    for m in re.finditer(r'^\s+(\S+)\s+v\S+', content, re.MULTILINE):
        dep = m.group(1)
        if dep not in ctx.dependencies:
            ctx.dependencies.append(dep)


def _check_gemfile(ws: Path, ctx: ProjectContext):
    """Detect Ruby project metadata from Gemfile."""
    path = ws / "Gemfile"
    if not path.exists():
        return

    ctx.language = ctx.language or "Ruby"
    ctx.build_system = ctx.build_system or "Bundler"
    content = path.read_text(encoding="utf-8", errors="replace")

    for m in re.finditer(r'^\s*gem\s+["\']([^"\']+)["\']', content, re.MULTILINE):
        dep = m.group(1)
        if dep not in ctx.dependencies:
            ctx.dependencies.append(dep)

    # Detect framework
    deps_str = " ".join(ctx.dependencies).lower()
    if "rails" in deps_str:
        ctx.framework = ctx.framework or "Rails"
    elif "sinatra" in deps_str:
        ctx.framework = ctx.framework or "Sinatra"


def _check_docker(ws: Path, ctx: ProjectContext):
    """Detect Docker configuration."""
    for name in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".dockerignore"):
        if (ws / name).exists():
            ctx.has_docker = True
            return


def _check_makefile(ws: Path, ctx: ProjectContext):
    """Detect Makefile."""
    if (ws / "Makefile").exists() or (ws / "makefile").exists():
        ctx.has_makefile = True


def _check_tests(ws: Path, ctx: ProjectContext):
    """Detect test configuration and test files."""
    # Check for test directories
    for test_dir in ("tests", "test", "spec", "__tests__"):
        p = ws / test_dir
        if p.exists() and p.is_dir():
            ctx.has_tests = True
            break

    # Check for test config files (if not already detected)
    if not ctx.test_framework:
        for cfg in ("pytest.ini", ".pytest.ini"):
            if (ws / cfg).exists():
                ctx.test_framework = "pytest"
                ctx.has_tests = True
                break

    if not ctx.test_framework:
        for cfg in ("jest.config.js", "jest.config.ts", "vitest.config.ts"):
            if (ws / cfg).exists():
                ctx.test_framework = "Jest" if "jest" in cfg else "Vitest"
                ctx.has_tests = True
                break


def _detect_python_framework(ctx: ProjectContext):
    """Infer Python framework from detected dependencies."""
    deps_str = " ".join(ctx.dependencies).lower()
    if "fastapi" in deps_str:
        ctx.framework = ctx.framework or "FastAPI"
    elif "django" in deps_str:
        ctx.framework = ctx.framework or "Django"
    elif "flask" in deps_str:
        ctx.framework = ctx.framework or "Flask"
    elif "aiohttp" in deps_str:
        ctx.framework = ctx.framework or "aiohttp"


def _infer_project_type(ctx: ProjectContext):
    """Infer project type from detected metadata."""
    if not ctx.project_type:
        deps_str = " ".join(ctx.dependencies + ctx.dev_dependencies).lower()
        if any(fw in deps_str for fw in ("fastapi", "django", "flask", "aiohttp", "express", "next")):
            ctx.project_type = "web application"
        elif ctx.language == "Rust" and any("cli" in d.lower() for d in ctx.dependencies):
            ctx.project_type = "CLI tool"
        elif ctx.language == "Python" and not ctx.framework:
            ctx.project_type = "library/script"
        elif ctx.language == "Go":
            ctx.project_type = "application"
