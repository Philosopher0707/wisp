"""The agent cannot plant code in `.git/hooks` or redirect git through `.git/config`.

`git_commit`, `git_push` and `gh_pr_create` run on the host, outside the bash sandbox
(`wisp/tools/git.py`). Git executes whatever is in `.git/hooks/pre-commit` (and friends) when they
run, and `.git/config` can point `core.hooksPath`, `core.fsmonitor`, `core.sshCommand` or an
`alias.x = !cmd` at any command. `write_file` is auto-approved in the default AUTO_EDIT mode, so an
agent that could write there could get arbitrary host execution from one file write plus one
`git_commit` — a sandbox escape that needs no bash at all.

The protected-path guard only knew `.wisp/hooks`. It now also covers the git metadata that
executes: `.git/hooks` and `.git/config`, at any depth (submodules, nested checkouts). Reads stay
allowed, and look-alikes (`.github/`, `.gitignore`, `.git/config.bak`) are not caught by accident.
"""

from __future__ import annotations

import pytest

from wisp.pathsec import is_protected_path


def _agent_allows(tool: str, args: dict, tmp_path, mode: str = "full") -> bool:
    from wisp.auth.decision import authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import WorkspaceTrust

    principal = local_principal(workspace=str(tmp_path), profile="local")
    return authorize(principal, tool, args,
                     workspace_trust=WorkspaceTrust.TRUSTED,
                     permission_mode=mode).allowed


EXECUTABLE_GIT_METADATA = [
    ".git/hooks/pre-commit",
    ".git/hooks",
    ".git/config",
    "sub/module/.git/hooks/pre-push",
    "sub/module/.git/config",
    "./.git/hooks/../hooks/post-commit",
    "x.git/hooksfoo/.git/hooks/pre-commit",  # first hit is not a boundary; a later one is
]


@pytest.mark.parametrize("path", EXECUTABLE_GIT_METADATA)
def test_predicate_flags_executable_git_metadata(path):
    assert is_protected_path(path), path


@pytest.mark.parametrize("path", [
    ".github/workflows/ci.yml", ".gitignore", ".gitattributes", ".git/config.bak",
    ".git/hooksfoo/x", ".gitconfig", "docs/git/hooks/notes.md", ".git/HEAD",
    ".git/info/exclude", "src/config",
])
def test_predicate_leaves_look_alikes_alone(path):
    assert not is_protected_path(path), path


@pytest.mark.parametrize("tool", ["write_file", "edit_file", "delete_file"])
@pytest.mark.parametrize("path", [".git/hooks/pre-commit", ".git/config", "sub/.git/hooks/pre-push"])
@pytest.mark.parametrize("mode", ["full", "auto_edit"])
def test_agent_cannot_mutate_it(tool, path, mode, tmp_path):
    assert not _agent_allows(tool, {"path": path}, tmp_path, mode)


def test_agent_cannot_rename_into_it(tmp_path):
    assert not _agent_allows(
        "edit_file", {"path": "x.sh", "op": "rename", "new_path": ".git/hooks/pre-commit"}, tmp_path)


def test_agent_can_still_read_it(tmp_path):
    assert _agent_allows("read_file", {"path": ".git/config"}, tmp_path)
    assert _agent_allows("read_file", {"path": ".git/hooks/pre-commit.sample"}, tmp_path)


def test_agent_can_still_write_the_look_alikes(tmp_path):
    assert _agent_allows("write_file", {"path": ".github/workflows/ci.yml"}, tmp_path)
    assert _agent_allows("write_file", {"path": ".gitignore"}, tmp_path)


def test_the_file_tools_refuse_it_too(tmp_path):
    """Not only the authorizer: the tool implementations use the same predicate."""
    from wisp.tools._utils import _is_hook_controlled_path

    assert _is_hook_controlled_path(".git/hooks/pre-commit")
    assert _is_hook_controlled_path(".git/config")
