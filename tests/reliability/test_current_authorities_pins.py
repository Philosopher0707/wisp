"""`CURRENT_AUTHORITIES.md` is a *derived* document — guard the derivation.

The page exists because the corpus is 47 ADRs and the *answers* are scattered across six
of them; anyone implementing the next decision would otherwise re-derive the current
state from ADR-0035 → 0036 → 0037 → 0042 → 0044 → 0047, and that reconstruction is where
drift re-enters. A page that can silently rot is worse than no page, so this file is its
safety net: it fails the moment a pin stops resolving, the matrix stops matching the
arbiter, or the page stops declaring itself derived.

Three properties, each independently falsifiable:

1. **Every `path:line` pin resolves — and names the symbol it claims.** A pin is a *claim
   that a specific line says a specific thing*. Two checks, because the weaker one alone
   was not enough: the line must exist and not be blank, **and** the window around it
   must contain an identifier the page's prose names on that line. The second check was
   added after the first caught **one of five** stale pins when `runtime.py` moved, the
   other four being found by reading (ADR-0053 §6, recurring in ADR-0054).
2. **The §3 matrix is the arbiter's.** Row-by-row, the page's condition → result mapping
   is compared against `goal.PRECEDENCE` and against `derive_goal_state` itself. The table
   is therefore *reproduced*, not paraphrased: change a row in the code and the page fails.
3. **The page declares itself regenerable.** A prose tripwire, in the repo's established
   style — a documented property should also be asserted.

Non-vacuity was checked by breaking each property in turn (a moved pin, a mutated row,
a deleted header) and confirming the corresponding test fails.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PAGE = REPO / "CURRENT_AUTHORITIES.md"

#: `path/to/module.py:123` or `path/to/module.py:123-145`, inside backticks.
PIN_RE = re.compile(r"`([A-Za-z0-9_./-]+\.py):(\d+)(?:-(\d+))?`")

#: Identifiers long enough to be a code symbol rather than a word.
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")

#: How far a pin may drift before the content check calls it stale. A pin may
#: legitimately move a line or two when code moves around it; requiring the exact line
#: would make the guard a nuisance rather than a check. Four pins drifted by a few lines
#: in one mission and the old check caught **one** of them (ADR-0053 §6).
PIN_WINDOW = 2

#: The six authorities the page must document, in chain order.
AUTHORITY_HEADINGS = (
    "### 1.1 turn predicate",
    "### 1.2 stream state",
    "### 1.3 acceptance verdict",
    "### 1.4 progress verdict",
    "### 1.5 goal state",
    "### 1.6 recovery ladder state",
)

#: The §3 matrix, as the page states it. `#` → the result token it must name.
#: This is the *page's* claim; `test_the_matrix_is_the_arbiter` checks it against the code.
EXPECTED_MATRIX = (
    (0, "frozen"),
    (1, "CANCELLED"),
    (2, "ESCALATED_TO_HUMAN"),
    (3, "GOAL_FAILED"),
    (4, "GOAL_FAILED"),
    (5, "GOAL_STAGNATED"),
    (6, "GOAL_MET"),
    (7, "GOAL_UNVERIFIED"),
)


@pytest.fixture(scope="module")
def page_text() -> str:
    assert PAGE.exists(), f"{PAGE.name} is missing — it is a deliverable, not a draft"
    return PAGE.read_text(encoding="utf-8")


class TestThePageIsAWellFormedDerivedDocument:
    def test_it_exists_and_is_under_two_hundred_lines(self, page_text):
        lines = page_text.splitlines()
        assert len(lines) < 200, (
            f"{PAGE.name} is {len(lines)} lines; the brief caps it at 200. "
            "A page too long to read whole is not a replacement for re-deriving.")

    def test_it_says_it_is_regenerated_not_edited(self, page_text):
        head = "\n".join(page_text.splitlines()[:12]).lower()
        assert "regenerate" in head and "do not edit" in head, (
            "the regeneration contract must be stated at the top, or the page will be "
            "hand-edited and become a second authority")

    def test_it_states_that_it_introduces_no_decision(self, page_text):
        head = page_text[:2500].lower()
        assert "no decision" in head or "introduces no decision" in head, (
            "the page must disclaim decision authority — it cites ADRs, it does not make them")

    def test_it_names_the_revision_it_was_generated_at(self, page_text):
        assert re.search(r"\b[0-9a-f]{7}\b", page_text[:2500]), (
            "the page must name the commit it was generated at; without it there is no "
            "way to tell how stale it is")

    @pytest.mark.parametrize("heading", AUTHORITY_HEADINGS)
    def test_each_authority_has_a_section(self, page_text, heading):
        assert heading in page_text, f"missing authority section: {heading}"

    def test_each_authority_states_what_it_cannot_decide(self, page_text):
        assert page_text.count("**Cannot decide**") >= 6, (
            "every authority must state what it may NOT decide — that is the half of an "
            "authority boundary that prevents the next F60")

    def test_each_authority_states_its_record_fields(self, page_text):
        assert page_text.count("**Durable record fields**") >= 6, (
            "every authority must name the journaled keys that carry it, or a replay "
            "consumer cannot find them")


class TestEveryPinResolves:
    """Property 1 — a pin is a claim, and a stale claim must fail."""

    def test_there_are_pins_at_all(self, page_text):
        """A vacuous pass is the failure mode this test class must not have."""
        pins = PIN_RE.findall(page_text)
        assert len(pins) >= 30, (
            f"only {len(pins)} pins found — either the page lost its pins or the pin "
            "grammar drifted; both make the guard vacuous")

    def test_every_authority_section_carries_its_own_pins(self, page_text):
        """The floor is *structural*, not a magic count: each of the six authority
        sections must pin both its owner and at least one record field, or the claim
        "every claim is pinned" is false for that section."""
        sections = re.split(r"^### 1\.\d ", page_text, flags=re.M)[1:]
        assert len(sections) == 6, (
            f"expected 6 authority sections, found {len(sections)}")
        thin = [i + 1 for i, body in enumerate(sections) if len(PIN_RE.findall(body)) < 2]
        assert not thin, (
            f"authority section(s) {thin} carry fewer than two pins — an unpinned claim "
            "is a finding, not a claim")

    def test_every_pin_names_a_real_line(self, page_text):
        broken = []
        for path, start, end in PIN_RE.findall(page_text):
            f = REPO / path
            if not f.exists():
                broken.append(f"{path}:{start} — no such file")
                continue
            lines = f.read_text(encoding="utf-8").splitlines()
            hi = int(end) if end else int(start)
            if int(start) < 1 or hi > len(lines):
                broken.append(f"{path}:{start}-{end or start} — out of range "
                              f"(file has {len(lines)} lines)")
                continue
            if not lines[int(start) - 1].strip():
                broken.append(f"{path}:{start} — the pinned line is blank")
        assert not broken, "stale pins in CURRENT_AUTHORITIES.md:\n  " + "\n  ".join(broken)

    def test_pins_are_fully_qualified(self, page_text):
        """A bare `:123` shorthand cannot be checked, so it is not a pin."""
        shorthand = [m for m in re.findall(r"`[^`]*:[0-9]+[^`]*`", page_text)
                     if not re.match(r"`[A-Za-z0-9_./-]+\.py:[0-9]", m)]
        assert not shorthand, (
            "unqualified pin shorthands cannot be verified mechanically:\n  "
            + "\n  ".join(shorthand))

    def test_every_pin_names_the_symbol_it_claims(self, page_text):
        """Property 1, strengthened — the check the test's name already claimed.

        The check above asserts a pin points at a **non-blank** line. That is weaker than
        the claim: a pin is a claim that a *specific line says a specific thing*, and a
        stale pin that lands on unrelated code passes a non-emptiness test. **Measured:
        when `runtime.py` moved, it caught one stale pin of five, and four were found by
        reading** (ADR-0053 §6, and again in ADR-0054).

        So the assertion is on the **content**: the window `±PIN_WINDOW` around the pin
        must contain an identifier the page's own prose names on that line. The page
        writes `symbol` … (`path:line`), so the other backticked spans on the line are the
        expectation set.

        Two boundaries, both stated rather than silent:

        * **The window is a window.** A pin that drifts by one or two lines is not stale;
          one that drifts further is, and fails.
        * **A line with no other backticked span is not content-checkable** — a §3 matrix
          row names its result in plain prose, not a symbol. Those pins are counted
          separately, and the check asserts enough of them ARE checkable, so it cannot
          quietly become vacuous.
        """
        # Identifiers that come from the pin PATHS themselves are noise — `wisp`, `core`,
        # `goal` and `py` appear in almost any line of almost any file.
        path_noise = {m for path, _s, _e in PIN_RE.findall(page_text)
                      for m in IDENT_RE.findall(path)}

        checkable = 0
        checked = 0
        broken = []
        for page_line, raw in enumerate(page_text.splitlines(), 1):
            pins = PIN_RE.findall(raw)
            if not pins:
                continue
            spans = [s for s in re.findall(r"`([^`]+)`", raw)
                     if not PIN_RE.fullmatch(f"`{s}`")]
            expected = {m for s in spans for m in IDENT_RE.findall(s)} - path_noise
            for path, start, end in pins:
                if not expected:
                    continue                      # not content-checkable; counted below
                checkable += 1
                f = REPO / path
                if not f.exists():
                    continue                      # the existence test reports this
                lines = f.read_text(encoding="utf-8").splitlines()
                lo = int(start) - 1
                # A RANGE pin claims its whole span, so the whole span is the window;
                # a single-line pin gets `±PIN_WINDOW`. Checking only the start of a
                # range would call a range stale whose content is one line below it.
                hi = (int(end) - 1) if end else lo
                window = " ".join(lines[max(0, lo - PIN_WINDOW):hi + PIN_WINDOW + 1])
                if any(name in window for name in expected):
                    checked += 1
                else:
                    broken.append(
                        f"{path}:{start} — none of {sorted(expected)} appears within "
                        f"±{PIN_WINDOW} lines (page line {page_line})")

        assert checkable >= 15, (
            f"only {checkable} pins are content-checkable — the page's pin format drifted "
            "and this check has gone vacuous; the non-emptiness test above would still "
            "pass, which is exactly the weakness this test exists to close")
        assert not broken, (
            f"{len(broken)} pin(s) name a symbol that is not where the page says:\n  "
            + "\n  ".join(broken))
        assert checked == checkable, (
            f"{checked}/{checkable} content-checkable pins resolved — a pin was skipped")


class TestTheMatrixIsTheArbiter:
    """Property 2 — the table is reproduced from the code, not paraphrased."""

    def test_the_matrix_lists_every_row_the_page_claims(self, page_text):
        """Each row must exist **and name its own result**.

        Checking only that row N exists is not enough: it passes when the page's *result*
        column is falsified. Found by a mutation probe that flipped row 6's result to
        `GOAL_UNVERIFIED` and was **not** caught — the probe is why the result cell is
        parsed here rather than merely the row number.
        """
        for row, result in EXPECTED_MATRIX:
            m = re.search(rf"^\|\s*{row}\s*\|([^|]*)\|([^|]*)\|", page_text, re.M)
            assert m, f"§3 has no row {row} — the matrix must be total"
            assert result in m.group(2), (
                f"§3 row {row} must name `{result}` in its Result column, "
                f"but it reads {m.group(2).strip()!r}")

    def test_the_matrix_matches_goal_precedence(self):
        """The page's rows are `goal.PRECEDENCE`'s rows, in the same order."""
        from wisp.core.goal import PRECEDENCE

        assert [r[0] for r in PRECEDENCE] == [r for r, _ in EXPECTED_MATRIX], (
            "goal.PRECEDENCE's row numbers have moved away from the page's matrix")

    def test_the_matrix_matches_derive_goal_state(self):
        """The page's `# → result` mapping is what the arbiter actually returns.

        This is the assertion that makes the page a *projection* of the authority rather
        than a description of it: each row is driven through the real function with the
        inputs that select it, and the result must be the row's stated result.
        """
        from wisp.core.goal import GoalState, TerminalOutcome, derive_goal_state

        # (row, kwargs that select that row, expected state)
        selectors = [
            (1, dict(terminal_outcome=TerminalOutcome.SUCCEEDED, cancelled=True),
             GoalState.CANCELLED),
            (2, dict(terminal_outcome=TerminalOutcome.SUCCEEDED, escalated=True),
             GoalState.ESCALATED_TO_HUMAN),
            (3, dict(terminal_outcome=TerminalOutcome.SUCCEEDED, acceptance_verdict="fail"),
             GoalState.GOAL_FAILED),
            (4, dict(terminal_outcome=TerminalOutcome.FAILED, acceptance_verdict=None),
             GoalState.GOAL_FAILED),
            (5, dict(terminal_outcome=TerminalOutcome.SUCCEEDED, acceptance_verdict="pass",
                     stagnating=True), GoalState.GOAL_STAGNATED),
            (6, dict(terminal_outcome=TerminalOutcome.SUCCEEDED, acceptance_verdict="pass"),
             GoalState.GOAL_MET),
            (7, dict(terminal_outcome=TerminalOutcome.SUCCEEDED, acceptance_verdict=None),
             GoalState.GOAL_UNVERIFIED),
        ]
        stated = dict(EXPECTED_MATRIX)
        for row, kwargs, expected in selectors:
            got = derive_goal_state(**kwargs)
            assert got is expected, (
                f"row {row}: the page says {stated[row]}, the arbiter returns {got} "
                f"for {kwargs}")
            # The page names the enum MEMBER (`GOAL_MET`), not its value (`goal_met`);
            # compare the member name so the two cannot drift in casing either.
            assert got.name == stated[row], (
                f"row {row}: the page names {stated[row]!r}, the arbiter returns "
                f"{got.name!r} for {kwargs}")

    def test_row_zero_is_frozen(self):
        """Row 0 is the one row with no input selector — it is a *prior* state."""
        from wisp.core.goal import GoalState, TerminalOutcome, derive_goal_state

        frozen = derive_goal_state(
            terminal_outcome=TerminalOutcome.FAILED,
            already_recorded=GoalState.GOAL_MET)
        assert frozen is GoalState.GOAL_MET, (
            "row 0 no longer freezes a recorded state — a later observation could "
            "rewrite history")

    def test_the_page_marks_the_cell_f60_moved(self, page_text):
        """The brief requires the moved combination to be marked, not merely implied."""
        assert "F60" in page_text, "the page must mark the combination F60 moved"
        assert "FAILED" in page_text and "GOAL_MET" in page_text, (
            "the page must name the fatal+PASS cell that moved to GOAL_MET")

    def test_the_page_records_its_findings_and_their_disposition(self, page_text):
        """Every finding the page lists must be marked DECIDED (with an ADR) or OPEN.

        This replaces an earlier assertion that the page contains the phrase *"could not
        pin"*. That phrase described the page's state **before ADR-0049** decided the two
        claims it had recorded, so the assertion went red when the page was correctly
        regenerated — **the guard had pinned the old state, not the property.** The
        property is: the page may not carry an undated, undispositioned finding.

        The disposition group is `[^*]*`, not `[^*]+`, so a finding that has **lost** its
        disposition still matches and is then rejected. A mutation probe found the
        `+`-form passing vacuously: an emptied disposition made the regex skip the finding,
        and an empty finding list satisfies a `for` loop.
        """
        assert "## 5." in page_text, (
            "the page must keep a section for its own findings")
        section = page_text.split("## 5.", 1)[1].split("\n## ", 1)[0]
        findings = re.findall(r"\*\*(F-\d+) — ?([^*]*)\*\*", section)
        assert len(findings) >= 2, (
            f"only {len(findings)} findings parsed from §5 — the loop below would pass "
            "vacuously; either the section lost its findings or the finding format moved")
        for fid, disposition in findings:
            assert "DECIDED" in disposition or "OPEN" in disposition, (
                f"{fid} is neither DECIDED nor OPEN — a finding must carry its "
                f"disposition, or the page is asserting something it has not resolved: "
                f"{disposition.strip()!r}")
            if "DECIDED" in disposition:
                assert re.search(r"ADR-\d{4}", disposition), (
                    f"{fid} claims to be DECIDED with no ADR cited — a decision "
                    "without a record is not a decision")

    def test_the_page_does_not_re_argue_a_decision(self, page_text):
        """A derived page cites a decision; re-arguing it makes a second authority."""
        section = page_text.split("## 5.", 1)[1].split("\n## ", 1)[0]
        assert "Alternatives rejected" not in section, (
            "§5 is re-arguing a decision — that belongs in the ADR, not on a derived page")


class TestTheSixAuthoritiesAreTheOnesTheChainNames:
    """The owners named in the page are the owners the code actually has."""

    @pytest.mark.parametrize("owner,module,attr", [
        ("turn predicate", "wisp.core.goal", "terminal_outcome_from_evidence"),
        ("acceptance verdict", "wisp.core.acceptance", "evaluate"),
        ("progress verdict", "wisp.core.progress", "evaluate_progress"),
        ("goal state", "wisp.core.goal", "derive_goal_state"),
    ])
    def test_the_named_owner_exists(self, owner, module, attr):
        import importlib

        mod = importlib.import_module(module)
        assert callable(getattr(mod, attr, None)), (
            f"the page names {module}.{attr} as the owner of the {owner}, but it is "
            "not callable")

    def test_the_ladder_state_owner_exists_and_is_named_ladder_state(self):
        from wisp.core.recovery import RecoveryLadder

        assert hasattr(RecoveryLadder, "ladder_state"), (
            "the page names RecoveryLadder.ladder_state (ADR-0044 R6); the attribute is gone")
        assert not hasattr(RecoveryLadder, "terminal_outcome"), (
            "`terminal_outcome` is back on the ladder — ADR-0044 R6's rename was undone, "
            "and the page names the wrong owner")

    def test_the_stream_guard_owner_exists(self):
        from wisp.core.provider_stream import guarded_provider_stream

        assert callable(guarded_provider_stream), (
            "the page names guarded_provider_stream as the stream-state owner")
