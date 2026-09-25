"""`CURRENT_FINDINGS.md` is a *derived* document — guard the derivation.

The page exists because the findings log has **104 entries in three homes** and no single
place answers *"what is the current status of Fn?"*: `WISP_MIGRATION_STATUS.md` §23 runs
F1–F44 and F64–F74, its §0 carries F45–F63, and **F75–F104 have no ledger row at all** —
they live only in `CONTEXT.md` §0's phase table and in the phase reports. Reconstructing a
status from those three homes is where drift re-enters. A page that can silently rot is
worse than no page, so this file is its safety net.

Six properties, each independently falsifiable:

1. **The page declares itself derived.** A prose tripwire, in the repo's established style.
2. **The register is total.** Every `F1`…`F104` has a row — and a **floor**, because a
   check whose subject is a collection and which can pass by finding nothing has not run
   (`CONTEXT.md` §10, F81's class).
3. **Every `source` resolves.** A row's status is a *claim that a specific line records it*.
   The line must exist, be in range, and the citation must be a real artifact.
4. **Every `tripwire` names a real test.** A pin that names a test which does not exist is
   a claim with no instrument.
5. **The vocabulary is stated and used.** Every status cell is one of the seven words §(a)
   defines, and every word §(a) defines is used — a vocabulary that has drifted from its
   own register is the defect the page exists to catch.
6. **The page is reproducible from its generator.** The strongest property: the page is a
   *projection* of `scripts/derive_current_findings.py`, not a hand-typed table. Regenerating
   must produce the page byte-for-byte, modulo the commit it names.

Non-vacuity was checked by breaking each property in turn (a deleted row, a moved source, a
renamed tripwire, a coined status word, an edited count, an edited body) and confirming the
corresponding test fails.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PAGE = REPO / "CURRENT_FINDINGS.md"
GENERATOR = REPO / "scripts" / "derive_current_findings.py"

#: `path/to/file.md:123` at the start of a source cell.
SOURCE_RE = re.compile(r"^([A-Za-z0-9_./-]+):(\d+)")
#: A row: `| **F7** | title | `STATUS` | class | decided | source | tripwire |`
ROW_RE = re.compile(
    r"^\|\s*\*\*(F\d{1,3})\*\*\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|\s*$",
    re.M,
)


@pytest.fixture(scope="module")
def page_text() -> str:
    assert PAGE.exists(), f"{PAGE.name} is missing — it is a deliverable, not a draft"
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rows(page_text) -> list[tuple[str, str, str, str, str, str, str]]:
    found = ROW_RE.findall(page_text)
    assert found, (
        "no rows parsed — either the register is gone or the row grammar drifted; "
        "every check below would pass vacuously")
    return found


def _generator():
    """Import the generator by path — it lives in `scripts/`, not on the package path."""
    spec = importlib.util.spec_from_file_location("derive_current_findings", GENERATOR)
    assert spec and spec.loader, f"cannot load {GENERATOR}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestThePageIsAWellFormedDerivedDocument:
    def test_it_says_it_is_regenerated_not_edited(self, page_text):
        head = "\n".join(page_text.splitlines()[:8]).lower()
        assert "regenerate" in head and "do not edit" in head, (
            "the regeneration contract must be stated at the top, or the page will be "
            "hand-edited and become a second authority for the findings log")

    def test_it_states_that_it_introduces_no_decision(self, page_text):
        head = page_text[:2500].lower()
        assert "introduces no decision" in head, (
            "the page must disclaim decision authority — it cites rows, it does not make them")

    def test_it_names_the_revision_it_was_generated_at(self, page_text):
        assert re.search(r"\b[0-9a-f]{7}\b", page_text[:2500]), (
            "the page must name the commit it was generated at; without it there is no way "
            "to tell how stale it is")

    def test_it_points_at_its_sibling_registers(self, page_text):
        """A reader who lands on one derived page must be able to reach the rest."""
        head = page_text[:2500]
        for sibling in ("CURRENT_AUTHORITIES.md", "CURRENT_OPEN_ITEMS.md", "CURRENT_FLAGS.md"):
            assert sibling in head, (
                f"the banner does not name {sibling} — a derived page nobody can reach "
                "from its siblings is a page that rots")

    def test_it_states_the_source_of_every_status(self, page_text):
        assert "pinned to a source" in page_text[:2500], (
            "the page must say that every row's status is pinned, or a reader cannot tell "
            "a cited status from an asserted one")


class TestTheRegisterIsTotal:
    """Property 2 — with the floor the discipline requires."""

    def test_there_is_a_floor_on_the_row_count(self, rows):
        assert len(rows) >= 104, (
            f"only {len(rows)} rows parsed — the corpus has 104 findings (F1–F104); a "
            "register that can shrink without failing is not a register")

    def test_every_finding_number_has_exactly_one_row(self, rows):
        ids = [fid for fid, *_ in rows]
        assert len(ids) == len(set(ids)), (
            "a finding has two rows: "
            + ", ".join(sorted(i for i in set(ids) if ids.count(i) > 1)))
        expected = {f"F{n}" for n in range(1, 105)}
        missing = sorted(expected - set(ids), key=lambda s: int(s[1:]))
        assert not missing, (
            f"{len(missing)} finding(s) have no row: {missing} — the register must be total")

    def test_the_rows_are_sorted_by_number(self, rows):
        nums = [int(fid[1:]) for fid, *_ in rows]
        assert nums == sorted(nums), "the register must be sorted by finding number"


class TestEverySourceResolves:
    """Property 3 — a source is a claim that a specific line records the status."""

    def test_every_row_cites_a_path_and_line(self, rows):
        bad = [fid for fid, _t, _s, _c, _d, src, _tr in rows if not SOURCE_RE.match(src.strip())]
        assert not bad, (
            f"row(s) {bad} carry no `path:line` source — a status without a location is "
            "an assertion, not a citation")

    def test_every_cited_file_exists(self, rows):
        bad = []
        for fid, _t, _s, _c, _d, src, _tr in rows:
            m = SOURCE_RE.match(src.strip())
            if m and not (REPO / m.group(1)).exists():
                bad.append(f"{fid}: {m.group(1)}")
        assert not bad, "rows cite files that do not exist:\n  " + "\n  ".join(bad)

    def test_every_cited_line_is_in_range(self, rows):
        bad = []
        for fid, _t, _s, _c, _d, src, _tr in rows:
            m = SOURCE_RE.match(src.strip())
            if not m:
                continue
            f = REPO / m.group(1)
            if not f.exists():
                continue                       # reported by the test above
            lines = f.read_text(encoding="utf-8").splitlines()
            n = int(m.group(2))
            if not (1 <= n <= len(lines)):
                bad.append(f"{fid}: {m.group(1)}:{n} is out of range ({len(lines)} lines)")
        assert not bad, "rows cite lines that do not exist:\n  " + "\n  ".join(bad)

    def test_every_row_quotes_its_source(self, rows):
        """The source cell must carry the source's own words, not just a location.

        This is what stops a status being *paraphrased* into existence: the page cannot
        say `FIXED` while the row it cites says `NOT FIXED` without the contradiction being
        visible on the line itself.
        """
        bad = [fid for fid, _t, _s, _c, _d, src, _tr in rows if "—" not in src]
        assert not bad, (
            f"row(s) {bad} cite a location with no quotation from it; a location alone "
            "cannot be checked against the status the row asserts")


class TestEveryTripwireNamesARealTest:
    """Property 4 — a pin that names a nonexistent test is a claim with no instrument."""

    def test_enough_rows_carry_a_tripwire(self, rows):
        """The floor: a register where every tripwire is `—` passes vacuously."""
        n = sum(1 for *_r, tr in rows if tr.strip() != "—")
        assert n >= 30, (
            f"only {n} rows carry a tripwire — either the register lost its pins or the "
            "column drifted; both make this class vacuous")

    def test_every_named_tripwire_exists(self, rows):
        bad = []
        for fid, _t, _s, _c, _d, _src, tr in rows:
            tr = tr.strip()
            if tr == "—":
                continue
            path = tr.split("::", 1)[0]
            if not (REPO / path).exists():
                bad.append(f"{fid}: {path} does not exist")
                continue
            parts = tr.split("::")
            if len(parts) > 1:
                names = set(re.findall(r"\b(?:def|class)\s+(\w+)", (REPO / path).read_text(encoding="utf-8")))
                missing = [p for p in parts[1:] if p not in names]
                if missing:
                    bad.append(f"{fid}: {path} has no {missing}")
        assert not bad, "rows name tripwires that do not exist:\n  " + "\n  ".join(bad)


class TestTheVocabularyIsStatedAndUsed:
    """Property 5 — the register's words and its stated vocabulary cannot drift apart."""

    def test_every_status_is_one_of_the_stated_words(self, page_text, rows):
        defined = set(re.findall(r"^\| `([A-Z][A-Z-]+)` \|", page_text, re.M))
        # §(a) is the only place the vocabulary is defined; §(c) also uses the row form.
        vocab = {"OPEN", "FIXED", "CLOSED", "SUPERSEDED", "DECIDED", "DEFECT-PIN", "UNRESOLVED"}
        assert vocab <= defined, (
            f"§(a) does not define {sorted(vocab - defined)} — the vocabulary must be "
            "stated once, and the register must use it")
        used = {s.strip().strip("`") for _f, _t, s, _c, _d, _sr, _tr in rows}
        stray = sorted(used - vocab)
        assert not stray, (
            f"the register uses status word(s) {stray} that §(a) does not define — either "
            "define it or fix the row; a second vocabulary is the defect this page catches")

    def test_every_defined_word_is_used(self, page_text, rows):
        vocab = {"OPEN", "FIXED", "CLOSED", "SUPERSEDED", "DECIDED", "DEFECT-PIN", "UNRESOLVED"}
        used = {s.strip().strip("`") for _f, _t, s, _c, _d, _sr, _tr in rows}
        unused = sorted(vocab - used)
        assert not unused, (
            f"§(a) defines {unused} and no row uses them — a vocabulary that has drifted "
            "from its own register is the F81 class")

    def test_it_maps_the_sources_own_words(self, page_text):
        assert "the source writes" in page_text, (
            "the page must map the sources' own words to its vocabulary; the corpus does "
            "not use one vocabulary and a reader with the ledger's words needs the mapping")


