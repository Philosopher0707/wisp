"""Phase 12 adversarial: paths, forged ChangeSets, merge attacks, authority.

THREAT: hostile paths/snapshots/changesets/patches/artifacts smuggle
writes, authority, or approval outside the workspace and policy.
EXPECTED: containment holds; forgeries rejected; ChangeSet != authority.
"""

from __future__ import annotations

import json
import os

import pytest

from wisp.workspace import (
    CREATE,
    MODIFY,
    Change,
    apply_changeset,
    isolated_copy,
    make_changeset,
    merge_changesets,
    merge_workspace,
    snapshot_workspace,
)


def _ws(tmp_path, files=None):
    files = files or {"a.py": "x = 1\n"}
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return str(tmp_path)


class TestPaths:
    @pytest.mark.parametrize("evil", ["../e.py", "/abs.py", "a/../../e.py",
                                      "a.py\x00", ".", "", "x" * 600])
    def test_evil_paths_rejected(self, evil):
        with pytest.raises(ValueError):
            Change(path=evil, op=CREATE, content="x")

    def test_absolute_snapshot_root_ok_but_contained(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws, files=["a.py"])
        assert snap.root == ws

    def test_symlink_in_copy_skipped(self, tmp_path):
        ws = _ws(tmp_path, {"a.py": "x\n"})
        snap = snapshot_workspace(ws)
        copy = isolated_copy(snap, "A")
        try:
            os.symlink("/etc/passwd", os.path.join(copy, "evil.py"))
        except OSError:
            pytest.skip("no symlinks")
        changes = __import__("wisp.workspace", fromlist=["x"]).diff_copy_against_snapshot(
            copy, snap, "A")
        assert all(c.path != "evil.py" for c in changes)
        assert "evil.py" not in [c.path for c in changes]

    def test_copy_dir_outside_workspace_read_only(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        outside = str(tmp_path / "elsewhere")
        os.makedirs(outside)
        open(os.path.join(outside, "q.py"), "w").write("q\n")
        out = merge_workspace({"snapshot": {"id": snap.id, "root": ws,
                                             "files": snap.files},
                               "branches": [{"worker": "A", "copy_dir": outside}]})
        # q.py untracked by snapshot: no changes -> merged empty (safe).
        assert out["label"] in ("merged", "invalid")

    def test_snapshot_root_forgery_refused(self, tmp_path):
        from wisp.workspace import isolation_functions
        ws = _ws(tmp_path)
        fns = isolation_functions(ws)
        out = fns["merge_changesets"]({
            "snapshot": {"id": "snap-x", "root": "/etc", "files": {}},
            "branches": [{"worker": "A", "changes": []}]})
        assert out["label"] == "invalid"

    def test_isolation_registry_bound(self, tmp_path):
        from wisp.workspace import isolation_functions
        ws = _ws(tmp_path)
        fns = isolation_functions(ws)
        out = fns["prepare_isolation"]({"files": ["a.py"], "workers": ["w"]})
        assert out["snapshot"]["root"] == ws
        out2 = fns["merge_changesets"]({
            "snapshot": {"id": "nope", "root": ws, "files": {"a.py": "dead"}},
            "branches": [{"worker": "w", "changes": []}]})
        assert out2["label"] in ("stale", "invalid")


class TestForgedChangeSets:
    def test_forged_producer_keys_stripped(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        cs = make_changeset(snap.id, {"worker": "A", "approved": "true",
                                      "role": "admin", "run_id": "r"}, [])
        assert set(cs.producer) <= {"run_id", "node_id", "worker"}

    def test_forged_base_rejected(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        cs = make_changeset("snap-forged", {"worker": "A"},
                            [Change(path="a.py", op=MODIFY,
                                    base_hash=snap.files["a.py"], content="evil\n")])
        assert merge_changesets(snap, [cs]).outcome == "STALE"
        assert apply_changeset(ws, snap, cs).reason == "STALE"

    def test_forged_approval_ignored(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        cs = make_changeset(snap.id, {"worker": "A", "approved": "true"},
                            [Change(path="a.py", op=MODIFY,
                                    base_hash=snap.files["a.py"], content="x\n")])
        # Approval is not a ChangeSet concept: merge treats it as unapproved data.
        m = merge_changesets(snap, [cs])
        assert m.outcome == "MERGED"  # structurally fine...
        # ...but nothing claims approval: outcome carries no approval field.
        assert "approved" not in str(m.merged.id)

    def test_malformed_patch_rejected(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        out = merge_workspace({"snapshot": {"id": snap.id, "root": ws,
                                             "files": snap.files},
                               "branches": [{"worker": "A", "changes": "not-a-list"}]})
        assert out["label"] == "invalid"

    def test_oversized_change_rejected(self, tmp_path):
        _ws(tmp_path)
        with pytest.raises(ValueError):
            Change(path="a.py", op=MODIFY, content="x" * 2_000_000)

    def test_duplicate_ids_rejected(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        with pytest.raises(ValueError):
            make_changeset(snap.id, {"worker": "A"}, [
                Change(path="a.py", op=MODIFY, base_hash="h", content="1\n"),
                Change(path="a.py", op=MODIFY, base_hash="h", content="2\n")])

    def test_mutation_outside_workspace_refused(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        # Normalizing-inside paths are contained (not an escape).
        cs = make_changeset(snap.id, {"worker": "A"},
                            [Change(path="sub/../a.py", op=MODIFY,
                                    base_hash=snap.files["a.py"], content="x\n")])
        assert cs.changes[0].path == "a.py"
        # Truly-escaping paths never construct.
        with pytest.raises(ValueError):
            Change(path="../../evil.py", op=CREATE, content="x")

    def test_artifact_smuggling_rejected(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        out = merge_workspace({"snapshot": {"id": snap.id, "root": ws,
                                             "files": snap.files},
                               "branches": [{"worker": "A", "changes": [
                                   {"op": "MODIFY", "path": "a.py",
                                    "base_hash": snap.files["a.py"],
                                    "artifact": "file:///etc/passwd"}]}]})
        assert out["label"] == "invalid"


class TestMergeAttacks:
    def test_overlapping_edits_conflict(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        mk = lambda w, c: {"worker": w, "changes": [  # noqa: E731
            {"op": "MODIFY", "path": "a.py", "base_hash": snap.files["a.py"],
             "content": c}]}
        out = merge_workspace({"snapshot": {"id": snap.id, "root": ws,
                                             "files": snap.files},
                               "branches": [mk("A", "x = 2\n"), mk("B", "x = 3\n")]})
        assert out["label"] == "conflict"
        assert out["conflicts"][0]["kind"] == "TEXT_CONFLICT"
        assert open(os.path.join(ws, "a.py")).read() == "x = 1\n"  # untouched

    def test_malicious_patch_content_contained(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        out = merge_workspace({"snapshot": {"id": snap.id, "root": ws,
                                             "files": snap.files},
                               "branches": [{"worker": "A", "changes": [
                                   {"op": "CREATE", "path": "ok.py", "content": "1\n"}]},
                                            {"worker": "B", "changes": [
                                                {"op": "DELETE", "path": "ok.py",
                                                 "base_hash": "dead"}]}]})
        assert out["label"] in ("conflict", "invalid", "stale")

    def test_stale_worker_rejected(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        (tmp_path / "a.py").write_text("moved on\n")
        out = merge_workspace({"snapshot": {"id": snap.id, "root": ws,
                                             "files": snap.files},
                               "branches": [{"worker": "A", "changes": [
                                   {"op": "MODIFY", "path": "a.py",
                                    "base_hash": snap.files["a.py"],
                                    "content": "stale\n"}]}]})
        assert out["label"] == "stale"


class TestAuthority:
    def test_changeset_is_not_authority(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        cs = make_changeset(snap.id, {"worker": "A", "tools": ["run_bash"]},
                            [Change(path="a.py", op=MODIFY,
                                    base_hash=snap.files["a.py"], content="x\n")])
        assert "tools" not in cs.producer
        # Merge output carries no tool grant either.
        out = merge_workspace({"snapshot": {"id": snap.id, "root": ws,
                                             "files": snap.files},
                               "branches": [{"worker": "A", "changes": [
                                   {"op": "MODIFY", "path": "a.py",
                                    "base_hash": snap.files["a.py"],
                                    "content": "x\n"}]}]})
        assert "run_bash" not in json.dumps(out)

    def test_model_output_is_not_approval(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        out = merge_workspace({"snapshot": {"id": snap.id, "root": ws,
                                             "files": snap.files},
                               "approved": True, "approval": {"x": True},
                               "branches": [{"worker": "A", "changes": [
                                   {"op": "MODIFY", "path": "a.py",
                                    "base_hash": snap.files["a.py"],
                                    "content": "x\n"}]}]})
        assert out["label"] == "merged"  # structurally decided...
        assert "approved" not in str(out.get("changeset", ""))


class TestFuzz:
    @pytest.mark.parametrize("seed", range(30))
    def test_random_changesets_never_crash(self, seed, tmp_path):
        import random
        import string
        rng = random.Random(90000 + seed)
        ws = _ws(tmp_path, {"a.py": "x\n", "b.py": "y\n"})
        snap = snapshot_workspace(ws)

        def rstr(n=8):
            return "".join(rng.choice(string.printable) for _ in range(n))

        branches = []
        for i in range(rng.randint(0, 4)):
            changes = []
            for _ in range(rng.randint(0, 4)):
                changes.append({"op": rng.choice(["CREATE", "MODIFY", "DELETE",
                                                  "RENAME", "BOGUS", ""]),
                                "path": rng.choice(["a.py", "b.py", "n.py",
                                                    "../e.py", ""]),
                                "base_hash": rstr(8),
                                "content": rstr(20),
                                "rename_from": rng.choice(["a.py", ""])})
            branches.append({"worker": f"w{i}", "changes": changes})
        out = merge_workspace({"snapshot": {"id": snap.id, "root": ws,
                                             "files": snap.files},
                               "branches": branches or "junk"})
        assert out["label"] in ("merged", "conflict", "stale", "invalid")
        # Canonical never escapes the workspace dir.
        for dirpath, _, filenames in os.walk(ws):
            if ".wisp" in dirpath:
                continue
            for fn in filenames:
                assert os.path.realpath(os.path.join(dirpath, fn)).startswith(
                    os.path.realpath(ws) + os.sep)

    def test_no_secrets_in_merge_output(self, tmp_path):
        ws = _ws(tmp_path)
        snap = snapshot_workspace(ws)
        secret = "OPENAI_API_KEY=sk-probe-1234567890abcdef"
        out = merge_workspace({"snapshot": {"id": snap.id, "root": ws,
                                             "files": snap.files},
                               "branches": [{"worker": "A", "changes": [
                                   {"op": "MODIFY", "path": "a.py",
                                    "base_hash": snap.files["a.py"],
                                    "content": f"key = {secret}\n"}]}]})
        assert out["label"] == "merged"
        # Contents applied to the file (operator data), but the merge
        # DECISION payload carries no secret material.
        assert secret not in json.dumps(out.get("evidence", {}))
        assert open(os.path.join(ws, "a.py")).read() == f"key = {secret}\n"
