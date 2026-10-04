"""Two writers appending to one audit log must still form one chain.

`AuditTrail.record` used to trust an in-memory `_last_hash`. The CLI, the server and the test suite all append
to the same file, so the second writer chained onto a stale head, forked the chain, and `wisp audit verify`
reported TAMPERED on a log in which no entry had been edited (observed on the live log: 8 fork points).
"""

from __future__ import annotations

import multiprocessing as mp
import sys
from pathlib import Path

import pytest

from wisp.infra.audit import AuditTrail

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="relies on POSIX file locking")


def test_two_instances_interleaving_appends_keep_one_chain(tmp_path):
    p = tmp_path / "audit.jsonl"
    a, b = AuditTrail(p), AuditTrail(p)
    a.record("a1")
    b.record("b1")
    a.record("a2")
    b.record("b2")
    assert AuditTrail(p).verify() is None


def _writer(path: str, tag: str, n: int) -> None:
    trail = AuditTrail(Path(path))
    for i in range(n):
        trail.record(f"{tag}-{i}")


def test_separate_processes_appending_concurrently_keep_one_chain(tmp_path):
    p = tmp_path / "audit.jsonl"
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_writer, args=(str(p), f"w{k}", 40)) for k in range(4)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(60)
        assert proc.exitcode == 0
    assert len(p.read_text().splitlines()) == 160
    assert AuditTrail(p).verify() is None


def test_the_hash_returned_is_the_one_that_was_chained(tmp_path):
    p = tmp_path / "audit.jsonl"
    a = AuditTrail(p)
    h1 = a.record("one")
    AuditTrail(p).record("interloper")
    h3 = a.record("three")
    lines = [__import__("json").loads(x) for x in p.read_text().splitlines()]
    assert lines[0]["_hash"] == h1
    assert lines[2]["_hash"] == h3
    assert lines[2]["_prev_hash"] == lines[1]["_hash"]


def test_a_corrupt_trailing_line_is_reported_not_chained_onto_silently(tmp_path):
    p = tmp_path / "audit.jsonl"
    a = AuditTrail(p)
    a.record("one")
    p.write_text(p.read_text() + "garbage\n")
    AuditTrail(p).record("two")
    assert AuditTrail(p).verify() is not None  # the corruption stays visible
