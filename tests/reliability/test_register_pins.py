"""`register.md` is a *derived* document — guard the derivation.

The fifth register, and the last one to get a guard. `CONTEXT.md` ADR-0062 **R8** requires every
page that declares itself derived to have **both** a committed generator and a reproducibility
guard; `register.md` had the generator and the page but not the guard, so a *tracked* test
(`tests/reliability/test_corpus_editorial_decisions.py::TestR8EveryDerivedPageHasACommittedGenerator`)
failed while `register.md` sat untracked in the working tree. This file closes that.

Its generator is the only one of the five that is **stdlib-only** — `python3
scripts/derive_register.py`, no venv and no `env -u PYTHONPATH` — so unlike its siblings this guard
does not shell out to an interpreter.

Six properties, each independently falsifiable:

1. **The page declares itself derived** and names the generator in its banner.
2. **The register is total, both ways** — every exception class an AST walk finds under `wisp/`,
   `wisp_net/` and `agent/` has a row, and every row names one of them. That is *mechanical*, so
   it is asserted rather than trusted, and it means **adding a class without regenerating fails
   here**.
3. **Every `defined_at` pin resolves** — the file exists, the line is in range, and the window
   around it declares `class <name>`. Checked against the file, not against the generator, so a
   bug in the generator's own resolver cannot hide behind it.
4. **The table has a floor** — a scan that reaches nothing passes vacuously.
5. **The generator reports a sound table** — `_check()` is the derivation's own consistency rule.
6. **The page is reproducible from its generator** — the strongest property, and what makes the
   "derived" declaration true rather than aspirational.

Non-vacuity was checked by breaking each property in turn (see the mission report).
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PAGE = REPO / "register.md"
GENERATOR = REPO / "scripts" / "derive_register.py"

#: The exceptions table's header, which is how the table is located without depending on line numbers.
TABLE_HEADER = "| exception | base | role | phase | defined_at |"

ROW_RE = re.compile(
    r"^\|\s*`(?P<name>[A-Za-z_][A-Za-z0-9_]*)`\s*"
    r"\|\s*`(?P<base>[^`]+)`\s*"
    r"\|\s*(?P<role>[a-z-]+)\s*"
    r"\|\s*(?P<phase>[a-z-]+|—)\s*"          # `—` is a real value: an unwired class has no phase
    r"\|\s*`(?P<path>[A-Za-z0-9_./-]+):(?P<line>\d+)`\s*"
    r"\|\s*(?P<raises>\d+)\s*\|"
)

#: A floor for the table. The tree has 47 rows today; a scan that finds nothing must not pass.
ROW_FLOOR = 20


@pytest.fixture(scope="module")
def page_text() -> str:
    assert PAGE.exists(), f"{PAGE.name} is missing — it is a deliverable, not a draft"
    return PAGE.read_text(encoding="utf-8")


def _rows(text: str) -> list[re.Match]:
    """The exceptions table's rows, located by its header rather than by line number.

    **The parse is total, and asserted to be.** The first version of this helper skipped any row
    `ROW_RE` did not match — so a row whose `phase` was `—` instead of a word (an *unwired* class
    has no phase) was silently dropped, and the totality check then reported nine classes as
    "absent from the register" when they were present and merely unparsed. A parser that skips
    what it does not understand cannot be trusted to report what is missing. It now counts the
    table's data rows independently and fails if any of them did not match.
    """
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(TABLE_HEADER)), None)
    assert start is not None, f"the exceptions table's header is gone: {TABLE_HEADER!r}"
    out: list[re.Match] = []
    data_rows = 0
    for ln in lines[start + 2:]:
        if not ln.startswith("|"):
            break
        data_rows += 1
        m = ROW_RE.match(ln)
        if m:
            out.append(m)
    assert len(out) == data_rows, (
        f"{data_rows - len(out)} of {data_rows} rows of the exceptions table do not match "
        "ROW_RE. A row this guard cannot parse is a row it silently ignores — widen the pattern "
        "rather than letting the totality check report a phantom absence.")
    return out


def _generator():
    spec = importlib.util.spec_from_file_location("derive_register", GENERATOR)
    assert spec and spec.loader, f"cannot load {GENERATOR}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _classes_by_ast() -> set[str]:
    """Every exception class the tree defines, by AST — **independent of the generator**.

    The same rule the generator documents: a `ClassDef` whose bases name `Error` or `Exception`.
    Written out here rather than imported so that a bug in the generator's scan cannot make the
    totality check agree with a wrong page.
    """
    found: set[str] = set()
    for pkg in ("wisp", "wisp_net", "agent"):
        root = REPO / pkg
        if not root.exists():
            continue
        for py in sorted(root.rglob("*.py")):
            if "tests" in py.parts or "__pycache__" in py.parts:
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and any(
                    "Error" in ast.unparse(b) or "Exception" in ast.unparse(b)
                    for b in node.bases
                ):
                    found.add(node.name)
    return found


class TestThePageIsAWellFormedDerivedDocument:
    def test_it_says_it_is_regenerated_not_edited(self, page_text):
        head = "\n".join(page_text.splitlines()[:8]).lower()
        assert "regenerate" in head and "do not edit" in head, (
            "the regeneration contract must be stated at the top")

    def test_its_banner_names_its_generator(self, page_text):
        assert "scripts/derive_register.py" in page_text.split("\n---", 1)[0], (
            "the banner no longer names the generator the R8 guard looks for")

    def test_it_states_that_it_introduces_no_decision(self, page_text):
        # Normalise the banner before searching: the sentence is wrapped across two quoted
        # lines, so a literal substring search misses it — and the second line's leading `>`
        # survives a naive whitespace collapse. (The first version of this test did exactly
        # that and reported the statement absent when it was there.)
        banner = page_text.split("\n---", 1)[0]
        head = " ".join(ln.lstrip("> ").strip() for ln in banner.splitlines())
        assert "introduces no decision" in head.lower(), (
            "a derived page must say it decides nothing, or it is a second authority")


class TestTheRegisterIsTotal:
    def test_the_table_has_a_floor(self, page_text):
        rows = _rows(page_text)
        assert len(rows) >= ROW_FLOOR, (
            f"only {len(rows)} rows parsed — the table or this parser moved")

    def test_every_defined_class_has_a_row(self, page_text):
        """Totality, direction 1: nothing in the tree is missing from the page."""
        missing = _classes_by_ast() - {m.group("name") for m in _rows(page_text)}
        assert not missing, (
            f"{sorted(missing)} are defined in the tree and absent from the register — "
            "regenerate with `python3 scripts/derive_register.py`")

    def test_every_row_names_a_real_class(self, page_text):
        """Totality, direction 2: nothing in the page is invented."""
        invented = {m.group("name") for m in _rows(page_text)} - _classes_by_ast()
        assert not invented, (
            f"{sorted(invented)} have rows but no definition — a hand-edit, or a class was "
            "removed without regenerating")

    def test_no_row_appears_twice(self, page_text):
        """Keyed on **(name, path)**, not on the name alone.

        A class *name* is not an identity: `SchemaValidationError` is defined in **two**
        modules — `wisp/structured_output.py:37` and `wisp/multi_agent/schema_validator.py:16` —
        and the register lists both, each with its own `defined_at`. That is why the table has
        47 rows and 46 distinct names, and it is recorded rather than asserted away: the
        duplication is a fact about the code, and a guard that failed on it would be failing on
        a legitimate state.
        """
        keys = [(m.group("name"), m.group("path")) for m in _rows(page_text)]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        assert not dupes, f"duplicated rows: {dupes}"


class TestThePinsResolve:
    def test_every_defined_at_pin_resolves(self, page_text):
        """Read the *file*, not the generator, so a resolver bug cannot hide here."""
        checked = 0
        for m in _rows(page_text):
            name, rel, line = m.group("name"), m.group("path"), int(m.group("line"))
            path = REPO / rel
            assert path.exists(), f"{name}: defined_at names {rel}, which does not exist"
            lines = path.read_text(encoding="utf-8").splitlines()
            assert 1 <= line <= len(lines), f"{name}: {rel}:{line} is out of range"
            window = "\n".join(lines[max(0, line - 3):line + 2])
            assert f"class {name}" in window, (
                f"{name}: {rel}:{line} does not declare `class {name}` — the pin has drifted")
            checked += 1
        assert checked >= ROW_FLOOR, "floor: the pin check reached almost nothing"


class TestTheDerivationIsSound:
    def test_the_generator_reports_a_sound_table(self):
        mod = _generator()
        defined, raises, catches = mod._scan()
        assert mod._check(defined, raises, catches) == [], (
            "the generator reports a broken table")

    def test_regenerating_reproduces_the_page(self, page_text):
        """R8's requirement, and the property that makes 'derived' true rather than aspirational.

        **Only the commit is normalised** — a page cannot record the commit that contains it.
        Everything else must match exactly, including the "named by no test file" figure and its
        list.

        **This test earned its place by failing.** Its first run, in a clean checkout, disagreed
        with a page that was correct: `derive_register.py` walked the working *directory*, so the
        `tripwire` column, the untested figure and its list all moved when six untracked test files
        were present or absent. R8's promise — *regenerating reproduces the page* — was therefore
        **false for this register**: a page derived from a developer's uncommitted work cannot be
        reproduced from a clone, and CI would have failed on it. The generator now reads the
        **tracked** tree (`_tracked()`, `git ls-files`), and this assertion is what keeps it that
        way.
        """
        fresh = _generator().render()
        norm = lambda t: re.sub(r"`[0-9a-f]{7,40}`", "`<sha>`", t)  # noqa: E731
        assert norm(fresh) == norm(page_text), (
            "register.md does not match what its generator produces — the page has been "
            "hand-edited, or the tree moved without regenerating. Run:\n"
            "  python3 scripts/derive_register.py")
