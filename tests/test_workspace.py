"""Phase 12: snapshots, ChangeSets, isolation, conflicts, merge, apply."""

from __future__ import annotations

import os

import pytest

from wisp.workspace import (
    CREATE,
    DELETE,
    MODIFY,
    RENAME,
    Change,
    apply_changeset,
    classify_pair,
    diff_copy_against_snapshot,
    isolated_copy,
    make_changeset,
    merge_changesets,
    recover,
    rollback_changeset,
    snapshot_workspace,
)


def _ws(tmp_path, files=None):
    files = files or {"a.py": "def foo():\n    return 1\n",
                      "b.py": "def bar():\n    return 2\n"}
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return str(tmp_path)


def _mod(snap, worker, path, content):
    return make_changeset(snap.id, {"run_id": "r", "node_id": "n", "worker": worker},
                          [Change(path=path, op=MODIFY, base_hash=snap.files[path],
                                  content=content)])


class TestSnapshot:
    def test_deterministic_id(self, tmp_path):
        ws = _ws(tmp_path)
        assert snapshot_workspace(ws).id == snapshot_workspace(ws).id

    def test_change_updates_id(self, tmp_path):
        ws = _ws(tmp_path)
        s1 = snapshot_workspace(ws)
        (tmp_path / "a.py").write_text("changed\n")
        assert snapshot_workspace(ws).id != s1.id

    def test_explicit_file_list(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws, files=["a.py"])
        assert sorted(snap.files) == ["a.py"]

    def test_symlinks_untracked(self, tmp_path):
        ws = _ws(tmp_path)
        try:
            os.symlink(str(tmp_path / "a.py"), str(tmp_path / "link.py"))
        except OSError:
            pytest.skip("no symlinks")
        assert "link.py" not in snapshot_workspace(ws).files

    def test_traversal_rejected(self, tmp_path):
        ws = _ws(tmp_path)
        with pytest.raises(ValueError):
            snapshot_workspace(ws, files=["../evil.py"])


