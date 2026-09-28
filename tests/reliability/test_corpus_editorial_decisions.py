"""ADR-0062's editorial decisions hold in the artifacts — one class per rule.

ADR-0062 decided eight findings about the corpus's own shape (F106–F110, F112–F114). A
decision about a document rots the way a decision about code does, so each rule is asserted
against the artifact it governs:

* **R1** (F106) `CURRENT_FINDINGS.md` is canonical; the ledger names it and is not backfilled.
* **R2** (F107) the id collisions are annotated once, and each annotation's pin names its id.
* **R3** (F108) `DECIDED` is a reason, never a state; the register's states are the ledger's.
* **R4** (F109) the reading rule is ADR-0002's; the paraphrase appears only named as a defect.
* **R5** (F110) `verification_gate` / `graph_mutation` are not flags anywhere.
* **R6** (F112) the concurrent-run rule is stated, and both canonical blocks pass `--basetemp`.
* **R7** (F114) a phase report landed since ADR-0062 is in `CONTEXT.md` §13.
* **R8** (F113) every derived page has a committed generator and a reproducibility guard.

How the instrument is held to the corpus's own discipline (`CONTEXT.md` §10): each check has a
**floor** so it cannot pass by finding nothing (F81); Python is read with **AST**, never as text
(F73); the observation point is the **production artifact** — the ledger's own `:41` line, the
live `get_schema()`, the git index — not a copy in this file (F96); and each check fails on a
real violation and stays silent on the next legitimate addition (F92) — e.g. R7 compares against
git history rather than a list of report names, and R8 discovers derived pages by their banner.
"""
from __future__ import annotations

