"""`CURRENT_OPEN_ITEMS.md` is a *derived* document — guard the derivation.

The page exists because an open item in this corpus is named in any of four places and
nothing compares them: `CONTEXT.md` §12 (the live table), `CONTEXT.md` §0.0.x,
`WISP_MIGRATION_STATUS.md` (the phase ledger and its per-phase deferred items), each ADR's
`### Residuals` section, and each phase report's residual section. §12's `G1` row said
`OPEN` for three ADRs while `§0.0.14` recorded it closed — **F98**. Reconstructing an item's
state from those four homes is where drift re-enters.

Six properties, each independently falsifiable:

1. **The page declares itself derived, and states the ledger's vocabulary.**
2. **Both tables are non-empty** — a floor, because a check over a collection that can pass
   by finding nothing has not run (`CONTEXT.md` §10).
3. **Every `source` resolves**, and quotes the source it cites.
4. **Every `tripwire` names a real test.**
5. **Every state is in the ledger's vocabulary** — six words, no seventh.
6. **The page is reproducible from its generator** — the strongest property, and what makes
   the "derived" declaration true rather than aspirational.

Non-vacuity was checked by breaking each property in turn (a deleted row, a moved source, a
renamed tripwire, a coined state, an edited count, a hand-edited body, a dropped §Findings
entry) and confirming the corresponding test fails.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PAGE = REPO / "CURRENT_OPEN_ITEMS.md"
GENERATOR = REPO / "scripts" / "derive_current_open_items.py"

SOURCE_RE = re.compile(r"^([A-Za-z0-9_./-]+):(\d+)")
ROW_RE = re.compile(
    r"^\|\s*\*\*([^*]+)\*\*\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|\s*$", re.M)

#: The ledger's vocabulary (`WISP_MIGRATION_STATUS.md:41`). The register uses these six.
VOCABULARY = ("NOT STARTED", "IN PROGRESS", "PARTIAL", "BLOCKED", "COMPLETE", "SUPERSEDED")
CLOSED_STATES = frozenset({"COMPLETE", "SUPERSEDED"})


@pytest.fixture(scope="module")
def page_text() -> str:
    assert PAGE.exists(), f"{PAGE.name} is missing — it is a deliverable, not a draft"
    return PAGE.read_text(encoding="utf-8")


def _section(page_text: str, heading: str) -> str:
    assert heading in page_text, f"the page has no {heading!r} section"
    return page_text.split(heading, 1)[1].split("\n## ", 1)[0]


@pytest.fixture(scope="module")
def open_rows(page_text):
    rows = ROW_RE.findall(_section(page_text, "## The register — items not yet closed"))
    assert rows, "no open rows parsed — the loop below would pass vacuously"
    return rows


@pytest.fixture(scope="module")
def closed_rows(page_text):
    rows = ROW_RE.findall(_section(page_text, "## The closed items"))
    assert rows, "no closed rows parsed — the page must keep its closed items"
    return rows


def _generator():
    spec = importlib.util.spec_from_file_location("derive_current_open_items", GENERATOR)
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
            "hand-edited and become a second authority for the open-items table")

    def test_it_states_that_it_introduces_no_decision(self, page_text):
        assert "introduces no decision" in page_text[:2500].lower(), (
            "the page must disclaim decision authority")

    def test_it_names_the_revision_it_was_generated_at(self, page_text):
        assert re.search(r"\b[0-9a-f]{7}\b", page_text[:2500]), (
            "the page must name the commit it was generated at")

    def test_it_points_at_its_sibling_registers(self, page_text):
        head = page_text[:2500]
        for sibling in ("CURRENT_AUTHORITIES.md", "CURRENT_FINDINGS.md", "CURRENT_FLAGS.md"):
            assert sibling in head, (
                f"the banner does not name {sibling} — a derived page nobody can reach from "
                "its siblings is a page that rots")

    def test_it_names_the_ledger_as_the_vocabulary_authority(self, page_text):
        head = page_text[:3000]
        assert "WISP_MIGRATION_STATUS.md:41" in head, (
            "the page must cite where the state vocabulary comes from; the mission requires "
            "the ledger's vocabulary to govern, and a citation is what makes that checkable")


class TestTheTablesAreNonEmpty:
    """The floor — a check whose subject is a collection needs one."""

    def test_the_open_table_has_a_floor(self, open_rows):
        assert len(open_rows) >= 50, (
            f"only {len(open_rows)} open rows parsed — the corpus has far more; a register "
            "that can shrink without failing is not a register")

    def test_the_closed_table_has_a_floor(self, closed_rows):
        assert len(closed_rows) >= 20, (
            f"only {len(closed_rows)} closed rows — the page must keep its closed items, or "
            "an item that reopens vanishes with nothing to compare against")

    def test_ids_are_unique_within_each_table(self, open_rows, closed_rows):
        for label, rows in (("open", open_rows), ("closed", closed_rows)):
            ids = [r[0].strip() for r in rows]
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            assert not dupes, f"the {label} table repeats id(s) {dupes}"

    def test_no_id_is_in_both_tables(self, open_rows, closed_rows):
        both = {r[0].strip() for r in open_rows} & {r[0].strip() for r in closed_rows}
        assert not both, (
            f"id(s) {sorted(both)} appear in both tables — an item is either open or closed")


class TestEverySourceResolves:
    def test_every_row_cites_a_path_and_line(self, open_rows, closed_rows):
        bad = [r[0].strip() for r in open_rows + closed_rows
               if not SOURCE_RE.match(r[5].strip())]
        assert not bad, (
            f"row(s) {bad} carry no `path:line` source — a state without a location is an "
            "assertion, not a citation")

    def test_every_cited_file_exists(self, open_rows, closed_rows):
        bad = []
        for row in open_rows + closed_rows:
            m = SOURCE_RE.match(row[5].strip())
            if m and not (REPO / m.group(1)).exists():
                bad.append(f"{row[0].strip()}: {m.group(1)}")
        assert not bad, "rows cite files that do not exist:\n  " + "\n  ".join(bad)

    def test_every_cited_line_is_in_range(self, open_rows, closed_rows):
        bad = []
        for row in open_rows + closed_rows:
            m = SOURCE_RE.match(row[5].strip())
            if not m:
                continue
            f = REPO / m.group(1)
            if not f.exists():
                continue
            lines = f.read_text(encoding="utf-8").splitlines()
            n = int(m.group(2))
            if not (1 <= n <= len(lines)):
                bad.append(f"{row[0].strip()}: {m.group(1)}:{n} out of range ({len(lines)})")
        assert not bad, "rows cite lines that do not exist:\n  " + "\n  ".join(bad)

    def test_every_row_quotes_its_source(self, open_rows, closed_rows):
        bad = [r[0].strip() for r in open_rows + closed_rows if "—" not in r[5]]
        assert not bad, (
            f"row(s) {bad} cite a location with no quotation from it; a location alone "
            "cannot be checked against the state the row asserts")


class TestEveryTripwireNamesARealTest:
    def test_enough_rows_carry_a_tripwire(self, open_rows, closed_rows):
        n = sum(1 for r in open_rows + closed_rows if r[4].strip() != "—")
        assert n >= 10, (
            f"only {n} rows carry a tripwire — either the page lost its pins or the column "
            "drifted; both make this class vacuous")

    def test_every_named_tripwire_exists(self, open_rows, closed_rows):
        bad = []
        for row in open_rows + closed_rows:
            tr = row[4].strip()
            if tr == "—":
                continue
            path = tr.split("::", 1)[0]
            if not (REPO / path).exists():
                bad.append(f"{row[0].strip()}: {path} does not exist")
        assert not bad, "rows name tripwires that do not exist:\n  " + "\n  ".join(bad)


class TestTheVocabularyIsTheLedgers:
    def test_every_state_is_in_the_ledgers_vocabulary(self, open_rows, closed_rows):
        used = {r[2].strip().strip("`") for r in open_rows + closed_rows}
        stray = sorted(used - set(VOCABULARY))
        assert not stray, (
            f"the register uses state word(s) {stray} that the ledger's vocabulary does not "
            "define — a reason is not a state (§(b)); map it or fix the row")

    def test_the_page_states_the_vocabulary_once(self, page_text):
        section = _section(page_text, "## (a) The state vocabulary")
        for word in VOCABULARY:
            assert f"`{word}`" in section, (
                f"§(a) does not define `{word}` — the vocabulary must be stated once")

    def test_the_page_maps_the_other_sources_words(self, page_text):
        section = _section(page_text, "## (a) The state vocabulary")
        for word in ("OPEN", "DECIDED", "DEFERRED"):
            assert f"`{word}`" in section, (
                f"§(a) does not map the source word `{word}` — the register uses the ledger's "
                "vocabulary, so every other source's word needs a mapping or it is a second "
                "vocabulary")

    def test_the_page_states_that_a_reason_is_not_a_state(self, page_text):
        assert "## (b) The reasons are not states" in page_text, (
            "the page must state the rule once, or a reason will be coined as a state again")

    def test_the_reason_is_not_a_state_section_names_where_it_happened(self, page_text):
        section = _section(page_text, "## (b) The reasons are not states")
        assert "DECIDED" in section, (
            "§(b) must name `DECIDED` — §12 uses it as a state word and the ledger has no "
            "such state; recording it is the point of the section")


class TestTheCountsAreTheRows:
    def test_the_open_count_equals_the_rows_not_closed(self, page_text, open_rows):
        m = re.search(r"\*\*Open\*\* — everything not in §The closed items — \*\*(\d+)\*\*",
                      page_text)
        assert m, "§(c) no longer states the open count"
        assert int(m.group(1)) == len(open_rows), (
            f"§(c) says {m.group(1)} open; the open table has {len(open_rows)} rows")

    def test_every_state_has_a_count_row(self, page_text):
        section = _section(page_text, "## (c) The open count")
        for word in VOCABULARY:
            assert re.search(rf"^\| `{word}` \| \d+ \|", section, re.M), (
                f"§(c) has no count row for `{word}`")

    def test_the_counts_sum_to_the_register(self, page_text, open_rows, closed_rows):
        section = _section(page_text, "## (c) The open count")
        counts = [int(n) for n in re.findall(r"^\| `[A-Z ]+` \| (\d+) \|", section, re.M)]
        total = int(re.search(r"^\| \*\*total\*\* \| \*\*(\d+)\*\* \|", section, re.M).group(1))
        assert counts, "floor: §(c) states no counts"
        assert sum(counts) == total == len(open_rows) + len(closed_rows), (
            f"§(c)'s counts sum to {sum(counts)}, its total says {total}, the tables have "
            f"{len(open_rows) + len(closed_rows)} rows")


class TestThePageIsReproducibleFromItsGenerator:
    """The strongest property — the page is a projection, not a hand-typed table.

    `CONTEXT.md` §0.0.9's finding **F75**: *"an instrument that cannot be committed is not a
    re-runnable measurement."* A hand edit to the page fails here.
    """

    def test_the_generator_is_committed(self):
        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(GENERATOR.relative_to(REPO))],
            cwd=REPO, capture_output=True, text=True)
        assert tracked.returncode == 0, (
            f"{GENERATOR.relative_to(REPO)} is not tracked — F75's lesson")

    def test_regenerating_reproduces_the_page(self, page_text):
        fresh = _generator().render()
        # The page names its commit twice: in the banner and in §(c)'s measurement line.
        # Normalising only the banner's made the guard fail on a page that was correct —
        # a real defect in this guard, found by running it after a commit moved HEAD.
        norm = lambda t: re.sub(r"`[0-9a-f]{7,40}`", "`<sha>`", t)
        assert norm(fresh) == norm(page_text), (
            "CURRENT_OPEN_ITEMS.md does not match what its generator produces — the page has "
            "been hand-edited, or the data table moved without regenerating. Run:\n"
            "  env -u PYTHONPATH .venv/bin/python scripts/derive_current_open_items.py")

    def test_the_generator_refuses_an_unsound_table(self):
        mod = _generator()
        assert mod._check() == [], "the generator reports a broken table"
        assert len(mod.ROWS) >= 100, f"the generator's table has {len(mod.ROWS)} rows"


class TestFindingsSectionRecordsTheDisagreements:
    def test_it_has_a_findings_section(self, page_text):
        assert "## §Findings" in page_text, (
            "the page must carry its own findings section, or a disagreement has nowhere to go")

    def test_it_records_the_section_12_against_section_0_0_cross_check(self, page_text):
        section = page_text.split("## §Findings", 1)[1]
        assert "§12 against §0.0" in section or "§0.0" in section, (
            "the mission requires §12 to be cross-checked against §0.0; the result must be "
            "recorded whether or not it found a disagreement")

    def test_it_records_the_id_collision(self, page_text):
        section = page_text.split("## §Findings", 1)[1]
        assert "F1" in section and "M4" in section, (
            "§Findings must record that §12's `F1`–`F5` and `M4` collide with the findings "
            "log's and the governance layer's namespaces")

    def test_it_records_the_vocabulary_disagreement(self, page_text):
        section = page_text.split("## §Findings", 1)[1]
        assert "brief" in section.lower() and "vocabulary" in section.lower(), (
            "§Findings must record that the brief's state vocabulary is not the ledger's")

    def test_it_records_the_scope_of_adr_residuals(self, page_text):
        section = page_text.split("## §Findings", 1)[1]
        assert "ADR-0053" in section, (
            "§Findings must record that no ADR before 0053 has a named-residual section, so "
            "*\"every ADR's named residuals\"* resolves to six ADRs, not 61")

    def test_it_does_not_resolve_a_disagreement(self, page_text):
        section = page_text.split("## §Findings", 1)[1]
        for phrase in ("we resolve", "is resolved by", "the correct state is"):
            assert phrase not in section, (
                f"§Findings contains {phrase!r} — a derived page may record a disagreement, "
                "never resolve one")


# ── the source pin carries its quoted words (register-source-pins mission) ──
#
# Until this mission the generator checked a source's line was IN RANGE, not that it said what
# the row quotes — so pins drifted onto unrelated text and passed (`PHASE_REGISTER_SOURCE_PINS.md`).
# The rule lives once, in `scripts/register_pins.py`, for both registers.


def _pins():
    import sys as _sys

    _sys.path.insert(0, str(REPO / "scripts"))
    import register_pins

    return register_pins


class TestEverySourcePinCarriesItsQuote:
    def test_every_quoted_source_is_on_its_line(self):
        problems = _generator()._source_quote_problems()
        assert problems == [], (
            "CURRENT_OPEN_ITEMS.md cites line(s) that no longer carry the quoted words:\n  "
            + "\n  ".join(problems))

    def test_the_check_has_a_floor(self):
        mod, pins = _generator(), _pins()
        _problems, checkable = pins.source_quote_problems([(r[0], r[5]) for r in mod.ROWS], REPO)
        assert checkable >= mod.QUOTE_FLOOR, (
            f"only {checkable} sources carry a checkable quote — the check has gone vacuous")

    def _a_pinned_row(self):
        mod, pins = _generator(), _pins()
        for r in mod.ROWS:
            pin = pins.pinned_quote(r[5])
            if pin is None:
                continue
            path, line, frags = pin
            lines = (REPO / path).read_text(encoding="utf-8").splitlines()
            if (pins.quote_is_at(lines, line, frags, 0) and line + 40 <= len(lines)
                    and not pins.quote_is_at(lines, line + 40, frags)):
                return r, path, line, pins
        raise AssertionError("floor: no row is pinned exactly to its quote")

    def test_a_pin_moved_past_the_window_is_refused(self):
        row, path, line, pins = self._a_pinned_row()
        moved = row[5].replace(f"{path}:{line} —", f"{path}:{line + 40} —", 1)
        problems, _ = pins.source_quote_problems([(row[0], moved)], REPO)
        assert problems, (
            f"{row[0]}'s pin moved 40 lines onto other text and the check accepted it")

    def test_a_shift_inside_the_window_is_silent(self):
        """F92: a heading added a line or two above a row is not drift."""
        row, path, line, pins = self._a_pinned_row()
        shifted = row[5].replace(f"{path}:{line} —", f"{path}:{line + 2} —", 1)
        problems, _ = pins.source_quote_problems([(row[0], shifted)], REPO)
        assert not problems, f"a 2-line shift was called stale: {problems}"
