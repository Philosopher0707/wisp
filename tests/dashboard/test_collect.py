from __future__ import annotations

import json
import os
import subprocess


from tests.dashboard.conftest import SECRET_KEY, SECRET_PROMPT, make_workspace
from wisp.dashboard import collect as C


class TestDiscovery:
    def test_finds_workspaces_with_wisp_data_and_nothing_else(self, home):
        a = make_workspace(home / "active", "a")
        b = make_workspace(home / "active" / "nested", "b", with_db=False)
        (home / "active" / "empty" / ".wisp").mkdir(parents=True)  # a .wisp dir with no data
        found = C.find_workspaces([home / "active"])
        assert set(found) == {a.resolve(), b.resolve()}

    def test_the_walk_is_bounded_in_depth_and_skips_dependency_dirs(self, home):
        deep = make_workspace(home / "active" / "l1" / "l2" / "l3" / "l4" / "l5", "deep")
        skipped = make_workspace(home / "active" / "node_modules", "pkg")
        assert C.find_workspaces([home / "active"], max_depth=3) == []
        assert deep.resolve() not in C.find_workspaces([home / "active"], max_depth=3) and skipped.resolve() not in C.find_workspaces([home / "active"])

    def test_scratch_and_archive_checkouts_are_not_real_use(self, home):
        make_workspace(home / "active" / "_scratch", "wisp-x")
        make_workspace(home / "active" / "_archive", "old")
        real = make_workspace(home / "active", "real")
        assert C.find_workspaces([home / "active"]) == [real.resolve()]

    def test_the_default_roots_leave_out_the_dev_checkouts(self, home, monkeypatch, tmp_path):
        make_workspace(home / "dev", "wisp")
        mine = make_workspace(home / "active", "mine")
        elsewhere = tmp_path / "elsewhere"  # the working directory is a default root too; keep it away from the fake home
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        assert C.find_workspaces() == [mine.resolve()]

    def test_missing_or_unreadable_roots_are_ignored(self, home):
        assert C.find_workspaces([home / "nope"]) == []


class TestUsage:
    def test_models_tools_and_outcomes(self, home):
        ws = make_workspace(home, "p")
        u = C.usage_data([ws])
        assert u["sessions"] == 2 and u["models"][0]["model"] == "vendor/model-a" and u["models"][0]["messages"] == 10
        assert u["models"][0]["first"] == "2026-10-01T10:00:00" and u["models"][0]["last"] == "2026-10-03T10:30:00"
        rb = next(t for t in u["tools"] if t["tool"] == "run_bash")
        assert (rb["calls"], rb["not_ok"], rb["error_rate"]) == (2, 1, 0.5)
        assert u["per_day"] == {"2026-10-01": 2, "2026-10-02": 2} and u["tool_calls"] == 4
        assert u["turn_completion"]["user_messages"] == 2 and u["turn_completion"]["done"] == 1 and u["turn_completion"]["ratio"] == 0.5
        assert u["background_runs"] == {"succeeded": 1, "failed": 1} and u["graph_runs"] == {"running": 1}

    def test_test_doubles_are_tagged_not_hidden(self, home):
        ws = make_workspace(home, "p", model="mock-t")
        assert C.usage_data([ws])["models"][0]["test_double"] is True
        assert C.usage_data([make_workspace(home, "q", model="vendor/real-model")])["models"][0]["test_double"] is False

    def test_a_workspace_without_a_database_still_reports_its_audit(self, home):
        ws = make_workspace(home, "p", with_db=False)
        u = C.usage_data([ws])
        assert u["sessions"] == 0 and u["tool_calls"] == 4

    def test_a_corrupt_database_is_reported_not_raised(self, home):
        ws = make_workspace(home, "p", with_db=False)
        (ws / ".wisp" / "wisp.db").write_bytes(b"this is not sqlite" * 50)
        u = C.usage_data([ws])
        assert u["sessions"] == 0 and u["tool_calls"] == 4

    def test_the_database_is_opened_read_only(self, home):
        ws = make_workspace(home, "p")
        before = (ws / ".wisp" / "wisp.db").read_bytes()
        C.usage_data([ws])
        assert (ws / ".wisp" / "wisp.db").read_bytes() == before

    def test_runtime_log_counts_events_and_never_returns_lines(self, home):
        ws = make_workspace(home, "p")
        r = C.runtime_log_data([ws])
        assert r["total"] == {"429 retries": 2, "path jail refusals": 1}
        assert SECRET_PROMPT not in json.dumps(r)


