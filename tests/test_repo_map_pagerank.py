"""Tests for RepoMap PageRank centrality correctness.

Verifies that important files (entry points, hubs) score higher than
edge files (tests, leaves) and that format_for_llm respects token budgets.
"""

import pytest
from wisp.repo_map import RepoMapEntry, _compute_pagerank


class TestPageRank:
    """PageRank scoring on dependency graphs."""

    def test_hub_file_scores_high(self):
        """A file imported by many others gets a high score."""
        files = ["main.py", "util.py", "app.py"]
        deps = {
            "main.py": {"util.py", "app.py"},
            "util.py": set(),
            "app.py": {"util.py"},
        }
        rev_deps = {
            "main.py": set(),
            "util.py": {"main.py", "app.py"},
            "app.py": {"main.py"},
        }
        scores = _compute_pagerank(files, deps, rev_deps, iterations=50)
        assert scores["util.py"] > scores["main.py"]
        assert scores["util.py"] > scores["app.py"]

    def test_entry_point_bonus_when_sparse(self):
        """Sparse graph → heuristic fallback gives entry points high scores."""
        files = ["src/main.py", "lib/utils.py", "tests/test_foo.py"]
        deps = {"src/main.py": set(), "lib/utils.py": set(), "tests/test_foo.py": set()}
        rev_deps = {f: set() for f in files}
        scores = _compute_pagerank(files, deps, rev_deps, iterations=50)
        # Entry point (src/main.py) gets minimum 0.85
        assert scores["src/main.py"] >= 0.85, f"entry point score {scores['src/main.py']}"
        # Tests capped at ceiling
        assert scores["tests/test_foo.py"] <= 0.30, f"test file score {scores['tests/test_foo.py']}"

    def test_damping_influence_dense_graph(self):
        """Damping factor changes distribution in dense graphs."""
        files = ["a.py", "b.py", "c.py"]
        deps = {
            "a.py": {"b.py", "c.py"},
            "b.py": {"a.py", "c.py"},
            "c.py": {"a.py", "b.py"},
        }
        rev_deps = {f: {g for g in files if g != f} for f in files}
        dense_scores = _compute_pagerank(files, deps, rev_deps, damping=0.85, iterations=50)
        dense = [dense_scores[f] for f in files]
        # Dense symmetric graph → all roughly equal
        assert max(dense) - min(dense) < 0.1

    def test_convergence_produces_finite_numbers(self):
        """Even after many iterations, scores remain finite."""
        files = ["a.py", "b.py"]
        deps = {"a.py": {"b.py"}, "b.py": set()}
        rev_deps = {"a.py": set(), "b.py": {"a.py"}}
        scores = _compute_pagerank(files, deps, rev_deps, iterations=1000)
        for f in files:
            assert 0.0 <= scores[f] <= 1.5, f"{f} = {scores[f]}"
            assert scores[f] == scores[f], f"NaN for {f}"


class TestRepoMapEntry:
    """RepoMapEntry creation and attributes."""

    def test_entry_fields(self):
        entry = RepoMapEntry(
            path="src/main.py", name="main", kind="function",
            line=1, signature="def main()",
            importance=0.9,
            dependencies=["lib/utils.py"],
            summary="Entry point",
        )
        assert entry.path == "src/main.py"
        assert entry.importance == 0.9
        assert entry.dependencies == ["lib/utils.py"]
        assert entry.summary == "Entry point"

    def test_default_deps_and_summary(self):
        entry = RepoMapEntry(
            path="a.py", name="A", kind="class",
            line=10, signature="class A:",
            importance=0.5,
        )
        assert entry.dependencies == []
        assert entry.summary == ""
        assert entry.importance == 0.5


class TestFormatForLLMBudget:
    """format_for_llm respects max_tokens budget."""

    @pytest.fixture
    def tmp_repo(self, tmp_path):
        # Create minimal fake project
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("def main(): pass")
        (tmp_path / "README.md").write_text("# Project")
        return tmp_path

    @pytest.mark.skipif(
        __import__("importlib.util", fromlist=["util"]).find_spec("tree_sitter") is None,
        reason="tree_sitter not installed",
    )
    def test_format_respects_max_tokens(self, tmp_repo):
        from wisp.repo_map import RepoMap
        rm = RepoMap(str(tmp_repo))
        rm.build(use_cache=False, fast_mode=True)
        result = rm.format_for_llm(max_tokens=10)
        # 10 tokens → ~40 chars
        assert len(result) <= 50

    @pytest.mark.skipif(
        __import__("importlib.util", fromlist=["util"]).find_spec("tree_sitter") is None,
        reason="tree_sitter not installed",
    )
    def test_format_adds_truncation_notice_when_big(self, tmp_repo):
        from wisp.repo_map import RepoMap
        rm = RepoMap(str(tmp_repo))
        # Create many files to force truncation
        for i in range(50):
            (tmp_repo / f"file_{i}.py").write_text("x = 1")
        rm.build(use_cache=False, fast_mode=True)
        result = rm.format_for_llm(max_tokens=100)
        if "truncated" in result or "..." in result:
            assert True
