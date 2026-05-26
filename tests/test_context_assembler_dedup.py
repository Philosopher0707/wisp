"""Tests for cross-source deduplication in ContextAssembler.

When multiple context sources mention the same file,
the assembler should not duplicate information.
"""

from wisp.context_assembler import ContextAssembler


class TestCrossSourceDedup:

    def test_same_file_in_code_index_and_repo_map_prefers_index(self):
        """code_index_summary and repo_map should not both mention the same file."""
        assembler = ContextAssembler()
        index = "## Code Index\n\napp.py — 3 classes, 47 functions\n"
        repo = "## Repo Map\n\napp.py — 1 class, 12 functions (deprecated)\n"

        result = assembler.build(
            workspace="/tmp",
            default_system="SYS",
            code_index_summary=index,
            repo_map=repo,
        )

        # After dedup, only one mention of app.py should remain
        assert result.count("app.py") == 1, (
            f"Duplicate file 'app.py' appears multiple times in prompt:\n{result}"
        )

    def test_no_false_positives_on_similar_content(self):
        """Strings containing the filename in prose should NOT be removed."""
        assembler = ContextAssembler()
        index = "app.py\ndef main(): pass\n"
        repo = "README.md\nCheck out app.py for examples.\n"

        result = assembler.build(
            workspace="/tmp",
            default_system="SYS",
            code_index_summary=index,
            repo_map=repo,
        )

        # README mentions app.py in prose — that should still be there
        assert "out app.py" in result

    def test_distinct_files_preserved(self):
        """Different files in each source should both appear."""
        assembler = ContextAssembler()
        index = "main.py\n"
        repo = "utils.py\n"

        result = assembler.build(
            workspace="/tmp",
            default_system="SYS",
            code_index_summary=index,
            repo_map=repo,
        )

        assert "main.py" in result
        assert "utils.py" in result
