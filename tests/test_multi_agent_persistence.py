"""Unit tests for Persistence."""

import json
from pathlib import Path

import pytest

from wisp.multi_agent._persistence import Persistence
from wisp.multi_agent.task import SubagentContract, SubagentResult


class TestPersistence:

    def test_save_creates_file(self, tmp_path):
        p = Persistence(tmp_path / "results.jsonl")
        c = SubagentContract(task="test")
        r = SubagentResult(task_id="t", success=True, output="hello")
        p.save(c, r)
        assert p._path.exists()

    def test_save_content(self, tmp_path):
        p = Persistence(tmp_path / "results.jsonl")
        c = SubagentContract(task="do something important")
        r = SubagentResult(task_id="task-1", success=True, output="result", elapsed_seconds=2.5, tokens_used=100)
        p.save(c, r)
        lines = p._path.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["task_id"] == "task-1"
        assert data["success"] is True
        assert data["elapsed_seconds"] == 2.5
        assert data["tokens_used"] == 100
        assert data["task"] == "do something important"
        assert "output_preview" in data

    def test_load_limit(self, tmp_path):
        p = Persistence(tmp_path / "results.jsonl")
        for i in range(5):
            c = SubagentContract(task=f"task {i}")
            r = SubagentResult(task_id=f"t{i}", success=True, output=f"out{i}")
            p.save(c, r)
        results = p.load(limit=3)
        assert len(results) == 3
        # Should be last 3
        assert results[0]["task_id"] == "t2"
        assert results[2]["task_id"] == "t4"

    def test_load_empty(self, tmp_path):
        p = Persistence(tmp_path / "results.jsonl")
        assert p.load() == []

    def test_load_missing_file(self, tmp_path):
        p = Persistence(tmp_path / "nonexistent.jsonl")
        assert p.load() == []

    def test_clear(self, tmp_path):
        p = Persistence(tmp_path / "results.jsonl")
        c = SubagentContract(task="test")
        r = SubagentResult(task_id="t", success=True, output="hello")
        p.save(c, r)
        p.clear()
        assert not p._path.exists()
        assert p.load() == []

    def test_multiple_saves_append(self, tmp_path):
        p = Persistence(tmp_path / "results.jsonl")
        for i in range(3):
            c = SubagentContract(task=f"task {i}")
            r = SubagentResult(task_id=f"t{i}", success=True, output=f"out{i}")
            p.save(c, r)
        lines = p._path.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_corrupted_line_skipped(self, tmp_path):
        p = Persistence(tmp_path / "results.jsonl")
        # Write a valid line and a corrupted line
        with open(p._path, "w") as f:
            f.write(json.dumps({"task_id": "good"}) + "\n")
            f.write("this is not json\n")
        results = p.load()
        assert len(results) == 1
        assert results[0]["task_id"] == "good"
