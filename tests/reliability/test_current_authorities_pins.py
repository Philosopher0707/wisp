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
4. **The page is reproducible from its committed generator** (ADR-0062 R8, closing F113's
   build half). `scripts/derive_current_authorities.py` renders it; regenerating must
   reproduce it byte-for-byte modulo the commit and date it names, the generator must
   refuse a stale pin, a matrix row the code does not return and an unresolved ADR chain,
   and §5 is append-only — emitted unchanged, never losing a finding.

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


# ── the page's PROSE claims, checked mechanically (ADR-0057 §2.3) ───────────
#
# F81 was this page carrying a superseded disposition ("enablement is ADR-0016's
# question and remains NOT_YET_DETERMINABLE") for three ADRs. The `path:line` guard
# could not catch it, because it checks pins and the drift was prose. These four
# properties are the ones a derived page can be held to WITHOUT interpreting meaning:
# a citation must resolve, must not be superseded, and the header's commit and ADR
# range must be consistent with what the page actually cites.
#
# The reversal condition is explicit: if a check here needs to READ for meaning rather
# than PARSE a citation, it is the "pins a state, not a property" class and must not be
# written. Each of the four below parses a citation or a header field.

DECISIONS = REPO / "WISP_ARCHITECTURE_DECISIONS.md"
ADR_HEADING_RE = re.compile(r"^## ADR-(\d{4})\b", re.M)
ADR_CITE_RE = re.compile(r"ADR-(\d{4})")
HEADER_RE = re.compile(
    r"Generated (\d{4}-\d{2}-\d{2}) at `([0-9a-f]{7,40})`.*?"
    r"covers \*\*ADR-0001 … ADR-(\d{4})\*\*", re.S)


def _defined_adrs() -> set[int]:
    return {int(m) for m in ADR_HEADING_RE.findall(DECISIONS.read_text(encoding="utf-8"))}


def _cited_adrs(page_text: str) -> set[int]:
    return {int(m) for m in ADR_CITE_RE.findall(page_text)}


def _superseded_adrs() -> set[int]:
    """Index rows whose Status cell begins SUPERSEDED."""
    out = set()
    for line in DECISIONS.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| (\d{4}) \|", line)
        if not m:
            continue
        cells = line.split("|")
        if len(cells) > 4 and cells[4].strip().upper().startswith("SUPERSEDED"):
            out.add(int(m.group(1)))
    return out


class TestTheProseClaimsAreMechanicallyCheckable:
    def test_every_cited_adr_exists(self, page_text):
        """A citation of an ADR that does not exist is a broken reference."""
        cited = _cited_adrs(page_text)
        assert cited, "floor: the page cites no ADR at all"
        assert len(cited) >= 20, f"the citation set shrank to {len(cited)}"
        dangling = sorted(cited - _defined_adrs())
        assert not dangling, (
            "the page cites ADRs that do not exist: "
            + ", ".join(f"ADR-{d:04d}" for d in dangling)
        )

    def test_no_cited_adr_is_superseded(self, page_text):
        """A superseded decision must not be cited as current authority."""
        superseded = _superseded_adrs()
        cited = _cited_adrs(page_text)
        assert cited, "floor"
        bad = sorted(cited & superseded)
        assert not bad, (
            "the page cites superseded ADRs as current: "
            + ", ".join(f"ADR-{b:04d}" for b in bad)
        )

    def test_the_header_range_covers_every_adr_the_page_cites(self, page_text):
        """**F81's drift, mechanised.**

        The header said it covered ADR-0001 … ADR-0049 while the page cited ADR-0051,
        0053 and 0054 — a stale header on a page whose whole purpose is to state the
        current state. This parses the range and compares it to the citations.
        """
        m = HEADER_RE.search(page_text)
        assert m, (
            "the header no longer states `Generated <date> at `<sha>` … covers "
            "**ADR-0001 … ADR-NNNN**` — the range cannot be checked without it"
        )
        covered = int(m.group(3))
        cited = _cited_adrs(page_text)
        assert cited, "floor"
        beyond = sorted(c for c in cited if c > covered)
        assert not beyond, (
            f"the page cites {', '.join(f'ADR-{b:04d}' for b in beyond)} but its header "
            f"claims to cover only up to ADR-{covered:04d} — regenerate the header"
        )

    def test_the_header_names_a_real_ancestor_commit(self, page_text):
        """The generated-at commit must exist and be an ancestor of HEAD.

        Not `== HEAD`: that would fail on every subsequent commit, which is the
        nuisance class. *Ancestor* is the property — a header naming a commit that is
        not in this history is either a typo or a lie about when it was generated.
        """
        import subprocess

        m = HEADER_RE.search(page_text)
        assert m, "the header no longer names the commit it was generated at"
        sha = m.group(2)
        have = subprocess.run(["git", "cat-file", "-e", sha],
                              cwd=REPO, capture_output=True)
        assert have.returncode == 0, (
            f"the header names `{sha}`, which is not a commit in this repository"
        )
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "HEAD"],
            cwd=REPO, capture_output=True)
        assert ancestor.returncode == 0, (
            f"the header names `{sha}`, which is not an ancestor of HEAD — the page "
            "claims a revision that is not in this history"
        )