class TestTheCountsAreTheRows:
    """Property 6 — the stated count is the measured count (F85's discipline)."""

    def test_the_open_count_equals_the_rows_counted_open(self, page_text, rows):
        m = re.search(r"^\| `OPEN` \| (\d+) \|", page_text, re.M)
        assert m, "§(c) no longer states an `OPEN` count"
        stated = int(m.group(1))
        measured = sum(1 for _f, _t, s, _c, _d, _sr, _tr in rows
                       if s.strip().strip("`") == "OPEN")
        assert stated == measured, (
            f"§(c) says {stated} OPEN; the register has {measured} — the count is not the set")

    def test_every_status_has_a_count_row(self, page_text):
        for word in ("OPEN", "FIXED", "CLOSED", "SUPERSEDED", "DECIDED", "DEFECT-PIN", "UNRESOLVED"):
            assert re.search(rf"^\| `{word}` \| \d+ \|", page_text, re.M), (
                f"§(c) has no count row for `{word}`")

    def test_the_counts_sum_to_the_register(self, page_text, rows):
        counts = [int(n) for n in re.findall(r"^\| `[A-Z][A-Z-]+` \| (\d+) \|", page_text, re.M)]
        assert counts, "floor: §(c) states no counts"
        total = int(re.search(r"^\| \*\*total\*\* \| \*\*(\d+)\*\* \|", page_text, re.M).group(1))
        assert sum(counts) == total == len(rows), (
            f"§(c)'s counts sum to {sum(counts)}, its total says {total}, the register has "
            f"{len(rows)} rows")


