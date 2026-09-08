"""Tests for speculative search (GH#29).

Hermetic fake backend for oracle logic (keys bound at creation — no
shared mutable resolution); one REAL end-to-end over git +
WorktreeManager proving isolation, pruning, apply-back, and cleanup.
"""

import asyncio
import subprocess
import sys

from wisp.core.speculative.search import (
    Candidate,
    SpeculativeSearchEngine,
    diff_delta,
)


class FakeBackend:
    """In-memory trial sandbox with scripted oracle outcomes."""

    instances: list["FakeBackend"] = []

    def __init__(self, primary: dict, outcomes: dict, key: str):
        self._primary = primary
        self._outcomes = outcomes
        self.key = key
        from pathlib import Path
        self.root = Path(f"/fake/{key}")
        self.files: dict[str, str] = {}
        self.cleaned = False
        FakeBackend.instances.append(self)

    async def write_files(self, writes):
        if self._outcomes.get(self.key) == "crash":
            raise RuntimeError("injected trial crash")
        self.files.update(writes)

    async def run_tests(self, command):
        outcome = self._outcomes.get(self.key, (1, "fail"))
        if isinstance(outcome, tuple) and outcome[0] == "sleep":
            await asyncio.sleep(outcome[1])
            return 1, "too slow"
        code, text = outcome
        return code, text

    async def get_patch(self):
        if not self.files:
            return ""
        body = "\n".join(f"+++ {k}\n+{v}" for k, v in self.files.items())
        return f"{self.key}\n{body}"

    async def cleanup(self):
        self.cleaned = True


def _fake_factory(primary, outcomes, keys):
    queue = list(keys)

    async def _new():
        return FakeBackend(primary, outcomes, queue.pop(0))

    async def _apply(patch):
        key = patch.splitlines()[0]
        src = next(b for b in FakeBackend.instances if b.key == key)
        primary.update(src.files)
        primary["_patch"] = patch
        return True

    return _new, _apply


def _engine_for(primary, outcomes, keys, command=("t",), **kw):
    new_backend, apply_patch = _fake_factory(primary, outcomes, keys)
    return SpeculativeSearchEngine(new_backend, list(command), apply_patch, **kw)


def _run(coro):
    return asyncio.run(coro)


class TestDiffDelta:
    def test_counts_plus_minus_skips_headers(self):
        patch = ("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n"
                 "-old\n+new\n+more\n context\n")
        assert diff_delta(patch) == 3

    def test_empty_zero(self):
        assert diff_delta("") == 0


class TestOracle:
    def setup_method(self):
        FakeBackend.instances.clear()

    def test_winner_applied_losers_discarded(self):
        primary: dict = {}
        outcomes = {"c1": (1, "FAILED"), "c2": (0, "ok"), "c3": (1, "FAILED")}
        engine = _engine_for(primary, outcomes, ["c1", "c2", "c3"], ("pytest", "-q"))
        cands = [Candidate("c1", {"a.py": "bad"}), Candidate("c2", {"a.py": "good"}),
                 Candidate("c3", {"a.py": "worse"})]
        res = _run(engine.search(cands))
        assert res.winner is not None and res.winner.candidate_key == "c2"
        assert res.applied is True
        assert primary.get("a.py") == "good"
        assert len(FakeBackend.instances) == 3  # one backend per trial
        assert all(b.cleaned for b in FakeBackend.instances)

    def test_all_fail_no_apply(self):
        primary: dict = {}
        engine = _engine_for(primary, {"c1": (1, "nope")}, ["c1"])
        res = _run(engine.search([Candidate("c1", {"a.py": "bad"})]))
        assert res.winner is None and res.applied is False
        assert primary == {}

    def test_empty_cohort(self):
        engine = _engine_for({}, {}, [])
        res = _run(engine.search([]))
        assert res.winner is None and res.trials == ()

    def test_max_candidates_caps_cohort(self):
        outcomes = {f"c{i}": (0, "ok") for i in range(5)}
        engine = _engine_for({}, outcomes, [f"c{i}" for i in range(5)],
                             max_candidates=3)
        res = _run(engine.search([Candidate(f"c{i}", {"f": str(i)}) for i in range(5)]))
        assert len(res.trials) == 3
        assert len(FakeBackend.instances) == 3

    def test_smallest_diff_wins_ties_broken_by_speed(self):
        outcomes = {"big": (0, "ok"), "small": (0, "ok")}
        engine = _engine_for({}, outcomes, ["big", "small"])
        cands = [Candidate("big", {"a.py": "1\n2\n3\n4\n5\n"}),
                 Candidate("small", {"a.py": "1\n"})]
        res = _run(engine.search(cands))
        assert res.winner is not None and res.winner.candidate_key == "small"

    def test_trial_crash_isolated(self):
        outcomes = {"c1": "crash", "c2": (0, "ok")}
        engine = _engine_for({}, outcomes, ["c1", "c2"])
        res = _run(engine.search(
            [Candidate("c1", {"a": "1"}), Candidate("c2", {"a": "2"})]))
        assert res.winner is not None and res.winner.candidate_key == "c2"

    def test_timeout_marks_failed(self):
        outcomes = {"c1": ("sleep", 60)}
        engine = _engine_for({}, outcomes, ["c1"], trial_timeout_s=0.1)
        res = _run(engine.search([Candidate("c1", {"a": "1"})]))
        assert res.winner is None and "timed out" in res.trials[0].output_tail

    def test_empty_winner_patch_not_applied(self):
        outcomes = {"c1": (0, "ok")}
        engine = _engine_for({}, outcomes, ["c1"])
        res = _run(engine.search([Candidate("c1", {})]))
        assert res.winner is not None and res.applied is False
        assert "empty patch" in res.apply_error


class TestRealWorktreeEndToEnd:
    """REAL git worktrees + subprocess oracle (no fakes)."""

    def test_parallel_trials_prune_and_apply(self, tmp_path):
        from wisp.core.speculative.worktrees import new_manager_backend
        from wisp.multi_agent._worktree_manager import WorktreeManager

        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
        (tmp_path / "val.py").write_text("X = 0\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)

        manager = WorktreeManager(tmp_path)
        oracle = [sys.executable, "-c",
                  "import val, sys; sys.exit(0 if val.X == 2 else 1)"]

        async def _new():
            return await new_manager_backend(manager, "spec-test")

        engine = SpeculativeSearchEngine(
            _new, oracle, manager.apply_patch, trial_timeout_s=120)
        cands = [Candidate("wrong", {"val.py": "X = 1\n"}),
                 Candidate("right", {"val.py": "X = 2\n"})]
        res = asyncio.run(engine.search(cands))
        assert res.winner is not None and res.winner.candidate_key == "right"
        assert res.applied is True
        assert (tmp_path / "val.py").read_text() == "X = 2\n"
        wt_root = tmp_path / ".wisp" / "worktrees"
        remaining = list(wt_root.glob("*")) if wt_root.exists() else []
        assert remaining == []