# ── property 4 — the page is reproducible from its committed generator ─────
#
# F113: the founding page of the derived-register pattern declared "REGENERATE, DO NOT
# EDIT IN PLACE" and had no generator, so "regeneration" meant by hand — F75's class.
# ADR-0062 R8 makes a committed generator the rule. These tests hold the page to it.

GENERATOR = REPO / "scripts" / "derive_current_authorities.py"
SHA_RE = re.compile(r"`[0-9a-f]{7,40}`")
DATE_RE = re.compile(r"Generated \d{4}-\d{2}-\d{2} at")


def _generator():
    import importlib.util
    import sys

    assert GENERATOR.exists(), (
        f"{GENERATOR.relative_to(REPO)} is missing — ADR-0062 R8 requires a committed "
        "generator for every derived page (F113)")
    spec = importlib.util.spec_from_file_location("derive_current_authorities", GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _section_5(text: str) -> str:
    assert "\n## 5." in text, "the page has no §5"
    return "## 5." + text.split("\n## 5.", 1)[1].split("\n---\n", 1)[0]


def _normalise(text: str) -> str:
    """The commit and the date are the only fields that move without a source change."""
    return DATE_RE.sub("Generated <date> at", SHA_RE.sub("`<sha>`", text))


class TestThePageIsReproducibleFromItsGenerator:
    def test_the_generator_is_not_ignored_by_git(self):
        """F75's failure mode was an instrument `.gitignore` excluded. Committable is the
        property; `git ls-files` would fail on the uncommitted working tree it is written in."""
        import subprocess

        _generator()
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", str(GENERATOR.relative_to(REPO))],
            cwd=REPO, capture_output=True)
        assert ignored.returncode == 1, (
            "the generator is git-ignored — an instrument that cannot be committed is not a "
            "re-runnable measurement (F75)")

    def test_the_banner_names_the_generator(self, page_text):
        banner = page_text.split("\n---", 1)[0]
        assert "scripts/derive_current_authorities.py" in banner, (
            "the banner must name its generator, as the other three registers' banners do")

    def test_regenerating_reproduces_the_page(self, page_text):
        fresh = _generator().render()
        assert _normalise(fresh) == _normalise(page_text), (
            "CURRENT_AUTHORITIES.md does not match what its generator produces — the page "
            "was hand-edited, or a source moved without regenerating. Run:\n"
            "  env -u PYTHONPATH .venv/bin/python scripts/derive_current_authorities.py")

    def test_the_generator_accepts_the_current_tree(self):
        problems = _generator()._check()
        assert problems == [], "the generator refuses the current tree:\n  " + "\n  ".join(
            problems)

    def test_the_generator_refuses_a_stale_pin(self):
        mod = _generator()
        good = mod.render()
        assert mod._pin_problems(good) == [], "floor: the rendered page must pin cleanly"
        stale = good.replace("`wisp/core/provider_stream.py:122`",
                             "`wisp/core/provider_stream.py:1`", 1)
        assert stale != good, "the probe did not change the page — the pin moved"
        assert mod._pin_problems(stale), (
            "a pin moved onto an unrelated line and the generator did not refuse it")

    def test_the_generator_refuses_a_matrix_row_the_code_does_not_return(self):
        mod = _generator()
        from wisp.core import goal

        assert mod._matrix_problems(goal.PRECEDENCE) == [], "floor"
        lying = tuple((n, c, "GOAL_FAILED" if n == 6 else r) for n, c, r in goal.PRECEDENCE)
        assert mod._matrix_problems(lying), (
            "PRECEDENCE was made to disagree with derive_goal_state's row-6 branch and the "
            "generator did not refuse it")

    def test_the_generator_refuses_an_unresolved_adr_chain(self):
        mod = _generator()
        assert mod._chain_problems(mod.AUTHORITIES) == [], "floor"
        # 1.2 cites ADR-0041 R3/R7, which ADR-0043 supersedes. Dropping ADR-0043 leaves a
        # chain that names a superseded clause as current.
        dropped = [(h, o, c, a.replace("**ADR-0043**", "ADR-9043"), f)
                   for h, o, c, a, f in mod.AUTHORITIES]
        assert mod._chain_problems(dropped), (
            "a chain lost the ADR that supersedes one it cites, and the generator accepted it")
        dangling = [(h, o, c, a + " → **ADR-0999**", f) for h, o, c, a, f in mod.AUTHORITIES]
        assert mod._chain_problems(dangling), "a chain cites a non-existent ADR and passed"

    def test_the_matrix_reads_precedence_at_render_time(self, monkeypatch):
        """The observation point is the arbiter's table, not a copy in the generator (F96)."""
        mod = _generator()
        from wisp.core import goal

        moved = tuple((n, c, "GOAL_PROBE" if n == 6 else r) for n, c, r in goal.PRECEDENCE)
        monkeypatch.setattr(goal, "PRECEDENCE", moved)
        row6 = [ln for ln in mod.render_matrix().splitlines() if ln.startswith("| 6 |")]
        assert row6 and "`GOAL_PROBE`" in row6[0], (
            "§3's row 6 did not follow goal.PRECEDENCE — the matrix is transcribed, not "
            "generated")

    def test_the_header_range_is_the_log_extent(self, page_text):
        """F97: the range is the log's extent, read from the log — not a claim someone typed."""
        m = HEADER_RE.search(page_text)
        assert m, "the header's range is not parseable"
        defined = _defined_adrs()
        assert len(defined) >= 60, "floor: the ADR log parsed too few headings"
        assert int(m.group(3)) == max(defined), (
            f"the header covers up to ADR-{int(m.group(3)):04d} but the log runs to "
            f"ADR-{max(defined):04d} — regenerate")