class TestThePageIsReproducibleFromItsGenerator:
    """Property 6, the strongest form — the page is a projection, not a hand-typed table.

    `CONTEXT.md` §0.0.9 records finding **F75**: *"an instrument that cannot be committed is
    not a re-runnable measurement."* The page declares itself *derived, regenerated, never
    edited* — so the derivation is committed, and this test is what makes the declaration
    true. A hand edit to the page fails here.
    """

    def test_the_generator_is_committed(self):
        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(GENERATOR.relative_to(REPO))],
            cwd=REPO, capture_output=True, text=True)
        assert tracked.returncode == 0, (
            f"{GENERATOR.relative_to(REPO)} is not tracked — F75's lesson: an instrument "
            "that cannot be committed is not a re-runnable measurement")

    def test_regenerating_reproduces_the_page(self, page_text):
        mod = _generator()
        fresh = mod.render()
        # The generated-at commit is a property of *when*, not of *what*; normalise it and
        # the date so the comparison is about content. Everything else must be identical.
        norm = lambda t: re.sub(r"Generated \d{4}-\d{2}-\d{2} at `[0-9a-f]+`",
                                "Generated <date> at <sha>", t)
        assert norm(fresh) == norm(page_text), (
            "CURRENT_FINDINGS.md does not match what its generator produces — the page has "
            "been hand-edited, or the data table moved without regenerating. Run:\n"
            "  env -u PYTHONPATH .venv/bin/python scripts/derive_current_findings.py")

    def test_the_generator_refuses_an_unsound_table(self):
        """The generator must fail loudly on a broken citation, not emit a broken page."""
        mod = _generator()
        assert mod._check_sources() == [], "the generator reports a broken source"
        assert mod._check_tripwires() == [], "the generator reports a broken tripwire"
        assert len(mod.ROWS) == 104, f"the generator's table has {len(mod.ROWS)} rows, not 104"


