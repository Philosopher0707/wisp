"""Graph DSL — declarative YAML definitions. Prompts stay small; context
flows via artifact references, not embedded transcripts."""

from __future__ import annotations

from typing import Any

from wisp.graph.types import (
    CycleSpec,
    EdgeMapping,
    Graph,
    GraphNode,
    GraphPolicy,
    JoinPolicy,
    ModelPolicy,
    NodeContract,
    NodeType,
    RetryPolicy,
)


def graph_from_dict(data: dict[str, Any]) -> Graph:
    g = data.get("graph", data)
    nodes = tuple(_node(nid, ndef) for nid, ndef in (g.get("nodes", {}) or {}).items())
    edges = tuple(_edge(e) for e in (g.get("edges", []) or []))
    return Graph(id=g.get("id", ""), version=str(g.get("version", "1")),
                 entrypoint=g.get("entry", g.get("entrypoint", "")),
                 nodes=nodes, edges=edges,
                 policies=_policy(g.get("policies", {}) or {}))


def graph_from_yaml(text: str) -> Graph:
    import yaml
    return graph_from_dict(yaml.safe_load(text))


def graph_to_dict(graph: Graph) -> dict[str, Any]:
    return {"version": graph.version,
            "graph": {"id": graph.id, "entry": graph.entrypoint,
                      "nodes": {n.id: {"type": n.type.value,
                                       "function": n.function or None,
                                       "join_policy": n.join_policy.value,
                                       "routes": dict(n.routes) or None}
                                for n in graph.nodes},
                      "edges": [{"from": e.from_node, "to": e.to_node,
                                 "reason": e.reason, "mapping": dict(e.mapping),
                                 "when": e.condition or None} for e in graph.edges]}}


def _node(nid: str, d: dict[str, Any]) -> GraphNode:
    d = d or {}
    c = d.get("contract", {}) or {}
    contract = NodeContract(
        id=nid, name=str(d.get("name", nid)),
        description=str(d.get("responsibility", d.get("description", ""))),
        input_schema=d.get("input_schema", c.get("input_schema", {})) or {},
        output_schema=d.get("output_schema", c.get("output_schema", {})) or {},
        allowed_tools=tuple(d.get("allowed_tools", c.get("allowed_tools", ("all",)))),
        model_policy=_model(d.get("model", c.get("model", {})) or {}),
        retry_policy=_retry(d.get("retry", c.get("retry", {})) or {}),
        timeout_s=float(d.get("timeout", c.get("timeout", 300))),
        idempotent=bool(d.get("idempotent", c.get("idempotent", True))))
    join = str(d.get("join_policy", d.get("policy", "all"))).lower()
    routes = {str(k): str(v) for k, v in (d.get("routes", {}) or {}).items()}
    cycle = None
    if d.get("cycle"):
        cy = d["cycle"]
        cycle = CycleSpec(entry=str(cy.get("entry", nid)),
                          body=tuple(cy.get("body", ())),
                          exit_gate=str(cy.get("exit", "")),
                          max_iterations=int(cy.get("max_iterations", 3)))
    # Static input bindings: input: {from: "planner.plan"} shorthand.
    return GraphNode(id=nid, type=NodeType(str(d.get("type", "agent"))),
                     contract=contract, function=str(d.get("function", "")),
                     join_policy=JoinPolicy(join), join_param=int(d.get("join_n", 0)),
                     routes=routes, default_route=str(d.get("default_route", "")),
                     cycle=cycle, config={k: v for k, v in d.items()
                                          if k in ("prompt", "input", "responsibility")})


def _edge(e: Any) -> EdgeMapping:
    if isinstance(e, str):  # "a -> b" (reason required elsewhere; flagged by validator)
        parts = e.split("->")
        return EdgeMapping(parts[0].strip(), parts[1].strip())
    src = str(e.get("from", e.get("from_node", "")))
    dst = str(e.get("to", e.get("to_node", "")))
    mapping = dict(e.get("mapping", {}) or {})
    if "from" in str(e.get("input", "")) or isinstance(e.get("input"), dict):
        mapping.update(e["input"] if isinstance(e["input"], dict) else {})
    return EdgeMapping(src, dst, reason=str(e.get("reason", "")),
                       mapping=mapping, condition=str(e.get("when", "") or ""))


def _model(d: Any) -> ModelPolicy:
    if isinstance(d, str):
        return ModelPolicy(model_class=d)
    d = d or {}
    return ModelPolicy(model_class=str(d.get("class", "standard")),
                       provider=d.get("provider"), model=d.get("model"),
                       fallback_chain=tuple(d.get("fallback", ())))


def _retry(d: dict) -> RetryPolicy:
    return RetryPolicy(max_attempts=int(d.get("max_attempts", 1)),
                       retry_on_timeout=bool(d.get("on_timeout", False)),
                       unsafe_side_effects=bool(d.get("unsafe_side_effects", False)))


def _policy(d: dict) -> GraphPolicy:
    b = d.get("budget", {}) or {}
    return GraphPolicy(
        allowed_nodes=tuple(d.get("allowed_nodes", ())),
        allowed_tools=tuple(d.get("allowed_tools", ("all",))),
        allowed_models=tuple(d.get("allowed_models", ())),
        allowed_providers=tuple(d.get("allowed_providers", ())),
        max_nodes=int(d.get("max_nodes", 64)),
        max_concurrency=int((d.get("concurrency", {}) or {}).get("graph",
                            d.get("max_concurrency", 8))),
        max_retries=int(d.get("max_retries", 2)),
        max_tokens=b.get("max_tokens"), max_cost_usd=b.get("max_cost_usd"),
        max_runtime_s=b.get("max_runtime_s"))
