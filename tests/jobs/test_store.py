"""The job store: workspace binding (BJ2), honest status (BJ4), ceilings (BJ7), pruning (BJ9). No subprocesses here."""

from __future__ import annotations

import json
import os
import time

import pytest

from tests.jobs.conftest import fake_running
from wisp.jobs.store import ID_RE, LOST, RUNNING, UNKNOWN, JobStore, Limits, _write_atomic, workspace_hash


def finish(store, job_id, status="exited", code=0, finished=None):
    d = store.path(job_id)
    _write_atomic(d / "state.json", {"status": status, "finished": finished or time.time(), "exit_code": code})


class TestIds:
    def test_a_new_id_has_the_exact_shape_and_a_private_directory(self, store):
        job_id, d = store.new_dir()
        assert ID_RE.match(job_id) and oct(d.stat().st_mode & 0o777) == "0o700" and oct(store.root.stat().st_mode & 0o777) == "0o700"

    def test_ids_are_unique(self, store):
        assert len({store.new_dir()[0] for _ in range(50)}) == 50

    @pytest.mark.parametrize("bad", ["..", "../x", "/etc/passwd", "bj-", "bj-zzzzzzzzzzzzzzzz", "bj-0123456789abcdef0", "BJ-0123456789ABCDEF", "", None, 5, "bj-0123456789abcdef/../..", "bj-0123456789abcdef\n"])
    def test_bj2_nothing_but_an_id_ever_becomes_a_path(self, store, bad):
        assert store.path(bad) is None and store.view(bad) is None  # type: ignore[arg-type]

    def test_bj2_another_workspaces_id_resolves_to_nothing(self, root, ws, tmp_path):
        other_ws = tmp_path / "other"
        other_ws.mkdir()
        mine, theirs = JobStore(root, ws), JobStore(root, str(other_ws))
        job_id = fake_running(mine)
        assert mine.view(job_id) is not None and theirs.view(job_id) is None and theirs.path(job_id) is None

    def test_bj2_an_existing_id_with_a_path_attached_is_still_nothing(self, store, root, ws, tmp_path):
        job_id = fake_running(store)
        wh = workspace_hash(ws)
        for sneaky in (f"{job_id}/../{job_id}", f"../{wh}/{job_id}", f"{job_id}/", f"{job_id}\n", f"{job_id}x", f" {job_id}"):
            assert store.path(sneaky) is None and store.view(sneaky) is None, sneaky

    def test_the_same_workspace_through_a_symlink_is_the_same_store(self, root, ws, tmp_path):
        link = tmp_path / "link"
        link.symlink_to(ws)
        assert workspace_hash(str(link)) == workspace_hash(ws) and JobStore(root, str(link)).dir == JobStore(root, ws).dir


class TestHonestStatus:
    def test_a_live_supervisor_means_running(self, store):
        assert store.view(fake_running(store)).status == RUNNING

    def test_bj4_a_dead_supervisor_with_no_result_is_lost_never_success(self, store):
        job_id = fake_running(store)
        (store.path(job_id) / "supervisor.json").write_text(json.dumps({"pid": 2**30, "start": "Thu Jan  1 00:00:00 1970"}))
        v = store.view(job_id)
        assert v.status == LOST and v.exit_code is None and not (v.status == "exited")

    def test_bj4_a_reused_pid_is_not_the_supervisor(self, store):
        job_id = fake_running(store)
        (store.path(job_id) / "supervisor.json").write_text(json.dumps({"pid": os.getpid(), "start": "Mon Jan  1 00:00:00 1990"}))
        assert store.view(job_id).status == LOST

    @pytest.mark.parametrize("content", ["", "{", "[]", "null", '{"status": "exited"', '{"status": "success"}', '{"status": 5}', "\x00\x01"])
    def test_bj4_a_truncated_or_alien_state_is_never_a_result(self, store, content):
        job_id = fake_running(store)
        (store.path(job_id) / "state.json").write_text(content)
        assert store.view(job_id).status in (RUNNING, LOST, UNKNOWN) and store.view(job_id).exit_code is None

    @pytest.mark.parametrize("code", ["3", None, 3.5, True, [0], "0"])
    def test_an_exit_code_that_is_not_an_integer_is_not_reported(self, store, code):
        job_id = fake_running(store)
        _write_atomic(store.path(job_id) / "state.json", {"status": "exited", "finished": time.time(), "exit_code": code})
        assert store.view(job_id).exit_code in (None, True) and (code is True or store.view(job_id).exit_code is None)

    def test_missing_metadata_is_unknown(self, store):
        job_id, _ = store.new_dir()
        assert store.view(job_id).status == UNKNOWN

    def test_a_supervisor_that_never_announced_is_starting_then_lost(self, store):
        job_id, d = store.new_dir()
        (d / "meta.json").write_text(json.dumps({"command": "x", "workspace": store.workspace, "created": time.time()}))
        assert store.view(job_id).status == RUNNING
        (d / "meta.json").write_text(json.dumps({"command": "x", "workspace": store.workspace, "created": time.time() - 3600}))
        assert store.view(job_id).status == LOST

    def test_a_recorded_result_is_reported_as_recorded(self, store):
        job_id = fake_running(store)
        finish(store, job_id, "exited", 3)
        (store.path(job_id) / "out.log").write_text("[exit code: 3]\nboom")
        v = store.view(job_id)
        assert (v.status, v.exit_code, v.output) == ("exited", 3, "[exit code: 3]\nboom") and v.is_finished

    def test_output_is_tailed_and_flagged(self, store):
        job_id = fake_running(store)
        finish(store, job_id)
        (store.path(job_id) / "out.log").write_text("a" * 50_000 + "END")
        v = store.view(job_id)
        assert v.truncated and v.output.endswith("END") and len(v.output) == 20_000

    def test_notification_is_once(self, store):
        job_id = fake_running(store)
        finish(store, job_id)
        assert [v.id for v in store.unnotified_finished()] == [job_id]
        store.mark_notified(job_id)
        assert store.unnotified_finished() == []


class TestCeilingsAndPruning:
    def test_bj7_running_counts_per_workspace_and_whole_store(self, root, ws, tmp_path):
        other = tmp_path / "o"
        other.mkdir()
        a, b = JobStore(root, ws), JobStore(root, str(other))
        fake_running(a), fake_running(a), fake_running(b)
        assert len(a.running_ids()) == 2 and len(b.running_ids()) == 1 and len(a.running_ids(whole_store=True)) == 3

    def test_finished_jobs_do_not_count_as_running(self, store):
        finish(store, fake_running(store))
        assert store.running_ids() == []

    def test_bj9_prune_removes_old_finished_jobs_but_never_a_running_one(self, root, ws):
        s = JobStore(root, ws, Limits(max_age_s=100))
        old, fresh, running = fake_running(s), fake_running(s), fake_running(s)
        finish(s, old, finished=time.time() - 1000)
        finish(s, fresh)
        assert s.prune() == [old] and s.path(old) is None and s.path(fresh) and s.path(running)

    def test_bj9_prune_keeps_only_the_newest_finished(self, root, ws):
        s = JobStore(root, ws, Limits(max_finished=2))
        ids = []
        for _ in range(4):
            ids.append(fake_running(s))
            finish(s, ids[-1])
            time.sleep(0.01)
        assert len(s.prune()) == 2 and [i for i in ids if s.path(i)] == ids[2:]

    def test_prune_on_an_empty_store_is_a_noop(self, store):
        assert store.prune() == [] and store.jobs() == []
