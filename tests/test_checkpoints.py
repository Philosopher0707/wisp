"""GH#15: checkpoint/rewind for file edits (P1.1).

Every write/edit auto-snapshots pre-mutation content; the rewind tool
restores it. Restore snapshots pre-restore state first (rewindable
rewind); failed mutations leave no checkpoint behind.
"""

from __future__ import annotations

import json

import pytest

from wisp.tools.checkpoints import (
    CheckpointStore,
    get_checkpoint_store,
    reset_checkpoint_stores,
    tool_rewind,
)
from wisp.tools.filesystem import tool_edit_file, tool_write_file


@pytest.fixture(autouse=True)
def _clean_stores():
    reset_checkpoint_stores()
    yield
    reset_checkpoint_stores()


def _ws(tmp_path) -> str:
    return str(tmp_path)


class TestAutoCheckpoint:
    def test_write_snapshots_old_content(self, tmp_path) -> None:
        ws = _ws(tmp_path)
        (tmp_path / "a.txt").write_text("original")
        tool_write_file(path="a.txt", workspace=ws, content="changed")
        cps = get_checkpoint_store(ws).list("a.txt")
        assert len(cps) == 1
        assert cps[0].content == "original"
        assert cps[0].kind == "write"

    def test_new_file_checkpoint_is_none(self, tmp_path) -> None:
        ws = _ws(tmp_path)
        tool_write_file(path="new.txt", workspace=ws, content="hello")
        cps = get_checkpoint_store(ws).list("new.txt")
        assert len(cps) == 1
        assert cps[0].content is None

    def test_edit_snapshots(self, tmp_path) -> None:
        ws = _ws(tmp_path)
        (tmp_path / "b.txt").write_text("foo bar")
        tool_edit_file(path="b.txt", workspace=ws, old_text="bar", new_text="baz")
        cps = get_checkpoint_store(ws).list("b.txt")
        assert len(cps) == 1 and cps[0].content == "foo bar"

    def test_failed_edit_leaves_no_checkpoint(self, tmp_path) -> None:
        from wisp.tools.errors import ToolError

        ws = _ws(tmp_path)
        (tmp_path / "c.txt").write_text("stable")
        with pytest.raises(ToolError):
            tool_edit_file(path="c.txt", workspace=ws,
                           old_text="missing-needle", new_text="x")
        assert get_checkpoint_store(ws).list("c.txt") == []
        assert (tmp_path / "c.txt").read_text() == "stable"


class TestStoreBounds:
    def test_evicts_oldest_first_by_count(self) -> None:
        store = CheckpointStore(max_per_file=2, max_bytes=10**9)
        for i in range(4):
            store.snapshot("f.txt", f"v{i}", "write")
        assert [c.content for c in store.list("f.txt")] == ["v3", "v2"]

    def test_evicts_oldest_first_by_bytes(self) -> None:
        store = CheckpointStore(max_per_file=1000, max_bytes=100)
        store.snapshot("f.txt", "x" * 60, "write")
        store.snapshot("f.txt", "y" * 60, "write")
        assert [c.content for c in store.list("f.txt")] == ["y" * 60]

    def test_drop_removes_single(self) -> None:
        store = CheckpointStore()
        a = store.snapshot("f.txt", "v1", "write")
        store.snapshot("f.txt", "v2", "write")
        assert store.drop(a.seq) is True
        assert [c.content for c in store.list("f.txt")] == ["v2"]
        assert store.drop(99999) is False


class TestRewindTool:
    def test_list_and_restore_by_seq(self, tmp_path) -> None:
        ws = _ws(tmp_path)
        (tmp_path / "a.txt").write_text("v1")
        tool_write_file(path="a.txt", workspace=ws, content="v2")
        listed = tool_rewind(workspace=ws, list_only=True)
        assert listed["status"] == "ok" and "#1" in listed["data"]
        seq = get_checkpoint_store(ws).list("a.txt")[0].seq
        out = tool_rewind(workspace=ws, seq=seq)
        assert out["status"] == "ok"
        assert (tmp_path / "a.txt").read_text() == "v1"

    def test_restore_by_path_takes_latest(self, tmp_path) -> None:
        ws = _ws(tmp_path)
        (tmp_path / "a.txt").write_text("v1")
        tool_write_file(path="a.txt", workspace=ws, content="v2")
        tool_write_file(path="a.txt", workspace=ws, content="v3")
        out = tool_rewind(workspace=ws, path="a.txt")
        assert out["status"] == "ok"
        assert (tmp_path / "a.txt").read_text() == "v2"

    def test_rewind_of_new_file_deletes(self, tmp_path) -> None:
        ws = _ws(tmp_path)
        tool_write_file(path="n.txt", workspace=ws, content="temp")
        out = tool_rewind(workspace=ws, path="n.txt")
        assert out["status"] == "ok" and "deleted" in out["data"]
        assert not (tmp_path / "n.txt").exists()

    def test_rewind_is_rewindable(self, tmp_path) -> None:
        ws = _ws(tmp_path)
        (tmp_path / "a.txt").write_text("v1")
        tool_write_file(path="a.txt", workspace=ws, content="v2")
        tool_rewind(workspace=ws, path="a.txt")  # back to v1
        assert (tmp_path / "a.txt").read_text() == "v1"
        tool_rewind(workspace=ws, path="a.txt")  # forward to v2
        assert (tmp_path / "a.txt").read_text() == "v2"

    def test_unknown_target_errors(self, tmp_path) -> None:
        from wisp.tools.errors import ToolError

        with pytest.raises(ToolError, match="No checkpoint"):
            tool_rewind(workspace=_ws(tmp_path), seq=424242)

    def test_rewind_via_registry(self, tmp_path) -> None:
        from wisp.tools.registry import execute_tool

        ws = _ws(tmp_path)
        (tmp_path / "a.txt").write_text("v1")
        tool_write_file(path="a.txt", workspace=ws, content="v2")
        raw = execute_tool("rewind", {"list_only": True}, ws, _skip_authorize=True)
        body = json.loads(raw)
        assert body["status"] == "ok" and "a.txt" in body["data"]
        seq = get_checkpoint_store(ws).list("a.txt")[0].seq
        raw = execute_tool("rewind", {"seq": seq}, ws, _skip_authorize=True)
        assert json.loads(raw)["status"] == "ok"
        assert (tmp_path / "a.txt").read_text() == "v1"
