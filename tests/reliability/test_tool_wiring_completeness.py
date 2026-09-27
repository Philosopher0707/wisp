"""Every tool is complete: implemented, advertised, and risk-classified.

`scripts/tool_wiring_audit.py` found the gap this guards — `rewind` was in `TOOL_IMPLS` and
`TOOL_SCHEMAS` but **absent from `TOOL_RISK_TABLE`**, so `risk_for_tool` returned its default,
`EXEC` — the same class a *nonexistent* tool gets. Three tables must agree about 42 tools, and
nothing was checking that they do.

The default is the dangerous part. `risk_for_tool` cannot fail: an unclassified name silently
becomes `EXEC`. That is *conservative* for a write (over-gated) and would be *unsafe* for nothing —
but it is also **invisible**, which is this repository's named dominant pathology
(`docs/audit-2026-08-24.md:270`: a written-but-unwired control). A tool added without a risk row
gets a class nobody chose.

AST-free: all three tables are importable data, so this reads them directly rather than parsing.
"""
from __future__ import annotations

from wisp.core.contracts import TOOL_RISK_TABLE, ToolRisk
from wisp.tools.registry import TOOL_IMPLS, TOOL_SCHEMAS

#: A floor. Three tables that reached nothing would pass vacuously.
TOOL_FLOOR = 30

#: The classes a tool may declare. `risk_for_tool`'s default is `EXEC`; declaring it explicitly is
#: allowed — what is refused is *not* declaring anything.
DECLARABLE = frozenset(ToolRisk)


def _advertised() -> dict[str, dict]:
    """Schema names, from the OpenAI function-calling shape `TOOL_SCHEMAS` uses."""
    out = {}
    for s in TOOL_SCHEMAS:
        fn = s.get("function", s) if isinstance(s, dict) else {}
        if isinstance(fn, dict) and fn.get("name"):
            out[fn["name"]] = s
    return out


def test_the_tables_are_not_empty():
    """Non-vacuity: three empty tables would satisfy every check below."""
    assert len(TOOL_IMPLS) >= TOOL_FLOOR, f"only {len(TOOL_IMPLS)} impls — the import drifted"
    assert len(TOOL_SCHEMAS) >= TOOL_FLOOR, f"only {len(TOOL_SCHEMAS)} schemas"
    assert len(TOOL_RISK_TABLE) >= TOOL_FLOOR, f"only {len(TOOL_RISK_TABLE)} risk rows"


def test_every_implemented_tool_is_advertised():
    """An impl the model cannot see is a written-but-unwired control."""
    missing = sorted(set(TOOL_IMPLS) - set(_advertised()))
    assert not missing, (
        f"these tools have an implementation but no schema, so the model is never told they "
        f"exist: {missing}")


def test_every_advertised_tool_is_implemented():
    """The worse direction: a schema the dispatcher cannot satisfy is a guaranteed error."""
    unimpl = sorted(set(_advertised()) - set(TOOL_IMPLS))
    assert not unimpl, (
        f"these tools are advertised to the model but have no implementation — every call to one "
        f"is a guaranteed failure: {unimpl}")


def test_every_tool_is_risk_classified():
    """The gap that found this guard. `risk_for_tool` cannot fail — it defaults to EXEC."""
    unclassified = sorted(set(TOOL_IMPLS) - set(TOOL_RISK_TABLE))
    assert not unclassified, (
        f"these tools have no row in `TOOL_RISK_TABLE`, so `risk_for_tool` returns its default "
        f"(`{ToolRisk.EXEC}`) — a class nobody chose, and the same one a nonexistent tool gets: "
        f"{unclassified}")


def test_every_declared_class_is_a_real_class():
    bogus = sorted((n, c) for n, c in TOOL_RISK_TABLE.items() if c not in DECLARABLE)
    assert not bogus, f"`TOOL_RISK_TABLE` declares classes that are not `ToolRisk`: {bogus}"


def test_no_risk_row_names_a_tool_that_does_not_exist():
    """A row for a removed tool is a dead entry that hides a rename."""
    stale = sorted(set(TOOL_RISK_TABLE) - set(TOOL_IMPLS))
    assert not stale, (
        f"these risk rows name tools that are not in `TOOL_IMPLS` — the tool was renamed or "
        f"removed; re-pin rather than leaving a dead entry: {stale}")
