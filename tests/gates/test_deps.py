"""Layer 4. INVARIANT D1: nothing adds or changes a dependency, or writes a manifest, while the lock is closed."""

from __future__ import annotations

import os

import pytest

from tests.gates.conftest import rules
from wisp.core.gates import check_command, check_tool_args, parse_lock
from wisp.core.gates.deps import is_manifest_path

ADDS = [
    "pip install requests", "pip3 install -U numpy", "python -m pip install flask", "python3 -m pip install flask==2.0", "pip install git+https://github.com/x/y.git", "pip install https://x.example/a.whl",
    "pip install requests -r requirements.txt", "pip download requests", "uv add httpx", "uv pip install rich", "uv tool install ruff", "uv run --with rich script.py", "poetry add pytest", "poetry update", "pipenv install requests",
    "pipx install black", "conda install numpy", "mamba install x", "npm install lodash", "npm i left-pad", "npm add x", "npm install -g typescript", "npm update", "npm uninstall x", "yarn add react", "yarn upgrade",
    "pnpm add vue", "pnpm update", "bun add zod", "cargo add serde", "cargo install ripgrep", "cargo update", "go get github.com/x/y", "go install github.com/x/y@latest", "go mod tidy", "gem install rails",
    "bundle add rails", "composer require x/y", "dotnet add package Newtonsoft.Json", "swift package update", "brew install jq", "brew upgrade", "apt install curl", "apt-get install -y curl", "yum install x", "apk add curl",
    "npx create-react-app app", "npx some-unknown-tool", "bunx cowsay hi", "npm exec some-unknown-tool", "sudo apt install x",
]
ALLOWED = [
    "npm ci", "npm install", "npm i", "npm run build", "npm test", "yarn install", "yarn", "pnpm install", "bun install", "pip install -r requirements.txt", "pip install -r req.txt -c constraints.txt",
    "pip install -e .", "pip install .", "pip install ./local/pkg", "pip install dist/pkg-1.0.whl", "python -m pip install -e .", "uv sync", "uv run pytest", "uv run python x.py", "uv pip install -r requirements.txt",
    "poetry install", "poetry run pytest", "pipenv install", "cargo build", "cargo test", "cargo check", "go build ./...", "go test ./...", "go mod download", "go install ./cmd/x", "bundle install", "swift build",
    "pip list", "pip freeze", "pip show requests", "npm ls", "npm outdated", "npm audit", "cargo tree", "npx --no-install jest", "echo pip install requests", "grep 'npm install' README.md",
]


@pytest.mark.parametrize("command", ADDS)
def test_adding_or_changing_a_dependency_is_refused_while_locked(ctx, command):
    assert "DEPENDENCY_LOCKED" in rules(check_command(command, ctx)), command


@pytest.mark.parametrize("command", ALLOWED)
def test_installing_what_is_already_declared_is_not_refused(ctx, command):
    found = [v for v in check_command(command, ctx) if v.layer == "dependency"]
    assert not found, (command, [v.render() for v in found])


def test_the_lock_opens_only_when_the_operator_says_unlocked(make_ctx):
    unlocked = make_ctx(deps_locked=False)
    for c in ("pip install requests", "npm install lodash", "echo x >> package.json", "cargo add serde"):
        assert not [v for v in check_command(c, unlocked) if v.layer == "dependency"], c


@pytest.mark.parametrize("value,locked", [("unlocked", False), ("UNLOCKED", False), (" unlocked ", False), ("locked", True), ("", True), (None, True), ("open", True), ("unlock", True), ("false", True), ("0", True), ("off", True), (False, True)])
def test_g4_only_the_exact_word_unlocked_opens_it(value, locked):
    assert parse_lock(value) is locked