class TestChangeSet:
    def test_content_addressed(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        c1 = _mod(snap, "A", "a.py", "x\n")
        c2 = _mod(snap, "A", "a.py", "x\n")
        assert c1.id == c2.id
        c3 = _mod(snap, "B", "a.py", "x\n")
        assert c1.id != c3.id  # producer in identity

    def test_immutable_frozen(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        cs = _mod(snap, "A", "a.py", "x\n")
        with pytest.raises(Exception):
            cs.id = "forged"  # type: ignore[misc]

    def test_bounds(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        with pytest.raises(ValueError):
            make_changeset(snap.id, {}, [Change(path=f"f{i}.py", op=CREATE, content="x")
                                         for i in range(101)])
        with pytest.raises(ValueError):
            Change(path="a.py", op="BOGUS")
        with pytest.raises(ValueError):
            Change(path="../x.py", op=CREATE, content="x")

    def test_producer_restricted_keys(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        cs = make_changeset(snap.id, {"worker": "A", "approved": "true",
                                      "run_id": "r"}, [])
        assert "approved" not in cs.producer
        assert cs.producer["worker"] == "A"


class TestIsolation:
    def test_copy_isolated(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        copy = isolated_copy(snap, "A")
        with open(os.path.join(copy, "a.py"), "w") as fh:
            fh.write("worker edit\n")
        assert open(os.path.join(ws, "a.py")).read() != "worker edit\n"
        assert snapshot_workspace(ws).id == snap.id  # canonical untouched

    def test_diff_collects_changes(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        copy = isolated_copy(snap, "A")
        with open(os.path.join(copy, "a.py"), "w") as fh:
            fh.write("changed\n")
        os.unlink(os.path.join(copy, "b.py"))
        with open(os.path.join(copy, "c.py"), "w") as fh:
            fh.write("new\n")
        changes = diff_copy_against_snapshot(copy, snap, "A")
        by_path = {c.path: c.op for c in changes}
        assert by_path == {"a.py": MODIFY, "b.py": DELETE, "c.py": CREATE}


class TestConflicts:
    def test_disjoint_no_conflict(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        assert classify_pair(snap, _mod(snap, "A", "a.py", "x\n"),
                             _mod(snap, "B", "b.py", "y\n")) == []

    def test_same_result_dedup(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        out = classify_pair(snap, _mod(snap, "A", "a.py", "x\n"),
                            _mod(snap, "B", "a.py", "x\n"))
        assert [c.kind for c in out] == ["SAME_RESULT"]

    def test_text_conflict(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        out = classify_pair(snap, _mod(snap, "A", "a.py", "def foo():\n    return 9\n"),
                            _mod(snap, "B", "a.py", "def foo():\n    return 8\n"))
        assert [c.kind for c in out] == ["TEXT_CONFLICT"]

    def test_symbol_separated_merges(self, tmp_path):
        ws = _ws(tmp_path, {"a.py": "def foo():\n    return 1\ndef bar():\n    return 2\n"})
        snap = snapshot_workspace(ws)
        out = classify_pair(
            snap,
            _mod(snap, "A", "a.py", "def foo():\n    return 100\ndef bar():\n    return 2\n"),
            _mod(snap, "B", "a.py", "def foo():\n    return 1\ndef bar():\n    return 200\n"))
        assert out == []  # distinct symbols: NO_CONFLICT

    def test_same_symbol_conflicts(self, tmp_path):
        ws = _ws(tmp_path, {"a.py": "def foo():\n    return 1\ndef bar():\n    return 2\n"})
        snap = snapshot_workspace(ws)
        out = classify_pair(
            snap,
            _mod(snap, "A", "a.py", "def foo():\n    return 100\ndef bar():\n    return 2\n"),
            _mod(snap, "B", "a.py", "def foo():\n    return 200\ndef bar():\n    return 2\n"))
        assert [c.kind for c in out] == ["TEXT_CONFLICT"]

    def test_delete_modify(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        ca = make_changeset(snap.id, {"worker": "A"},
                            [Change(path="a.py", op=DELETE, base_hash=snap.files["a.py"])])
        out = classify_pair(snap, ca, _mod(snap, "B", "a.py", "x\n"))
        assert [c.kind for c in out] == ["DELETE_MODIFY"]

    def test_create_collision(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        mk = lambda w, c: make_changeset(  # noqa: E731
            snap.id, {"worker": w}, [Change(path="n.py", op=CREATE, content=c)])
        out = classify_pair(snap, mk("A", "1\n"), mk("B", "2\n"))
        assert [c.kind for c in out] == ["CREATE_COLLISION"]
        out2 = classify_pair(snap, mk("A", "1\n"), mk("B", "1\n"))
        assert [c.kind for c in out2] == ["SAME_RESULT"]

    def test_stale_base(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        old = make_changeset("snap-deadbeef", {"worker": "A"}, [])
        out = classify_pair(snap, old, _mod(snap, "B", "a.py", "x\n"))
        assert [c.kind for c in out] == ["STALE_BASE"]

    def test_rename_conflict(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        mk = lambda w, dst: make_changeset(  # noqa: E731
            snap.id, {"worker": w},
            [Change(path=dst, op=RENAME, rename_from="a.py")])
        out = classify_pair(snap, mk("A", "x.py"), mk("B", "y.py"))
        assert [c.kind for c in out] == ["RENAME_CONFLICT"]


class TestMerge:
    def test_merge_disjoint(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        m = merge_changesets(snap, [_mod(snap, "A", "a.py", "x\n"),
                                    _mod(snap, "B", "b.py", "y\n")])
        assert m.outcome == "MERGED" and m.merged is not None
        assert sorted(c.path for c in m.merged.changes) == ["a.py", "b.py"]

    def test_merge_order_independent(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        a = _mod(snap, "A", "a.py", "x\n")
        b = _mod(snap, "B", "b.py", "y\n")
        m1 = merge_changesets(snap, [a, b])
        m2 = merge_changesets(snap, [b, a])
        assert m1.merged.id == m2.merged.id

    def test_merge_conflict_explicit(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        m = merge_changesets(snap, [_mod(snap, "A", "a.py", "x\n"),
                                    _mod(snap, "B", "a.py", "y\n")])
        assert m.outcome == "CONFLICT" and m.merged is None
        assert m.conflicts[0].kind == "TEXT_CONFLICT"

    def test_no_last_writer_wins(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        m = merge_changesets(snap, [_mod(snap, "A", "a.py", "x\n"),
                                    _mod(snap, "B", "a.py", "y\n")])
        assert m.outcome != "MERGED"

    def test_stale_rejected(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        old = make_changeset("snap-old", {"worker": "A"}, [])
        assert merge_changesets(snap, [old]).outcome == "STALE"

    def test_invalid_rejected(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        assert merge_changesets(snap, ["junk"]).outcome == "INVALID"  # type: ignore[list-item]


class TestApply:
    def test_apply_clean(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        cs = _mod(snap, "A", "a.py", "applied\n")
        r = apply_changeset(ws, snap, cs)
        assert r.ok and open(os.path.join(ws, "a.py")).read() == "applied\n"

    def test_apply_create_delete(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        cs = make_changeset(snap.id, {"worker": "A"}, [
            Change(path="n.py", op=CREATE, content="new\n"),
            Change(path="b.py", op=DELETE, base_hash=snap.files["b.py"])])
        assert apply_changeset(ws, snap, cs).ok
        assert open(os.path.join(ws, "n.py")).read() == "new\n"
        assert not os.path.exists(os.path.join(ws, "b.py"))

    def test_external_modification_refused(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        (tmp_path / "b.py").write_text("user edit\n")  # outside ChangeSet scope too
        cs = _mod(snap, "A", "a.py", "applied\n")
        r = apply_changeset(ws, snap, cs)
        assert not r.ok and r.reason == "EXTERNAL_MODIFICATION"
        assert open(os.path.join(ws, "a.py")).read() != "applied\n"

    def test_stale_snapshot_refused(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        cs = make_changeset("snap-old", {"worker": "A"}, [])
        assert apply_changeset(ws, snap, cs).reason == "STALE"

    def test_traversal_in_changes_refused(self, tmp_path):
        _ws(tmp_path)
        # Change construction itself rejects; forged dicts fail at merge fn.
        with pytest.raises(ValueError):
            Change(path="../../evil.py", op=CREATE, content="x")

    def test_rollback(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        cs = _mod(snap, "A", "a.py", "applied\n")
        assert apply_changeset(ws, snap, cs).ok
        r = rollback_changeset(ws, snap, cs)
        assert r.ok
        assert open(os.path.join(ws, "a.py")).read() == "def foo():\n    return 1\n"

    def test_rollback_refuses_external_drift(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        cs = _mod(snap, "A", "a.py", "applied\n")
        assert apply_changeset(ws, snap, cs).ok
        (tmp_path / "a.py").write_text("user post-apply edit\n")
        r = rollback_changeset(ws, snap, cs)
        assert not r.ok and r.reason == "EXTERNAL_MODIFICATION"
        assert open(os.path.join(ws, "a.py")).read() == "user post-apply edit\n"

    def test_rollback_unknown_changeset(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        cs = _mod(snap, "A", "a.py", "applied\n")
        r = rollback_changeset(ws, snap, cs)  # never applied: no journal
        assert not r.ok

    def test_failure_injection_stage(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        cs = _mod(snap, "A", "a.py", "applied\n")
        with pytest.raises(RuntimeError):
            apply_changeset(ws, snap, cs, fail_at="stage")
        # Crash before commit: canonical untouched...
        assert open(os.path.join(ws, "a.py")).read() != "applied\n"
        # ...restart recovery converges forward to complete-new state.
        assert recover(ws) == "completed:1"
        assert open(os.path.join(ws, "a.py")).read() == "applied\n"
        assert recover(ws) == "clean"

    def test_failure_injection_commit(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        cs = make_changeset(snap.id, {"worker": "A"}, [
            Change(path="a.py", op=MODIFY, base_hash=snap.files["a.py"], content="v2\n"),
            Change(path="b.py", op=MODIFY, base_hash=snap.files["b.py"], content="v2\n")])
        with pytest.raises(RuntimeError):
            apply_changeset(ws, snap, cs, fail_at="commit")
        # Crash mid-commit: partial state on disk, journal pending...
        assert open(os.path.join(ws, "a.py")).read() == "v2\n"
        assert open(os.path.join(ws, "b.py")).read() != "v2\n"
        # ...restart recovery converges forward to complete-new state.
        assert recover(ws) == "completed:1"
        assert open(os.path.join(ws, "a.py")).read() == "v2\n"
        assert open(os.path.join(ws, "b.py")).read() == "v2\n"
        assert recover(ws) == "clean"

    def test_recover_clean(self, tmp_path):
        assert recover(_ws(tmp_path)) == "clean"


class TestProperties:
    def _snap(self, tmp_path):
        return snapshot_workspace(_ws(tmp_path, {"a.py": "a\n", "b.py": "b\n",
                                                 "c.py": "c\n"}))

    def test_snapshot_idempotent_inspection(self, tmp_path):
        ws = _ws(tmp_path)
        s1 = snapshot_workspace(ws)
        s2 = snapshot_workspace(ws)
        assert s1.id == s2.id and s1.files == s2.files

    def test_no_lost_changes(self, tmp_path):
        snap = self._snap(tmp_path)
        a = make_changeset(snap.id, {"worker": "A"},
                           [Change(path="a.py", op=MODIFY, base_hash=snap.files["a.py"],
                                   content="a2\n")])
        b = make_changeset(snap.id, {"worker": "B"},
                           [Change(path="b.py", op=MODIFY, base_hash=snap.files["b.py"],
                                   content="b2\n")])
        m = merge_changesets(snap, [a, b])
        assert m.outcome == "MERGED"
        got = {(c.op, c.path): c.content for c in m.merged.changes}
        assert got == {("MODIFY", "a.py"): "a2\n", ("MODIFY", "b.py"): "b2\n"}

    def test_no_stale_base_acceptance(self, tmp_path):
        import random
        rng = random.Random(1234)
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        for seed in range(20):
            fake = f"snap-{rng.getrandbits(64):016x}"
            cs = make_changeset(fake, {"worker": "w"}, [])
            assert merge_changesets(snap, [cs]).outcome == "STALE"
            assert apply_changeset(ws, snap, cs).reason == "STALE"

    def test_fuzz_paths_contained(self, tmp_path):
        import random
        import string
        rng = random.Random(5678)
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        for _ in range(200):
            p = "".join(rng.choice(string.printable) for _ in range(rng.randint(0, 40)))
            try:
                c = Change(path=p or "x.py", op="CREATE", content="v\n")
            except ValueError:
                continue  # rejected at construction: correct
            full = os.path.realpath(os.path.join(ws, c.path))
            assert full.startswith(os.path.realpath(ws) + os.sep)
        assert snapshot_workspace(ws).id == snap.id  # inspection changed nothing

    def test_fuzz_merge_deterministic(self, tmp_path):
        import random
        rng = random.Random(999)
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        payloads = ["x\n", "y\n", "x = 1\n", "x = 2\n", ""]
        for _ in range(60):
            def mk(w):
                return make_changeset(snap.id, {"worker": w}, [
                    Change(path=rng.choice(["a.py", "b.py", "c.py", "n.py"]),
                           op=rng.choice(["CREATE", "MODIFY", "DELETE"]),
                           base_hash=snap.files.get("a.py", ""),
                           content=rng.choice(payloads))])
            a, b = mk("A"), mk("B")
            m1 = merge_changesets(snap, [a, b])
            m2 = merge_changesets(snap, [b, a])
            assert m1.outcome == m2.outcome
            if m1.outcome == "MERGED":
                assert m1.merged.id == m2.merged.id
            assert m1.outcome in ("MERGED", "CONFLICT", "STALE", "INVALID")
