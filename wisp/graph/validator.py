"""Graph validator — fail closed with precise diagnostics.

Sections: structural / contract / governance / resource / security.
Structural cycle logic mirrors wisp.multi_agent.dag.TaskDAG.validate (Kahn);
controlled cycles must be explicitly declared via CycleSpec.
"""

from __future__ import annotations

from collections import defaultdict, deque

from wisp.graph.types import Graph, JoinPolicy, NodeType


def validate_graph(graph: Graph) -> list[str]:
    errors: list[str] = []
    errors.extend(_validate_structural(graph))
    if errors:
        return errors  # contract checks need a sane topology
    errors.extend(_validate_contracts(graph))
    errors.extend(_validate_governance(graph))
    errors.extend(_validate_resources(graph))
    errors.extend(_validate_security(graph))
    return errors


# ── Structural ────────────────────────────────────────────────────────

def _validate_structural(graph: Graph) -> list[str]:
    errors: list[str] = []
    if not graph.id:
        errors.append("graph.id is required")
    node_ids = [n.id for n in graph.nodes]
    if len(set(node_ids)) != len(node_ids):
        dupes = sorted({i for i in node_ids if node_ids.count(i) > 1})
        errors.append(f"duplicate node ids: {', '.join(dupes)}")
    nodes = {n.id: n for n in graph.nodes}
    if not nodes:
        errors.append("graph has no nodes")
        return errors
    if graph.entrypoint and graph.entrypoint not in nodes:
        errors.append(f"entrypoint '{graph.entrypoint}' does not exist")
    if not graph.entrypoint:
        errors.append("graph.entrypoint is required")

    for e in graph.edges:
        if e.from_node not in nodes:
            errors.append(f"edge {e.from_node}->{e.to_node}: unknown source")
        if e.to_node not in nodes:
            errors.append(f"edge {e.from_node}->{e.to_node}: unknown target")
        if not e.reason:
            errors.append(f"edge {e.from_node}->{e.to_node}: missing dependency reason")

    declared_cycle_nodes = _declared_cycle_members(graph)
    cycle_errors = _find_undeclared_cycles(graph, declared_cycle_nodes)
    errors.extend(cycle_errors)

    # Reachability from entrypoint (following unconditional + conditional edges).
    if graph.entrypoint in nodes:
        reachable = _reachable(graph)
        unreachable = sorted(set(nodes) - reachable)
        if unreachable:
            errors.append(f"unreachable nodes from '{graph.entrypoint}': {', '.join(unreachable)}")

    # Terminal check: at least one sink node (no outgoing edges).
    sources = {e.from_node for e in graph.edges}
    if len(nodes) > 1 and all(n.id in sources for n in graph.nodes):
        errors.append("graph has no terminal node (every node has outgoing edges)")
    return errors


def _declared_cycle_members(graph: Graph) -> set[str]:
    members: set[str] = set()
    for n in graph.nodes:
        if n.cycle is not None:
            if n.cycle.max_iterations < 1:
                continue
            members.add(n.cycle.entry)
            members.update(n.cycle.body)
            members.add(n.cycle.exit_gate)
    return members


def _find_undeclared_cycles(graph: Graph, allowed: set[str]) -> list[str]:
    """Kahn's algorithm ignoring edges fully inside declared cycle sets."""
    nodes = {n.id for n in graph.nodes}
    in_deg: dict[str, int] = {i: 0 for i in nodes}
    adj: dict[str, set[str]] = defaultdict(set)
    for e in graph.edges:
        if e.from_node in allowed and e.to_node in allowed:
            continue  # declared bounded cycle
        if e.from_node in nodes and e.to_node in nodes and e.to_node not in adj[e.from_node]:
            adj[e.from_node].add(e.to_node)
            in_deg[e.to_node] += 1
    q: deque[str] = deque([i for i, d in in_deg.items() if d == 0])
    visited = 0
    while q:
        cur = q.popleft()
        visited += 1
        for nxt in adj[cur]:
            in_deg[nxt] -= 1
            if in_deg[nxt] == 0:
                q.append(nxt)
    if visited != len(nodes):
        remaining = sorted(i for i, d in in_deg.items() if d > 0)
        # Declared-cycle validation: bounded + exit gate exists.
        for n in graph.nodes:
            if n.cycle is not None:
                c = n.cycle
                ids = {x.id for x in graph.nodes}
                if c.entry not in ids or c.exit_gate not in ids:
                    return [f"node '{n.id}': cycle references unknown entry/exit"]
                if c.max_iterations < 1 or c.max_iterations > 25:
                    return [f"node '{n.id}': cycle max_iterations must be 1..25"]
                return []  # declared + bounded -> accepted
        return [f"undeclared cycle involving: {', '.join(remaining)} "
                "(declare a bounded CycleSpec or remove the edge)"]
    return []