class TestSectionFiveIsAppendOnly:
    """ADR-0062 R8's one permitted exception: §5 is emitted unchanged, never regenerated."""

    def test_section_5_is_the_generators_record_verbatim(self, page_text):
        assert _section_5(page_text).rstrip("\n") == _generator().SECTION_5.rstrip("\n"), (
            "§5 on the page differs from the generator's SECTION_5 — §5 was edited on the "
            "page; append to SECTION_5 in the generator instead")

    def test_no_finding_is_lost_against_the_committed_page(self, page_text):
        """Append-only, as a property: every finding HEAD's page carries is still here, and a
        DECIDED finding stays DECIDED. Comparing against HEAD (not a list in this file) means a
        new finding is a legitimate addition and does not fire this (F92)."""
        import subprocess

        head = subprocess.run(["git", "show", "HEAD:CURRENT_AUTHORITIES.md"], cwd=REPO,
                              capture_output=True, text=True)
        if head.returncode != 0:
            pytest.skip("CURRENT_AUTHORITIES.md is not in HEAD")
        pat = re.compile(r"\*\*(F-\d+) — ?([^*]*)\*\*")
        before = dict(pat.findall(_section_5(head.stdout)))
        now = dict(pat.findall(_section_5(page_text)))
        assert len(before) >= 2, "floor: HEAD's §5 parsed fewer than two findings"
        lost = sorted(set(before) - set(now))
        assert not lost, f"§5 lost finding(s) {lost} — §5 is append-only"
        undecided = sorted(f for f, d in before.items()
                           if "DECIDED" in d and "DECIDED" not in now.get(f, ""))
        assert not undecided, f"finding(s) {undecided} were DECIDED and no longer are"
