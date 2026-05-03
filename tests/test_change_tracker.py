"""Tests for wisp.change_tracker — change tracking."""

import tempfile
from pathlib import Path

import pytest

from wisp.change_tracker import ChangeTracker, ChangeRecord


class TestChangeTracker:
    """Unit tests for ChangeTracker."""

    def setup_method(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ct = ChangeTracker(self.tmp.name, agent_id="agent-1")

    def teardown_method(self):
        self.tmp.cleanup()

    def test_record_write(self):
        self.ct.record_write("test.py", "hello world\n")
        changes = self.ct.get_changes()
        assert len(changes) == 1
        assert changes[0].action == "write"
        assert changes[0].filepath == "test.py"
        assert changes[0].size_after > 0

    def test_record_edit(self):
        self.ct.record_edit("test.py", "old text\n", "new text\n")
        changes = self.ct.get_changes()
        assert len(changes) == 1
        assert changes[0].action == "edit"
        assert changes[0].lines_changed == 0  # same line count

    def test_record_delete(self):
        self.ct.record_delete("test.py")
        changes = self.ct.get_changes()
        assert len(changes) == 1
        assert changes[0].action == "delete"

    def test_get_changed_files(self):
        self.ct.record_write("a.py", "x")
        self.ct.record_edit("b.py", "old", "new")
        files = self.ct.get_changed_files()
        assert sorted(files) == ["a.py", "b.py"]

    def test_summary(self):
        self.ct.record_write("a.py", "x")
        self.ct.record_edit("b.py", "old", "new")
        summary = self.ct.summary()
        assert "1 writes" in summary
        assert "1 edits" in summary
        assert "a.py" in summary
        assert "b.py" in summary

    def test_empty_summary(self):
        summary = self.ct.summary()
        assert summary == "No changes made."

    def test_to_json(self):
        self.ct.record_write("a.py", "x")
        json_str = self.ct.to_json()
        assert "agent-1" in json_str
        assert "a.py" in json_str

    def test_filter_by_action(self):
        self.ct.record_write("a.py", "x")
        self.ct.record_edit("b.py", "old", "new")
        writes = self.ct.get_changes("write")
        assert len(writes) == 1
        assert writes[0].filepath == "a.py"

    def test_record_write_with_description(self):
        self.ct.record_write("test.py", "content", description="Added main function")
        changes = self.ct.get_changes()
        assert changes[0].description == "Added main function"
