from __future__ import annotations

import json
import os

import pytest

from wisp.dashboard import bench as B
from wisp.judge import core


def verdict(task="clamp", v="SOLVED", message="", reasons=None, claim=True, honest=True, seconds=12.0, changed=("a.py",)):
    return core.Verdict(task, v, list(reasons or []), list(changed), 0, 0, claim, honest, [], [], seconds, message)


class Script:
    """A runner that plays a list of verdicts per task, in order, and records the environment each call saw."""

    def __init__(self, plan):
        self.plan = {k: list(v) for k, v in plan.items()}
        self.seen_env = []
        self.calls = []

    def __call__(self, task, cfg, addendum=""):
        self.calls.append(task.id)
        self.seen_env.append({k: os.environ.get(k) for k in ("PYTHONPATH", "WISP_REASONING_CORE", "OPENAI_SECRET_KEY")})
        return self.plan[task.id].pop(0)


def spec(**kw):
    base = dict(model="m", label="t", tasks=["clamp", "dedupe"], infra_retries=2, backoff_s=7.0, wisp_path="/checkout")
    base.update(kw)
    return B.BenchSpec(**base)


def go(sp, out, **kw):
    """run_bench with plenty of disk reported, so the tests do not depend on this machine's free space (the guard has its own tests)."""
    kw.setdefault("free_mb", lambda path: 10**6)
    return B.run_bench(sp, out, **kw)


def rows_of(path):
    return B.read_rows(path)


class TestRetries:
    def test_an_infrastructure_failure_is_retried_and_only_the_last_attempt_is_final(self, tmp_path):
        run = Script({"clamp": [verdict("clamp", "INFRA", message="HTTP 429"), verdict("clamp", "INFRA", message="HTTP 429"), verdict("clamp", "SOLVED")],
                      "dedupe": [verdict("dedupe", "FAILED", claim=False, honest=True)]})
        sleeps = []
        path = go(spec(), tmp_path, runner=run, sleep=sleeps.append)
        rows = rows_of(path)
        clamp = [r for r in rows if r["task"] == "clamp"]
        assert [r["verdict"] for r in clamp] == ["INFRA", "INFRA", "SOLVED"] and [r["final"] for r in clamp] == [False, False, True]
        assert sleeps == [7.0, 14.0]  # linear backoff, injected so the test does not wait
        assert [r["verdict"] for r in rows if r["task"] == "dedupe"] == ["FAILED"]

    def test_when_every_retry_is_infrastructure_the_last_one_is_final_and_still_infra(self, tmp_path):
        run = Script({"clamp": [verdict("clamp", "INFRA", message="429")] * 3, "dedupe": [verdict("dedupe")]})
        rows = rows_of(go(spec(), tmp_path, runner=run, sleep=lambda s: None))
        clamp = [r for r in rows if r["task"] == "clamp"]
        assert len(clamp) == 3 and clamp[-1]["final"] and clamp[-1]["verdict"] == "INFRA"

    def test_a_failure_whose_text_names_a_429_is_infrastructure_even_if_the_judge_scored_it(self, tmp_path):
        run = Script({"clamp": [verdict("clamp", "NO-OP", message="Provider kept rejecting requests (HTTP 429) after 3 attempts", claim=False, honest=False, changed=()),
                                verdict("clamp", "SOLVED")], "dedupe": [verdict("dedupe")]})
        rows = rows_of(go(spec(), tmp_path, runner=run, sleep=lambda s: None))
        first = [r for r in rows if r["task"] == "clamp"][0]
        assert first["verdict"] == "INFRA" and first["original_verdict"] == "NO-OP" and first["final"] is False

    def test_a_real_failure_is_not_retried(self, tmp_path):
        run = Script({"clamp": [verdict("clamp", "FAILED", claim=True, honest=False)], "dedupe": [verdict("dedupe")]})
        go(spec(), tmp_path, runner=run, sleep=lambda s: pytest.fail("slept"))
        assert run.calls == ["clamp", "dedupe"]

    def test_broken_stays_broken_and_is_not_retried(self, tmp_path):
        run = Script({"clamp": [verdict("clamp", "BROKEN", reasons=["visible check passed before any work"])], "dedupe": [verdict("dedupe")]})
        rows = rows_of(go(spec(), tmp_path, runner=run, sleep=lambda s: pytest.fail("slept")))
        assert [r["verdict"] for r in rows if r["task"] == "clamp"] == ["BROKEN"]

    def test_repeats_run_every_task_each_time(self, tmp_path):
        run = Script({"clamp": [verdict("clamp"), verdict("clamp")], "dedupe": [verdict("dedupe"), verdict("dedupe")]})
        rows = rows_of(go(spec(repeats=2), tmp_path, runner=run, sleep=lambda s: None))
        assert sorted((r["repeat"], r["task"]) for r in rows) == [(1, "clamp"), (1, "dedupe"), (2, "clamp"), (2, "dedupe")]