class TestBench:
    def seed(self, d, label="default", solved=3, failed=1, infra=1, sha="abc1234"):
        rid = f"20261008T000000Z-{label}"
        rows = []
        for i in range(solved):
            rows.append({"run_id": rid, "task": f"t{i}", "verdict": "SOLVED", "final": True, "model": "m", "label": label, "seconds": 5, "claim": True, "claim_honest": True,
                         "ts": "2026-10-08T00:00:00+00:00", "harness_sha": sha, "attempt": 1})
        for i in range(failed):
            rows.append({"run_id": rid, "task": f"f{i}", "verdict": "FAILED", "final": True, "model": "m", "label": label, "seconds": 9, "claim": True, "claim_honest": False,
                         "ts": "2026-10-08T00:00:00+00:00", "harness_sha": sha, "attempt": 1})
        for i in range(infra):
            rows.append({"run_id": rid, "task": f"i{i}", "verdict": "INFRA", "final": False, "model": "m", "label": label, "seconds": 2, "ts": "2026-10-08T00:00:00+00:00",
                         "harness_sha": sha, "attempt": 1})
        (d / f"{rid}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        (d / f"{rid}.meta.json").write_text(json.dumps({"run_id": rid, "spec": {"model": "m", "label": label, "provider": "p", "env": {"X": "1"}},
                                                         "harness": {"sha": sha, "dirty": False}, "expected_outcomes": 6, "started": 1.0, "finished": 2.0}))

    def test_groups_intervals_and_the_trust_sentence(self, tmp_path):
        self.seed(tmp_path)
        b = C.bench_data(tmp_path)
        g = b["groups"][0]
        assert g["model"] == "m" and g["label"] == "default" and g["summary"]["solved"] == 3 and g["summary"]["scored"] == 4
        assert g["summary"]["pass_rate"] == 0.75 and 0 < g["summary"]["ci_low"] < 0.75 < g["summary"]["ci_high"] <= 1 and "only 4 scored" in g["trust"]
        assert g["harness_shas"] == ["abc1234"] and b["runs"][0]["done"] == 4 and b["runs"][0]["in_progress"] is False
        assert "13 small" in b["scope_note"]

    def test_two_configurations_of_one_model_are_compared_with_an_interval(self, tmp_path):
        self.seed(tmp_path, "default", solved=9, failed=1, infra=0)
        self.seed(tmp_path, "core-off", solved=4, failed=6, infra=0)
        c = C.bench_data(tmp_path)["comparisons"]
        assert len(c) == 1 and c[0]["model"] == "m" and c[0]["diff"] != 0 and c[0]["low"] < c[0]["diff"] < c[0]["high"]

    def test_a_run_without_a_finish_time_is_in_progress(self, tmp_path):
        self.seed(tmp_path)
        meta = next(tmp_path.glob("*.meta.json"))
        d = json.loads(meta.read_text())
        d["finished"] = None
        meta.write_text(json.dumps(d))
        assert C.bench_data(tmp_path)["runs"][0]["in_progress"] is True

    def test_empty_missing_or_torn_results_are_not_an_error(self, tmp_path):
        assert C.bench_data(tmp_path / "nope")["groups"] == []
        (tmp_path / "x.meta.json").write_text("{not json")
        (tmp_path / "y.meta.json").write_text(json.dumps({"run_id": "y", "spec": {"model": "m", "label": "l"}}))
        (tmp_path / "y.jsonl").write_text("{torn\n")
        b = C.bench_data(tmp_path)
        assert b["groups"] == [] and len(b["runs"]) == 1

    def test_per_task_matrix_and_daily_trend(self, tmp_path):
        self.seed(tmp_path)
        b = C.bench_data(tmp_path)
        assert b["per_task"]["t0"]["m | default"]["solved"] == 1 and b["tasks"] == sorted(b["per_task"])
        assert b["daily"]["2026-10-08"]["scored"] == 4


