#!/usr/bin/env python3
"""Provenance for every contract class and function.

Answers three questions per symbol, because "provenance" needs all three:

  1. **DEFINED** — which module declares it.
  2. **AUTHORISED BY** — the decision or spec that put it there. Read from the module's own
     docstring/comments (an ADR id, a spec path) — never inferred. A symbol whose authorisation
     cannot be found is reported as `—`, which is a finding, not a gap in the tool.
  3. **CONSUMED BY** — production importers and test importers, counted separately. A contract with
     no production consumer is a **frozen seam** (deliberate, per the M1a spec) or a
     **written-but-unwired control** (this repository's named pathology). Which one it is depends on
     whether the spec says it is additive — so the spec's own words are quoted when found.

Two families, and they are NOT the same kind of thing:
  - `wisp/contracts/`   — the M1a freeze. Deliberately additive: the spec's goal is *"Pure addition:
                          no existing producer or consumer changes behavior."*
  - `wisp/core/contracts.py` — the core vocabulary (risk classes, the error taxonomy, session state).
                          Widely consumed; its provenance is the ADRs that introduced each part.

Read-only. This reports; it does not decide.
"""
from __future__ import annotations

import ast
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]

M1A_SPEC = "docs/superpowers/specs/2026-09-04-enterprise-contracts-m1a-design.md"
ADR_RE = re.compile(r"ADR-\d{4}")
SPEC_RE = re.compile(r"[\w./-]*superpowers/(?:specs|plans)/[\w.-]+\.md")


def module_provenance(path: pathlib.Path) -> tuple[list[str], list[str], str]:
    """(ADRs cited, specs cited, the module docstring's first sentence)."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    doc = (ast.get_docstring(tree) or "").strip()
    first = doc.split("\n\n")[0].replace("\n", " ") if doc else ""
    # Cite only what the file itself says — never guess from a sibling.
    adrs = sorted(set(ADR_RE.findall(text)))
    specs = sorted({m.group(0).split("/")[-1] for m in SPEC_RE.finditer(text)})
    return adrs, specs, first[:150]


def importers(symbol: str, defining: pathlib.Path) -> tuple[list[str], list[str], list[str]]:
    """(external production, tests, INTERNAL) importers of `symbol` from `defining`.

    **Internal is kept separate on purpose.** `wisp/contracts/__init__.py` re-exports the whole M1a
    family, so a naive count reports every symbol as `WIRED` — and a re-export is not a consumer. The
    first version of this did exactly that. Internal = the package's own `__init__.py` and its
    siblings; only what lies outside the family counts as a consumer.
    """
    # REPO-RELATIVE, not absolute. Built from the absolute path this produced a module name like
    # `/Users/…/wisp.contracts.tool`, matched nothing, and reported **every** symbol as having zero
    # consumers — including `ToolRisk`, which is imported across the tree. A detector that returns
    # zeros looks exactly like a clean result.
    mod = defining.relative_to(REPO).with_suffix("").as_posix().replace("/", ".")
    if mod.endswith(".__init__"):
        mod = mod[: -len(".__init__")]
    prod, tests, internal = [], [], []
    for p in REPO.rglob("*.py"):
        if ".venv" in p.parts or "__pycache__" in p.parts:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        hit = False
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module:
                if n.module == mod or n.module.startswith(mod + "."):
                    if any(a.name == symbol for a in n.names):
                        hit = True
            elif isinstance(n, ast.Import):
                if any(a.name.startswith(mod) for a in n.names):
                    hit = True
        if not hit:
            continue
        rel = p.relative_to(REPO).as_posix()
        if "tests" in p.parts:
            tests.append(rel)
        elif p.name == "__init__.py" and p.parent == defining.parent:
            # ONLY the directory's `__init__.py` is a re-export. A same-directory SIBLING is a real
            # consumer: for `wisp/core/contracts.py` that is `wisp/core/stateless.py` and friends.
            # Bucketing siblings as internal reported `ApprovalDecision` as unconsumed while
            # `wisp/core/*` was importing it — the second version of this detector's error.
            internal.append(rel)
        else:
            prod.append(rel)
    return sorted(prod), sorted(tests), sorted(internal)


def symbols_of(path: pathlib.Path) -> list[tuple[str, str, int]]:
    """(kind, name, lineno) for top-level classes and functions."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [(type(n).__name__.replace("Def", "").lower(), n.name, n.lineno)
            for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))]


def main() -> int:
    families = {
        "M1a freeze (wisp/contracts/)":
            sorted(p for p in (REPO / "wisp" / "contracts").glob("*.py") if p.name != "__init__.py"),
        "core vocabulary (wisp/core/contracts.py)":
            [REPO / "wisp" / "core" / "contracts.py"],
    }

    total = 0
    for title, files in families.items():
        print("=" * 100)
        print(title)
        print("=" * 100)
        for f in files:
            adrs, specs, first = module_provenance(f)
            rel = f.relative_to(REPO).as_posix()
            print(f"\n{rel}")
            print(f"  docstring : {first or '<none>'}")
            print(f"  cites     : ADRs {adrs or '—'}   specs {specs or '—'}")
            for kind, name, line in symbols_of(f):
                prod, tests, internal = importers(name, f)
                total += 1
                if prod:
                    verdict = "CONSUMED outside the family"
                elif internal or tests:
                    verdict = "RE-EXPORTED only — no external consumer"
                else:
                    verdict = "UNREFERENCED anywhere"
                print(f"    {kind:9s} {name:26s} :{line:<5} "
                      f"external={len(prod):<3} test={len(tests):<3} internal={len(internal):<3} {verdict}")
                if prod:
                    print(f"        external: {prod[:4]}{' …' if len(prod) > 4 else ''}")

    # The spec's own words, because they decide what an unconsumed symbol MEANS.
    spec = REPO / M1A_SPEC
    if spec.exists():
        text = spec.read_text(encoding="utf-8")
        m = re.search(r"Pure addition[^\n]*", text)
        print("\n" + "=" * 100)
        print(f"THE AUTHORISING SPEC — {M1A_SPEC}")
        print("=" * 100)
        print(f"  {m.group(0) if m else '<the quote was not found — re-read the spec>'}")
        print("  (so an unconsumed symbol in wisp/contracts/ is a SEAM, not an oversight)")
    else:
        print(f"\n⚠ {M1A_SPEC} not found — the freeze's authorisation cannot be quoted")

    print(f"\n{total} contract symbols across {sum(len(v) for v in families.values())} modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
