"""``wisp graph ...`` CLI + REPL ``/graph`` — thin verbs over GraphExecutor.

Subcommands: list | show | validate | run | resume | status | cancel |
trace | inspect | metrics. Graphs resolve from ./graphs/*.yaml (or a path),
plus the built-in 'coding-agent' reference graph.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from typing import Any

from wisp.graph.security import scrub_text as redact
from wisp.graph.dsl import MAX_YAML_BYTES, graph_from_yaml
from wisp.graph.executor import GraphExecutor
from wisp.graph.reference import REFERENCE_YAML, coding_agent_graph
from wisp.graph.store import GraphStore
from wisp.graph.trace import quality_metrics, render_ascii, render_dot, render_json
from wisp.graph.validator import validate_graph
from wisp.pathsec import resolve_contained

USAGE = ("Usage: wisp graph <verb> [args]\n"
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
         "  metrics <run-id>        Quality metrics (parallelism, failures…)\n"
         "  plan \"<objective>\" [--json]  Propose a graph (never executes)\n"
         "  proposals               List planner proposals\n"
         "  inspect-prop <id>       Show a proposal (IR + diagnostics)\n"
         "  execute <id> --yes ['<json-input>']     Run the approved proposal")


def _load_graph(name: str):
    if name == "coding-agent":
        return coding_agent_graph()
    if os.path.isfile(name):
        if os.path.getsize(name) > MAX_YAML_BYTES:
            raise ValueError(f"graph file too large (max {MAX_YAML_BYTES} bytes)")
        with open(name) as fh:
            return graph_from_yaml(fh.read())
    base = os.path.abspath("graphs")
    for cand in (f"{name}.yaml", f"{name}.yml"):
        # Contained lookup: names cannot traverse out of ./graphs/.
        # Semantics live in wisp.pathsec (the single authority) — a local
        # realpath+prefix copy here previously accepted control characters.
        try:
            path = resolve_contained(base, cand)
        except ValueError:
            continue
        if os.path.isfile(path):
            if os.path.getsize(path) > MAX_YAML_BYTES:
                raise ValueError("graph file too large")
            with open(path) as fh:
                return graph_from_yaml(fh.read())
    raise FileNotFoundError(f"unknown graph '{name}' (try 'coding-agent' or a YAML path)")


def _builtin_defs() -> dict[str, Any]:
    import yaml
    return {"coding-agent": yaml.safe_load(REFERENCE_YAML)["graph"]}


def main(argv: list[str], workspace: str = ".", model: str = "",
           provider: str = "") -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    verb, args = argv[0], argv[1:]
    try:
        return _main(verb, args, workspace, model, provider)
    except (FileNotFoundError, ValueError, KeyError, PermissionError) as exc:
        # Fail closed with a diagnostic — never a traceback with paths/SQL.
        print(f"✗ {redact(str(exc))[:500]}")
        return 1


def _main(verb: str, args: list[str], workspace: str, model: str = "",
          provider: str = "") -> int:
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
                try:
                    from wisp.graph.audit import GraphSecurityAuditor
                    reason = "; ".join(errors[:5])
                    gov = any(k in reason for k in ("policy", "not in graph",
                                                   "forbidden", "allowed_"))
                    GraphSecurityAuditor(workspace=workspace).emit(
                        "graph.policy_rejected" if gov else "graph.validation_rejected",
                        graph_id=graph.id, graph_hash=graph.fingerprint(),
                        allowed=False, reason=reason)
                except Exception:
                    pass
                return 1
            print(f"✓ {graph.id} v{graph.version}: valid "
                  f"({len(graph.nodes)} nodes, {len(graph.edges)} edges)")
            return 0
        fmt = "ascii"
        if "--format" in args:
            idx = args.index("--format") + 1
            if idx >= len(args):
                print("Usage: wisp graph show <graph> [--format ascii|dot|json]"); return 1
            fmt = args[idx]
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
        if len(raw) > 1_048_576:
            print("✗ input too large (max 1MB)"); return 1
        try:
            inputs = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"✗ input must be JSON: {exc}"); return 1
        if not isinstance(inputs, dict):
            print("✗ input must be a JSON object"); return 1
        max_c = 8
        if "--max" in args:
            idx = args.index("--max") + 1
            if idx >= len(args):
                print("Usage: wisp graph run <graph> '<json>' [--max N]"); return 1
            try:
                max_c = max(1, min(int(args[idx]), 32))
            except ValueError:
                print("✗ --max must be an integer"); return 1
        from wisp.graph.runner import default_executor
        ex = default_executor(workspace, max_c)
        result = asyncio.run(ex.run(graph, inputs))
        print(render_ascii(result))
        return 0 if result["status"] == "succeeded" else 1
    if verb == "resume":
        if not args:
            print("Usage: wisp graph resume <run-id> [--approve node=true]"); return 1
        approvals: dict[str, bool] = {}
        for a in args[1:]:
            # Accept both "--approve node=true" and "--approve=node=true".
            if a == "--approve":
                continue
            if a.startswith("--approve="):
                _, _, kv = a.partition("=")
            elif "=" in a and not a.startswith("--"):
                kv = a
            else:
                continue
            k, _, v = kv.partition("=")
            k, v = k.strip(), v.strip().lower()
            if k and v in ("true", "false"):
                approvals[k] = v == "true"
        row = store.get_run(args[0])
        if row is None:
            print(f"✗ unknown run {args[0]}"); return 1
        graph = _load_graph(row["graph_id"]) if row["graph_id"] != "coding-agent" \
            else coding_agent_graph()
        from wisp.graph.runner import default_executor
        ex = default_executor(workspace, 8, store)
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
        if store.get_run(args[0]) is None:
            print(f"✗ unknown run {args[0]}"); return 1
        GraphExecutor(workspace=workspace, store=store).cancel(args[0])
        print(f"cancelled {args[0]}")
        return 0
    if verb in ("trace", "inspect", "metrics"):
        if not args:
            print(f"Usage: wisp graph {verb} <run-id>"); return 1
        row = store.get_run(args[0])
        if row is None:
            print(f"✗ unknown run {args[0]}"); return 1
        nodes = {}
        for n in store.node_runs(args[0]):
            try:
                payload = json.loads(n["result"] or "{}")
            except (ValueError, TypeError):
                payload = {"status": "corrupt"}
            if not isinstance(payload, dict):
                payload = {"value": payload}
            # Stored outputs win over row columns on conflict.
            nodes[n["node_id"]] = {"status": n["status"], "attempt": n["attempt"],
                                   **payload}
        trace = {"graph_id": row["graph_id"], "run_id": row["run_id"],
                 "status": row["status"], "results_by_node": nodes,
                 "wall_s": 0, "tokens": {}, "cost_usd": 0}
        if verb == "trace":
            print(render_ascii(trace))
        elif verb == "metrics":
            print(json.dumps(quality_metrics(trace), indent=2))
        else:
            # inspect dumps operator-visible state: redact like any audit sink.
            print(redact(render_json(trace)))
        return 0
    if verb == "pause":
        # Runs are synchronous: pause == cancel now, resume later from the
        # durable checkpoint. Terminal runs are untouched (cancel is a no-op).
        if not args:
            print("Usage: wisp graph pause <run-id>"); return 1
        row = store.get_run(args[0])
        if row is None:
            print(f"✗ unknown run {args[0]}"); return 1
        if row["status"] in ("succeeded", "failed", "cancelled"):
            print(f"run {args[0]} already {row['status']}"); return 0
        GraphExecutor(workspace=workspace, store=store).cancel(args[0])
        print(f"paused {args[0]} (resume with: wisp graph resume {args[0]})")
        return 0
    if verb == "plan":
        return _plan(args, workspace, store,
                     model=model or _opt(args, "--model"),
                     provider=provider or _opt(args, "--provider"),
                     as_json="--json" in args)
    if verb == "proposals":
        for p in store.list_proposals():
            print(f"  {p['proposal_id']}  {p['graph_id']}  {p['status']}  "
                  f"{str(p.get('objective', ''))[:60]}")
        return 0
    if verb == "inspect-prop":
        if not args:
            print("Usage: wisp graph inspect-prop <proposal-id>"); return 1
        prop = store.get_proposal(args[0])
        if prop is None:
            print(f"✗ unknown proposal {args[0]}"); return 1
        print(f"Proposal: {prop['proposal_id']}  Status: {prop['status']}")
        print(f"Graph: {prop['graph_id']}  hash: {prop['graph_hash']}")
        print(f"IR hash: {prop['ir_hash']}  model: {prop['planner_model']}")
        for d in prop.get("diagnostics", []):
            print(f"  - {d}")
        if "--ir" in args:
            print(redact(json.dumps(prop.get("ir", {}), indent=2)[:8000]))
        return 0
    if verb == "execute":
        if not args or "--yes" not in args:
            print("Usage: wisp graph execute <proposal-id> --yes ['<json-input>']")
            print("Execution requires explicit --yes approval."); return 2
        pid = args[0]
        prop = store.get_proposal(pid)
        if prop is None:
            print(f"✗ unknown proposal {pid}"); return 1
        if prop["status"] not in ("APPROVAL_REQUIRED", "APPROVED"):
            print(f"✗ proposal is {prop['status']}, not executable"); return 1
        raw = next((a for a in args[1:] if not a.startswith("--")), "{}")
        try:
            inputs = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"✗ input must be JSON: {exc}"); return 1
        if not isinstance(inputs, dict):
            print("✗ input must be a JSON object"); return 1
        from wisp.graph.planner import PlanError, execute_proposal
        from wisp.graph.runner import default_executor
        try:
            result = asyncio.run(execute_proposal(
                prop, default_executor(workspace, 8, store), inputs, approve=True))
        except PlanError as exc:
            print(f"✗ {exc}"); return 1
        store.set_proposal_status(pid, "EXECUTED" if result["status"] == "succeeded"
                                  else "APPROVED")
        print(render_ascii(result))
        return 0 if result["status"] == "succeeded" else 1
    print(USAGE)
    return 1


def _opt(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1] if flag in args and \
        args.index(flag) + 1 < len(args) else ""


def _plan(args: list[str], workspace: str, store, model: str, provider: str,
          as_json: bool) -> int:
    positional = [a for a in args if not a.startswith("--")]
    if not positional:
        print('Usage: wisp graph plan "<objective>" [--json] [--model M] [--provider P]')
        return 1
    objective = positional[0]
    if len(objective) > 8192:
        print("✗ objective too long (max 8192 chars)"); return 1
    from wisp.config import WispConfig
    from wisp.graph.planner import PlanError, propose
    from wisp.graph.types import GraphPolicy
    from wisp.providers import get_provider
    config = WispConfig()
    if model:
        config = config.replace(model=model)
    if provider:
        from wisp.provider_select import with_provider
        config = with_provider(config, provider)
    try:
        prov = get_provider(config)
    except Exception as exc:
        print(f"✗ cannot build provider: {exc}"); return 1
    policy = GraphPolicy(workspace=workspace)
    try:
        proposal = propose(objective, prov, policy,
                           model=model or config.model)
    except PlanError as exc:
        try:
            from wisp.graph.audit import GraphSecurityAuditor
            GraphSecurityAuditor(workspace=workspace).emit(
                "graph.proposal_decided", allowed=False,
                reason=f"{exc.code}: {exc}", evidence={"objective": objective[:200]})
        except Exception:
            pass
        print(f"✗ proposal {exc.code}: {exc}"); return 1
    store.put_proposal(proposal)
    if any("narrowed" in d or "clamped" in d for d in proposal["diagnostics"]):
        try:
            from wisp.graph.audit import GraphSecurityAuditor
            GraphSecurityAuditor(workspace=workspace).emit(
                "graph.policy_narrowed", graph_id=proposal["graph_id"],
                graph_hash=proposal["graph_hash"], allowed=True,
                reason="; ".join(proposal["diagnostics"][:3]))
        except Exception:
            pass
    if as_json:
        print(redact(json.dumps({k: v for k, v in proposal.items() if k != "ir"},
                                indent=2, default=str)))
        return 0
    q = proposal["quality"]
    print(f"Plan generated.\n\nGraph: {proposal['graph_id']}\n"
          f"Proposal: {proposal['proposal_id']}\n"
          f"Nodes: {proposal['nodes']}  Edges: {proposal['edges']}  "
          f"Quality: {q['score']}/100")
    for f in q["findings"]:
        print(f"  ! {f}")
    for d in proposal["diagnostics"]:
        print(f"  - {d}")
    print(f"\nUse:\n  wisp graph inspect-prop {proposal['proposal_id']}\n"
          f"  wisp graph execute {proposal['proposal_id']} --yes")
    return 0


def register_repl(dispatcher) -> None:
    """Register /graph with the REPL dispatcher (additive; no legacy changes)."""
    from wisp.cli.dispatcher import CommandResult

    @dispatcher.register("graph", "Graph execution (list/run/status/trace)", usage="/graph ...")
    def _graph(ctx, args: str):
        try:
            parts = shlex.split(args) if args else ["list"]
        except ValueError as exc:
            ctx.emit(f"bad quoting: {exc}")
            return CommandResult.CONSUMED
        if not parts:
            return CommandResult.CONSUMED
        from io import StringIO
        import contextlib as _cl
        buf = StringIO()
        ws = getattr(ctx.config, "workspace", ".") if not isinstance(ctx.config, dict) \
            else ctx.config.get("workspace", ".")
        with _cl.redirect_stdout(buf):
            code = main(parts, workspace=ws)
        ctx.emit(buf.getvalue().strip() or f"(exit {code})")
        return CommandResult.CONSUMED