class TestFindingsSectionRecordsWhatCouldNotBePinned:
    """The page may record a conflict; it may not resolve one."""

    def test_it_has_a_findings_section(self, page_text):
        assert "## §Findings" in page_text, (
            "the page must carry its own findings section, or an unpinnable claim has "
            "nowhere to go and gets guessed instead")

    def test_it_records_the_unpinnable_finding(self, page_text):
        section = page_text.split("## §Findings", 1)[1]
        assert "F77" in section, (
            "F77 is cited by CONTEXT.md:215 as found by PHASE_DAG_RETIREMENT.md, which does "
            "not contain it; the page must record that rather than invent a statement")

    def test_it_records_the_missing_ledger_rows(self, page_text):
        section = page_text.split("## §Findings", 1)[1]
        assert "F75–F104" in section or "F75-F104" in section, (
            "the page must record that F75–F104 have no ledger row — 30 of 104 findings "
            "are invisible to a reader sent to the ledger")

    def test_it_records_every_source_conflict(self, page_text):
        section = page_text.split("## §Findings", 1)[1]
        for fid in ("F8", "F19", "F37", "F39", "F89"):
            assert f"**{fid}.**" in section, (
                f"the page's own data table records a conflict for {fid}; §Findings must "
                "list it, or the conflict is silently resolved")

    def test_it_does_not_resolve_a_conflict(self, page_text):
        section = page_text.split("## §Findings", 1)[1]
        for phrase in ("we resolve", "is resolved by", "the correct status is"):
            assert phrase not in section, (
                f"§Findings contains {phrase!r} — a derived page may record a conflict, "
                "never resolve one")
