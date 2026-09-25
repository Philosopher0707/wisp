"""The objective path's two criteria flags, pinned (ADR-0056).

ADR-0050's follow-up 1 asked whether `WISP_CRITERIA_STRICT_DERIVATION` and
`WISP_CRITERIA_STRUCTURED_DECLARATION` compose. ADR-0056 answered **independent**,
and measured the one interaction that exists: a valid declaration is
*authoritative*, so `explain_acceptance` returns early and `strict` has nothing
to withhold — it is recorded and **inert**.

These tests pin that decision. They fail if:

* `strict` becomes **dependent** on the declaration flag (it would silently
  disable ADR-0048's fix for the measured false `GOAL_MET`), or
* a declaration stops **pre-empting** `strict` (the normative reading of R2), or
* `use_declaration=True` without a block stops being a no-op, or
* the two flags stop being read at one composition point and gating two
  separate parameters.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

#: ADR-0048's own MODE A objective — this repository's benchmark task. Its
#: verifier requires `sum_to(5) == 15`, so it plainly requires a green suite,
#: but the prose grammar finds no suite word. On a red baseline the absolute
#: criterion is advisory and the derivation is `UNDETERMINED`.
MODE_A_OBJECTIVE = (
    "totals.py defines sum_to(n) which should sum integers 1..n inclusive, but it is "
    "off by one: sum_to(5) returns 10 instead of 15. Fix the bug in totals.py.")

DECLARATION = "symbol_defined: totals.py::sum_to"


def _declared(objective: str, body: str = DECLARATION) -> str:
    from wisp.core.convergence import DECLARATION_CLOSE, DECLARATION_OPEN
    return f"{DECLARATION_OPEN}\n{body}\n{DECLARATION_CLOSE}\n\n{objective}"


@pytest.fixture()
def ws(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    (root / "totals.py").write_text("def sum_to(n):\n    return 0\n")
    return root


class _InjectedProbe:
    """`CommandProbe` with the subprocess replaced by an injected payload."""

    def __new__(cls, specs, payload):
        from wisp.core.convergence import CommandProbe

        class _P(CommandProbe):
            def _run_command(self, spec, workspace):  # noqa: D102
                return dict(payload), f"{spec.criteria_id}: injected"

        return _P(specs)


def _baseline(ws: pathlib.Path):
    """A red baseline, so the absolute criterion is advisory."""
    from wisp.core.convergence import derive_acceptance

    _, specs = derive_acceptance("", str(ws))
    payload = {"exit": 1, "collected": 3, "failed": 1,
               "output_tail": "3 failed in 0.04s", "inputs_digest": "baseline",
               "inputs_files": 2}
    return _InjectedProbe(specs, payload).measure(str(ws))


def _derive(objective: str, ws: pathlib.Path, *, strict: bool,
            use_declaration: bool):
    from wisp.core.convergence import explain_acceptance

    return explain_acceptance(objective, str(ws), baseline=_baseline(ws),
                              strict=strict, use_declaration=use_declaration)


# ── R1/R3 — the flags are independent ────────────────────────────────────────


def test_strict_alone_withholds_on_the_prose_path(ws):
    """**Measurement 1.** `strict` is not dependent on the declaration flag.

    If this fails, `strict` has become conditional on `use_declaration` — which
    would silently disable ADR-0048's fix for the measured false `GOAL_MET` for
    every caller that does not also enable declarations (ADR-0056 R3).
    """
    off = _derive(MODE_A_OBJECTIVE, ws, strict=False, use_declaration=False)
    on = _derive(MODE_A_OBJECTIVE, ws, strict=True, use_declaration=False)

    assert off.undetermined == (), "floor: the baseline is not producing UNDETERMINED"
    assert on.undetermined, (
        "`strict` no longer withholds on the prose path — ADR-0056 R3's rejection "
        "of the 'dependent' reading rests on this measurement"
    )
    added = {c.criteria_id for c in on.criteria} - {c.criteria_id for c in off.criteria}
    assert added == {"verify:cmd0:requirement_declared"}, (
        f"strict added {added} instead of the one required criterion R2 defines"
    )


def test_declaration_alone_changes_the_derivation(ws):
    """**Measurement 2.** `use_declaration` alone is not inert."""
    off = _derive(_declared(MODE_A_OBJECTIVE), ws, strict=False,
                  use_declaration=False)
    on = _derive(_declared(MODE_A_OBJECTIVE), ws, strict=False,
                 use_declaration=True)

    assert {r[1] for r in off.reasons} == {"undetermined"}
    assert {r[1] for r in on.reasons} == {"declared"}, (
        "a declaration no longer short-circuits the prose grammar (ADR-0050 R1)"
    )
    assert len(on.criteria) < len(off.criteria), (
        "the declared path should produce the declared criteria only"
    )


# ── R2 — the one interaction: a declaration pre-empts strict ─────────────────


def test_a_declaration_pre_empts_strict(ws):
    """**Measurement 3 — the normative reading of R2.**

    With a valid declaration, `strict` is **recorded and inert**: the derivation
    returns early, the prose grammar is not consulted, and `undetermined` is
    empty either way. If this fails, the declaration path has started producing
    `undetermined` entries — i.e. R2's reading is no longer the behaviour, and
    ADR-0056 must be revisited.
    """
    decl_off = _derive(_declared(MODE_A_OBJECTIVE), ws, strict=False,
                       use_declaration=True)
    decl_on = _derive(_declared(MODE_A_OBJECTIVE), ws, strict=True,
                      use_declaration=True)

    assert decl_off.undetermined == (), "floor: the declared path is already undetermined"
    assert decl_on.undetermined == (), (
        "a declaration no longer pre-empts strict — ADR-0056 R2 says it is "
        "authoritative, so there is nothing for strict to withhold"
    )
    assert [c.criteria_id for c in decl_on.criteria] == \
        [c.criteria_id for c in decl_off.criteria], (
        "the criteria set changed when strict was enabled alongside a declaration; "
        "the declaration path must be strict-independent"
    )
    # `strict` is still RECORDED — the residual ADR-0056 names.
    assert decl_on.strict is True, (
        "the derivation no longer records that strict was requested; the residual "
        "ADR-0056 names has changed shape"
    )


def test_use_declaration_without_a_block_is_a_no_op(ws):
    """**Measurement 4.** No declaration in the objective ⇒ the prose grammar runs.

    This is why ADR-0056 R4 could reject the 'composed' reading as *not
    implemented*: enabling declarations does not make a declaration mandatory.
    """
    strict_only = _derive(MODE_A_OBJECTIVE, ws, strict=True, use_declaration=False)
    both = _derive(MODE_A_OBJECTIVE, ws, strict=True, use_declaration=True)

    assert [c.criteria_id for c in both.criteria] == \
        [c.criteria_id for c in strict_only.criteria], (
        "use_declaration=True without a block changed the derivation — it is "
        "documented as a no-op (ADR-0056 R4)"
    )
    assert both.undetermined == strict_only.undetermined


# ── R5/R6 — no widening, and derive_acceptance is untouched ─────────────────


def test_explain_acceptance_signature_is_not_widened():
    """R5 — composition is a *policy*, not a third parameter (ADR-0009)."""
    import inspect

    from wisp.core.convergence import explain_acceptance

    assert list(inspect.signature(explain_acceptance).parameters) == [
        "goal", "workspace", "baseline", "strict", "use_declaration",
    ], "a parameter was added — ADR-0056 R5 forbids naming the composition here"


def test_derive_acceptance_is_unaffected():
    """R6 — frozen signature, `strict=False`, never `use_declaration`."""
    import inspect

    from wisp.core.convergence import derive_acceptance

    assert list(inspect.signature(derive_acceptance).parameters) == [
        "goal", "workspace", "baseline",
    ]
    src = inspect.getsource(derive_acceptance)
    assert "strict=False" in src
    assert "use_declaration" not in src, (
        "derive_acceptance now consults a declaration — its signature is frozen "
        "(ADR-0009) and ADR-0056 R6 says neither flag reaches it"
    )


# ── R8 — the composition point, parsed not scanned ──────────────────────────


def test_both_flags_are_read_once_at_one_composition_point():
    """The read sites, from the AST of `autonomous.py`.

    Pins R1's *shape*: one composition point, two parameters, neither implying
    the other. A string scan would read the comments that describe the flags.
    """
    tree = ast.parse((REPO / "wisp/autonomous.py").read_text(encoding="utf-8"))

    # Find the function that calls explain_acceptance.
    caller = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and getattr(sub.func, "id", "") == \
                    "explain_acceptance":
                caller = node
                break
        if caller is not None:
            break
    assert caller is not None, "explain_acceptance has no caller in autonomous.py"

    reads: dict[str, list[int]] = {}
    keywords: set[str] = set()
    for sub in ast.walk(caller):
        if isinstance(sub, ast.Call):
            name = getattr(sub.func, "id", "")
            if name in ("_strict_derivation_enabled",
                        "_structured_declaration_enabled"):
                reads.setdefault(name, []).append(sub.lineno)
            if name == "explain_acceptance":
                keywords |= {k.arg for k in sub.keywords if k.arg}

    assert set(reads) == {"_strict_derivation_enabled",
                          "_structured_declaration_enabled"}, (
        f"the composition point no longer reads both flags: {sorted(reads)}"
    )
    for name, lines in reads.items():
        assert len(lines) == 1, f"{name} is read {len(lines)} times — R1 says once"
    assert {"strict", "use_declaration"} <= keywords, (
        f"the flags no longer gate separate parameters: {sorted(keywords)}"
    )
    # R1 pins ONE composition point: the two reads sit together, not in
    # different functions or far apart in this one.
    gap = abs(reads["_strict_derivation_enabled"][0]
              - reads["_structured_declaration_enabled"][0])
    assert gap <= 2, (
        f"the two flag reads are {gap} lines apart — ADR-0056 R1 pins them at one "
        "composition point"
    )
