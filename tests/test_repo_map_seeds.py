"""RepoMap issue/traceback seeding + frame expansion (GH#26)."""

from wisp.repo_map import RepoMap, _compute_pagerank, extract_seeds
from wisp.test_distill import distill_traceback, parse_frames


def _make_ws(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text(
        "from pkg.helpers import fmt\n\n"
        "def save(obj):\n"
        "    return fmt(obj)\n\n"
        "def load(name):\n"
        "    return name\n"
    )
    (pkg / "helpers.py").write_text(
        "def fmt(obj):\n"
        "    return str(obj)\n"
    )
    (pkg / "main.py").write_text(
        "from pkg.core import save\n\n"
        "if __name__ == '__main__':\n"
        "    save(1)\n"
    )
    return tmp_path


ISSUE_TEXT = """\
Saving objects is broken: pkg/core.py save() raises TypeError when the
object has no __str__. Traceback points at File "pkg/core.py", line 4,
in save. helpers.fmt seems fine.
"""


class TestExtractSeeds:
    def test_issue_text_seeds_mentioned_file_and_symbol(self):
        files = ["pkg/core.py", "pkg/helpers.py", "pkg/main.py"]
        syms = {"pkg/core.py": ["save", "load"], "pkg/helpers.py": ["fmt"]}
        seeds = extract_seeds(ISSUE_TEXT, files, syms)
        assert seeds.get("pkg/core.py", 0) > seeds.get("pkg/main.py", 0)
        assert "pkg/core.py" in seeds

    def test_noise_words_seed_nothing(self):
        files = ["pkg/core.py", "pkg/helpers.py"]
        seeds = extract_seeds("the object has no string and seems fine", files, {})
        assert seeds == {}

    def test_unknown_paths_ignored(self):
        files = ["pkg/core.py"]
        seeds = extract_seeds('File "nope/missing.py", line 1, in gone', files, {})
        assert seeds == {}


class TestSeededPageRank:
    def test_no_seeds_identical_to_default(self):
        files = ["a.py", "b.py"]
        deps = {"a.py": {"b.py"}, "b.py": set()}
        rev = {"a.py": set(), "b.py": {"a.py"}}
        assert _compute_pagerank(files, deps, rev) == _compute_pagerank(files, deps, rev, seeds=None)

    def test_seed_lifts_obscure_file(self):
        files = ["main.py", "util.py", "obscure.py"]
        deps = {"main.py": {"util.py"}, "util.py": set(), "obscure.py": set()}
        rev = {"main.py": set(), "util.py": {"main.py"}, "obscure.py": set()}
        plain = _compute_pagerank(files, deps, rev, iterations=50)
        seeded = _compute_pagerank(files, deps, rev, iterations=50, seeds={"obscure.py": 5.0})
        assert seeded["obscure.py"] > plain["obscure.py"]
        # Unmentioned files keep their relative order.
        assert (seeded["util.py"] > seeded["main.py"]) == (plain["util.py"] > plain["main.py"])

    def test_seed_works_in_sparse_heuristic_branch(self):
        files = ["src/main.py", "lib/utils.py"]
        deps = {f: set() for f in files}
        rev = {f: set() for f in files}
        plain = _compute_pagerank(files, deps, rev)
        seeded = _compute_pagerank(files, deps, rev, seeds={"lib/utils.py": 4.0})
        assert seeded["lib/utils.py"] > plain["lib/utils.py"]
        assert seeded["lib/utils.py"] >= seeded["src/main.py"]

    def test_seeded_build_raises_mentioned_file(self, tmp_path):
        _make_ws(tmp_path)
        rm = RepoMap(tmp_path)
        plain = {e.path: e.importance for e in rm.build(use_cache=False) if e.kind == "file"}
        seeds = extract_seeds(ISSUE_TEXT, list(plain),
                              {"pkg/core.py": ["save", "load"], "pkg/helpers.py": ["fmt"]})
        boosted = rm.seeded_importance(seeds)
        assert boosted["pkg/core.py"] > plain["pkg/core.py"]

    def test_seeded_importance_touches_no_disk(self, tmp_path):
        _make_ws(tmp_path)
        rm = RepoMap(tmp_path)
        rm.build(use_cache=False)
        cache = tmp_path / ".wisp" / "repo_map.json"
        before = cache.stat().st_mtime_ns if cache.exists() else None
        rm.seeded_importance({"pkg/core.py": 3.0})
        after = cache.stat().st_mtime_ns if cache.exists() else None
        assert before == after


class TestExpandFrames:
    def test_frame_pulls_definition_site_and_neighbors(self, tmp_path):
        _make_ws(tmp_path)
        rm = RepoMap(tmp_path)
        rm.build(use_cache=False)
        got = rm.expand_frames([("pkg/core.py", 4, "save")])
        by_name = {(e.path, e.name) for e in got}
        assert ("pkg/core.py", "save") in by_name  # definition site
        assert any(p == "pkg/helpers.py" for p, _ in by_name)  # import neighbor

    def test_unknown_file_tolerated(self, tmp_path):
        _make_ws(tmp_path)
        rm = RepoMap(tmp_path)
        rm.build(use_cache=False)
        assert rm.expand_frames([("nope/missing.py", 1, "gone")]) == []

    def test_distill_to_expand_roundtrip(self):
        tb = ('tests/test_math.py:9: in test_sum_to\n'
              '    assert sum_to(3) == 7\n'
              'E   AssertionError: assert 6 == 7\n')
        frames = parse_frames(distill_traceback(tb))
        assert frames == [("tests/test_math.py", 9, "test_sum_to")]
