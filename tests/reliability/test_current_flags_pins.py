"""`CURRENT_FLAGS.md` is a *derived* document — guard the derivation.

`CONTEXT.md` ADR-0002 states *"one rollback flag per concern, read via `getattr` with a safe
default"*. The corpus now has more than a dozen flags and **no page stated which are on by
default, where each is read, or which depend on which** — so anyone asking *"what does
`WISP_ACCEPTANCE_GATE` default to and what does it depend on?"* had to reconstruct the answer
from `config.py`, the ADR that introduced it, and the guard that pins it. That reconstruction
is where drift re-enters.

Seven properties, each independently falsifiable:

1. **The page declares itself derived.**
2. **The register is total** — every `bool` setting in `WispConfig`'s schema has a row, plus
   the three switches read from the environment. That is *mechanical*, so it can be asserted
   rather than trusted, and it means **adding a flag without regenerating fails here**.
3. **Every `default` is the schema's** — read from `wisp.config.get_schema()`, the production
   path, not from prose. Prose goes stale; the schema is the artifact.
4. **Every `env` is the schema's `env_var`.**
5. **Every `read_at` resolves** — the file exists, the line is in range, and the window around
   it names the flag (by setting name, env var name, or the constant holding the env var).
6. **The reading rule is stated, and its departures are recorded as findings.**
7. **The page is reproducible from its generator** — the strongest property, and what makes
   the "derived" declaration true rather than aspirational.

Non-vacuity was checked by breaking each property in turn and confirming the corresponding
test fails.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PAGE = REPO / "CURRENT_FLAGS.md"
GENERATOR = REPO / "scripts" / "derive_current_flags.py"

ROW_RE = re.compile(
    r"^\|\s*`([a-z_]+)`\s*\|"
    r"\s*`(WISP_[A-Z0-9_]+)`\s*\|"
    r"\s*\*\*(ON|OFF)\*\*\s*\|"
    r"([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|\s*$", re.M)
READ_AT_RE = re.compile(r"`([A-Za-z0-9_./-]+\.py):(\d+)`")


@pytest.fixture(scope="module")
def page_text() -> str:
    assert PAGE.exists(), f"{PAGE.name} is missing — it is a deliverable, not a draft"
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rows(page_text):
    found = ROW_RE.findall(page_text)
    assert found, (
        "no rows parsed — either the register is gone or the row grammar drifted; every "
        "check below would pass vacuously")
    return found


@pytest.fixture(scope="module")
def schema() -> dict:
    from wisp.config import get_schema

    return get_schema()


def _generator():
    spec = importlib.util.spec_from_file_location("derive_current_flags", GENERATOR)
    assert spec and spec.loader, f"cannot load {GENERATOR}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestThePageIsAWellFormedDerivedDocument:
    def test_it_says_it_is_regenerated_not_edited(self, page_text):
        head = "\n".join(page_text.splitlines()[:8]).lower()
        assert "regenerate" in head and "do not edit" in head, (
            "the regeneration contract must be stated at the top")

    def test_it_states_that_it_introduces_no_decision(self, page_text):
        assert "introduces no decision" in page_text[:2500].lower(), (
            "the page must disclaim decision authority")

    def test_it_names_the_revision_it_was_generated_at(self, page_text):
        assert re.search(r"\b[0-9a-f]{7}\b", page_text[:2500]), (
            "the page must name the commit it was generated at")

    def test_it_points_at_its_sibling_registers(self, page_text):
        head = page_text[:2500]
        for sibling in ("CURRENT_AUTHORITIES.md", "CURRENT_FINDINGS.md",
                        "CURRENT_OPEN_ITEMS.md"):
            assert sibling in head, f"the banner does not name {sibling}"

    def test_it_says_the_default_is_read_from_config(self, page_text):
        assert "read from `wisp/config.py`" in page_text[:2500], (
            "the page must state that the default comes from the artifact, not from prose")


class TestTheRegisterIsTotal:
    """Property 2 — mechanical, and the check that catches a flag added without regenerating."""

    def test_there_is_a_floor_on_the_row_count(self, rows):
        assert len(rows) >= 20, (
            f"only {len(rows)} rows parsed — a register that can shrink without failing is "
            "not a register")

    def test_every_bool_setting_in_config_has_a_row(self, rows, schema):
        bools = {k for k, v in schema.items() if v.get("type") is bool}
        stated = {r[0] for r in rows}
        missing = sorted(bools - stated)
        assert not missing, (
            f"`WispConfig` has bool setting(s) {missing} with no row — either regenerate "
            "(`env -u PYTHONPATH .venv/bin/python scripts/derive_current_flags.py`) or the "
            "flag was added without a row, which is the drift this page exists to catch")

    def test_the_env_only_switches_have_rows(self, rows):
        stated = {r[0] for r in rows}
        for name in ("criteria_strict_derivation", "criteria_structured_declaration",
                     "ws_auto_approve"):
            assert name in stated, (
                f"{name} is read from `os.environ` and is not a `WispConfig` field, so the "
                "schema check above cannot see it; it must be listed explicitly")

    def test_no_row_names_a_setting_that_does_not_exist(self, rows, schema):
        env_only = {"criteria_strict_derivation", "criteria_structured_declaration",
                    "ws_auto_approve"}
        bools = {k for k, v in schema.items() if v.get("type") is bool}
        stray = sorted({r[0] for r in rows} - bools - env_only)
        assert not stray, (
            f"row(s) {stray} name neither a `WispConfig` bool setting nor an env-only switch")

    def test_names_are_unique(self, rows):
        names = [r[0] for r in rows]
        dupes = sorted({n for n in names if names.count(n) > 1})
        assert not dupes, f"the register repeats {dupes}"


class TestEveryDefaultIsTheSchemas:
    """Property 3 — the default is read from the artifact, not from prose."""

    def test_every_default_matches_config(self, rows, schema):
        wrong = []
        for name, _env, default, *_rest in rows:
            if name not in schema:
                continue                      # env-only; the schema check above covers it
            actual = schema[name].get("default")
            if (default == "ON") is not bool(actual):
                wrong.append(f"{name}: the page says {default}, config.py says {actual}")
        assert not wrong, (
            "the page's defaults are not the schema's — regenerate:\n  " + "\n  ".join(wrong))

    def test_both_defaults_are_represented(self, rows):
        """A page where every flag is ON (or OFF) passes the check above vacuously."""
        values = {r[2] for r in rows}
        assert values == {"ON", "OFF"}, (
            f"the register states only {values} — a real register has both")

    def test_every_env_matches_config(self, rows, schema):
        wrong = []
        for name, env, _default, *_rest in rows:
            if name not in schema:
                continue
            actual = schema[name].get("env_var")
            if actual != env:
                wrong.append(f"{name}: the page says {env}, config.py says {actual}")
        assert not wrong, "the page's env names are not the schema's:\n  " + "\n  ".join(wrong)


class TestEveryReadSiteResolves:
    """Property 5 — a read site is a claim that a specific line reads the flag."""

    def test_every_read_at_is_a_path_and_line_or_a_stated_absence(self, rows):
        bad = [r[0] for r in rows if r[3].strip() != "—" and not READ_AT_RE.search(r[3])]
        assert not bad, (
            f"row(s) {bad} carry a `read_at` that is neither a `path:line` nor `—`; a "
            "location that cannot be checked is not a location")

    def test_enough_rows_pin_a_read_site(self, rows):
        n = sum(1 for r in rows if r[3].strip() != "—")
        assert n >= 15, (
            f"only {n} rows pin a read site — the rest are `—` and this class would be "
            "vacuous")

    def test_every_read_site_exists_and_names_the_flag(self, rows):
        bad = []
        for name, env, _default, read_at, *_rest in rows:
            for path, num in READ_AT_RE.findall(read_at):
                f = REPO / path
                if not f.exists():
                    bad.append(f"{name}: {path} does not exist")
                    continue
                lines = f.read_text(encoding="utf-8").splitlines()
                n = int(num)
                if not (1 <= n <= len(lines)):
                    bad.append(f"{name}: {path}:{n} is out of range ({len(lines)} lines)")
                    continue
                window = " ".join(lines[max(0, n - 3):n + 2])
                if not any(t in window for t in (name, env, env.removeprefix("WISP_"))):
                    bad.append(
                        f"{name}: {path}:{n} names neither the setting nor `{env}` within "
                        "±3 lines — stale pin")
        assert not bad, "stale read sites:\n  " + "\n  ".join(bad)

    def test_every_read_site_is_a_real_module_in_the_package(self, rows):
        for _name, _env, _d, read_at, *_rest in rows:
            for path, _num in READ_AT_RE.findall(read_at):
                assert path.startswith("wisp/"), (
                    f"read site {path} is outside the package — a flag read from a script or "
                    "a test is not a production read site")


class TestEveryTripwireNamesARealTest:
    """The page's `tripwire` column — a pin that names a nonexistent test is a claim with
    no instrument."""

    def test_enough_rows_carry_a_tripwire(self, rows):
        n = sum(1 for r in rows if r[7].strip() != "—")
        assert n >= 10, (
            f"only {n} rows carry a tripwire — the corpus has a guard for most flags; either "
            "the column drifted or the page lost its pins, and this class would be vacuous")

    def test_every_named_tripwire_exists(self, rows):
        bad = []
        for name, _env, _d, _ra, _g, _dep, _adr, trip in rows:
            trip = trip.strip()
            if trip == "—":
                continue
            path = trip.split("::", 1)[0]
            if not (REPO / path).exists():
                bad.append(f"{name}: {path} does not exist")
        assert not bad, "rows name tripwires that do not exist:\n  " + "\n  ".join(bad)


class TestTheReadingRuleIsStatedAndItsDeparturesRecorded:
    """Property 6 — the rule, and the measured places the tree departs from it."""

    def test_the_page_states_the_rule(self, page_text):
        section = page_text.split("## (a) The reading rule", 1)
        assert len(section) == 2, "the page has no §(a) stating the reading rule"
        body = section[1].split("\n## ", 1)[0]
        for part in ("One flag per concern", "consumption site", "environment"):
            assert part in body, (
                f"§(a) does not state the rule's part {part!r} — ADR-0002 has three parts and "
                "the page must state them as the ADR does")

    def test_it_cites_adr_0002_as_the_authority(self, page_text):
        """The citation must be **where the rule is stated**, not anywhere in §(a).

        The weaker form — `"ADR-0002" in body` — passed on a mutation that deleted the
        rule's own attribution, because §(a) mentions ADR-0002 again in its departures.
        A citation test that any mention satisfies is not testing the attribution.
        """
        body = page_text.split("## (a) The reading rule", 1)[1].split("\n## ", 1)[0]
        head = body[:400]
        assert "ADR-0002" in head, (
            "§(a)'s opening does not attribute the rule to ADR-0002 — the rule is the ADR's, "
            "not the page's, and the attribution must be where the rule is stated")

    def test_it_records_the_env_only_departure(self, page_text):
        body = page_text.split("## (a) The reading rule", 1)[1].split("\n## ", 1)[0]
        for name in ("criteria_strict_derivation", "criteria_structured_declaration",
                     "ws_auto_approve"):
            assert name in body, (
                f"§(a) must record that {name} is read from `os.environ` and not via "
                "`getattr(config, …)` — that is a departure from ADR-0002's part 2")

    def test_it_records_that_the_briefs_paraphrase_is_wrong(self, page_text):
        section = page_text.split("## §Findings", 1)
        assert len(section) == 2, "the page has no §Findings section"
        body = section[1]
        assert "read once" in body and "consumption site" in body, (
            "§Findings must record that the brief's paraphrase ('read once, at the "
            "composition point') is not ADR-0002's rule ('read at the consumption site'), "
            "because under the paraphrase two legitimate flags become violations")

    def test_it_records_the_flags_the_brief_names_that_do_not_exist(self, page_text):
        body = page_text.split("## §Findings", 1)[1]
        assert "verification_gate" in body and "graph_mutation" in body, (
            "§Findings must record that two of the brief's flag names appear nowhere in "
            "`wisp/` — the brief's claims are hypotheses")


class TestTheInteractionMatrix:
    """Property 6b — every interaction cites the ADR that states it."""

    def test_the_matrix_exists_and_has_a_floor(self, page_text):
        section = page_text.split("## (b) The interaction matrix", 1)
        assert len(section) == 2, "the page has no §(b) interaction matrix"
        rows = [l for l in section[1].splitlines() if l.startswith("| `")]
        assert len(rows) >= 5, (
            f"only {len(rows)} interactions listed — the corpus states at least five")

    def test_every_interaction_cites_an_adr(self, page_text):
        section = page_text.split("## (b) The interaction matrix", 1)[1]
        body = section.split("\n## ", 1)[0]
        rows = [l for l in body.splitlines() if l.startswith("| `")]
        assert rows, "floor: the matrix has no rows"
        bad = [l for l in rows if not re.search(r"\*\*ADR-\d{4}\*\*", l)]
        assert not bad, (
            "interaction row(s) with no ADR cited — an interaction nobody decided is a "
            "finding, not a row:\n  " + "\n  ".join(b[:90] for b in bad))

    def test_every_interaction_names_two_real_flags(self, page_text, rows):
        section = page_text.split("## (b) The interaction matrix", 1)[1]
        body = section.split("\n## ", 1)[0]
        known = {r[0] for r in rows}
        for line in [l for l in body.splitlines() if l.startswith("| `")]:
            names = re.findall(r"`([a-z_]+)`", line)
            assert len(names) >= 2, f"an interaction row names fewer than two flags: {line[:80]}"
            unknown = [n for n in names[:2] if n not in known]
            assert not unknown, f"an interaction names unknown flag(s) {unknown}"


class TestThePageIsReproducibleFromItsGenerator:
    """The strongest property — the page is a projection, not a hand-typed table."""

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
            "CURRENT_FLAGS.md does not match what its generator produces — the page has been "
            "hand-edited, or the data table moved without regenerating. Run:\n"
            "  env -u PYTHONPATH .venv/bin/python scripts/derive_current_flags.py")

    def test_the_generator_refuses_an_unsound_table(self):
        mod = _generator()
        assert mod._check() == [], "the generator reports a broken table"

    def test_the_generator_reads_defaults_from_the_schema(self):
        """The derivation's *observation point* must be the artifact, not the table."""
        mod = _generator()
        bools = mod._bools()
        assert len(bools) >= 20, (
            f"the generator sees {len(bools)} bool settings — it is not reading the schema")
        # The table must agree with what the schema says *now*, which is what makes a
        # hand-edit of a default fail.
        for name, _env, default, *_r in mod.ROWS:
            if name in bools:
                assert (default == "ON") is bool(bools[name].get("default")), (
                    f"{name}'s default in the table is not the schema's")
