"""Tests for project context detection."""

from wisp.project_context import (
    ProjectContext,
    detect_project_context,
    format_context,
)


def test_empty_workspace(tmp_path):
    """Empty directory yields no context."""
    ctx = detect_project_context(str(tmp_path))
    assert ctx.project_name is None
    assert ctx.language is None
    assert format_context(ctx) == ""


def test_pyproject_toml(tmp_path):
    """Detect Python project from pyproject.toml."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\n'
        'name = "my-project"\n'
        'requires-python = ">=3.11"\n'
        'dependencies = ["requests>=2.28", "fastapi>=0.100"]\n'
    )
    ctx = detect_project_context(str(tmp_path))
    assert ctx.project_name == "my-project"
    assert ctx.language == "Python"
    assert ctx.language_version == ">=3.11"
    assert "requests>=2.28" in ctx.dependencies
    assert "fastapi>=0.100" in ctx.dependencies
    assert ctx.framework == "FastAPI"


def test_cargo_toml(tmp_path):
    """Detect Rust project from Cargo.toml."""
    cargo = tmp_path / "Cargo.toml"
    cargo.write_text(
        '[package]\n'
        'name = "my-rust-app"\n'
        'edition = "2021"\n'
        '\n'
        '[dependencies]\n'
        'serde = "1.0"\n'
        'tokio = { version = "1", features = ["full"] }\n'
    )
    ctx = detect_project_context(str(tmp_path))
    assert ctx.project_name == "my-rust-app"
    assert ctx.language == "Rust"
    assert ctx.language_version == "2021"
    assert "serde" in ctx.dependencies
    assert "tokio" in ctx.dependencies


def test_package_json(tmp_path):
    """Detect Node.js project from package.json."""
    pkg = tmp_path / "package.json"
    pkg.write_text(
        '{\n'
        '  "name": "my-app",\n'
        '  "dependencies": {\n'
        '    "express": "^4.18",\n'
        '    "react": "^18.2"\n'
        '  },\n'
        '  "devDependencies": {\n'
        '    "jest": "^29.0"\n'
        '  }\n'
        '}'
    )
    ctx = detect_project_context(str(tmp_path))
    assert ctx.project_name == "my-app"
    assert ctx.language == "JavaScript"
    assert "express" in ctx.dependencies
    assert "react" in ctx.dependencies
    assert "jest" in ctx.dev_dependencies
    assert ctx.framework == "React"
    assert ctx.test_framework == "Jest"


def test_go_mod(tmp_path):
    """Detect Go project from go.mod."""
    gomod = tmp_path / "go.mod"
    gomod.write_text(
        'module github.com/user/my-go-app\n'
        'go 1.21\n'
        '\n'
        'require (\n'
        '\tgithub.com/gin-gonic/gin v1.9.0\n'
        '\tgithub.com/stretchr/testify v1.8.0\n'
        ')\n'
    )
    ctx = detect_project_context(str(tmp_path))
    assert ctx.project_name == "github.com/user/my-go-app"
    assert ctx.language == "Go"
    assert ctx.language_version == "1.21"
    assert "github.com/gin-gonic/gin" in ctx.dependencies


def test_docker_detection(tmp_path):
    """Detect Docker presence."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.11")
    ctx = detect_project_context(str(tmp_path))
    assert ctx.has_docker is True


def test_test_detection(tmp_path):
    """Detect test directory."""
    (tmp_path / "tests").mkdir()
    ctx = detect_project_context(str(tmp_path))
    assert ctx.has_tests is True


def test_format_context():
    """Format context as human-readable block."""
    ctx = ProjectContext(
        project_name="test-proj",
        language="Python",
        language_version=">=3.11",
        framework="FastAPI",
        build_system="poetry",
        dependencies=["requests", "pydantic"],
        has_tests=True,
        test_framework="pytest",
    )
    formatted = format_context(ctx)
    assert "## Project Context" in formatted
    assert "test-proj" in formatted
    assert "Python" in formatted
    assert "FastAPI" in formatted
    assert "requests" in formatted
    assert "pytest" in formatted


def test_format_context_empty():
    """Empty context yields empty string."""
    assert format_context(ProjectContext()) == ""