class TestHarness:
    def test_git_facts_and_merged_prs(self, tmp_path):
        run = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True, env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})  # noqa: E731
        run("init", "-q", "-b", "main")
        (tmp_path / "a").write_text("1")
        run("add", "a")
        run("commit", "-qm", "first")
        run("checkout", "-qb", "feat/x")
        (tmp_path / "a").write_text("2")
        run("commit", "-qam", "work")
        run("checkout", "-q", "main")
        run("merge", "--no-ff", "-m", "Merge pull request #42 from someone/feat/x", "feat/x")
        h = C.harness_data(tmp_path)
        assert h["branch"] == "main" and len(h["sha"]) >= 7 and h["dirty"] is False
        assert h["merged"] and h["merged"][0]["pr"] == 42 and h["merged"][0]["branch"] == "feat/x"

    def test_disk_is_reported(self, tmp_path):
        d = C.disk_data(tmp_path)
        assert d["free_gb"] >= 0 and d["total_gb"] > 0 and 0 <= d["used_pct"] <= 100
        assert "disk" in C.harness_data(tmp_path)

    def test_not_a_repo_is_unknown(self, tmp_path):
        h = C.harness_data(tmp_path)
        assert h["sha"] == "unknown" and h["merged"] == []

    def test_flags_report_the_effective_reasoning_modes(self, monkeypatch, tmp_path):
        for k in ("WISP_REASONING_CORE", "WISP_REASONING_CORE_RULES"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr("wisp.config.load_config", lambda: {})
        f = C.harness_data(tmp_path)["flags"]
        assert f["reasoning core: R1"] == "enforce" and f["reasoning core: R4"] == "enforce" and f["reasoning core: R2"] == "observe"
        monkeypatch.setenv("WISP_REASONING_CORE", "off")
        assert C.harness_data(tmp_path)["flags"]["reasoning core: R1"] == "off"


class TestFindings:
    def test_rows_become_items_with_a_status(self, tmp_path):
        d = tmp_path / "docs" / "harness"
        d.mkdir(parents=True)
        (d / "field-observations-2026-10-07.md").write_text(
            "| ID | Session | What | How | Status |\n|---|---|---|---|---|\n"
            "| O-1 | a | a loop **happened** | observed | open; R5 designed |\n"
            "| O-3 | b | Ctrl-C killed the REPL | verified | **fixed in PR `fix/graph-ctrl-c`** (pending merge) |\n"
            "| I-15 | c | seed policy | verified, 100 seeds | **refuted as a policy** |\n"
            "| O-9 | d | nudge was false | verified | fixed in PR |\n")
        f = C.findings_data(tmp_path)
        by = {i["id"]: i for i in f["items"]}
        assert by["O-1"]["status"] == "open" and by["O-3"]["status"] == "fixed" and by["I-15"]["status"] == "refuted" and by["O-9"]["status"] == "fixed"
        assert f["counts"] == {"open": 1, "fixed": 2, "refuted": 1} and f["total"] == 4 and "*" not in by["O-1"]["summary"]

    def test_no_docs_is_empty(self, tmp_path):
        assert C.findings_data(tmp_path)["items"] == []


class TestLearning:
    def test_the_stores_are_counted_never_read_out(self, home):
        cfg = home / ".config" / "wisp"
        (cfg / "agent_memory").mkdir(parents=True)
        (cfg / "memory.json").write_text(json.dumps({"global_facts": [{"content": SECRET_PROMPT}], "workspace_facts": {"/p": [{"content": "a"}, {"content": "b"}]}}))
        (cfg / "memory.json.bak-1").write_text(json.dumps({"global_facts": [], "workspace_facts": {"/p": [{"content": "x"}] * 5}}))
        (cfg / "agent_memory" / "sessions.jsonl").write_text("{}\n{}\n{}\n")
        ws = make_workspace(home, "p")
        skill = ws / ".wisp" / "skills" / "auto" / "s1"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: s1\n---\n" + SECRET_PROMPT)
        l = C.learning_data([ws])
        assert l["facts"] == 3 and l["workspace_fact_buckets"] == 1 and l["session_summaries"] == 3
        assert l["backups"] == [{"name": "memory.json.bak-1", "facts": 5, "bytes": (cfg / "memory.json.bak-1").stat().st_size}]
        assert l["captured_skills"]["total"] == 1 and l["remember"] == {"calls": 1, "ok": 0, "error": 1}

    def test_nothing_exists_yet(self, home):
        l = C.learning_data([])
        assert l["facts"] is None and l["backups"] == [] and l["captured_skills"]["total"] == 0


def test_no_collector_ever_returns_prompt_text_or_a_key(home, tmp_path):
    """The one property that matters most for a dashboard over private session data."""
    ws = make_workspace(home, "p")
    (home / ".config" / "wisp").mkdir(parents=True)
    (home / ".config" / "wisp" / "memory.json").write_text(json.dumps({"global_facts": [{"content": SECRET_PROMPT + SECRET_KEY}], "workspace_facts": {}}))
    TestBench().seed(tmp_path)
    everything = json.dumps([C.usage_data([ws]), C.runtime_log_data([ws]), C.learning_data([ws]), C.bench_data(tmp_path), C.harness_data(tmp_path), C.findings_data(tmp_path)])
    assert SECRET_PROMPT not in everything and SECRET_KEY not in everything and "sk-or-v1" not in everything
