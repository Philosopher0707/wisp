"""The bash sandbox cannot rewrite the git metadata that the host later executes.

`DockerSandbox` bind-mounts the whole workspace read-write, `.git` included. Sandboxed bash could
therefore create `.git/hooks/pre-commit` or run `git config core.hooksPath ...`, and the next
host-run `git_commit` / `git_push` (typed tools, outside the sandbox) executes it: a container
escape through a file the host trusts. The command-string scan in the authorizer is a heuristic
(`cd .git && cd hooks && ...` walks past it), so the boundary is enforced where it cannot be
walked around: `.git/hooks` and `.git/config` are mounted read-only over the workspace mount.

The container start-up arguments are built by a pure function so this can be held without Docker.
"""

from __future__ import annotations

import os

from wisp.sandbox import DockerSandbox, docker_run_args


def _repo(tmp_path, *, hooks=True, config=True, git_is_file=False):
    if git_is_file:
        (tmp_path / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
        return tmp_path
    git = tmp_path / ".git"
    git.mkdir()
    if hooks:
        (git / "hooks").mkdir()
        (git / "hooks" / "pre-commit.sample").write_text("#!/bin/sh\n")
    if config:
        (git / "config").write_text("[core]\n")
    return tmp_path


def _mounts(args: list[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-v"]


def test_workspace_is_still_mounted_read_write(tmp_path):
    ws = os.path.realpath(_repo(tmp_path))
    mounts = _mounts(docker_run_args("c", ws, "ubuntu:22.04", "2g", "2"))
    assert f"{ws}:/workspace" in mounts


def test_hooks_and_config_are_mounted_read_only_over_it(tmp_path):
    ws = os.path.realpath(_repo(tmp_path))
    mounts = _mounts(docker_run_args("c", ws, "ubuntu:22.04", "2g", "2"))
    assert f"{ws}/.git/hooks:/workspace/.git/hooks:ro" in mounts
    assert f"{ws}/.git/config:/workspace/.git/config:ro" in mounts
    assert mounts.index(f"{ws}:/workspace") < mounts.index(f"{ws}/.git/hooks:/workspace/.git/hooks:ro"), (
        "the read-only overlays must come after the workspace mount they sit on top of")


def test_only_what_exists_is_overlaid(tmp_path):
    ws = os.path.realpath(_repo(tmp_path, hooks=False))
    mounts = _mounts(docker_run_args("c", ws, "ubuntu:22.04", "2g", "2"))
    assert any(m.endswith("/.git/config:/workspace/.git/config:ro") for m in mounts)
    assert not any("/.git/hooks" in m for m in mounts)


def test_no_git_dir_means_no_overlay(tmp_path):
    ws = os.path.realpath(tmp_path)
    mounts = _mounts(docker_run_args("c", ws, "ubuntu:22.04", "2g", "2"))
    assert mounts == [f"{ws}:/workspace"]


def test_a_git_file_worktree_is_left_alone(tmp_path):
    """`.git` as a file points at a gitdir outside the mount; the container cannot reach it."""
    ws = os.path.realpath(_repo(tmp_path, git_is_file=True))
    mounts = _mounts(docker_run_args("c", ws, "ubuntu:22.04", "2g", "2"))
    assert not any(m.endswith(":ro") for m in mounts)


def test_the_sandbox_starts_the_container_with_those_arguments(tmp_path, monkeypatch):
    """The wiring: `_ensure_container` passes exactly what the builder returns to `docker run`."""
    import wisp.sandbox as mod

    ws = _repo(tmp_path)
    sb = DockerSandbox(str(ws))
    seen: list[list[str]] = []

    class _Done:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        return _Done()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(sb, "is_available", lambda: True)
    sb._ensure_container()

    run = next(c for c in seen if c[:3] == ["docker", "run", "-d"])
    assert run == docker_run_args(sb.container_name, sb.workspace, sb.image, sb.memory, sb.cpus)
    assert any(a.endswith("/.git/hooks:/workspace/.git/hooks:ro") for a in run)
