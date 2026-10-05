"""A workspace that is the home directory is not a project.

Every file-reading feature (affected-test lookup, grep, repo map, code index) treats the workspace as the tree to read, and
the workspace is simply the directory wisp was launched from. From `$HOME` that is the whole machine: the 581 s write.
One predicate, `is_home_directory`, is shared by the bounded features (which refuse to analyse it) and by the boot
preflight (which tells the user at launch, once, instead of leaving them to wonder why everything is slow).
"""
from __future__ import annotations

import asyncio

from wisp.core.doctor import CheckStatus, _check_path_environment
from wisp.core.workspace_walk import is_home_directory


def test_the_home_directory_is_recognised(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert is_home_directory(tmp_path)
    assert is_home_directory(str(tmp_path))
    assert is_home_directory(tmp_path / "." )


def test_a_symlink_to_home_is_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(home, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))
    assert is_home_directory(link)


def test_a_project_inside_home_is_not_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / "proj"
    proj.mkdir()
    assert not is_home_directory(proj)
    assert not is_home_directory(tmp_path.parent)


def test_a_missing_path_is_not_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert not is_home_directory(tmp_path / "gone")


def _preflight_path_check(workspace, monkeypatch, home):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WISP_WORKSPACE", str(workspace))
    monkeypatch.chdir(workspace)
    return asyncio.run(_check_path_environment())


def test_the_boot_preflight_warns_when_the_workspace_is_the_home_directory(tmp_path, monkeypatch):
    result = _preflight_path_check(tmp_path, monkeypatch, home=tmp_path)
    assert result.status == CheckStatus.WARN
    assert "home directory" in result.message and "project" in result.message


def test_a_project_workspace_is_unchanged(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    result = _preflight_path_check(proj, monkeypatch, home=tmp_path)
    assert result.status == CheckStatus.OK
