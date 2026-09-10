"""12.5A quarantine: legacy GraphRunner strata stay isolated, canonical runtime owns execution.

THREAT: a new consumer silently re-activates the parallel runtime.
EXPECTED: allowlisted importers only; wisp.core clean; legacy importable but dead.
"""

from __future__ import annotations

import ast
import os


LEGACY = {"wisp/core/agentic_graph.py", "wisp/core/graph_nodes.py",
          "wisp/core/graph_state.py"}
# Intra-legacy + doctor diagnostic + tests only. Anything else = new consumer.
ALLOWLIST = {"wisp/core/agentic_graph.py", "wisp/core/graph_nodes.py",
             "wisp/core/graph_state.py", "wisp/core/doctor.py"}


def _importers():
    found: dict[str, list[str]] = {}
    for dp, dn, fn in os.walk("wisp"):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p)
            try:
                tree = ast.parse(open(p, errors="replace").read())
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                mod = None
                if isinstance(n, ast.Import):
                    for a in n.names:
                        if a.name in ("wisp.core.agentic_graph", "wisp.core.graph_nodes",
                                      "wisp.core.graph_state"):
                            mod = a.name
                elif isinstance(n, ast.ImportFrom) and (n.module or "") in (
                        "wisp.core.agentic_graph", "wisp.core.graph_nodes",
                        "wisp.core.graph_state"):
                    mod = n.module
                if mod:
                    found.setdefault(rel, []).append(mod)
    return found


class TestQuarantine:
    def test_no_new_consumers(self):
        bad = {f: ms for f, ms in _importers().items() if f not in ALLOWLIST}
        assert not bad, f"new legacy-runtime consumers: {bad}"

    def test_core_init_clean(self):
        import sys
        for m in [m for m in sys.modules
                  if m in ("wisp.core.graph_state", "wisp.core.graph_nodes",
                           "wisp.core.agentic_graph")]:
            del sys.modules[m]
        import wisp.core  # noqa: F401
        assert "wisp.core.graph_state" not in sys.modules
        assert "wisp.core.agentic_graph" not in sys.modules

    def test_legacy_still_imports(self):
        # Quarantine is isolation, not deletion: pinned suites keep running.
        import wisp.core.agentic_graph as ag
        import wisp.core.graph_nodes as gn
        import wisp.core.graph_state as gs
        assert ag.GraphRunner is not None
        assert gn.planner_coder_node is not None
        assert gs.GraphState is not None

    def test_deprecation_marked(self):
        for rel in LEGACY:
            with open(rel, errors="replace") as fh:
                head = fh.read(1200)
            assert "deprecated" in head.lower() and "12.5A" in head, rel

    def test_runtime_has_no_legacy_hydration(self):
        src = open("wisp/core/runtime.py", errors="replace").read()
        assert "from wisp.core.graph_state import" not in src
        assert 'session["graph_state"] =' not in src
