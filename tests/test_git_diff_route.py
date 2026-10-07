"""GET /api/git/diff: real git repo, real subprocesses, the handler called directly."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from wisp.server.routes import git as git_routes


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                        "HOME": str(cwd)})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _run(tmp_path, "git", "init", "-q")
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    (tmp_path / "old name.txt").write_text("keep\n")
    _run(tmp_path, "git", "add", "-A")
    _run(tmp_path, "git", "commit", "-q", "-m", "init")
    monkeypatch.setattr(git_routes, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


def _diff() -> dict:
    return asyncio.run(git_routes.git_diff())


def test_not_a_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(git_routes, "WORKSPACE_ROOT", tmp_path)
    assert _diff() == {"git": False, "files": [], "truncated": False}


def test_clean_tree_has_no_files(repo):
    assert _diff()["files"] == []


def test_modified_untracked_and_deleted(repo):
    (repo / "a.txt").write_text("one\nTWO\n")
    (repo / "new file.txt").write_text("hello\n")
    (repo / "old name.txt").unlink()
    out = {f["path"]: f for f in _diff()["files"]}
    assert out["a.txt"]["status"] == "modified"
    assert "-two" in out["a.txt"]["diff"] and "+TWO" in out["a.txt"]["diff"]
    assert out["new file.txt"]["status"] == "untracked"
    assert "+hello" in out["new file.txt"]["diff"]
    assert out["old name.txt"]["status"] == "deleted"
    assert "-keep" in out["old name.txt"]["diff"]


def test_rename_is_one_entry_with_the_new_path(repo):
    _run(repo, "git", "mv", "old name.txt", "renamed.txt")
    paths = [f["path"] for f in _diff()["files"]]
    assert paths == ["renamed.txt"]


def test_a_huge_diff_is_clipped(repo, monkeypatch):
    monkeypatch.setattr(git_routes, "MAX_DIFF_BYTES_PER_FILE", 100)
    (repo / "a.txt").write_text("x\n" * 500)
    f = _diff()["files"][0]
    assert f["clipped"] is True and len(f["diff"]) == 100


def test_file_count_is_capped(repo, monkeypatch):
    monkeypatch.setattr(git_routes, "MAX_DIFF_FILES", 2)
    for i in range(4):
        (repo / f"n{i}.txt").write_text("x\n")
    out = _diff()
    assert len(out["files"]) == 2 and out["truncated"] is True


def test_untracked_directory_lists_its_files_not_the_directory(repo):
    (repo / "pkg").mkdir()
    (repo / "pkg" / "a.py").write_text("x = 1\n")
    (repo / "pkg" / "b.py").write_text("y = 2\n")
    paths = sorted(f["path"] for f in _diff()["files"])
    assert paths == ["pkg/a.py", "pkg/b.py"]
