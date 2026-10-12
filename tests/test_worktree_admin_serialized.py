"""`git worktree add` / `remove` / `prune` rewrite `.git/worktrees/`; git does not tolerate two of them running at once in one repository.

CI failed three times on different PRs in `test_conflicting_shared_edits_reported_not_corrupted` with
`fatal: failed to read .git/worktrees/writer-1-.../commondir`: writer 2's `worktree add` listed the admin directories while writer 1's `worktree remove` was
deleting its own. The manager now runs those three commands one at a time per repository.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from wisp.multi_agent._worktree_manager import WorktreeManager


@pytest.fixture
def repo(tmp_path):
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (tmp_path / "shared.txt").write_text("base\n")
    run("add", ".")
    run("commit", "-q", "-m", "base")
    return tmp_path


def watch_worktree_commands(monkeypatch) -> dict:
    """Count `git worktree add|remove|prune` processes alive at the same time."""
    seen = {"now": 0, "max": 0, "calls": 0}
    real = asyncio.create_subprocess_exec

    async def tracked(*args, **kwargs):
        proc = await real(*args, **kwargs)
        if len(args) >= 3 and args[0] == "git" and args[1] == "worktree" and args[2] in ("add", "remove", "prune"):
            seen["calls"] += 1
            seen["now"] += 1
            seen["max"] = max(seen["max"], seen["now"])
            original = proc.communicate

            async def communicate(*a, **k):
                try:
                    return await original(*a, **k)
                finally:
                    seen["now"] -= 1

            proc.communicate = communicate  # type: ignore[method-assign]
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", tracked)
    return seen


@pytest.mark.asyncio
async def test_concurrent_writers_never_run_two_worktree_commands_at_once(repo, monkeypatch):
    seen = watch_worktree_commands(monkeypatch)
    manager = WorktreeManager(repo)

    async def writer(i: int) -> None:
        path = await manager.create(f"writer-{i}")
        (path / "shared.txt").write_text(f"edited by {i}\n")
        await manager.cleanup(path)

    await asyncio.gather(*(writer(i) for i in range(6)))
    assert seen["calls"] >= 12  # six adds and six removes at least: the probe really saw them
    assert seen["max"] == 1, seen


@pytest.mark.asyncio
async def test_the_serialisation_survives_a_second_event_loop(repo):
    """The orchestrator is used from the REPL's loop and from the graph's own `asyncio.run`: a lock made on one must not break the other."""
    manager = WorktreeManager(repo)

    for _ in range(2):
        path = await asyncio.to_thread(lambda: asyncio.run(_roundtrip(manager)))
        assert path is not None


async def _roundtrip(manager):
    path = await manager.create("solo")
    await manager.cleanup(path)
    return path


@pytest.mark.asyncio
async def test_the_orphan_reaper_prunes_one_at_a_time_too(repo, monkeypatch):
    """`reap_orphans` ends in a `git worktree prune`; it must not overlap a writer's `remove` either."""
    seen = watch_worktree_commands(monkeypatch)
    manager = WorktreeManager(repo)
    paths = [await manager.create(f"w{i}") for i in range(4)]
    for n in range(4):
        (manager._worktrees_root / f"orphan-{n}").mkdir()  # not registered with git: the reaper removes these, then prunes
    seen["max"] = 0
    await asyncio.gather(*(manager.cleanup(p) for p in paths), manager.reap_orphans(active_paths={str(p) for p in paths}))
    assert seen["max"] == 1, seen
