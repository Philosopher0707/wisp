"""Graph validator — fail closed with precise diagnostics.

Sections: structural / contract / governance / resource / security.
Structural cycle logic mirrors wisp.multi_agent.dag.TaskDAG.validate (Kahn);
controlled cycles must be explicitly declared via CycleSpec.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict, deque

from wisp.graph.types import Graph, JoinPolicy, NodeType

# Absolute ceilings — enforced regardless of policy (DoS guard).
MAX_NODES_ABSOLUTE = 1024
MAX_EDGES_ABSOLUTE = 8192
MAX_ID_LEN = 128
MAX_REASON_LEN = 2048
MAX_RETRIES_ABSOLUTE = 10
MAX_JOIN_PARAM = 1024
_NODE_ID_RX = re.compile(r"[A-Za-z0-9_.\-]+")
# Tool names may be MCP-qualified (server:name); unknown names fail closed downstream.
_TOOL_RX = re.compile(r"[A-Za-z0-9_.:/\-]+")


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
    if len(graph.id) > MAX_ID_LEN:
        errors.append("graph.id too long")
    if len(graph.nodes) > MAX_NODES_ABSOLUTE:
        errors.append(f"graph has {len(graph.nodes)} nodes > absolute ceiling "
                      f"{MAX_NODES_ABSOLUTE}")
        return errors
    if len(graph.edges) > MAX_EDGES_ABSOLUTE:
        errors.append(f"graph has {len(graph.edges)} edges > absolute ceiling "
                      f"{MAX_EDGES_ABSOLUTE}")
        return errors
    node_ids = [n.id for n in graph.nodes]
    seen: set[str] = set()
    dupes: set[str] = set()
    for i in node_ids:
        if i in seen:
            dupes.add(i)
        seen.add(i)
    if dupes:
        errors.append(f"duplicate node ids: {', '.join(sorted(dupes))}")
    for nid in node_ids:
        if not nid or len(nid) > MAX_ID_LEN or _NODE_ID_RX.fullmatch(nid) is None:
            errors.append(f"invalid node id {nid!r}: use [A-Za-z0-9_.-], max {MAX_ID_LEN}")
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
        if len(e.reason) > MAX_REASON_LEN:
            errors.append(f"edge {e.from_node}->{e.to_node}: reason too long")
        if len(e.mapping) > 256:
            errors.append(f"edge {e.from_node}->{e.to_node}: too many mappings")

    cycle_errors = _validate_cycles(graph)
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


def _validate_cycles(graph: Graph) -> list[str]:
    """Per-cycle validation + Kahn check exempting only declared members."""
    errors: list[str] = []
    ids = {n.id for n in graph.nodes}
    allowed: set[str] = set()
    for n in graph.nodes:
        if n.cycle is None:
            continue
        c = n.cycle
        if c.entry not in ids or c.exit_gate not in ids:
            errors.append(f"node '{n.id}': cycle references unknown entry/exit")
            continue
        unknown_body = [b for b in c.body if b not in ids]
        if unknown_body:
            errors.append(f"node '{n.id}': cycle body unknown: {', '.join(unknown_body)}")
            continue
        if not (1 <= c.max_iterations <= 25):
            errors.append(f"node '{n.id}': cycle max_iterations must be 1..25")
            continue
        if c.entry == c.exit_gate:
            errors.append(f"node '{n.id}': cycle entry == exit_gate")
            continue
        allowed.add(c.entry)
        allowed.update(c.body)
        allowed.add(c.exit_gate)
    if errors:
        return errors
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
        errors.append(f"undeclared cycle involving: {', '.join(remaining)} "
                      "(declare a bounded CycleSpec or remove the edge)")
    return errors


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
        if n.type == NodeType.JOIN:
            if n.join_policy in (JoinPolicy.QUORUM, JoinPolicy.MIN_SUCCESS):
                if not (1 <= n.join_param <= MAX_JOIN_PARAM):
                    errors.append(f"node '{n.id}': {n.join_policy.value} needs 1..{MAX_JOIN_PARAM}")
            if n.join_timeout_s is not None and not _finite_positive(n.join_timeout_s):
                errors.append(f"node '{n.id}': join_timeout_s must be finite positive")
        if n.type == NodeType.ROUTER:
            if not n.routes and not n.default_route:
                errors.append(f"node '{n.id}': router needs routes or a default_route")
            for label, target in n.routes.items():
                if target not in nodes:
                    errors.append(f"node '{n.id}': route '{label}' targets unknown '{target}'")
            if n.default_route and n.default_route not in nodes:
                errors.append(f"node '{n.id}': default_route '{n.default_route}' unknown")
        if n.type in (NodeType.FUNCTION, NodeType.GATE) and not n.function:
            errors.append(f"node '{n.id}': {n.type.value} node needs a function")
        for e in graph.edges:
            if e.to_node != n.id or not e.mapping:
                continue
            if len(e.mapping) > 256:
                errors.append(f"edge {e.from_node}->{e.to_node}: too many mappings")
            for dst, src in e.mapping.items():
                if not dst or not src or len(dst) > 512 or len(src) > 512:
                    errors.append(f"edge {e.from_node}->{e.to_node}: bad mapping "
                                  f"{dst!r}->{src!r}")
        rp = c.retry_policy
        if not (1 <= rp.max_attempts <= MAX_RETRIES_ABSOLUTE):
            errors.append(f"node '{n.id}': max_attempts must be 1..{MAX_RETRIES_ABSOLUTE}")
        if rp.max_attempts > 1 and rp.unsafe_side_effects and c.idempotent:
            errors.append(f"node '{n.id}': unsafe_side_effects contradicts idempotent=True")
        for tool in c.allowed_tools:
            if tool != "all" and (not tool or len(tool) > MAX_ID_LEN
                                  or _TOOL_RX.fullmatch(tool) is None):
                errors.append(f"node '{n.id}': invalid tool name {tool!r}")
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
            tools = n.effective_contract().allowed_tools
            # "all" against a restrictive graph policy is privilege escalation.
            if "all" in tools:
                errors.append(f"node '{n.id}': allowed_tools='all' forbidden by "
                              f"restrictive graph policy")
            for t in tools:
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
    if p.max_nodes < 1 or p.max_nodes > MAX_NODES_ABSOLUTE:
        errors.append(f"policies.max_nodes must be 1..{MAX_NODES_ABSOLUTE}")
    if not (1 <= p.max_concurrency <= 128):
        errors.append("policies.max_concurrency must be 1..128")
    if p.max_retries < 0 or p.max_retries > MAX_RETRIES_ABSOLUTE:
        errors.append(f"policies.max_retries must be 0..{MAX_RETRIES_ABSOLUTE}")
    for name, val in (("max_tokens", p.max_tokens), ("max_cost_usd", p.max_cost_usd),
                      ("max_runtime_s", p.max_runtime_s)):
        if val is not None and (not isinstance(val, (int, float)) or
                                isinstance(val, bool) or not math.isfinite(val) or val <= 0):
            errors.append(f"policies.{name} must be a finite positive number")
    for n in graph.nodes:
        c = n.effective_contract()
        if not _finite_positive(c.timeout_s):
            errors.append(f"node '{n.id}': timeout must be finite positive")
        if c.retry_policy.max_attempts - 1 > p.max_retries:
            errors.append(f"node '{n.id}': retries exceed graph max_retries {p.max_retries}")
        for name, val in (("budget.max_tokens", c.budget.max_tokens),
                          ("budget.max_cost_usd", c.budget.max_cost_usd),
                          ("budget.max_runtime_s", c.budget.max_runtime_s)):
            if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float))
                                    or not math.isfinite(val) or val <= 0):
                errors.append(f"node '{n.id}': {name} must be a finite positive number")
        if not math.isfinite(c.retry_policy.backoff_base_s) or c.retry_policy.backoff_base_s < 0:
            errors.append(f"node '{n.id}': backoff_base_s must be finite non-negative")
    if p.max_depth < 0 or p.max_depth > 256:
        errors.append("policies.max_depth must be 0..256")
    # max_depth: longest path from entrypoint must fit.
    if graph.entrypoint and 0 <= p.max_depth <= 256:
        depth = _longest_depth(graph)
        if depth is not None and depth > p.max_depth:
            errors.append(f"graph depth {depth} exceeds max_depth {p.max_depth}")
    return errors


def _finite_positive(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v) and v > 0


def _longest_depth(graph: Graph) -> int | None:
    """Longest entrypoint-rooted path, iterative (no recursion-limit DoS)."""
    allowed: set[str] = set()
    for n in graph.nodes:
        if n.cycle is not None:
            allowed.add(n.cycle.entry)
            allowed.update(n.cycle.body)
            allowed.add(n.cycle.exit_gate)
    adj: dict[str, list[str]] = {}
    indeg: dict[str, int] = {n.id: 0 for n in graph.nodes}
    for e in graph.edges:
        if e.from_node in allowed and e.to_node in allowed:
            continue
        if e.from_node in indeg and e.to_node in indeg:
            adj.setdefault(e.from_node, []).append(e.to_node)
            indeg[e.to_node] += 1
    dist: dict[str, int] = {n.id: 0 for n in graph.nodes}
    queue: deque[str] = deque([i for i, d in indeg.items() if d == 0])
    seen = 0
    while queue:
        cur = queue.popleft()
        seen += 1
        for m in adj.get(cur, []):
            dist[m] = max(dist[m], dist[cur] + 1)
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if seen != len(indeg):
        return None  # undeclared cycle; reported elsewhere
    return max([d for nid, d in dist.items()
                if nid in _reachable(graph)] or [0])


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