import ast
import pathlib
import re
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
CONTEXT = REPO / "CONTEXT.md"
AGENTS = REPO / "AGENTS.md"
LEDGER = REPO / "WISP_MIGRATION_STATUS.md"
DECISIONS = REPO / "WISP_ARCHITECTURE_DECISIONS.md"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The body under a `## ` heading, up to the next `## ` heading."""
    assert heading in text, f"missing section {heading!r}"
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                          check=True).stdout


# ── R1 ──────────────────────────────────────────────────────────────────────

class TestR1TheRegisterIsCanonicalAndTheLedgerIsNotBackfilled:
    def test_the_ledger_note_names_the_register_and_its_own_range(self):
        text = _read(LEDGER)
        note = [p for p in text.split("\n\n") if "resume here" in p or "canonical register" in p]
        assert note, "floor: the §23 note about the log's split is gone"
        body = " ".join(note)
        assert "CURRENT_FINDINGS.md" in body and "ADR-0062 R1" in body, (
            "the ledger's §23 note must name CURRENT_FINDINGS.md as the canonical register")
        assert "F64–F74" in body, "the note must state its table's actual range, F64–F74"

    def test_the_ledger_is_not_backfilled(self):
        rows = [int(n) for n in re.findall(r"^\| \*\*F(\d+)\*\*", _read(LEDGER), re.M)]
        assert len(rows) >= 40, f"floor: only {len(rows)} ledger finding rows parsed"
        beyond = sorted(n for n in rows if n > 74)
        assert not beyond, (
            f"the ledger gained finding row(s) {beyond} — ADR-0062 R1 decided it is NOT "
            "backfilled; a second home for a status is the defect the register exists to prevent")


# ── R2 ──────────────────────────────────────────────────────────────────────

class TestR2TheIdCollisionsAreAnnotatedOnce:
    @pytest.fixture(scope="class")
    def section_12(self) -> str:
        return _section(_read(CONTEXT), "## 12. Open items")

    def test_section_12_states_the_prefix_rule(self, section_12):
        assert "ITEM-F1" in section_12 and "FIND-F" in section_12, (
            "§12 must state the prose-prefix rule for the F1–F5 collision (ADR-0062 R2)")
        assert section_12.count("Two id namespaces (ADR-0062 R2)") == 1, (
            "the collision is annotated ONCE — a second annotation is a second home")

    def test_the_m4_annotation_names_row_e_and_its_pins_name_m4(self, section_12):
        note = section_12.split("Two id namespaces (ADR-0062 R2)", 1)[1].split("\n\n", 1)[0]
        assert "**`E`**" in note, "the annotation must name row E as the governance layer's row"
        pins = re.findall(r"`([A-Za-z_./]+\.md):(\d+)`", note)
        assert pins, "floor: the M4 annotation carries no path:line pin"
        for path, line in pins:
            cited = _read(REPO / path).splitlines()[int(line) - 1]
            assert re.search(r"\bM4\b", cited), (
                f"{path}:{line} is cited as a meaning of M4 and does not name M4 — "
                "the defect that made ':2041' a third meaning that never existed")

    def test_the_findings_register_records_the_collision(self):
        findings = _section(_read(REPO / "CURRENT_FINDINGS.md"), "## §Findings")
        assert "ITEM-F1" in findings and "FIND-F1" in findings, (
            "CURRENT_FINDINGS.md's §Findings must record the F1–F5 collision once (R2)")


# ── R3 ──────────────────────────────────────────────────────────────────────

LEDGER_VOCAB_RE = re.compile(r"^\*\*Status vocabulary:\*\* (.+)$", re.M)


def _ledger_vocabulary() -> list[str]:
    m = LEDGER_VOCAB_RE.search(_read(LEDGER))
    assert m, "the ledger no longer states its vocabulary line"
    return re.findall(r"`([A-Z ]+)`", m.group(1))


class TestR3DecidedIsAReasonNotAState:
    def test_the_ledger_states_six_words_including_blocked(self):
        vocab = _ledger_vocabulary()
        assert len(vocab) == 6 and "BLOCKED" in vocab and "DECIDED" not in vocab, (
            f"the ledger's vocabulary is {vocab}; ADR-0062 R3 decided six words, BLOCKED kept")

    def test_no_section_12_row_carries_decided_as_its_state(self):
        section = _section(_read(CONTEXT), "## 12. Open items")
        rows = [ln for ln in section.splitlines()
                if ln.startswith("| ") and not ln.startswith("| #") and "---" not in ln[:6]]
        assert len(rows) >= 20, f"floor: only {len(rows)} §12 rows parsed"
        offenders = []
        for row in rows:
            nature = row.rstrip(" |").rsplit(" | ", 1)[-1]
            first = re.match(r"\s*(?:✅\s*)?(?:~~[^~]*~~\s*)?\*\*([^*]+)\*\*", nature)
            if first and re.match(r"(SURVEYED AND )?DECIDED", first.group(1)):
                offenders.append(row[:70])
        assert not offenders, (
            "§12 rows record DECIDED as a state — it is a reason (ADR-0062 R3):\n  "
            + "\n  ".join(offenders))

    def test_the_open_items_register_uses_only_the_ledgers_words(self):
        page = _read(REPO / "CURRENT_OPEN_ITEMS.md")
        states = re.findall(r"^\| \*\*[^*]+\*\* \|[^|]*\| `([A-Z ]+)` \|", page, re.M)
        assert len(states) >= 50, f"floor: only {len(states)} register rows parsed"
        stray = sorted(set(states) - set(_ledger_vocabulary()))
        assert not stray, f"the register uses state word(s) outside the ledger's six: {stray}"

    def test_blocked_when_empty_is_stated_as_empty(self):
        """R3.2 names `BLOCKED`: kept as a state, its emptiness stated — not 'the word is unused'."""
        page = _read(REPO / "CURRENT_OPEN_ITEMS.md")
        states = re.findall(r"^\| \*\*[^*]+\*\* \|[^|]*\| `([A-Z ]+)` \|", page, re.M)
        assert states, "floor"
        if "BLOCKED" in states:
            return
        vocab_row = [ln for ln in page.splitlines() if ln.startswith("| `BLOCKED` |")]
        assert vocab_row and "No item is currently in this state" in vocab_row[0], (
            "BLOCKED has no member and §(a)'s BLOCKED row does not say 'No item is currently in "
            "this state' (ADR-0062 R3.2)")


# ── R4 ──────────────────────────────────────────────────────────────────────

#: The paraphrase, in the two shapes it has circulated in: the brief's sentence, and the
#: rule-form bullet AGENTS.md carried until ADR-0062.
PARAPHRASE_RE = re.compile(
    r"read once, at the composition point|\*\*read a flag once\.\*\*", re.I)
MARKERS = ("paraphrase", "not the rule", "adr-0062 r4", "is wrong", "defect")


def _rule_statements() -> dict[str, str]:
    """Where the corpus states the GENERAL reading rule (a local 'read once' about one named
    flag is a fact about that flag, which R4 explicitly permits)."""
    agents = _read(AGENTS)
    return {
        "AGENTS.md (the three rules)": agents.split("Three rules that are easy to get wrong", 1)[1]
                                             .split("\n### ", 1)[0],
        "CURRENT_FLAGS.md §(a)": _section(_read(REPO / "CURRENT_FLAGS.md"), "## (a)"),
        "CURRENT_FLAGS.md §Findings": _section(_read(REPO / "CURRENT_FLAGS.md"), "## §Findings"),
    }


class TestR4TheReadingRuleIsADR0002s:
    def test_each_statement_of_the_rule_says_consumption_site(self):
        statements = _rule_statements()
        assert len(statements) == 3, "floor"
        for where, text in list(statements.items())[:2]:
            assert "consumption site" in text, (
                f"{where} states the reading rule without ADR-0002's words — "
                "'read at the consumption site' (ADR-0062 R4)")

    def test_the_paraphrase_appears_only_named_as_the_defect(self):
        hits = 0
        for where, text in _rule_statements().items():
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if not PARAPHRASE_RE.search(line):
                    continue
                hits += 1
                near = " ".join(lines[max(0, i - 2):i + 3]).lower()
                assert any(m in near for m in MARKERS), (
                    f"{where}: the paraphrase is stated as the rule, unmarked — "
                    f"{line.strip()[:90]!r} (ADR-0062 R4)")
        assert hits >= 1, (
            "floor: the paraphrase is named nowhere — R4 requires it to be recorded AS the "
            "defect, so the next brief cannot inherit it silently")


# ── R5 ──────────────────────────────────────────────────────────────────────

PHANTOMS = ("verification_gate", "graph_mutation")


class TestR5ThePhantomNamesAreNotFlags:
    def test_no_python_under_wisp_names_them(self):
        """AST, not text (F73): identifiers, attributes, arguments and string constants."""
        files = list((REPO / "wisp").rglob("*.py"))
        assert len(files) >= 300, f"floor: only {len(files)} files under wisp/"
        found = []
        for path in files:
            for node in ast.walk(ast.parse(_read(path))):
                names = []
                if isinstance(node, ast.Name):
                    names.append(node.id)
                elif isinstance(node, ast.Attribute):
                    names.append(node.attr)
                elif isinstance(node, ast.arg):
                    names.append(node.arg)
                elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    names.append(node.name)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    names.extend(re.findall(r"[A-Za-z_]+", node.value))
                found += [f"{path.relative_to(REPO)}:{node.lineno}" for n in names
                          if n.lower() in PHANTOMS or n.upper() in {f"WISP_{p.upper()}" for p in PHANTOMS}]
        assert not found, (
            f"a phantom flag name is back in wisp/: {found} — ADR-0062 R5 decided they are "
            "wrong names and adds no alias")

    def test_the_real_names_are_the_schemas(self):
        import sys

        sys.path.insert(0, str(REPO))
        from wisp.config import get_schema

        schema = get_schema()
        assert len(schema) >= 20, "floor: the schema parsed too few settings"
        for real in ("verification_loop", "task_graph"):
            assert schema.get(real, {}).get("type") is bool, (
                f"{real} is not a bool setting — R5's correction points at a flag that moved")
        assert not set(PHANTOMS) & set(schema), "a phantom name became a setting"

    def test_no_flag_table_lists_them(self):
        tables = {
            "AGENTS.md": _read(AGENTS).split("## Durable-record flags", 1)[1].split("\n## ", 1)[0],
            "CURRENT_FLAGS.md": _section(_read(REPO / "CURRENT_FLAGS.md"), "## The register"),
        }
        for where, text in tables.items():
            rows = re.findall(r"^\| `([a-z_]+)` \|", text, re.M)
            assert len(rows) >= 10, f"floor: {where}'s flag table parsed {len(rows)} rows"
            assert not set(PHANTOMS) & set(rows), f"{where}'s flag table lists a phantom name"


# ── R6 ──────────────────────────────────────────────────────────────────────

def _canonical_block(text: str) -> str:
    """The fenced block that runs both the first and the M4 file of the canonical set.

    Matched by membership, not by which file ends the command: 69bdca0 appended the
    runtime-conformance guards after `test_m4_governance_wiring.py`.
    """
    blocks = re.findall(r"```bash\n(.*?)```", text, re.S)
    hits = [b for b in blocks if "tests/test_m4_governance_wiring.py" in b
            and "test_durable_layer_reachable.py" in b]
    assert len(hits) == 1, f"expected one canonical block, found {len(hits)}"
    return hits[0]


class TestR6MeasurementRunsAreExclusive:
    def test_section_6_states_the_rule_beside_the_shim_row(self):
        section = _section(_read(CONTEXT), "## 6. Environment gotchas")
        rows = [ln for ln in section.splitlines() if ln.startswith("| **")]
        assert len(rows) >= 8, "floor: §6's gotcha table parsed too few rows"
        rule = [r for r in rows if "--basetemp" in r and "concurrent" in r.lower()]
        assert rule and "ADR-0062 R6" in rule[0], (
            "§6 must state the concurrent-run rule and the --basetemp convention (ADR-0062 R6)")
        assert any("shim" in r for r in rows), (
            "the shim row must stay — it is a real hazard for a single run (R6)")

    @pytest.mark.parametrize("doc", [CONTEXT, AGENTS], ids=lambda p: p.name)
    def test_each_canonical_block_passes_basetemp(self, doc):
        assert "--basetemp" in _canonical_block(_read(doc)), (
            f"{doc.name}'s canonical block runs without --basetemp — two concurrent runs race "
            "on the shared temp dir (F112, ADR-0062 R6)")


# ── R7 ──────────────────────────────────────────────────────────────────────

def _adr_0062_commit() -> str:
    out = _git("log", "--format=%H", "--diff-filter=AM", "-S", "## ADR-0062 ",
               "--", "WISP_ARCHITECTURE_DECISIONS.md").split()
    assert out, "ADR-0062's landing commit is not in this history"
    return out[-1]


class TestR7AReportIsIndexedInTheChangeThatCreatesIt:
    def test_every_report_landed_since_adr_0062_is_in_the_index(self):
        """Compared against git history, not a list here — so the next report is checked
        without editing this file (F92), and the pre-ADR history is not re-litigated."""
        since = _adr_0062_commit()
        added = set(_git("log", "--diff-filter=A", "--name-only", "--format=",
                         f"{since}^..HEAD", "--", "PHASE_*.md").split())
        added |= set(_git("diff", "--cached", "--diff-filter=A", "--name-only",
                          "--", "PHASE_*.md").split())
        assert added, "floor: no phase report has landed since ADR-0062 — the check is idle"
        index = _section(_read(CONTEXT), "## 13. Document index")
        missing = sorted(r for r in added if f"`{r}`" not in index)
        assert not missing, (
            f"report(s) {missing} landed after ADR-0062 and are not in CONTEXT.md §13 — "
            "R7: a report enters the index in the change that creates it")


# ── R8 ──────────────────────────────────────────────────────────────────────

def _derived_pages() -> list[pathlib.Path]:
    return sorted(p for p in REPO.glob("*.md")
                  if "DERIVED DOCUMENT" in "\n".join(_read(p).splitlines()[:6]))


class TestR8EveryDerivedPageHasACommittedGenerator:
    def test_there_is_a_floor_of_derived_pages(self):
        pages = _derived_pages()
        assert len(pages) >= 4, f"floor: only {[p.name for p in pages]} declare themselves derived"

    @pytest.mark.parametrize("page", _derived_pages(), ids=lambda p: p.name)
    def test_the_page_has_a_committable_generator_its_banner_names(self, page):
        generator = REPO / "scripts" / f"derive_{page.stem.lower()}.py"
        assert generator.exists(), (
            f"{page.name} says it is derived and has no {generator.relative_to(REPO)} — "
            "ADR-0062 R8: a derived page must have a committed generator (F113)")
        ignored = subprocess.run(["git", "check-ignore", "-q", str(generator.relative_to(REPO))],
                                 cwd=REPO, capture_output=True)
        assert ignored.returncode == 1, f"{generator.name} is git-ignored (F75)"
        banner = _read(page).split("\n---", 1)[0]
        assert f"scripts/{generator.name}" in banner, (
            f"{page.name}'s banner does not name its generator")
        tree = ast.parse(_read(generator))
        funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        assert "render" in funcs, f"{generator.name} has no render() for the guard to compare"

    @pytest.mark.parametrize("page", _derived_pages(), ids=lambda p: p.name)
    def test_the_page_has_a_reproducibility_guard(self, page):
        guard = REPO / "tests" / "reliability" / f"test_{page.stem.lower()}_pins.py"
        assert guard.exists(), f"{page.name} has no guard at {guard.relative_to(REPO)}"
        tests = {n.name for n in ast.walk(ast.parse(_read(guard)))
                 if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
        assert any("reproduc" in t for t in tests), (
            f"{guard.name} has no reproducibility test — R8 requires that regenerating "
            "reproduces the page, or the 'derived' banner is aspirational")