class TestProvenanceAndSecrets:
    def test_the_child_runs_the_checkout_under_test_with_the_chosen_configuration_and_the_environment_is_restored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PYTHONPATH", "/existing")
        monkeypatch.delenv("WISP_REASONING_CORE", raising=False)
        run = Script({"clamp": [verdict("clamp")], "dedupe": [verdict("dedupe")]})
        go(spec(env={"WISP_REASONING_CORE": "off"}), tmp_path, runner=run, sleep=lambda s: None)
        assert run.seen_env[0]["PYTHONPATH"] == "/checkout" + os.pathsep + "/existing" and run.seen_env[0]["WISP_REASONING_CORE"] == "off"
        assert os.environ["PYTHONPATH"] == "/existing" and "WISP_REASONING_CORE" not in os.environ

    def test_secret_looking_environment_is_never_recorded(self, tmp_path):
        s = spec(env={"WISP_REASONING_CORE": "off", "OPENAI_SECRET_KEY": "sk-should-not-be-written", "MY_TOKEN": "t"})
        run = Script({"clamp": [verdict("clamp")], "dedupe": [verdict("dedupe")]})
        path = go(s, tmp_path, runner=run, sleep=lambda s: None)
        text = "".join(p.read_text() for p in tmp_path.iterdir())
        assert "sk-should-not-be-written" not in text and "MY_TOKEN" not in text and "WISP_REASONING_CORE" in text
        assert path.exists()

    def test_a_secret_in_the_agents_message_is_scrubbed(self, tmp_path):
        leaked = "sk-or-v1-" + "a1b2c3d4" * 8
        run = Script({"clamp": [verdict("clamp", "FAILED", message=f"used key {leaked} to call", claim=True, honest=False)], "dedupe": [verdict("dedupe")]})
        go(spec(), tmp_path, runner=run, sleep=lambda s: None)
        assert leaked not in "".join(p.read_text() for p in tmp_path.iterdir())

    def test_every_row_names_the_code_that_ran(self, tmp_path):
        run = Script({"clamp": [verdict("clamp")], "dedupe": [verdict("dedupe")]})
        rows = rows_of(go(spec(), tmp_path, runner=run, sleep=lambda s: None))
        assert all("harness_sha" in r and "harness_dirty" in r and r["model"] == "m" and r["label"] == "t" for r in rows)

    def test_meta_is_written_at_start_and_finished_at_the_end(self, tmp_path):
        run = Script({"clamp": [verdict("clamp")], "dedupe": [verdict("dedupe")]})
        path = go(spec(), tmp_path, runner=run, sleep=lambda s: None, clock=iter([100.0] + [101.0] * 40).__next__)
        meta = json.loads(path.with_name(path.name.replace(".jsonl", ".meta.json")).read_text())
        assert meta["expected_outcomes"] == 2 and meta["started"] == 100.0 and meta["finished"] is not None and meta["spec"]["wisp_path"] == "/checkout"


class TestIsolation:
    def test_every_attempt_gets_a_fresh_private_home_that_is_removed_and_the_real_home_is_untouched(self, tmp_path, monkeypatch):
        real = os.environ.get("HOME")
        homes = []

        def runner(task, cfg, addendum=""):
            h = os.environ["HOME"]
            homes.append(h)
            assert os.path.isdir(h) and os.listdir(h) == []
            open(os.path.join(h, "memory.json"), "w").write("leak")  # what a task's agent might write
            return verdict(task.id)

        go(spec(), tmp_path, runner=runner, sleep=lambda s: None)
        assert len(set(homes)) == 2 and all(not os.path.exists(h) for h in homes) and os.environ.get("HOME") == real
        assert real not in homes

    def test_isolation_can_be_turned_off(self, tmp_path):
        seen = []
        go(spec(isolate_home=False), tmp_path, runner=lambda t, c, a="": seen.append(os.environ.get("HOME")) or verdict(t.id), sleep=lambda s: None)
        assert set(seen) == {os.environ.get("HOME")}


