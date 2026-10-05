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
    MANAGED_BY,
    FleetManifestError,
    Problem,
    discover_unmanaged,
    load_manifest,
    merge_mcp_config,
    render_mcp_servers,
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

    def test_a_linked_worktree_of_a_managed_repo_is_not_unmanaged(self, tmp_path):
        known = _repo(tmp_path / "scan" / "known")
        _git(known, "worktree", "add", "-q", "-b", "side", str(tmp_path / "scan" / "known-side"))
        assert discover_unmanaged([tmp_path / "scan"], managed=[known], depth=2) == []

    def test_a_linked_worktree_of_an_untracked_repo_is_still_reported(self, tmp_path):
        stray = _repo(tmp_path / "elsewhere" / "stray")
        _git(stray, "worktree", "add", "-q", "-b", "side", str(tmp_path / "scan" / "stray-side"))
        assert discover_unmanaged([tmp_path / "scan"], managed=[], depth=2) == [tmp_path / "scan" / "stray-side"]

    def test_a_worktree_whose_owner_is_unmanaged_does_not_hide_the_owner(self, tmp_path):
        owner = _repo(tmp_path / "scan" / "owner")
        _git(owner, "worktree", "add", "-q", "-b", "side", str(tmp_path / "scan" / "owner-side"))
        assert discover_unmanaged([tmp_path / "scan"], managed=[], depth=2) == [owner, tmp_path / "scan" / "owner-side"]


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


_WORKER_MANIFEST = '''
[[repo]]
name = "orch"
path = "/o"
role = "orchestrator"
[[repo]]
name = "w"
path = "/w"
role = "agent"
[repo.worker]
command = ["~/bin/py", "-m", "w.mcp"]
timeout_seconds = 45
[repo.worker.env]
W_SPEC = "~/w/spec.json"
[repo.worker.tool_risk]
w_run = "exec"
w_status = "read"
'''


class TestWorkerManifest:
    def _load(self, tmp_path: Path, body: str = _WORKER_MANIFEST):
        p = tmp_path / "wisp.fleet.toml"
        p.write_text(body)
        return load_manifest(p)

    def test_worker_table_is_parsed(self, tmp_path):
        w = self._load(tmp_path).repos[1].worker
        assert w is not None
        assert w.command[-2:] == ["-m", "w.mcp"]
        assert w.timeout_seconds == 45
        assert w.tool_risk == {"w_run": "exec", "w_status": "read"}

    def test_repos_without_a_worker_table_have_none(self, tmp_path):
        assert self._load(tmp_path).repos[0].worker is None

    def test_empty_command_is_rejected(self, tmp_path):
        with pytest.raises(FleetManifestError, match="command"):
            self._load(tmp_path, _WORKER_MANIFEST.replace('command = ["~/bin/py", "-m", "w.mcp"]', "command = []"))

    def test_unknown_risk_level_is_rejected(self, tmp_path):
        with pytest.raises(FleetManifestError, match="risk"):
            self._load(tmp_path, _WORKER_MANIFEST.replace('"exec"', '"yolo"'))

    @pytest.mark.parametrize("role", ["archive", "orchestrator"])
    def test_a_worker_table_is_rejected_on_roles_that_are_not_driven(self, tmp_path, role):
        body = _WORKER_MANIFEST.replace('role = "agent"', f'role = "{role}"')
        if role == "orchestrator":
            body = body.replace('name = "orch"\npath = "/o"\nrole = "orchestrator"', 'name = "orch"\npath = "/o"\nrole = "tool"')
        with pytest.raises(FleetManifestError, match="worker"):
            self._load(tmp_path, body)


class TestRenderMcp:
    def test_entry_has_wisps_mcp_shape_and_is_marked_managed(self, tmp_path):
        p = tmp_path / "wisp.fleet.toml"
        p.write_text(_WORKER_MANIFEST)
        (entry,) = render_mcp_servers(load_manifest(p))
        assert entry["name"] == "w"
        assert entry["transport"] == "stdio"
        assert entry["command"] == str(Path.home() / "bin" / "py")
        assert entry["args"] == ["-m", "w.mcp"]
        assert entry["env"] == {"W_SPEC": str(Path.home() / "w" / "spec.json")}
        assert entry["timeout_seconds"] == 45
        assert entry["tool_risk"]["w_run"] == "exec"
        assert entry["managedBy"] == MANAGED_BY


class TestMergeMcpConfig:
    def _managed(self, name="w"):
        return [{"name": name, "command": "x", "managedBy": MANAGED_BY}]

    def test_keeps_unmanaged_entries_in_a_list_config(self):
        merged = merge_mcp_config([{"name": "browser", "command": "b"}], self._managed())
        assert [e["name"] for e in merged] == ["browser", "w"]

    def test_keeps_the_dict_container_shape(self):
        merged = merge_mcp_config({"mcpServers": [{"name": "browser"}], "other": 1}, self._managed())
        assert merged["other"] == 1
        assert [e["name"] for e in merged["mcpServers"]] == ["browser", "w"]

    def test_replaces_stale_managed_entries_and_drops_removed_ones(self):
        existing = [{"name": "gone", "managedBy": MANAGED_BY}, {"name": "w", "command": "old", "managedBy": MANAGED_BY}]
        merged = merge_mcp_config(existing, self._managed())
        assert merged == self._managed()

    def test_refuses_to_clobber_an_unmanaged_entry_with_the_same_name(self):
        with pytest.raises(FleetManifestError, match="already defined"):
            merge_mcp_config([{"name": "w", "command": "mine"}], self._managed())

    def test_missing_config_starts_empty(self):
        assert merge_mcp_config(None, self._managed()) == self._managed()

    def test_unrecognised_shape_is_an_error_not_an_overwrite(self):
        with pytest.raises(FleetManifestError, match="shape"):
            merge_mcp_config("nonsense", self._managed())


class TestWorkersCommand:
    def _manifest(self, tmp_path):
        p = tmp_path / "wisp.fleet.toml"
        p.write_text(_WORKER_MANIFEST)
        return p

    def test_prints_without_writing_by_default(self, tmp_path, capsys):
        assert run_fleet(["workers", "--manifest", str(self._manifest(tmp_path))]) == 0
        assert json.loads(capsys.readouterr().out)[0]["name"] == "w"

    def test_write_merges_into_an_existing_config_and_preserves_other_servers(self, tmp_path):
        cfg = tmp_path / "mcp.json"
        cfg.write_text(json.dumps([{"name": "browser", "command": "b"}]))
        assert run_fleet(["workers", "--manifest", str(self._manifest(tmp_path)), "--write", str(cfg)]) == 0
        assert [e["name"] for e in json.loads(cfg.read_text())] == ["browser", "w"]

    def test_write_is_idempotent(self, tmp_path):
        cfg = tmp_path / "mcp.json"
        for _ in range(2):
            run_fleet(["workers", "--manifest", str(self._manifest(tmp_path)), "--write", str(cfg)])
        assert [e["name"] for e in json.loads(cfg.read_text())] == ["w"]

    def test_a_corrupt_existing_config_is_left_untouched(self, tmp_path, capsys):
        cfg = tmp_path / "mcp.json"
        cfg.write_text("{not json")
        assert run_fleet(["workers", "--manifest", str(self._manifest(tmp_path)), "--write", str(cfg)]) == 2
        assert cfg.read_text() == "{not json"
