"""The four derived registers, and the entry point that names them.

Three derived pages plus `CURRENT_AUTHORITIES.md` is four pages a contributor must know
exist. A derived page that nobody knows to read is a page that rots — so this file guards two
things: that each page is reachable from the others, and that `CONTEXT.md` carries a section
that names all four.

Five properties, each independently falsifiable:

1. **All four pages exist.** They are deliverables, not drafts.
2. **Each page's banner carries the regeneration rule** — `regenerate` and `do not edit`.
3. **Each page's banner names the other three.** The pointer is bidirectional, so a reader who
   lands on any one can reach the rest.
4. **`CONTEXT.md` carries the entry-point section, and it names all four** — with a generator
   and a guard for each.
5. **The section is a pointer, not a summary.** It must not restate a page's content: no flag
   default, no finding row, no item state. A section that answered the questions would be a
   fifth authority for facts that already have one.

Non-vacuity was checked by breaking each property in turn (a renamed page, a removed banner
rule, a dropped sibling pointer, a dropped row from the section, and a section that restates a
default) and confirming the corresponding test fails.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
CONTEXT = REPO / "CONTEXT.md"

#: The four registers, and the generator + guard each names in the entry-point section.
REGISTERS = {
    "CURRENT_AUTHORITIES.md": (
        "scripts/derive_current_authorities.py",
        "tests/reliability/test_current_authorities_pins.py",
    ),
    "CURRENT_FINDINGS.md": (
        "scripts/derive_current_findings.py",
        "tests/reliability/test_current_findings_pins.py",
    ),
    "CURRENT_OPEN_ITEMS.md": (
        "scripts/derive_current_open_items.py",
        "tests/reliability/test_current_open_items_pins.py",
    ),
    "CURRENT_FLAGS.md": (
        "scripts/derive_current_flags.py",
        "tests/reliability/test_current_flags_pins.py",
    ),
}

SECTION_HEADING = "## The derived registers"


def _banner(text: str) -> str:
    """The page's leading blockquote — everything before the first `---`."""
    return text.split("\n---", 1)[0]


@pytest.fixture(scope="module")
def section() -> str:
    text = CONTEXT.read_text(encoding="utf-8")
    assert SECTION_HEADING in text, (
        f"CONTEXT.md has no {SECTION_HEADING!r} section — the registers exist but nothing "
        "points at them, which is how a derived page rots")
    return text.split(SECTION_HEADING, 1)[1].split("\n## ", 1)[0]


class TestAllFourPagesExist:
    @pytest.mark.parametrize("page", sorted(REGISTERS))
    def test_the_page_exists(self, page):
        assert (REPO / page).exists(), (
            f"{page} is missing — it is a deliverable of the corpus-governance mission, "
            "not a draft")

    def test_there_is_a_floor_on_the_page_count(self):
        missing = [p for p in REGISTERS if not (REPO / p).exists()]
        assert not missing, f"the register set is incomplete: {missing}"


class TestEachBannerCarriesTheRegenerationRule:
    @pytest.mark.parametrize("page", sorted(REGISTERS))
    def test_the_banner_says_regenerate_and_do_not_edit(self, page):
        head = _banner((REPO / page).read_text(encoding="utf-8")).lower()
        assert "regenerate" in head, (
            f"{page}'s banner does not say it is regenerated — a derived page that does not "
            "say so will be hand-edited")
        assert "do not edit" in head or "never edit" in head, (
            f"{page}'s banner does not say not to edit it in place")

    @pytest.mark.parametrize("page", sorted(REGISTERS))
    def test_the_banner_says_it_introduces_no_decision(self, page):
        head = _banner((REPO / page).read_text(encoding="utf-8")).lower()
        assert "no decision" in head, (
            f"{page}'s banner does not disclaim decision authority — every one of the four is "
            "derived, and a derived page cites rather than decides")