class TestManifests:
    @pytest.mark.parametrize("path", ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.toml", "Cargo.lock", "pyproject.toml", "requirements.txt", "requirements-dev.txt", "requirements/base.txt",
                                      "poetry.lock", "uv.lock", "Pipfile", "go.mod", "go.sum", "Gemfile", "Gemfile.lock", "pom.xml", "build.gradle", "Package.swift", "sub/package.json", "a/b/Cargo.toml", "setup.py"])
    def test_manifest_names_are_recognised(self, path):
        assert is_manifest_path(path), path

    @pytest.mark.parametrize("path", ["package.json.bak", "my-package.json", "README.md", "requirements.md", "src/main.py", "Cargo.toml.orig", "notes/package.txt", "poetry.lock.txt"])
    def test_other_files_are_not(self, path):
        assert not is_manifest_path(path), path

    @pytest.mark.parametrize("command", [
        "echo x >> package.json", "echo x > requirements.txt", "sed -i s/1/2/ package.json", "tee Cargo.toml < new", "cp new.json package.json", "mv new.toml pyproject.toml", "cat a > go.mod", "touch Gemfile.lock",
        "rm package-lock.json", "truncate -s 0 yarn.lock", "echo x > sub/package.json",
    ])
    def test_writing_a_manifest_from_the_shell_is_refused(self, ctx, command):
        assert "MANIFEST_LOCKED" in rules(check_command(command, ctx)), command

    def test_reading_a_manifest_is_always_fine(self, ctx):
        for c in ("cat package.json", "grep version Cargo.toml", "jq .dependencies package.json", "cp package.json /dev/null", "diff package.json package.json.bak"):
            assert not [v for v in check_command(c, ctx) if v.layer == "dependency"], c

    @pytest.mark.parametrize("tool", ["write_file", "edit_file", "edit_file_multi", "fs_mutate"])
    def test_writing_a_manifest_with_a_tool_is_refused(self, ctx, tool):
        assert "MANIFEST_LOCKED" in rules(check_tool_args(tool, {"path": "package.json"}, ctx))

    def test_a_manifest_write_is_allowed_once_unlocked(self, make_ctx):
        assert not check_tool_args("write_file", {"path": "package.json"}, make_ctx(deps_locked=False))


class TestProtectedProjectFiles:
    """`.gitignore` and CI workflows ride the same switch as the manifests (hybrid posture: workspace integrity is enforced, not heuristic)."""

    PROTECTED = [".gitignore", "sub/.gitignore", ".github/workflows/ci.yml", "repo/.github/workflows/release.yaml", ".circleci/config.yml", ".gitlab-ci.yml", "Jenkinsfile",
                 "azure-pipelines.yml", ".travis.yml", "bitbucket-pipelines.yml", ".github/dependabot.yml", "dependabot.yml"]

    @pytest.mark.parametrize("path", PROTECTED)
    def test_recognised(self, path):
        assert is_manifest_path(path), path

    @pytest.mark.parametrize("path", [".gitignore.bak", "my.gitignore", ".github/ISSUE_TEMPLATE/bug.md", ".github/CODEOWNERS", "workflows/ci.yml", "github/workflows/ci.yml", "docs/ci.yml", "gitignore"])
    def test_lookalikes_are_not(self, path):
        assert not is_manifest_path(path), path

    @pytest.mark.parametrize("path", PROTECTED)
    def test_a_tool_write_is_refused_while_locked(self, ctx, path):
        v = check_tool_args("write_file", {"path": path}, ctx)
        assert "MANIFEST_LOCKED" in rules(v) and "protected project file" in v[0].reason

    @pytest.mark.parametrize("command", ["echo node_modules >> .gitignore", "sed -i s/a/b/ .github/workflows/ci.yml", "tee .gitlab-ci.yml < new", "rm .circleci/config.yml", "cp new.yml .github/workflows/ci.yml"])
    def test_a_shell_write_is_refused_while_locked(self, ctx, command):
        assert "MANIFEST_LOCKED" in rules(check_command(command, ctx)), command

    def test_reading_them_is_fine(self, ctx):
        for c in ("cat .gitignore", "grep run .github/workflows/ci.yml", "git check-ignore -v build"):
            assert not [v for v in check_command(c, ctx) if v.layer == "dependency"], c

    @pytest.mark.parametrize("path", PROTECTED)
    def test_the_operator_can_open_it(self, make_ctx, path):
        assert not check_tool_args("write_file", {"path": path}, make_ctx(deps_locked=False))


class TestNpxUsesWhatIsInstalled:
    def test_a_locally_installed_binary_is_allowed(self, ws, ctx):
        bindir = os.path.join(ws, "node_modules", ".bin")
        os.makedirs(bindir)
        open(os.path.join(bindir, "tsc"), "w").close()
        assert not [v for v in check_command("npx tsc --noEmit", ctx) if v.layer == "dependency"]

    def test_a_missing_binary_would_be_downloaded_so_it_is_refused(self, ctx):
        assert "DEPENDENCY_LOCKED" in rules(check_command("npx tsc --noEmit", ctx))

    def test_a_path_traversal_in_the_name_does_not_pass_for_an_installed_tool(self, ws, ctx):
        os.makedirs(os.path.join(ws, "node_modules", ".bin"))
        assert "DEPENDENCY_LOCKED" in rules(check_command("npx ../../evil", ctx))
