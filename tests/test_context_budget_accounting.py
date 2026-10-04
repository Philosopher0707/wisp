"""`ContextAssembler._fit_sections`: the assembled prompt must fit the budget it was given, say what it cut,
and cost one length measurement per section.

Defects this pins (each reproduced with the real function before the fix):

* after a truncation the running total was overwritten with that one block's size, so later low-priority
  sections were judged against an almost empty budget (1000-token budget -> 1527 tokens on main);
* the `[SECTION TRUNCATED ...]` header, the `\\n\\n` separators and the closing NOTE were never counted;
* a section dropped for being too large left no note at all (`last_truncate_label` was only set for priority 0
  and memory), so a 10k-token skills block vanished without a trace.
"""

from __future__ import annotations

import pytest

from wisp.context_assembler import ContextAssembler

CHARS = 3  # the assembler's one ruler: tokens = len(text) // 3


def _text(tokens: int, word: str = "lorem ") -> str:
    return (word * (tokens * CHARS // len(word) + 1))[: tokens * CHARS]


@pytest.fixture
def asm():
    return ContextAssembler()


def _measure(asm: ContextAssembler, text: str) -> int:
    return asm._estimate_tokens(text)


class TestTheBudgetIsHonoured:
    def test_a_lower_priority_section_is_not_admitted_after_memory_was_truncated(self, asm):
        """The reproduced case: 696-token system, over-budget memory, 296-token project note, 1000 budget."""
        sections = [
            ("default_system", 0, _text(696)),
            ("memory_block", 2, _text(4400, "remember: tabs. ")),
            ("project_context", 3, _text(296, "project note. ")),
        ]
        out, used = asm._fit_sections(sections, 1000)
        assert "project note." not in out
        assert used <= 1000

    @pytest.mark.parametrize("budget", [400, 800, 1000, 1500, 3000])
    @pytest.mark.parametrize("sizes", [
        (300, 900, 200, 400), (700, 4400, 300, 300), (1200, 100, 100, 100), (50, 50, 5000, 5000), (500, 500, 500, 500),
    ])
    def test_the_final_text_never_exceeds_the_budget(self, asm, budget, sizes):
        s0, s1, s2, s3 = sizes
        sections = [
            ("default_system", 0, _text(s0)), ("skills_block", 2, _text(s1, "skill. ")),
            ("memory_block", 2, _text(s2, "memory. ")), ("repo_map", 3, _text(s3, "map. ")),
        ]
        out, used = asm._fit_sections(sections, budget)
        assert _measure(asm, out) <= budget, (budget, sizes, _measure(asm, out))
        assert used == _measure(asm, out)

    def test_the_truncation_header_and_the_note_are_inside_the_budget(self, asm):
        out, _ = asm._fit_sections([("default_system", 0, _text(2000))], 500)
        assert out.startswith("[SECTION TRUNCATED: default_system")
        assert _measure(asm, out) <= 500

    def test_a_tiny_budget_still_returns_the_start_of_the_critical_section(self, asm):
        out, _ = asm._fit_sections([("default_system", 0, "You are Wisp, a careful coding agent. " * 50)], 30)
        assert "You are Wisp" in out


class TestADegenerateBudgetNeverLeavesAnEmptyPrompt:
    def test_a_budget_smaller_than_the_truncation_header_keeps_a_slice_of_the_critical_section(self, asm):
        """My first version dropped the critical section here and returned ''. The one documented overshoot is that
        the first priority-0 section keeps a minimal slice, and the cut is disclosed."""
        sections = [("default_system", 0, "You are Wisp. " * 500), ("context_files", 1, "x" * 50000)]
        out, _ = asm._fit_sections(sections, 10)
        assert out.startswith("[SECTION TRUNCATED: default_system]")
        assert "You are Wisp." in out
        assert "context_files (omitted)" in out


class TestWhatWasCutIsSaid:
    def test_a_dropped_section_is_named_in_a_note(self, asm):
        """The skills block was dropped whole and nothing said so."""
        sections = [("default_system", 0, _text(300)), ("skills_block", 2, _text(5000, "skill. "))]
        out, _ = asm._fit_sections(sections, 1000)
        assert "skill." not in out
        assert "skills_block (omitted)" in out

    def test_a_truncated_section_is_named_too(self, asm):
        sections = [("default_system", 0, _text(300)), ("memory_block", 2, _text(4000, "memory. "))]
        out, _ = asm._fit_sections(sections, 1000)
        assert "memory_block (truncated)" in out

    def test_no_note_when_everything_fits(self, asm):
        out, _ = asm._fit_sections([("default_system", 0, _text(100)), ("repo_map", 3, _text(100, "map. "))], 1000)
        assert "[NOTE:" not in out and "TRUNCATED" not in out
        assert out == "\n\n".join([_text(100), _text(100, "map. ")])

    def test_sections_that_fit_survive_in_priority_order(self, asm):
        sections = [("repo_map", 3, "MAP"), ("default_system", 0, "SYSTEM"), ("skills_block", 2, "SKILLS")]
        out, _ = asm._fit_sections(sections, 1000)
        assert out == "SYSTEM\n\nSKILLS\n\nMAP"


class TestItIsCheap:
    def test_each_section_is_measured_once_not_per_decision(self, asm, monkeypatch):
        calls = []
        real = asm._estimate_tokens
        monkeypatch.setattr(asm, "_estimate_tokens", lambda t: (calls.append(len(t)), real(t))[1])
        sections = [(f"s{i}", i % 4, _text(300, f"w{i} ")) for i in range(10)]
        asm._fit_sections(sections, 1000)
        assert len(calls) <= 2, f"{len(calls)} measurements for 10 sections"
