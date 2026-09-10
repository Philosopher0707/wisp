"""Graph DSL — declarative YAML definitions. Prompts stay small; context
flows via artifact references, not embedded transcripts.

Fail-closed parsing: every coercion is strict (bad types/values raise
ValueError naming the offending path); input size is capped; safe_load only.
"""

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

MAX_YAML_BYTES = 1_000_000
MAX_ITEMS = 4096  # nodes+edges+mappings+routes guardrail


def _err(path: str, msg: str) -> ValueError:
    return ValueError(f"graph DSL {path}: {msg}")


def _str(v: Any, path: str, max_len: int = 2048, allow_empty: bool = True) -> str:
    if not isinstance(v, str):
        raise _err(path, f"expected string, got {type(v).__name__}")
    if not allow_empty and not v:
        raise _err(path, "must not be empty")
    if len(v) > max_len:
        raise _err(path, f"too long (max {max_len})")
    return v


def _bool(v: Any, path: str) -> bool:
    # NEVER bool(v): bool("false") is True — a privilege escalation.
    if isinstance(v, bool):
        return v
    raise _err(path, f"expected true/false, got {v!r}")


def _int(v: Any, path: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise _err(path, f"expected integer {lo}..{hi}, got {v!r}")
    if not (lo <= v <= hi):
        raise _err(path, f"expected integer {lo}..{hi}, got {v!r}")
    return v


def _float(v: Any, path: str, lo: float, hi: float) -> float:
    import math
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise _err(path, f"expected finite number, got {v!r}")
    f = float(v)
    if not (lo <= f <= hi):
        raise _err(path, f"expected {lo}..{hi}, got {v!r}")
    return f


def _str_list(v: Any, path: str, max_items: int = 256) -> tuple[str, ...]:
    if isinstance(v, str) or not isinstance(v, (list, tuple)):
        raise _err(path, f"expected list of strings, got {v!r}")
    if len(v) > max_items:
        raise _err(path, f"too many items (max {max_items})")
    return tuple(_str(x, f"{path}[]", max_len=256) for x in v)


def _str_dict(v: Any, path: str, max_items: int = 256) -> dict[str, str]:
    if not isinstance(v, dict):
        raise _err(path, f"expected mapping, got {type(v).__name__}")
    if len(v) > max_items:
        raise _err(path, f"too many items (max {max_items})")
    return {_str(k, f"{path}.key", max_len=256): _str(x, f"{path}.{k}", max_len=4096)
            for k, x in v.items()}


def graph_from_dict(data: dict[str, Any]) -> Graph:
    if not isinstance(data, dict):
        raise _err("root", f"expected mapping, got {type(data).__name__}")
    g = data.get("graph", data)
    if not isinstance(g, dict):
        raise _err("graph", f"expected mapping, got {type(g).__name__}")
    raw_nodes = g.get("nodes", {})
    if not isinstance(raw_nodes, dict):
        raise _err("graph.nodes", "expected mapping of id -> node")
    raw_edges = g.get("edges", [])
    if isinstance(raw_edges, dict) or isinstance(raw_edges, str):
        raise _err("graph.edges", "expected list of edges")
    if len(raw_nodes) + len(raw_edges) > MAX_ITEMS:
        raise _err("graph", f"too many nodes+edges (max {MAX_ITEMS})")
    nodes = tuple(_node(str(nid), ndef) for nid, ndef in raw_nodes.items())
    edges = tuple(_edge(e, i) for i, e in enumerate(raw_edges))
    return Graph(id=_str(g.get("id", ""), "graph.id", allow_empty=False),
                 version=_str(str(g.get("version", "1")), "graph.version", max_len=32),
                 entrypoint=_str(g.get("entry", g.get("entrypoint", "")),
                                 "graph.entry"),
                 nodes=nodes, edges=edges,
                 policies=_policy(g.get("policies", {}) or {}, "graph.policies"))


def graph_from_yaml(text: str) -> Graph:
    import yaml
    if not isinstance(text, str) or len(text) > MAX_YAML_BYTES:
        raise _err("document", f"YAML input too large (max {MAX_YAML_BYTES} bytes)")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _err("document", f"YAML parse failed: {exc}") from exc
    return graph_from_dict(data)


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
    path = f"nodes.{nid}"
    if not isinstance(d, dict):
        raise _err(path, f"expected mapping, got {type(d).__name__}")
    c = d.get("contract", {}) or {}
    if not isinstance(c, dict):
        raise _err(f"{path}.contract", "expected mapping")
    contract = NodeContract(
        id=nid, name=_str(d.get("name", nid), f"{path}.name", max_len=256),
        description=_str(d.get("responsibility", d.get("description", "")),
                         f"{path}.responsibility", max_len=4096),
        input_schema=_schema(d.get("input_schema", c.get("input_schema", {})) or {},
                             f"{path}.input_schema"),
        output_schema=_schema(d.get("output_schema", c.get("output_schema", {})) or {},
                              f"{path}.output_schema"),
        allowed_tools=_str_list(d.get("allowed_tools", c.get("allowed_tools", ("all",))),
                                f"{path}.allowed_tools"),
        model_policy=_model(d.get("model", c.get("model", {})) or {}, f"{path}.model"),
        retry_policy=_retry(d.get("retry", c.get("retry", {})) or {}, f"{path}.retry"),
        timeout_s=_float(d.get("timeout", c.get("timeout", 300)), f"{path}.timeout",
                         0.001, 86400),
        idempotent=_bool(d.get("idempotent", c.get("idempotent", True)),
                         f"{path}.idempotent"))
    ntype = _str(d.get("type", "agent"), f"{path}.type", max_len=32)
    try:
        node_type = NodeType(ntype)
    except ValueError:
        raise _err(f"{path}.type", f"unknown node type {ntype!r}") from None
    join = _str(d.get("join_policy", d.get("policy", "all")), f"{path}.join_policy",
                max_len=32).lower()
    try:
        join_policy = JoinPolicy(join)
    except ValueError:
        raise _err(f"{path}.join_policy", f"unknown join policy {join!r}") from None
    routes = _str_dict(d.get("routes", {}) or {}, f"{path}.routes")
    cycle = None
    if d.get("cycle"):
        cy = d["cycle"]
        if not isinstance(cy, dict):
            raise _err(f"{path}.cycle", "expected mapping")
        cycle = CycleSpec(entry=_str(cy.get("entry", nid), f"{path}.cycle.entry",
                                     max_len=128),
                          body=tuple(_str(x, f"{path}.cycle.body[]", max_len=128)
                                     for x in _as_list(cy.get("body", ()), f"{path}.cycle.body")),
                          exit_gate=_str(cy.get("exit", ""), f"{path}.cycle.exit",
                                         max_len=128),
                          max_iterations=_int(cy.get("max_iterations", 3),
                                              f"{path}.cycle.max_iterations", 1, 25))
    join_timeout = d.get("join_timeout")
    config = {k: _str(v, f"{path}.{k}", max_len=8192)
              for k, v in d.items() if k in ("prompt", "responsibility")}
    if isinstance(d.get("input"), dict):
        config["input"] = {str(k)[:256]: str(v)[:4096] for k, v in d["input"].items()}
    if isinstance(d.get("role"), str):
        config["role"] = _str(d["role"], f"{path}.role", max_len=64)
    if isinstance(d.get("max_iterations"), int) and not isinstance(d.get("max_iterations"), bool):
        config["max_iterations"] = _int(d["max_iterations"], f"{path}.max_iterations", 1, 100)
    return GraphNode(id=nid, type=node_type,
                     contract=contract, function=_str(d.get("function", ""),
                                                     f"{path}.function", max_len=256),
                     join_policy=join_policy,
                     join_param=_int(d.get("join_n", 0), f"{path}.join_n", 0, 1024),
                     join_timeout_s=_float(join_timeout, f"{path}.join_timeout", 0.001, 86400)
                     if join_timeout is not None else None,
                     routes=routes,
                     default_route=_str(d.get("default_route", ""),
                                        f"{path}.default_route", max_len=128),
                     cycle=cycle, config=config)


def _as_list(v: Any, path: str) -> list:
    if isinstance(v, str) or not isinstance(v, (list, tuple)):
        raise _err(path, f"expected list, got {v!r}")
    if len(v) > 1024:
        raise _err(path, "too many items")
    return list(v)


def _schema(v: Any, path: str) -> dict:
    if not isinstance(v, dict):
        raise _err(path, f"expected mapping, got {type(v).__name__}")
    if len(str(v)) > 65536:
        raise _err(path, "schema too large")
    return v


def _edge(e: Any, i: int) -> EdgeMapping:
    path = f"edges[{i}]"
    if isinstance(e, str):  # "a -> b" (reason required elsewhere; flagged by validator)
        parts = e.split("->")
        if len(parts) != 2:
            raise _err(path, f"expected 'a -> b', got {e!r}")
        return EdgeMapping(_str(parts[0].strip(), path, max_len=128),
                           _str(parts[1].strip(), path, max_len=128))
    if not isinstance(e, dict):
        raise _err(path, f"expected mapping, got {type(e).__name__}")
    src = _str(e.get("from", e.get("from_node", "")), f"{path}.from", max_len=128)
    dst = _str(e.get("to", e.get("to_node", "")), f"{path}.to", max_len=128)
    mapping = _str_dict(e.get("mapping", {}) or {}, f"{path}.mapping")
    extra = e.get("input")
    if isinstance(extra, dict):
        for k, v in extra.items():
            mapping[_str(k, f"{path}.input", max_len=512)] = _str(v, f"{path}.input.{k}",
                                                                   max_len=512)
    return EdgeMapping(src, dst, reason=_str(e.get("reason", ""), f"{path}.reason"),
                       mapping=mapping,
                       condition=_str(e.get("when", "") or "", f"{path}.when",
                                      max_len=256))


def _model(d: Any, path: str) -> ModelPolicy:
    if isinstance(d, str):
        name = _str(d, path, max_len=64)
        if name not in ("cheap", "standard", "strong"):
            raise _err(path, f"unknown model class {name!r}")
        return ModelPolicy(model_class=name)
    if not isinstance(d, dict):
        raise _err(path, f"expected mapping, got {type(d).__name__}")
    cls = _str(d.get("class", "standard"), f"{path}.class", max_len=32)
    if cls not in ("cheap", "standard", "strong"):
        raise _err(path, f"unknown model class {cls!r}")
    fb = d.get("fallback", ())
    return ModelPolicy(model_class=cls,
                       provider=_str(d["provider"], f"{path}.provider", max_len=128)
                       if "provider" in d else None,
                       model=_str(d["model"], f"{path}.model", max_len=256)
                       if "model" in d else None,
                       fallback_chain=tuple(_str(x, f"{path}.fallback[]", max_len=256)
                                            for x in _as_list(fb, f"{path}.fallback")))


def _retry(d: dict, path: str) -> RetryPolicy:
    if not isinstance(d, dict):
        raise _err(path, f"expected mapping, got {type(d).__name__}")
    return RetryPolicy(
        max_attempts=_int(d.get("max_attempts", 1), f"{path}.max_attempts", 1, 10),
        backoff_base_s=_float(d.get("backoff_base", 2.0), f"{path}.backoff_base", 0, 300),
        backoff_max_s=_float(d.get("backoff_max", 30.0), f"{path}.backoff_max", 0, 3600),
        retry_on_timeout=_bool(d.get("on_timeout", False), f"{path}.on_timeout"),
        unsafe_side_effects=_bool(d.get("unsafe_side_effects", False),
                                  f"{path}.unsafe_side_effects"))


def _policy(d: dict, path: str) -> GraphPolicy:
    if not isinstance(d, dict):
        raise _err(path, f"expected mapping, got {type(d).__name__}")
    b = d.get("budget", {}) or {}
    if not isinstance(b, dict):
        raise _err(f"{path}.budget", "expected mapping")
    conc = d.get("concurrency", {}) or {}
    if not isinstance(conc, dict):
        raise _err(f"{path}.concurrency", "expected mapping")
    budget = {}
    for k in ("max_tokens", "max_cost_usd", "max_runtime_s"):
        if k in b and b[k] is not None:
            budget[k] = _float(b[k], f"{path}.budget.{k}", 0.000001, 1e12)
    return GraphPolicy(
        allowed_nodes=_str_list(d.get("allowed_nodes", ()), f"{path}.allowed_nodes"),
        allowed_tools=_str_list(d.get("allowed_tools", ("all",)), f"{path}.allowed_tools"),
        allowed_models=_str_list(d.get("allowed_models", ()), f"{path}.allowed_models"),
        allowed_providers=_str_list(d.get("allowed_providers", ()),
                                    f"{path}.allowed_providers"),
        max_nodes=_int(d.get("max_nodes", 64), f"{path}.max_nodes", 1, 1024),
        max_concurrency=_int(conc.get("graph", d.get("max_concurrency", 8)),
                             f"{path}.max_concurrency", 1, 128),
        max_depth=_int(d.get("max_depth", 8), f"{path}.max_depth", 0, 256),
        max_retries=_int(d.get("max_retries", 2), f"{path}.max_retries", 0, 10),
        workspace=_str(d.get("workspace", ""), f"{path}.workspace", max_len=1024),
        approval_required=_str_list(d.get("approval_required", ()),
                                    f"{path}.approval_required"),
        **budget)
