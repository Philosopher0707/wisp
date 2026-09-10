"""``wisp graph ...`` CLI + REPL ``/graph`` — thin verbs over GraphExecutor.

Subcommands: list | show | validate | run | resume | status | cancel |
trace | inspect | metrics. Graphs resolve from ./graphs/*.yaml (or a path),
plus the built-in 'coding-agent' reference graph.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from wisp.graph.dsl import graph_from_yaml
from wisp.graph.executor import GraphExecutor
from wisp.graph.reference import REFERENCE_YAML, coding_agent_graph
from wisp.graph.store import GraphStore
from wisp.graph.trace import quality_metrics, render_ascii, render_dot, render_json
from wisp.graph.validator import validate_graph

USAGE = ("Usage: wisp graph <list|show|validate|run|resume|status|cancel|trace|inspect|metrics> [args]\n"
         "\n"
         "  list                    List runs (and built-in graphs)\n"
         "  show <graph> [--format ascii|dot|json]   Show topology\n"
         "  validate <graph>        Static validation with diagnostics\n"
         "  run <graph> '<json-input>' [--max N]     Execute a graph\n"
         "  resume <run-id> [--approve node=true]    Resume a paused run\n"
         "  status <run-id>         Run status + per-node states\n"
         "  cancel <run-id>         Cancel a run\n"
         "  trace <run-id>          ASCII execution trace\n"
         "  inspect <run-id>        Full result JSON\n"
         "  metrics <run-id>        Quality metrics (parallelism, failures…)")


def _load_graph(name: str):
    if name == "coding-agent":
        return coding_agent_graph()
    if os.path.isfile(name):
        with open(name) as fh:
            return graph_from_yaml(fh.read())
    for cand in (f"graphs/{name}.yaml", f"graphs/{name}.yml", f"{name}.yaml"):
        if os.path.isfile(cand):
            with open(cand) as fh:
                return graph_from_yaml(fh.read())
    raise FileNotFoundError(f"unknown graph '{name}' (try 'coding-agent' or a YAML path)")


def _builtin_defs() -> dict[str, Any]:
    import yaml
    return {"coding-agent": yaml.safe_load(REFERENCE_YAML)["graph"]}


def main(argv: list[str], workspace: str = ".") -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    verb, args = argv[0], argv[1:]
    store = GraphStore(workspace=workspace)
    if verb == "list":
        print("graphs: coding-agent")
        for r in store.list_runs():
            print(f"  {r['run_id']}  {r['graph_id']} v{r['graph_version']}  {r['status']}")
        return 0
    if verb in ("show", "validate"):
        if not args:
            print(f"Usage: wisp graph {verb} <graph>"); return 1
        graph = _load_graph(args[0])
        if verb == "validate":
            errors = validate_graph(graph)
            if errors:
                print("invalid:")
                for e in errors:
                    print(f"  ✗ {e}")
                return 1
            print(f"✓ {graph.id} v{graph.version}: valid "
                  f"({len(graph.nodes)} nodes, {len(graph.edges)} edges)")
            return 0
        fmt = "ascii"
        if "--format" in args:
            fmt = args[args.index("--format") + 1]
        gdef = {"id": graph.id, "nodes": [n.id for n in graph.nodes],
                "edges": [(e.from_node, e.to_node, e.condition) for e in graph.edges]}
        if fmt == "dot":
            print(render_dot(gdef))
        elif fmt == "json":
            print(json.dumps(gdef, indent=2))
        else:
            print(f"Graph: {graph.id} v{graph.version} (entry: {graph.entrypoint})")
            for n in graph.nodes:
                print(f"  [{n.type.value}] {n.id}")
            for e in graph.edges:
                when = f" when {e.condition}" if e.condition else ""
                print(f"  {e.from_node} -> {e.to_node}{when}  ({e.reason[:60]})")
        return 0
    if verb == "run":
        if len(args) < 1:
            print("Usage: wisp graph run <graph> '<json-input>' [--max N]"); return 1
        graph = _load_graph(args[0])
        raw = args[1] if len(args) > 1 and not args[1].startswith("--") else "{}"
        try:
            inputs = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"✗ input must be JSON: {exc}"); return 1
        max_c = int(args[args.index("--max") + 1]) if "--max" in args else 8
        from wisp.graph.runner import SubagentNodeRunner
        ex = GraphExecutor(workspace=workspace, max_concurrency=max_c)
        try:
            from wisp.multi_agent import SubagentOrchestrator
            ex._runner = SubagentNodeRunner(SubagentOrchestrator(), workspace)
        except Exception:
            pass
        for name, fn in _default_functions().items():
            ex.register_function(name, fn)
        result = asyncio.run(ex.run(graph, inputs))
        print(render_ascii(result))
        return 0 if result["status"] == "succeeded" else 1
    if verb == "resume":
        if not args:
            print("Usage: wisp graph resume <run-id> [--approve node=true]"); return 1
        approvals: dict[str, bool] = {}
        for a in args[1:]:
            if a.startswith("--approve"):
                _, _, kv = a.partition("=")
                k, _, v = kv.partition("=")
                approvals[k or ""] = v.lower() == "true"
        row = store.get_run(args[0])
        if row is None:
            print(f"✗ unknown run {args[0]}"); return 1
        graph = _load_graph(row["graph_id"]) if row["graph_id"] != "coding-agent" \
            else coding_agent_graph()
        ex = GraphExecutor(workspace=workspace, store=store)
        for name, fn in _default_functions().items():
            ex.register_function(name, fn)
        result = asyncio.run(ex.resume(graph, args[0], approvals or None))
        print(render_ascii(result))
        return 0
    if verb == "status":
        if not args:
            print("Usage: wisp graph status <run-id>"); return 1
        row = store.get_run(args[0])
        if row is None:
            print(f"✗ unknown run {args[0]}"); return 1
        print(f"{row['run_id']}  {row['graph_id']}  {row['status']}")
        for n in store.node_runs(args[0]):
            print(f"  {n['status']:9} {n['node_id']} (attempt {n['attempt']})")
        return 0
    if verb == "cancel":
        if not args:
            print("Usage: wisp graph cancel <run-id>"); return 1
        GraphExecutor(workspace=workspace, store=store).cancel(args[0])
        print(f"cancelled {args[0]}")
        return 0
    if verb in ("trace", "inspect", "metrics"):
        if not args:
            print(f"Usage: wisp graph {verb} <run-id>"); return 1
        row = store.get_run(args[0])
        if row is None:
            print(f"✗ unknown run {args[0]}"); return 1
        nodes = {n["node_id"]: {"status": n["status"], "attempt": n["attempt"],
                                **(json.loads(n["result"] or "{}"))}
                 for n in store.node_runs(args[0])}
        trace = {"graph_id": row["graph_id"], "run_id": row["run_id"],
                 "status": row["status"], "results_by_node": nodes,
                 "wall_s": 0, "tokens": {}, "cost_usd": 0}
        if verb == "trace":
            print(render_ascii(trace))
        elif verb == "metrics":
            print(json.dumps(quality_metrics(trace), indent=2))
        else:
            print(render_json(trace))
        return 0
    print(USAGE)
    return 1


def _default_functions() -> dict:
    from wisp.graph.reference import default_functions
    return default_functions()


def register_repl(dispatcher) -> None:
    """Register /graph with the REPL dispatcher (additive; no legacy changes)."""
    from wisp.cli.dispatcher import CommandResult

    @dispatcher.register("graph", "Graph execution (list/run/status/trace)", usage="/graph ...")
    def _graph(ctx, args: str):
        parts = args.split() if args else ["list"]
        from io import StringIO
        import contextlib as _cl
        buf = StringIO()
        ws = getattr(ctx.config, "workspace", ".") if not isinstance(ctx.config, dict) \
            else ctx.config.get("workspace", ".")
        with _cl.redirect_stdout(buf):
            code = main(parts, workspace=ws)
        ctx.emit(buf.getvalue().strip() or f"(exit {code})")
        return CommandResult.CONSUMED
