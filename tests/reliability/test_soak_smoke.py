"""G0 soak smoke: harness integrity in ~60s (not a growth gate).

Asserts the driver runs both workloads end-to-end, records samples, and
emits a well-formed summary. Growth thresholds are evaluated in the real
10/30/60m runs and the G0 report — NOT asserted here (the fd-retention
finding G0-NEW-1 would fail a threshold assertion by design).
"""
from __future__ import annotations

from tests.reliability.soak import run_soak


def test_soak_smoke_workload_a(tmp_path):
    summary = run_soak(45, "A", 10, str(tmp_path / "a"))
    assert summary["ops"] > 10, summary
    assert summary["op_errors"] == 0, summary
    assert set(summary["checks"]) == {"threads", "fds", "sockets",
                                        "children", "tasks", "rss",
                                        "db_per_op"}
    assert (tmp_path / "a" / "samples.jsonl").exists()
    assert summary["result"] in ("PASS", "FAIL")  # recorded, not gated here


def test_soak_smoke_workload_b(tmp_path):
    summary = run_soak(60, "B", 10, str(tmp_path / "b"))
    assert summary["ops"] > 0, summary
    assert summary["retries"] > 0, summary
    assert summary["timeouts"] > 0, summary
    assert summary["cancels"] > 0, summary
    assert (tmp_path / "b" / "samples.jsonl").exists()