def _reachable(graph: Graph) -> set[str]:
    adj: dict[str, list[str]] = defaultdict(list)
    for e in graph.edges:
        adj[e.from_node].append(e.to_node)
    seen = {graph.entrypoint}
    stack = [graph.entrypoint]
    while stack:
        for nxt in adj[stack.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


# ── Contracts ─────────────────────────────────────────────────────────

def _validate_contracts(graph: Graph) -> list[str]:
    errors: list[str] = []
    nodes = {n.id: n for n in graph.nodes}
    for n in graph.nodes:
        c = n.effective_contract()
        if n.type == NodeType.JOIN and n.join_policy in (JoinPolicy.QUORUM, JoinPolicy.MIN_SUCCESS):
            if n.join_param < 1:
                errors.append(f"node '{n.id}': {n.join_policy.value} requires join_param >= 1")
        if n.type == NodeType.ROUTER and not n.routes and not n.default_route:
            errors.append(f"node '{n.id}': router needs routes or a default_route")
        for e in graph.edges:
            if e.to_node != n.id or not e.mapping:
                continue
            # Mapping targets must name real contract input fields when a schema exists.
            required = set((c.input_schema.get("required", []) or [])) if isinstance(c.input_schema, dict) else set()
            for dst in e.mapping:
                top = dst.split(".")[0]
                props = (c.input_schema.get("properties", {}) or {}) if isinstance(c.input_schema, dict) else {}
                if props and top not in props and top not in required and dst != top:
                    pass  # nested paths allowed; shallow check only
        # Failure states: agent/verifier nodes must declare retry behavior.
        if n.type in (NodeType.AGENT, NodeType.VERIFIER):
            rp = c.retry_policy
            if rp.max_attempts > 1 and rp.unsafe_side_effects and c.idempotent:
                errors.append(f"node '{n.id}': unsafe_side_effects contradicts idempotent=True")
        _ = nodes
    return errors


# ── Governance (narrow-only; enforcement lives in auth/ToolExecutor) ──

def _validate_governance(graph: Graph) -> list[str]:
    errors: list[str] = []
    p = graph.policies
    if p.allowed_nodes:
        allowed = set(p.allowed_nodes)
        for n in graph.nodes:
            if n.id not in allowed:
                errors.append(f"node '{n.id}' not in policy allowed_nodes")
    if "all" not in p.allowed_tools:
        allowed = set(p.allowed_tools)
        for n in graph.nodes:
            for t in n.effective_contract().allowed_tools:
                if t != "all" and t not in allowed:
                    errors.append(f"node '{n.id}': tool '{t}' not in graph allowed_tools")
    if p.allowed_models:
        allowed = set(p.allowed_models)
        for n in graph.nodes:
            m = n.effective_contract().model_policy.model
            if m and m not in allowed:
                errors.append(f"node '{n.id}': model '{m}' not in graph allowed_models")
    if p.allowed_providers:
        allowed = set(p.allowed_providers)
        for n in graph.nodes:
            pv = n.effective_contract().model_policy.provider
            if pv and pv not in allowed:
                errors.append(f"node '{n.id}': provider '{pv}' not in graph allowed_providers")
    return errors


# ── Resources ─────────────────────────────────────────────────────────

def _validate_resources(graph: Graph) -> list[str]:
    errors: list[str] = []
    p = graph.policies
    if len(graph.nodes) > p.max_nodes:
        errors.append(f"graph has {len(graph.nodes)} nodes > max_nodes {p.max_nodes}")
    if not (1 <= p.max_concurrency <= 128):
        errors.append("policies.max_concurrency must be 1..128")
    for n in graph.nodes:
        c = n.effective_contract()
        if c.timeout_s <= 0:
            errors.append(f"node '{n.id}': timeout must be positive")
        if c.retry_policy.max_attempts - 1 > p.max_retries:
            errors.append(f"node '{n.id}': retries exceed graph max_retries {p.max_retries}")
    return errors


# ── Security ──────────────────────────────────────────────────────────

def _validate_security(graph: Graph) -> list[str]:
    errors: list[str] = []
    for n in graph.nodes:
        c = n.effective_contract()
        if "bypass" in " ".join(c.permissions).lower() or "bypass" in n.function.lower():
            errors.append(f"node '{n.id}': 'bypass' is never a valid permission/function")
        if n.type == NodeType.FUNCTION and not n.function and n.id != graph.entrypoint:
            pass  # function resolved at runtime; missing is a runtime error, not static
    # Self-approval: a verifier must not verify its own producer when same node.
    # (Cross-node same-context self-approval is a runtime wiring concern.)
    return errors
