"""wisp fleet: read-only status over the repos a manifest declares.

Every test builds real git repositories in tmp_path; nothing is mocked, because the whole value of the
command is that it reports what git says.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from wisp.fleet import (
    FleetManifestError,
    Problem,
    discover_unmanaged,
    load_manifest,
    repo_status,
    run_fleet,
)


def _git(cwd: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "HOME": str(cwd), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


def _repo(path: Path, *, commit: bool = True) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    if commit:
        (path / "a.txt").write_text("a")
        _git(path, "add", "a.txt")
        _git(path, "commit", "-q", "-m", "init")
    return path


def _with_remote(repo: Path, bare: Path) -> None:
    bare.mkdir(parents=True)
    _git(bare, "init", "-q", "--bare", "-b", "main")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")


def _kinds(problems: list[Problem]) -> set[str]:
    return {p.kind for p in problems}


class TestRepoStatus:
    def test_clean_pushed_repo_has_no_problems(self, tmp_path):
        repo = _repo(tmp_path / "r")
        _with_remote(repo, tmp_path / "r.git")
        st = repo_status("r", repo)
        assert st.problems == []
        assert st.branch == "main"

    def test_no_remote_is_a_problem(self, tmp_path):
        st = repo_status("r", _repo(tmp_path / "r"))
        assert "no-remote" in _kinds(st.problems)

    def test_dirty_files_are_counted(self, tmp_path):
        repo = _repo(tmp_path / "r")
        _with_remote(repo, tmp_path / "r.git")
        (repo / "a.txt").write_text("changed")
        (repo / "new.txt").write_text("x")
        st = repo_status("r", repo)
        assert st.dirty == 2
        assert "dirty" in _kinds(st.problems)

    def test_unpushed_commit_is_reported_with_its_count(self, tmp_path):
        repo = _repo(tmp_path / "r")
        _with_remote(repo, tmp_path / "r.git")
        (repo / "b.txt").write_text("b")
        _git(repo, "add", "b.txt")
        _git(repo, "commit", "-q", "-m", "second")
        st = repo_status("r", repo)
        assert st.ahead == 1
        assert "unpushed" in _kinds(st.problems)

    def test_branch_with_no_upstream_counts_commits_on_no_remote(self, tmp_path):
        repo = _repo(tmp_path / "r")
        _with_remote(repo, tmp_path / "r.git")
        _git(repo, "switch", "-q", "-c", "topic")
        (repo / "t.txt").write_text("t")
        _git(repo, "add", "t.txt")
        _git(repo, "commit", "-q", "-m", "topic work")
        st = repo_status("r", repo)
        assert st.upstream is None
        assert st.unpublished == 1
        assert "no-upstream" in _kinds(st.problems)

    def test_stash_is_a_problem(self, tmp_path):
        repo = _repo(tmp_path / "r")
        _with_remote(repo, tmp_path / "r.git")
        (repo / "a.txt").write_text("changed")
        _git(repo, "stash", "-q")
        assert "stash" in _kinds(repo_status("r", repo).problems)

    def test_missing_path_is_a_problem_not_a_crash(self, tmp_path):
        st = repo_status("ghost", tmp_path / "nope")
        assert _kinds(st.problems) == {"missing"}

    def test_a_directory_that_is_not_a_repo_is_reported(self, tmp_path):
        (tmp_path / "plain").mkdir()
        assert _kinds(repo_status("plain", tmp_path / "plain").problems) == {"not-a-repo"}

    def test_a_repo_without_commits_does_not_crash(self, tmp_path):
        st = repo_status("r", _repo(tmp_path / "r", commit=False))
        assert "no-remote" in _kinds(st.problems)


class TestManifest:
    def _write(self, tmp_path: Path, body: str) -> Path:
        p = tmp_path / "wisp.fleet.toml"
        p.write_text(body)
        return p

    def test_loads_repos_with_role_and_expanded_path(self, tmp_path):
        m = load_manifest(self._write(tmp_path, '''
[[repo]]
name = "a"
path = "~/x/a"
role = "orchestrator"
'''))
        assert m.repos[0].name == "a"
        assert m.repos[0].role == "orchestrator"
        assert m.repos[0].path == Path.home() / "x" / "a"

    def test_duplicate_names_are_rejected(self, tmp_path):
        with pytest.raises(FleetManifestError, match="duplicate"):
            load_manifest(self._write(tmp_path, '[[repo]]\nname="a"\npath="/a"\nrole="tool"\n[[repo]]\nname="a"\npath="/b"\nrole="tool"\n'))

    def test_unknown_role_is_rejected(self, tmp_path):
        with pytest.raises(FleetManifestError, match="role"):
            load_manifest(self._write(tmp_path, '[[repo]]\nname="a"\npath="/a"\nrole="wizard"\n'))

    def test_missing_field_is_rejected(self, tmp_path):
        with pytest.raises(FleetManifestError, match="path"):
            load_manifest(self._write(tmp_path, '[[repo]]\nname="a"\nrole="tool"\n'))

    def test_exactly_one_orchestrator_is_required(self, tmp_path):
        with pytest.raises(FleetManifestError, match="orchestrator"):
            load_manifest(self._write(tmp_path, '[[repo]]\nname="a"\npath="/a"\nrole="tool"\n'))

    def test_two_orchestrators_are_rejected(self, tmp_path):
        with pytest.raises(FleetManifestError, match="orchestrator"):
            load_manifest(self._write(tmp_path, '[[repo]]\nname="a"\npath="/a"\nrole="orchestrator"\n[[repo]]\nname="b"\npath="/b"\nrole="orchestrator"\n'))

    def test_archive_repos_do_not_need_a_remote(self, tmp_path):
        m = load_manifest(self._write(tmp_path, '''
[[repo]]
name = "a"
path = "/a"
role = "orchestrator"
[[repo]]
name = "old"
path = "/old"
role = "archive"
'''))
        assert [r.remote_required for r in m.repos] == [True, False]

    def test_unparseable_toml_is_a_manifest_error(self, tmp_path):
        with pytest.raises(FleetManifestError):
            load_manifest(self._write(tmp_path, "[[repo"))


class TestDiscoverUnmanaged:
    def test_finds_repos_under_scan_roots_that_the_manifest_omits(self, tmp_path):
        known = _repo(tmp_path / "scan" / "known")
        stray = _repo(tmp_path / "scan" / "stray")
        found = discover_unmanaged([tmp_path / "scan"], managed=[known], depth=2)
        assert found == [stray]

    def test_does_not_descend_into_a_repo(self, tmp_path):
        outer = _repo(tmp_path / "scan" / "outer")
        _repo(outer / "vendor" / "inner")
        assert discover_unmanaged([tmp_path / "scan"], managed=[], depth=4) == [outer]


class TestRunFleet:
    def _setup(self, tmp_path: Path) -> Path:
        orch = _repo(tmp_path / "orch")
        _with_remote(orch, tmp_path / "orch.git")
        bad = _repo(tmp_path / "bad")
        manifest = tmp_path / "wisp.fleet.toml"
        manifest.write_text(f'''
[fleet]
scan = ["{tmp_path}/scan"]
[[repo]]
name = "orch"
path = "{orch}"
role = "orchestrator"
[[repo]]
name = "bad"
path = "{bad}"
role = "tool"
''')
        return manifest

    def test_status_is_read_only_and_exits_zero_by_default(self, tmp_path, capsys):
        manifest = self._setup(tmp_path)
        before = _git(tmp_path / "bad", "status", "--porcelain=v2", "--branch")
        assert run_fleet(["status", "--manifest", str(manifest)]) == 0
        assert _git(tmp_path / "bad", "status", "--porcelain=v2", "--branch") == before
        out = capsys.readouterr().out
        assert "bad" in out and "no-remote" in out

    def test_strict_exits_nonzero_when_any_repo_has_a_problem(self, tmp_path):
        manifest = self._setup(tmp_path)
        assert run_fleet(["status", "--strict", "--manifest", str(manifest)]) == 1

    def test_json_output_is_machine_readable(self, tmp_path, capsys):
        manifest = self._setup(tmp_path)
        run_fleet(["status", "--json", "--manifest", str(manifest)])
        data = json.loads(capsys.readouterr().out)
        by_name = {r["name"]: r for r in data["repos"]}
        assert by_name["orch"]["problems"] == []
        assert by_name["bad"]["problems"][0]["kind"] == "no-remote"

    def test_doctor_reports_unmanaged_repos(self, tmp_path, capsys):
        manifest = self._setup(tmp_path)
        _repo(tmp_path / "scan" / "stray")
        assert run_fleet(["doctor", "--strict", "--manifest", str(manifest)]) == 1
        assert "stray" in capsys.readouterr().out

    def test_bad_manifest_exits_two_with_a_message(self, tmp_path, capsys):
        bad = tmp_path / "m.toml"
        bad.write_text("[[repo]]\nname='a'\n")
        assert run_fleet(["status", "--manifest", str(bad)]) == 2
        assert "path" in capsys.readouterr().err

    def test_unknown_action_exits_two(self, tmp_path, capsys):
        assert run_fleet(["frobnicate"]) == 2