class TestEachBannerNamesTheOtherThree:
    @pytest.mark.parametrize("page", sorted(REGISTERS))
    def test_the_banner_names_every_sibling(self, page):
        head = _banner((REPO / page).read_text(encoding="utf-8"))
        siblings = [p for p in REGISTERS if p != page]
        missing = [s for s in siblings if s not in head]
        assert not missing, (
            f"{page}'s banner does not name {missing} — the pointer must be bidirectional, or "
            "a reader who lands on one page cannot reach the rest")

    @pytest.mark.parametrize("page", sorted(REGISTERS))
    def test_the_banner_does_not_name_itself_as_a_sibling(self, page):
        """A self-reference is a sign the list was pasted rather than composed."""
        head = _banner((REPO / page).read_text(encoding="utf-8"))
        # The page names itself once, as its title; naming it again in the sibling sentence
        # is the defect. Count occurrences in the banner.
        assert head.count(page) <= 1, (
            f"{page}'s banner names itself {head.count(page)} times — once is the title, more "
            "is a pasted list")


class TestTheEntryPointSection:
    def test_it_names_all_four_pages(self, section):
        missing = [p for p in REGISTERS if p not in section]
        assert not missing, (
            f"the section does not name {missing} — a register nobody is pointed at is a page "
            "that rots")

    def test_it_names_a_generator_or_regeneration_route_for_each(self, section):
        for page, refs in REGISTERS.items():
            for ref in refs:
                assert ref in section, (
                    f"the section names {page} but not {ref} — the section must say how the "
                    "page is produced and what guards it, or a reader cannot regenerate it")

    def test_it_states_the_regenerate_not_edit_rule(self, section):
        low = section.lower()
        assert "regenerate, never edit" in low or "regenerate, do not edit" in low, (
            "the section must state the rule once for all four pages")

    def test_it_states_that_the_pages_cite_and_do_not_decide(self, section):
        assert "does not decide" in section or "do not decide" in section, (
            "the section must state that a derived page cites rather than decides")

    def test_it_names_the_precedent(self, section):
        assert "CURRENT_AUTHORITIES.md" in section and "F64" in section, (
            "the section must name the precedent — CURRENT_AUTHORITIES.md recording F64/F65 "
            "and refusing to decide them is the discipline all four follow")


class TestTheSectionIsAPointerNotASummary:
    """The section says *what each page answers*; it must not restate an answer."""

    def test_it_does_not_restate_a_flag_default(self, section):
        assert not re.search(r"\*\*(ON|OFF)\*\*", section), (
            "the section states a flag default — that is CURRENT_FLAGS.md's answer, and "
            "restating it makes a fifth authority for a fact that already has one")

    def test_it_does_not_restate_a_finding_row(self, section):
        assert not re.search(r"\|\s*\*\*F\d{1,3}\*\*\s*\|", section), (
            "the section carries a finding row — that is CURRENT_FINDINGS.md's content")

    def test_it_does_not_restate_an_item_state(self, section):
        assert not re.search(r"\|\s*\*\*M\d{1,2}\*\*\s*\|", section), (
            "the section carries an open-item row — that is CURRENT_OPEN_ITEMS.md's content")

    def test_it_is_short_enough_to_be_a_pointer(self, section):
        assert len(section.splitlines()) < 45, (
            f"the section is {len(section.splitlines())} lines — a pointer that long is "
            "summarising, and a summary of a derived page is a second copy of it")

    def test_each_row_states_a_question_the_page_answers(self, section):
        """Each row's second cell is the question its page answers, in italics."""
        questions = re.findall(r"^\|\s*`[A-Za-z_.]+`\s*\|\s*\*([^*]+)\*\s*\|", section, re.M)
        assert len(questions) >= 4, (
            f"only {len(questions)} of the four rows state the question their page answers — "
            "the section's job is to say what each page is *for*")
        assert all(q.strip().endswith("?") for q in questions), (
            "a row's description is not phrased as the question its page answers: "
            f"{[q for q in questions if not q.strip().endswith('?')]}")
