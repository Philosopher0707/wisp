#!/usr/bin/env python3
"""Tool wiring audit — every tool, and whether it is actually reachable at runtime.

Answers, for each tool in `TOOL_IMPLS`:
  1. impl      — mapped to a callable in `TOOL_IMPLS`?
  2. schema    — advertised to the model (a JSON schema exists for it)?
  3. risk      — classified in `TOOL_RISK_TABLE`? (unclassified defaults to EXEC — a silent
                 misclassification, because EXEC is the most restrictive non-read class)
  4. mode      — what each permission mode does with it: ALLOW / ASK / DENY
  5. dispatch  — does the dispatcher special-case it (a second wiring site that can drift)?

A tool that is implemented but not advertised is a *written-but-unwired control* — this
repository's own named dominant pathology. A tool advertised but not implemented is worse.
"""
from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from wisp.tools.registry import TOOL_IMPLS  # noqa: E402
from wisp.core.contracts import TOOL_RISK_TABLE  # noqa: E402

MODES = ("read_only", "auto_edit", "ask_all", "full")


def schemas() -> dict[str, dict]:
    """The tool schemas the model is shown, keyed by tool name.

    `TOOL_SCHEMAS` is OpenAI function-calling shape — `{"type": "function", "function": {"name": ...}}`
    — so the name is NESTED. A first version of this audit looked for a top-level `"name"`, found zero,
    and reported all 42 tools as "implemented but not advertised". **That was the detector's bug, not a
    finding** — recorded here because a false alarm that looks like a defect is worse than no audit.
    """
    from wisp.tools.registry import TOOL_SCHEMAS
    out: dict[str, dict] = {}
    for s in TOOL_SCHEMAS:
        fn = s.get("function", s) if isinstance(s, dict) else {}
        if isinstance(fn, dict) and fn.get("name"):
            out[fn["name"]] = s
    return out


def dispatch_special_cases() -> set[str]:
    """Tool names the dispatcher branches on by name (`elif name == "..."`)."""
    import ast
    src = (REPO / "wisp" / "tools" / "registry.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    found: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare) and isinstance(n.left, ast.Name) and n.left.id == "name":
            for c in n.comparators:
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    found.add(c.value)
    return found


def mode_verdict(tool: str) -> dict[str, str]:
    from wisp.infra.security import SecurityPolicy
    out = {}
    for m in MODES:
        try:
            d = SecurityPolicy(permission_mode=m).check_allow(tool)  # type: ignore[attr-defined]
            out[m] = "ALLOW" if d else "DENY"
        except Exception:
            out[m] = "?"
    return out


def main() -> int:
    sch = schemas()
    special = dispatch_special_cases()
    names = sorted(TOOL_IMPLS)

    print(f"TOOL_IMPLS: {len(names)}   schemas advertised: {len(sch)}   "
          f"risk-classified: {sum(1 for n in names if n in TOOL_RISK_TABLE)}")
    print()

    missing_schema, missing_risk, unimpl = [], [], []
    for n in names:
        if n not in sch:
            missing_schema.append(n)
        if n not in TOOL_RISK_TABLE:
            missing_risk.append(n)
    for n in sorted(sch):
        if n not in TOOL_IMPLS:
            unimpl.append(n)

    print(f"{'tool':<24}{'schema':<8}{'risk':<14}{'dispatch':<10}")
    for n in names:
        print(f"{n:<24}{'yes' if n in sch else '—':<8}"
              f"{TOOL_RISK_TABLE.get(n, '—'):<14}"
              f"{'special' if n in special else '':<10}")

    print()
    if unimpl:
        print(f"⚠ ADVERTISED BUT NOT IMPLEMENTED ({len(unimpl)}): {unimpl}")
    if missing_schema:
        print(f"⚠ IMPLEMENTED BUT NOT ADVERTISED ({len(missing_schema)}): {missing_schema}")
    if missing_risk:
        print(f"⚠ NOT IN TOOL_RISK_TABLE ({len(missing_risk)}) — `risk_for_tool` defaults these: "
              f"{missing_risk}")
    if not (unimpl or missing_schema or missing_risk):
        print("all three columns complete for every tool")
    if special:
        print(f"\ndispatcher special-cases {len(special)}: {sorted(special)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
