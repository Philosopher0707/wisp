"""Planner evaluation corpus (9I) — deterministic, no model calls.

Evaluates canned IRs (the 10 task shapes) through compile + quality +
policy, asserting the pipeline accepts good plans and flags bad ones.
Run: python scripts/eval_planner.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from wisp.graph.planner import compile_ir, quality_score  # noqa: E402
from wisp.graph.types import GraphPolicy  # noqa: E402
from wisp.graph.validator import validate_graph  # noqa: E402

POLICY = GraphPolicy(allowed_tools=("read_file", "run_bash"), max_concurrency=4)


def _n(**kw):
    d = {"type": "agent", "allowed_tools": ["read_file"]}
    d.update(kw)
    return d


def _e(f, t, reason="x", mapping=None, when=""):
    e = {"from": f, "to": t, "reason": reason}
    if mapping:
        e["mapping"] = mapping
    if when:
        e["when"] = when
    return e


CORPUS = [
    # 1. trivial task -> SINGLE_AGENT, one node
    ("trivial-typo", "SINGLE_AGENT", {"fix": _n()},
     [], True),
    # 2. single-file bug: inspect -> fix -> verify chain with dataflow
    ("single-file-bug", "GRAPH",
     {"inspect": _n(), "fix": _n(), "verify": _n()},
     [_e("inspect", "fix", "fix consumes inspect.output.symbols",
         {"scope": "output.symbols"}),
      _e("fix", "verify", "verify consumes fix.output.patch",
         {"patch": "output.patch"})], True),
    # 3-4. multi-module refactor + feature+tests: fan with join
    ("multimodule-fan", "GRAPH",
     {"split": _n(), "m1": _n(), "m2": _n(),
      "join": {"type": "join", "allowed_tools": ["read_file"]}},
     [_e("split", "m1", "m1 consumes split.output.slice"),
      _e("split", "m2", "m2 consumes split.output.slice"),
      _e("m1", "join", "join consumes m1.output"),
      _e("m2", "join", "join consumes m2.output")], True),
    # 5. security-sensitive: generator + independent verifier.
    # NOTE: planners may NOT bind `function:` gates (host-controlled); they
    # express acceptance with verifier accept-edges instead.
    ("security-change", "GRAPH",
     {"impl": _n(), "review": {"type": "verifier",
                               "allowed_tools": ["read_file"]},
      "done": _n()},
     [_e("impl", "review", "review consumes impl.output"),
      _e("review", "done", "done consumes review.output", when="accept")], True),
    # 6. FAKE DEPS: chain with no dataflow -> must flag OPT-001
    ("fake-serial", "GRAPH",
     {"a": _n(), "b": _n(), "c": _n(), "d": _n()},
     [_e("a", "b", "x"), _e("b", "c", "x"), _e("c", "d", "x"),
      _e("a", "d", "x")], "OPT-001"),
    # 7. missing verification on 3+ agents -> must flag OPT-002
    ("no-verifier", "GRAPH",
     {"a": _n(), "b": _n(), "c": _n()},
     [_e("a", "b", "x", {"v": "output.v"}),
      _e("b", "c", "x", {"v": "output.v"})], "OPT-002"),
]


def _ir(name, shape, nodes, edges, entry=None):
    entry = entry or next(iter(nodes))
    return {"objective": name, "execution_shape": shape,
            "graph": {"id": name, "entry": entry, "nodes": nodes, "edges": edges}}


def main() -> int:
    fails = 0
    for name, shape, nodes, edges, expect in CORPUS:
        ir = _ir(name, shape, nodes, edges)
        try:
            graph, _ = compile_ir(ir, POLICY)
            assert validate_graph(graph) == []
            q = quality_score(graph)
        except Exception as exc:
            print(f"FAIL {name}: unexpected compile error: {exc}")
            fails += 1
            continue
        if expect is True:
            status = "ok" if not q["findings"] else f"advisory {q['findings']}"
            print(f"ok   {name}: valid, score {q['score']} ({status})")
        elif isinstance(expect, str):
            if any(expect in f for f in q["findings"]):
                print(f"ok   {name}: flagged {expect} (score {q['score']})")
            else:
                print(f"FAIL {name}: expected finding {expect}, got {q['findings']}")
                fails += 1
    # Optimization readiness pointers for Phase 10.
    print("\nOPT candidates for Phase 10: OPT-001 serialization, OPT-002 verifiers, "
          "OPT-003 context, OPT-004 fanout/size.")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