class TestInstantFailuresAndDisk:
    def test_a_run_that_ends_in_a_blink_with_no_change_is_infrastructure_and_retried(self, tmp_path):
        run = Script({"clamp": [verdict("clamp", "NO-OP", claim=False, honest=False, seconds=0.3, changed=()), verdict("clamp", "SOLVED")], "dedupe": [verdict("dedupe")]})
        rows = rows_of(go(spec(), tmp_path, runner=run, sleep=lambda s: None))
        first = [r for r in rows if r["task"] == "clamp"][0]
        assert first["verdict"] == "INFRA" and first["original_verdict"] == "NO-OP" and first["final"] is False

    def test_a_slow_no_op_is_still_the_agents_failure(self, tmp_path):
        run = Script({"clamp": [verdict("clamp", "NO-OP", claim=False, honest=True, seconds=40.0, changed=())], "dedupe": [verdict("dedupe")]})
        rows = rows_of(go(spec(), tmp_path, runner=run, sleep=lambda s: pytest.fail("slept")))
        assert [r["verdict"] for r in rows if r["task"] == "clamp"] == ["NO-OP"]

    @pytest.mark.parametrize("text", ["OSError: [Errno 28] No space left on device", "sqlite3.OperationalError: disk I/O error", "ENOSPC"])
    def test_disk_trouble_in_the_failure_text_is_infrastructure(self, text):
        assert B.looks_like_infrastructure(verdict("clamp", "FAILED", message=text, seconds=30.0))

    def test_old_rows_are_reclassified_when_read_and_the_file_is_untouched(self, tmp_path):
        p = tmp_path / "x.jsonl"
        line = json.dumps({"task": "a", "verdict": "NO-OP", "seconds": 0.3, "final": True, "reasons": []})
        keep = json.dumps({"task": "b", "verdict": "NO-OP", "seconds": 25.0, "final": True, "reasons": []})
        p.write_text(line + "\n" + keep + "\n")
        before = p.read_text()
        a, b = B.read_rows(p)
        assert a["verdict"] == "INFRA" and a["original_verdict"] == "NO-OP" and "instant failure" in a["reasons"][-1] and b["verdict"] == "NO-OP"
        assert p.read_text() == before

    def test_the_runner_refuses_to_start_an_attempt_on_a_nearly_full_disk_and_writes_no_garbage(self, tmp_path):
        run = Script({"clamp": [verdict("clamp")], "dedupe": [verdict("dedupe")]})
        with pytest.raises(B.DiskTooFull, match="only 100 MB free"):
            B.run_bench(spec(), tmp_path, runner=run, sleep=lambda s: None, free_mb=lambda p: 100.0)
        assert run.calls == [] and all(not p.read_text() for p in tmp_path.glob("*.jsonl"))

    def test_the_minimum_is_configurable(self, tmp_path):
        run = Script({"clamp": [verdict("clamp")], "dedupe": [verdict("dedupe")]})
        go(spec(min_free_mb=50), tmp_path, runner=run, sleep=lambda s: None, free_mb=lambda p: 100.0)
        assert run.calls == ["clamp", "dedupe"]


class TestGuards:
    def test_an_unknown_task_is_refused_before_anything_runs(self, tmp_path):
        with pytest.raises(ValueError, match="unknown task"):
            go(spec(tasks=["nope"]), tmp_path, runner=Script({}), sleep=lambda s: None)
        assert not list(tmp_path.glob("*.jsonl"))

    def test_read_rows_skips_torn_and_foreign_lines(self, tmp_path):
        p = tmp_path / "x.jsonl"
        p.write_text('{"task": "a", "verdict": "SOLVED"}\n{torn\n[1,2]\n{"other": 1}\n\n{"task": "b", "verdict": "FAILED"}\n')
        assert [r["task"] for r in B.read_rows(p)] == ["a", "b"] and B.read_rows(tmp_path / "missing") == []

    def test_results_dir_follows_the_override_then_xdg(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WISP_BENCH_DIR", str(tmp_path / "x"))
        assert B.default_results_dir() == tmp_path / "x"
        monkeypatch.delenv("WISP_BENCH_DIR")
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        assert B.default_results_dir() == tmp_path / "state" / "wisp" / "bench"

    def test_harness_identity_of_a_non_repo_is_unknown_not_an_error(self, tmp_path):
        assert B.harness_identity(str(tmp_path)) == {"path": str(tmp_path), "sha": "unknown", "branch": "unknown", "dirty": None}
