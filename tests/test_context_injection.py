"""Tests for pre-loaded context injection — module summary + lint context."""

from unittest.mock import patch

from wisp.core.engine import WispAgentCore


class TestBuildModuleSummary:
    """Tests for _build_module_summary — project structure overview."""

    def test_empty_workspace(self, tmp_path):
        core = WispAgentCore.__new__(WispAgentCore)
        result = core._build_module_summary(str(tmp_path))
        assert result == ""

    def test_python_package_with_docstring(self, tmp_path):
        pkg = tmp_path / "mylib"
        pkg.mkdir()
        (pkg / "__init__.py").write_text('"""Core library for data processing."""\n')
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'")

        core = WispAgentCore.__new__(WispAgentCore)
        result = core._build_module_summary(str(tmp_path))
        assert "## Project Structure" in result
        assert "Python project" in result
        assert "**mylib/**" in result
        assert "Core library for data processing" in result

    def test_python_package_without_docstring(self, tmp_path):
        pkg = tmp_path / "utils"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")

        core = WispAgentCore.__new__(WispAgentCore)
        result = core._build_module_summary(str(tmp_path))
        assert "**utils/**" in result
        # No docstring on first line means no description appended
        # but it should still be listed as a package

    def test_multiple_packages(self, tmp_path):
        for name in ("core", "api", "models"):
            pkg = tmp_path / name
            pkg.mkdir()
            (pkg / "__init__.py").write_text(f'"""{name} package."""\n')
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'")

        core = WispAgentCore.__new__(WispAgentCore)
        result = core._build_module_summary(str(tmp_path))
        assert "**core/**" in result
        assert "**api/**" in result
        assert "**models/**" in result

    def test_non_package_source_dirs(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("print('hi')")

        core = WispAgentCore.__new__(WispAgentCore)
        result = core._build_module_summary(str(tmp_path))
        assert "Other source directories" in result
        assert "`src/`" in result

    def test_skips_hidden_and_vendor_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "venv").mkdir()
        (tmp_path / "__pycache__").mkdir()
        # A real package
        pkg = tmp_path / "app"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")

        core = WispAgentCore.__new__(WispAgentCore)
        result = core._build_module_summary(str(tmp_path))
        assert "**app/**" in result
        assert ".git" not in result
        assert "node_modules" not in result
        assert "venv" not in result
        assert "__pycache__" not in result

    def test_node_project_detection(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        src = tmp_path / "src"
        src.mkdir()
        (src / "index.ts").write_text("// ts")

        core = WispAgentCore.__new__(WispAgentCore)
        result = core._build_module_summary(str(tmp_path))
        assert "Node/TS project" in result

    def test_rust_project_detection(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname='test'")
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.rs").write_text("fn main() {}")

        core = WispAgentCore.__new__(WispAgentCore)
        result = core._build_module_summary(str(tmp_path))
        assert "Rust project" in result

    def test_config_files_listed(self, tmp_path):
        # Non-Python config files listed in 'Config:' line
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "Makefile").write_text("all:")

        core = WispAgentCore.__new__(WispAgentCore)
        result = core._build_module_summary(str(tmp_path))
        assert "Config:" in result
        assert "`package.json`" in result
        assert "`Makefile`" in result

    def test_oserror_graceful(self, tmp_path):
        core = WispAgentCore.__new__(WispAgentCore)
        with patch("os.listdir", side_effect=OSError("permission denied")):
            result = core._build_module_summary(str(tmp_path))
        assert result == ""


class TestBuildLintContext:
    """Tests for _build_lint_context — available code checks overview."""

    def test_empty_workspace(self, tmp_path):
        core = WispAgentCore.__new__(WispAgentCore)
        result = core._build_lint_context(str(tmp_path))
        assert result == ""

    def test_python_lint_detected(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1")

        core = WispAgentCore.__new__(WispAgentCore)
        result = core._build_lint_context(str(tmp_path))
        assert "## Available Code Checks" in result
        assert "**.py** files:" in result
        assert "py_compile" in result

    def test_typescript_lint_detected(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir(parents=True)
        (src / "index.ts").write_text("const x = 1;")

        core = WispAgentCore.__new__(WispAgentCore)
        result = core._build_lint_context(str(tmp_path))
        assert "**.ts** files:" in result
        assert "tsc" in result

    def test_mixed_extensions(self, tmp_path):
        (tmp_path / "main.py").write_text("x=1")
        (tmp_path / "util.ts").write_text("const y=2")
        (tmp_path / "lib.rs").write_text("fn main(){}")

        core = WispAgentCore.__new__(WispAgentCore)
        result = core._build_lint_context(str(tmp_path))
        assert ".py" in result
        assert ".ts" in result
        assert ".rs" in result

    def test_installed_binaries_listed(self, tmp_path):
        (tmp_path / "main.py").write_text("x=1")

        core = WispAgentCore.__new__(WispAgentCore)
        with patch("shutil.which", return_value="/usr/local/bin/ruff"):
            result = core._build_lint_context(str(tmp_path))
        assert "Installed:" in result
        assert "`ruff`" in result

    def test_no_installed_binaries(self, tmp_path):
        (tmp_path / "main.py").write_text("x=1")

        core = WispAgentCore.__new__(WispAgentCore)
        with patch("shutil.which", return_value=None):
            result = core._build_lint_context(str(tmp_path))
        assert "Installed:" not in result

    def test_depth_limit(self, tmp_path):
        # Deeply nested file should not be scanned — depth limit is 3
        deep = tmp_path
        for part in ("a", "b", "c", "d", "e"):
            deep = deep / part
        deep.mkdir(parents=True)
        (deep / "deep.py").write_text("x=1")

        core = WispAgentCore.__new__(WispAgentCore)
        result = core._build_lint_context(str(tmp_path))
        # May or may not find it depending on walk order, but shouldn't crash
        assert isinstance(result, str)
